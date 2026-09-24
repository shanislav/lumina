"""Take every finished download into the library (``download.completed``) — Lumina
replaces Radarr/Sonarr, nothing else imports.

Movies (needs ``movies_library_dir``):
- new movie → its own folder by the naming rules (``{year}/{title} ({year})``), file named
  by the naming rules; the movie is known (the user picked it) → status "manual"
- ``library_action`` version / replace (chosen in the download dialog), or an owned movie
  downloaded again without a choice (→ version): the file goes next to the existing version
- replace deletes the old video and its same-stem sidecars — but only after the new
  file is safely in place and only when the durations agree (±15 %). Otherwise the new
  file is kept as an additional version and nothing is deleted.
- subtitles next to the download that carry its name ("Movie.cs.srt") move with it

TV episodes (needs ``tv_library_dir``): ``{show} ({year})/Season NN/`` with the original file
name (Plex reads SxxEyy from it); without SxxEyy straight into the show folder.

Without the library folder set, the download stays where it is.
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime

from app.clients.tmdb import TMDBClient
from app.config import get_effective_settings
from app.core import naming
from app.core.film_match import length_verdict
from app.core.mediainfo import probe_async
from app.db import get_db
from app.core.release_name import VIDEO_EXTS
from app.modules.library.notify import emit_movie_updated
from app.modules.library.organize import _ensure_dir, naming_settings

logger = logging.getLogger(__name__)

REPLACE_MAX_DURATION_DIFF = 0.15
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}
_SEASON = re.compile(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e\d{1,3}|(?<![a-z0-9])(\d{1,2})x\d{2}(?![0-9])", re.IGNORECASE)


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def _move(src: str, dst: str) -> None:
    try:
        os.rename(src, dst)
    except OSError:
        # download folder on another filesystem — a new download may be copied
        shutil.move(src, dst)
    try:
        st = os.stat(os.path.dirname(dst))
        os.chown(dst, st.st_uid, st.st_gid)
        os.chmod(dst, 0o664)
    except (OSError, AttributeError):
        pass


def _subtitles_of(video: str) -> list[str]:
    """Subtitle files next to a video that carry its name: "Movie.srt", "Movie.cs.forced.srt"."""
    folder, name = os.path.split(video)
    stem = os.path.splitext(name)[0]
    return [
        os.path.join(folder, entry) for entry in sorted(os.listdir(folder))
        if entry.startswith(stem + ".") and os.path.splitext(entry)[1].lower() in SUBTITLE_EXTS
    ]


def _move_with_subtitles(src: str, target: str) -> None:
    """Move a video and its subtitles; subtitles keep their suffix after the stem (".cs.srt")."""
    subtitles = _subtitles_of(src)
    _move(src, target)
    old_stem = os.path.splitext(os.path.basename(src))[0]
    new_stem = os.path.splitext(target)[0]
    for sub in subtitles:
        suffix = os.path.basename(sub)[len(old_stem):]
        dst = new_stem + suffix
        if not os.path.exists(dst):
            _move(sub, dst)


def _delete_version(video: str) -> list[str]:
    """Delete a video and its same-stem sidecars (subtitles, .nfo). Returns deleted paths."""
    folder, name = os.path.split(video)
    stem = os.path.splitext(name)[0]
    deleted = []
    for entry in os.listdir(folder):
        path = os.path.join(folder, entry)
        if path == video or (entry.startswith(stem + ".") and os.path.splitext(entry)[1].lower() not in VIDEO_EXTS):
            os.remove(path)
            deleted.append(path)
    return deleted


def _durations_agree(a: int, b: int) -> bool:
    if not a or not b:
        return True  # unknown → do not block on it
    return abs(a - b) / max(a, b) <= REPLACE_MAX_DURATION_DIFF


async def on_download_completed(payload: dict) -> None:
    if payload.get("imported"):
        return
    content_type = payload.get("content_type") or "movie"
    if content_type == "tv":
        await import_episode(payload)
    elif content_type == "movie" and payload.get("tmdb_id"):
        await import_movie(payload)


async def import_episode(payload: dict) -> None:
    cfg = await get_effective_settings()
    root = cfg.get("tv_library_dir") or ""
    src = payload["path"]
    if not root:
        logger.info("TV library folder not set — %s stays in downloads", src)
        return
    title = payload.get("title") or ""
    if not title:
        logger.warning("Import of %s skipped: no show title", src)
        return
    year = str(payload.get("year") or "")[:4]
    show = naming.sanitize(f"{title} ({year})" if year else title)
    folder = os.path.join(root, show)
    m = _SEASON.search(os.path.basename(src))
    if m:
        folder = os.path.join(folder, f"Season {int(m.group(1) or m.group(2)):02d}")
    _ensure_dir(folder)
    target = _unique_path(os.path.join(folder, os.path.basename(src)))
    _move_with_subtitles(src, target)
    payload["path"] = target
    payload["imported"] = True
    logger.info("Imported episode %s", target)


async def import_movie(payload: dict) -> None:
    from app.modules.library.importer import tmdb_details

    action = payload.get("library_action") or {}
    mode = action.get("mode") if action.get("mode") in ("replace", "version") else "new"
    cfg = await get_effective_settings()
    root = cfg.get("movies_library_dir") or ""
    src = payload["path"]
    tmdb_id = payload["tmdb_id"]
    db = await get_db()
    client = TMDBClient(cfg.get("tmdb_api_key", ""))
    try:
        cursor = await db.execute(
            "SELECT * FROM library_movies WHERE tmdb_id = ? AND status IN ('matched', 'manual') ORDER BY id", (tmdb_id,)
        )
        owned = [dict(r) for r in await cursor.fetchall()]
        old = next((r for r in owned if r["id"] == action.get("file_id")), None) if mode == "replace" else None
        if mode == "new" and owned:
            mode = "version"  # owned movie downloaded again without a choice — never delete anything
        if not owned and not root:
            logger.info("Movie library folder not set — %s stays in downloads", src)
            return

        details = await tmdb_details(client, db, tmdb_id)
        if not details:
            logger.error("Import of %s skipped: no TMDB details for %s", src, tmdb_id)
            return
        settings = await naming_settings()
        title = naming.pick_title(details.get("titles_by_lang") or {}, details.get("original_language", ""),
                                  details.get("original_title", ""), settings["language"], settings["keep_local_original"])
        media = await probe_async(src)
        rel_folder, file_name = naming.movie_paths(
            {"tmdb_id": tmdb_id, "imdb_id": details.get("imdb_id"), "year": details.get("year")},
            media, os.path.basename(src), title, os.path.splitext(src)[1],
            settings["folder_format"], settings["file_format"],
        )
        # Keep versions together: next to the version being replaced / the first owned one.
        anchor = old or (owned[0] if owned else None)
        folder = os.path.dirname(anchor["file_path"]) if anchor else os.path.join(root, *rel_folder.split("/"))
        _ensure_dir(folder)
        target = _unique_path(os.path.join(folder, file_name))
        _move_with_subtitles(src, target)
        payload["path"] = target
        payload["imported"] = True

        # The user picked the movie, but the file may still be another cut, a sample or a
        # different film: a length that does not fit sends it to the review queue.
        mismatch = length_verdict(media.get("duration_s") or 0, details.get("runtime") or 0)
        if mismatch:
            logger.warning("Imported %s for review: %s", target, mismatch)
        candidates = [{
            "tmdb_id": tmdb_id, "title": details["title"], "original_title": details["original_title"],
            "year": details.get("year"), "runtime": details.get("runtime"), "poster_url": details.get("poster_url"),
            "score": 0, "reasons": [f"staženo jako tento film, ale {mismatch}"],
        }] if mismatch else []
        stat = os.stat(target)
        values = {
            "tmdb_id": tmdb_id, "title": details["title"], "original_title": details["original_title"],
            "year": str(details.get("year") or ""), "poster_url": details.get("poster_url"),
            "overview": details.get("overview"), "imdb_id": details.get("imdb_id", ""),
            "filename": os.path.basename(target), "file_path": target, "file_size": stat.st_size,
            "file_mtime": stat.st_mtime, "quality": naming.resolution_label(media) or "unknown",
            "language": ",".join(sorted({a["lang"].upper() for a in media.get("audio", []) if a.get("lang")})),
            "media": json.dumps(media), "duration_s": media.get("duration_s") or 0,
            "candidates": json.dumps(candidates), "confidence": 50 if mismatch else 100,
            "status": "review" if mismatch else "manual", "matched_by": "download",
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        cursor = await db.execute(
            f"INSERT INTO library_movies ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
        new_id = cursor.lastrowid
        await db.commit()
        logger.info("Imported %s as %s of tmdb %s", target, mode, tmdb_id)

        if old:
            if not mismatch and _durations_agree(media.get("duration_s") or 0, old.get("duration_s") or 0) and os.path.exists(old["file_path"]):
                deleted = _delete_version(old["file_path"])
                await db.execute("DELETE FROM library_movies WHERE id = ?", (old["id"],))
                for path in deleted:
                    await db.execute(
                        "INSERT INTO file_operations (batch_id, movie_id, src, dst, status) VALUES (?, ?, ?, '', 'deleted')",
                        (f"replace-{new_id}", old["id"], path),
                    )
                # same name as the replaced version → it had to wait under "… (2)"; take the clean name now
                desired = os.path.join(folder, file_name)
                if target != desired and not os.path.exists(desired):
                    for sub in _subtitles_of(target):
                        dst = os.path.splitext(desired)[0] + os.path.basename(sub)[len(os.path.splitext(os.path.basename(target))[0]):]
                        if not os.path.exists(dst):
                            os.rename(sub, dst)
                    os.rename(target, desired)
                    payload["path"] = target = desired
                    await db.execute("UPDATE library_movies SET file_path = ?, filename = ? WHERE id = ?",
                                     (desired, os.path.basename(desired), new_id))
                await db.commit()
                logger.info("Replaced %s (deleted %d files)", old["file_path"], len(deleted))
            else:
                logger.warning("Replace of %s skipped: length does not fit (%s s vs %s s) — kept both versions",
                               old["file_path"], media.get("duration_s"), old.get("duration_s"))

        await emit_movie_updated(db, new_id)
    finally:
        await client.close()
        await db.close()
