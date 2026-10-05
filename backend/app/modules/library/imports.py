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

A film TMDB does not know (searched straight in the files, e.g. the fan parody "Pár Pařmenů"):
the same naming rules with the title/year the user searched for (year from the file name
when missing); status "manual" without a tmdb_id — organize, NFO and upgrades skip it.

TV episodes (needs ``tv_library_dir``): into the show's existing folder (else ``{show} ({year})``),
each season into its existing folder (else ``Season NN``), with the original file name (Plex reads
SxxEyy from it); a season pack from a torrent brings all its episodes. See ``import_episode``.

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
from app.db import get_automation, get_db
from app.core.release_name import SUBTITLE_EXTS, VIDEO_EXTS
from app.core import events
from app.modules.library import episode_names, pack_plan, tv_inventory
from app.modules.library.notify import emit_movie_updated
from app.modules.library.organize import _ensure_dir, naming_settings

logger = logging.getLogger(__name__)

REPLACE_MAX_DURATION_DIFF = 0.15
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


def _delete_version(video: str, replacement: str = "") -> list[str]:
    """Delete a video and its same-stem sidecars (subtitles, .nfo). Returns deleted paths.

    When the video was the only one in its folder (besides its replacement), subtitles named after
    something else belong to it too ("Parasite (2021) [...].ass" from an older naming scheme) —
    they are timed to that release, so they go with it. The replacement's own subtitles stay.
    """
    folder, name = os.path.split(video)
    stem = os.path.splitext(name)[0]
    keep_stem = os.path.splitext(os.path.basename(replacement))[0] if replacement else None
    entries = os.listdir(folder)
    others = [e for e in entries if os.path.splitext(e)[1].lower() in VIDEO_EXTS
              and os.path.join(folder, e) not in (video, replacement)]
    deleted = []
    for entry in entries:
        path = os.path.join(folder, entry)
        ext = os.path.splitext(entry)[1].lower()
        own_sidecar = entry.startswith(stem + ".") and ext not in VIDEO_EXTS
        orphan_subtitle = (not others and ext in SUBTITLE_EXTS
                           and not (keep_stem and entry.startswith(keep_stem + ".")))
        if path == video or own_sidecar or orphan_subtitle:
            os.remove(path)
            deleted.append(path)
    return deleted


async def delete_version(db, movie_id: int, root: str) -> list[str]:
    """Delete one version of a movie from disk and the library (the user confirmed it; no trash).

    The video goes with its subtitles/NFO. When it was the last video in its own folder, the whole
    folder goes (posters, old NFOs …) and empty parents up to the library root. Returns deleted paths.
    """
    from app.modules.library.organize import _remove_empty_dirs

    cursor = await db.execute("SELECT id, tmdb_id, file_path FROM library_movies WHERE id = ?", (movie_id,))
    row = await cursor.fetchone()
    if not row:
        return []
    video, tmdb_id = row["file_path"], row["tmdb_id"]
    folder = os.path.dirname(video)
    deleted: list[str] = []
    if os.path.exists(video):
        deleted = _delete_version(video)
        own_folder = os.path.normpath(folder) != os.path.normpath(root)
        remaining = os.listdir(folder) if os.path.isdir(folder) else []
        if own_folder and not any(os.path.splitext(e)[1].lower() in VIDEO_EXTS for e in remaining):
            for entry in remaining:
                path = os.path.join(folder, entry)
                if os.path.isfile(path):
                    os.remove(path)
                    deleted.append(path)
            _remove_empty_dirs(folder, root)
    await db.execute("DELETE FROM library_movies WHERE id = ?", (movie_id,))
    for path in deleted:
        await db.execute("INSERT INTO file_operations (batch_id, movie_id, src, dst, status) VALUES (?, ?, ?, '', 'deleted')",
                         (f"delete-{movie_id}", movie_id, path))
    await db.commit()
    logger.info("Deleted version %s (%d files)", video, len(deleted))
    await events.emit("library.files_removed", {"folders": [folder]})
    # the other versions' NFO (list of versions) follows
    if tmdb_id:
        cursor = await db.execute("SELECT id FROM library_movies WHERE tmdb_id = ? AND status IN ('matched', 'manual')", (tmdb_id,))
        other = await cursor.fetchone()
        if other:
            await emit_movie_updated(db, other[0])
    return deleted


def _replaced(owned: dict, season: int, episodes: list[int], part: int | None) -> list[str]:
    """The owned files a new file of these episodes replaces: the episode's file with its parts ("- pt1" and
    "- pt2" both go for a whole new file or its 1st part); a new 2nd part replaces only the old 2nd part (never
    the new 1st part imported a moment ago, nor the old whole episode — that goes when the new 1st part comes). A
    file of more episodes ("S04E01-E02") goes only when the new one holds them all — else the other episode would
    be lost; the old file stays its file."""
    from app.modules.library.episodes import parts_of
    out: list[str] = []
    for ep in episodes:
        old = owned.get((season, ep))
        if not old or not os.path.exists(old):
            continue
        keeps = sorted(e for (s, e), p in owned.items() if p == old and (s != season or e not in episodes))
        if keeps:
            logger.info("S%02dE%02d: %s kept — it holds E%s too", season, ep, os.path.basename(old),
                        ", E".join(f"{e:02d}" for e in keeps))
            continue
        parts = parts_of(old)
        group = [p for n, p in parts if n == part] if part and part >= 2 else [p for _n, p in parts] or [old]
        out += [p for p in group if p not in out]
    return out


def _durations_agree(a: int, b: int) -> bool:
    if not a or not b:
        return True  # unknown → do not block on it
    return abs(a - b) / max(a, b) <= REPLACE_MAX_DURATION_DIFF


async def on_download_completed(payload: dict) -> None:
    # held_by: another module works on the file first and emits the event again when done
    if payload.get("imported") or payload.get("held_by"):
        return
    content_type = payload.get("content_type") or "movie"
    if content_type == "tv":
        await import_episode(payload)
    elif content_type == "movie" and payload.get("tmdb_id"):
        await import_movie(payload)
    elif content_type == "movie" and payload.get("title"):
        await import_custom_movie(payload)


async def import_custom_movie(payload: dict) -> None:
    """A film without a TMDB entry: folder/file by the naming rules from the searched title."""
    from app.core.release_name import parse_name

    cfg = await get_effective_settings()
    root = cfg.get("movies_library_dir") or ""
    src = payload["path"]
    if not root:
        logger.info("Movie library folder not set — %s stays in downloads", src)
        return
    title = payload["title"].strip()
    year = int(str(payload.get("year") or 0)[:4] or 0) or parse_name(os.path.basename(src)).year or None
    settings = await naming_settings()
    media = await probe_async(src)
    rel_folder, file_name = naming.movie_paths(
        {"tmdb_id": None, "imdb_id": None, "year": year}, media, os.path.basename(src), title,
        os.path.splitext(src)[1], settings["folder_format"], settings["file_format"],
    )
    folder = os.path.join(root, *[p for p in rel_folder.split("/") if p])
    _ensure_dir(folder)
    target = _unique_path(os.path.join(folder, file_name))
    _move_with_subtitles(src, target)
    payload["path"] = target
    payload["imported"] = True

    poster, overview = await _wikidata_extras(title, year)
    stat = os.stat(target)
    values = {
        "tmdb_id": None, "title": title, "original_title": title, "year": str(year or ""),
        "poster_url": poster, "overview": overview,
        "filename": os.path.basename(target), "file_path": target, "file_size": stat.st_size,
        "file_mtime": stat.st_mtime, "quality": naming.resolution_label(media) or "unknown",
        "language": ",".join(sorted({a["lang"].upper() for a in media.get("audio", []) if a.get("lang")})),
        "media": json.dumps(media), "duration_s": media.get("duration_s") or 0,
        "candidates": "[]", "confidence": 100, "status": "manual", "matched_by": "download",
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    db = await get_db()
    try:
        await db.execute(
            f"INSERT INTO library_movies ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
        await db.commit()
    finally:
        await db.close()
    logger.info("Imported %s (not in TMDB)", target)


async def _wikidata_extras(title: str, year: int | None) -> tuple[str | None, str]:
    """Poster and description of a film TMDB does not know, when Wikidata has it."""
    from app.clients.wikidata import WikidataClient
    client = WikidataClient()
    try:
        films = await client.search_films(title)
    except Exception as e:
        logger.info("Wikidata lookup of %s failed: %s", title, e)
        return None, ""
    finally:
        await client.close()
    match = next((f for f in films if year and f["year"] == year), None) or (films[0] if len(films) == 1 else None)
    return (match["poster_url"], match["overview"]) if match else (None, "")


def _show_folders(root: str, rows: list) -> tuple[str | None, dict[int, str]]:
    """The show's folder in the library and the folder of each season, from the episodes it has —
    new episodes go where the user keeps the others ("Black Books/Black Books EN/Black Books Season 1")."""
    show_root, seasons = None, {}
    for row in rows:
        path = row["file_path"] or ""
        try:
            rel = os.path.relpath(path, root) if path else ""
        except ValueError:                  # another drive (Windows)
            continue
        if not rel or rel.startswith("..") or os.sep not in rel:
            continue
        show_root = show_root or os.path.join(root, rel.split(os.sep)[0])
        seasons.setdefault(row["season"], os.path.dirname(path))
    return show_root, seasons


async def _ensure_show(db, tmdb_id: int, title: str, year: str) -> None:
    """The show's row in the library (the library page lists shows from it)."""
    if await (await db.execute("SELECT 1 FROM library_shows WHERE tmdb_id = ?", (tmdb_id,))).fetchone():
        return
    values = {"tmdb_id": tmdb_id, "title": title, "original_title": title, "year": year, "poster_url": None,
              "overview": "", "total_seasons": 0, "total_episodes": 0}
    cfg = await get_effective_settings()
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        d = await client.get_tv_details(tmdb_id)
        values.update(title=d["title"] or title, original_title=d["original_title"] or title,
                      year=(d.get("first_air_date") or "")[:4] or year, poster_url=d["poster_url"],
                      overview=d["overview"], total_seasons=d["total_seasons"], total_episodes=d["total_episodes"])
    except Exception as e:
        logger.info("TMDB details of show %s failed: %s", tmdb_id, e)
    finally:
        await client.close()
    await db.execute(f"INSERT OR IGNORE INTO library_shows ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                     tuple(values.values()))


async def _tv_names(db, tmdb_id: int, title: str, year: str) -> dict | None:
    """What a new episode is named by when the renamer is on: the TV templates, the show's title in the
    renamer's language, TMDB's episode names — as the TV renamer names the library."""
    from app.modules.library import organize_tv

    cfg = await get_effective_settings()
    client = TMDBClient(cfg.get("tmdb_api_key", ""))
    try:
        details = await organize_tv.show_details(client, db, tmdb_id) or {}
    finally:
        await client.close()
    settings = await naming_settings()
    plex = await (await db.execute("SELECT plex_title FROM tv_folders WHERE tmdb_id = ? AND plex_title != '' LIMIT 1",
                                   (tmdb_id,))).fetchone()
    show_title = organize_tv.show_title(details, settings, plex[0] if plex else "") or title
    titles = {(r[0], r[1]): r[2] or "" for r in await (await db.execute(
        "SELECT season, episode, episode_title FROM library_episodes WHERE show_tmdb_id = ?", (tmdb_id,))).fetchall()}
    return {"settings": settings, "title": show_title, "info": {"tmdb_id": tmdb_id, "year": details.get("year") or year},
            "titles": titles}


def _which_episode(name: str, season: int | None, episodes: list[int], cat: dict, action: dict,
                   show_names=()) -> tuple[int | None, list[int], str]:
    """(season, episodes, why) of a downloaded file: its own episode name when it surely is a TMDB episode
    (uploaders number by another order — "S01E02 - Sopka" is TMDB's S01E03), else the episode the user
    picked it for (a single download, ``action``), else its numbers."""
    if len(episodes) <= 1 and cat:
        hit = episode_names.release_episode(name, cat, season if season is not None else _int(action.get("season")),
                                            show_names)
        if hit and hit[1]:
            (s, e), _sure, title = hit
            return s, [e], f"staženo jako „{name}“ — podle názvu dílu „{title}“"
    if action.get("mode") == "episode" and action.get("episode") and len(episodes) <= 1:
        want = (int(action["season"]), [int(action["episode"])])
        if want != (season, episodes):
            return *want, f"staženo jako „{name}“ pro S{want[0]:02d}E{want[1][0]:02d}"
    return season, episodes, ""


_FOLDER_SEASON = re.compile(r"(?i)(?<![a-z0-9])(?:season|s[ée]rie|serija|sezona|s)[ ._-]?(\d{1,2})(?!\d)|(?<!\d)(\d{1,2})\.?[ ._-]?(?:s[ée]rie|serija|sezona|season)")
_LEADING = re.compile(r"^\s*(?:e|ep|díl|dil)?\s*(\d{1,3})(?!\d)[ ._-]")


def _in_pack(path: str, season: int | None, episodes: list[int], pack_season: int | None = None
             ) -> tuple[int | None, list[int]]:
    """A file of a whole-show pack: the season also from its folders ("South Park/Season 03/05 - Name.avi",
    "Série 3/"), else the pack's one season ("Chalupáři S01" holding "Chalupari/01 - Chudak dedecek.avi");
    the episode also from a leading number."""
    if season is None:
        from app.modules.downloads.monitor import SEEDING_DIR
        parts = [p for p in os.path.dirname(path).split(os.sep)
                 if p and p != SEEDING_DIR and not re.fullmatch(r"[0-9a-f]{16}", p)]   # the seeding copy's folder
        for part in reversed(parts[-4:]):
            m = _FOLDER_SEASON.search(part)
            if m:
                season = int(m.group(1) or m.group(2))
                break
    if season is None:
        season = pack_season
    if season is not None and not episodes:
        m = _LEADING.match(os.path.splitext(os.path.basename(path))[0])
        episodes = [int(m.group(1))] if m else []
    return season, episodes


UNSORTED_DIR = "Nezařazeno"


async def _extra(path: str, show_root: str, folder: str) -> str:
    """A pack's bonus into the show's Plex extras folder ("Behind The Scenes", "Other" …)."""
    target_dir = os.path.join(show_root, folder)
    _ensure_dir(target_dir)
    target = _unique_path(os.path.join(target_dir, os.path.basename(path)))
    _move_with_subtitles(path, target)
    logger.info("%s: a bonus — %s", os.path.basename(path), target)
    return target
_PT = re.compile(r"(?i) - pt(\d)\.[a-z0-9]{2,4}$")


def file_part(name: str, cat: dict, season: int | None, episodes: list[int], show_names=()) -> int | None:
    """The part of a two-part episode TMDB keeps as one, which this file is ("Vítej v Koreji II" → 2) — None
    for a whole episode."""
    if season is None or len(episodes) != 1 or not cat:
        return None
    for title in episode_names.release_titles(name, show_names):
        key, part = episode_names.part_episode(title, cat, season)
        if key == (season, episodes[0]) and part:
            return part
    return None


def is_episode_length(duration_s: int, cat: dict) -> bool:
    """A file as long as the show's episodes (not a film or a short extra of the pack). Its length unknown (no
    MediaInfo): no — it stays in the downloads, as before; TMDB knows no runtime: yes."""
    runtimes = sorted(v.get("runtime") or 0 for k, v in cat.items() if k[0] > 0 and v.get("runtime"))
    if not duration_s:
        return False
    if not runtimes:
        return True
    typical = runtimes[len(runtimes) // 2] * 60
    return 0.5 * typical <= duration_s <= 1.6 * typical


async def _unsorted(db, path: str, root: str, show_root: str, tmdb_id) -> str:
    """A downloaded episode Lumina can not place: into "<show>/Nezařazeno/", in the library check as an unknown
    episode — "Upravit díly…" places it (by hand, or the AI's suggestion)."""
    folder = os.path.join(show_root, UNSORTED_DIR)
    _ensure_dir(folder)
    target = _unique_path(os.path.join(folder, os.path.basename(path)))
    _move_with_subtitles(path, target)
    show_folder = os.path.relpath(show_root, root).split(os.sep)[0]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        await db.execute("INSERT OR IGNORE INTO tv_folders (folder, tmdb_id, source, files, scanned_at) VALUES (?, ?, 'lumina', 0, ?)",
                         (show_folder, tmdb_id, now))
        await db.execute("INSERT OR REPLACE INTO tv_files (file_path, folder, show_tmdb_id, season, episodes, status, note, facts, "
                         "scanned_at) VALUES (?, ?, ?, NULL, '[]', 'unknown', ?, ?, ?)",
                         (target, show_folder, tmdb_id, "stažený díl — Lumina ho nedokázala zařadit",
                          json.dumps({"title": episode_names.bare_title(os.path.basename(path))
                                      or episode_names.title_in_name(os.path.basename(path))}), now))
    except Exception as e:  # noqa: BLE001 — the inventory tables missing: the file is in the folder anyway
        logger.info("Unsorted %s: not in the library check: %s", target, e)
    logger.info("%s: episode not known — %s", os.path.basename(path), target)
    return target


def pack_by_name_or_order(path: str, cat: dict, show_names=(), duration_s: int = 0) -> tuple[int | None, list[int]]:
    """A file of a whole-show pack numbered through all the seasons, no season anywhere ("Columbo (CS)/
    01 - Vražda na předpis.avi" … "69 - …"): its episode name when TMDB surely knows it (with its length: a far
    match only when the length fits too), else a three-digit absolute number ("Naruto 153"), else a bare number
    as the n-th of the show's episodes in TMDB's order (no specials)."""
    if not cat:
        return None, []
    name = os.path.basename(path)
    hit = episode_names.release_episode(name, cat, None, show_names)
    if hit and hit[1]:
        (season, episode), _sure, _title = hit
        return season, [episode]
    titles = episode_names.release_titles(name, show_names)
    if duration_s:
        for title in titles:
            key, sure = episode_names.best(title, cat, None, duration_s)
            if key and sure:
                return key[0], [key[1]]
    order = sorted(k for k in cat if k[0] > 0)
    absolute = episode_names.absolute_in_name(name)
    if absolute and 0 < absolute <= len(order) and not titles:
        return order[absolute - 1][0], [order[absolute - 1][1]]
    if titles:
        return None, []          # it has a name TMDB does not know: its number may count other episodes — no guess
    m = re.match(r"(?i)^\s*(?:e|ep|díl|dil)?\s*(\d{1,3})(?!\d)", os.path.splitext(name)[0])
    if m and 1 <= int(m.group(1)) <= len(order):
        season, episode = order[int(m.group(1)) - 1]
        return season, [episode]
    return None, []


def pack_episodes(path: str, pack_season: int | None = None) -> tuple[int | None, list[int]]:
    """(season, episodes) of a file of a whole-show pack — its name, else its folders and a leading number."""
    from app.core.episode_match import parse_episode

    info = parse_episode(os.path.basename(path))
    season = info.season
    if season is None:
        m = _SEASON.search(os.path.basename(path))
        season = int(m.group(1) or m.group(2)) if m else None
    return _in_pack(path, season, list(info.episodes), pack_season)


def pack_skip(files: list[dict], owned: set[tuple[int, int]], pack_season: int | None = None) -> list[int]:
    """Indexes of a pack's files not to download: videos of episodes the user has (all of the file's episodes),
    with their subtitles. files: qBittorrent's [{index, name}]."""
    skip, stems = [], set()
    for f in files:
        name = f["name"].replace("\\", "/")
        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
            continue
        season, episodes = pack_episodes(name.replace("/", os.sep), pack_season)
        if season is not None and episodes and all((season, e) in owned for e in episodes):
            skip.append(f["index"])
            stems.add(os.path.splitext(name)[0])
    for f in files:
        name = f["name"].replace("\\", "/")
        if f["index"] not in skip and os.path.splitext(name)[1].lower() in SUBTITLE_EXTS                 and any(name.startswith(stem + ".") for stem in stems):
            skip.append(f["index"])
    return sorted(skip)


async def _episode_title(names: dict, cat: dict, tmdb_id: int, season: int, episode: int) -> str:
    """The episode's name for the file: the library's, else TMDB's catalog; a generic one ("2. epizoda") is
    none — a new episode is named shortly before it airs: the catalog is read again then (an hour old)."""
    title = names["titles"].get((season, episode)) or ""
    if naming.episode_title(title):
        return title
    title = _catalog_title(cat, season, episode)
    if not title and tmdb_id:
        from app.db import get_db as _db
        db = await _db()
        client = episode_names._client((await get_effective_settings()).get("tmdb_api_key", ""))
        try:
            fresh = await episode_names.catalog(client, db, tmdb_id, max_age_s=3600)
        except Exception:  # noqa: BLE001 — TMDB down: no name
            fresh = {}
        finally:
            await client.close()
            await db.close()
        cat.update(fresh)
        title = _catalog_title(cat, season, episode)
    return title


def _catalog_title(cat: dict, season: int, episode: int) -> str:
    """TMDB's name of the episode in the user's language, else the English one (a show new to the library has
    no names in library_episodes yet)."""
    entry = cat.get((season, episode)) or {}
    cs = entry.get("cs") or ""
    return cs if naming.episode_title(cs) else (entry.get("en") or "") if naming.episode_title(entry.get("en") or "") else ""


def _lang_from_release(media: dict, release: str) -> dict:
    """A file whose one sound track has no language (AVI, untagged MKV), from a release that names exactly one
    language ("Chalupáři S01 (1975)(CZ)"): that language — Lumina keeps it (the renamer writes "[CS]")."""
    from app.modules.library.files import _detect_language
    audio = (media or {}).get("audio") or []
    langs = [l for l in (_detect_language(release or "") or "").split(",") if l]
    if len(audio) != 1 or (audio[0].get("lang") or "").strip() or len(langs) != 1:
        return media
    code = {"CZ": "cs", "SK": "sk", "EN": "en", "JP": "ja"}.get(langs[0])
    return {**media, "audio": [{**audio[0], "lang": code}]} if code else media


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def import_episode(payload: dict) -> None:
    """Episodes into the show's folder (the one it has in the library, else "{show} ({year})"), each
    season into its folder (the existing one, else "Season NN"), names kept (Plex reads SxxEyy).
    A season pack brings all its episodes (``extra_paths``). The library learns about them at once.
    With the renamer on, a new show's folder, a new season's folder and the file are named by the TV
    templates (core/naming.episode_paths) — an existing folder of the show stays the one used.
    ``library_action`` {"mode": "episode", "season", "episode", "replace"}: the episode the user picked
    (for files without SxxEyy in the name); replace = the owned file of that episode goes."""
    from app.core.episode_match import parse_episode

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
    action = payload.get("library_action") or {}
    tmdb_id = payload.get("tmdb_id")
    year = str(payload.get("year") or "")[:4]
    db = await get_db()
    try:
        rows = []
        if tmdb_id:
            rows = await (await db.execute(
                "SELECT season, episode, file_path, language FROM library_episodes WHERE show_tmdb_id = ? AND has_file = 1 "
                "ORDER BY season, episode", (tmdb_id,))).fetchall()
        show_root, season_dirs = _show_folders(root, rows)
        renamer = await get_automation("renamer")
        names = await _tv_names(db, tmdb_id, title, year) if tmdb_id and renamer and renamer["enabled"] else None
        if not show_root and names:
            folder_rel, _s, _f = naming.episode_paths(names["info"], 1, [1], {}, "", names["title"], ".mkv",
                                                      folder_format=names["settings"]["tv_folder_format"])
            show_root = os.path.join(root, *folder_rel.split("/"))
        show_root = show_root or os.path.join(root, naming.sanitize(f"{title} ({year})" if year else title))
        owned = {(r["season"], r["episode"]): r["file_path"] for r in rows}
        # owned without Czech / Slovak sound ("EN"): a downloaded file with it takes the episode's place
        owned_foreign = {(r["season"], r["episode"]) for r in rows
                         if r["language"] and not ({"CS", "SK"} & set((r["language"] or "").upper().split(",")))}
        cat = {}
        if tmdb_id:
            _check, cat = await episode_names.release_checker(cfg.get("tmdb_api_key", ""), tmdb_id, None)
        extras = payload.get("extra_paths") or []
        # a pack's files: path here → its path in the torrent (its key in the plan)
        pack_files = dict(payload.get("pack_files") or {})
        if payload.get("pack_file"):
            pack_files[src] = payload["pack_file"]
        targets = []
        unsorted: list[str] = []
        placed: list[str] = []                 # "S01E04" — what went to the library (the notification names them)
        left: list[list[str]] = []             # [file name, why] — what stayed in the downloads
        for path in [src, *extras]:
            info = parse_episode(os.path.basename(path))
            season = info.season
            episodes = list(info.episodes)
            if path == src and action.get("season") is not None:
                season = season if season is not None else int(action["season"])
                episodes = episodes or ([int(action["episode"])] if action.get("episode") else [])
            if season is None:
                m = _SEASON.search(os.path.basename(path))
                season = int(m.group(1) or m.group(2)) if m else None
            pack = action.get("mode") == "pack"
            # the pack's plan (pack_plan, made when its list of files came): where this file goes
            planned = pack_plan.plan_for(action.get("plan"), pack_files.get(path)) if pack else None
            if planned and planned.get("kind") != "episode":
                kind = planned.get("kind")
                if kind == "extra":
                    target = await _extra(path, show_root, planned.get("extra") or "Other")
                    targets.append(target)
                    placed.append(f"bonus {os.path.basename(target)}")
                elif kind == "unknown":
                    unsorted.append(await _unsorted(db, path, root, show_root, tmdb_id))
                else:
                    left.append([os.path.basename(path), planned.get("why") or kind])
                continue
            if planned:
                file_numbers = (season, list(episodes))
                season, episodes = planned["season"], list(planned["episodes"])
                part = planned.get("part")
                duration = (await probe_async(path)).get("duration_s") or 0
                runtime = sum(cat.get((season, ep), {}).get("runtime") or 0 for ep in episodes)
                if not part and runtime and duration > 1.8 * runtime * 60:
                    logger.info("%s: %d min for S%02dE%02d of %d min — not placed", path, duration // 60, season,
                                episodes[0], runtime)
                    unsorted.append(await _unsorted(db, path, root, show_root, tmdb_id))
                    continue
                why = planned.get("why", "") if file_numbers != (season, episodes) else ""
            if pack and not planned:
                season, episodes = _in_pack(path, season, episodes, _int(action.get("pack_season")))
                if season is None or not episodes:
                    duration = (await probe_async(path)).get("duration_s") or 0
                    season, episodes = pack_by_name_or_order(path, cat, [title], duration)
                if season is None or not episodes:
                    if is_episode_length(duration, cat):
                        # an episode Lumina can not place: into the show's folder, for "Upravit díly…" (by hand / AI)
                        unsorted.append(await _unsorted(db, path, root, show_root, tmdb_id))
                    else:
                        logger.info("%s: no episode (a film, an extra) — left in downloads", path)
                        left.append([os.path.basename(path), "není díl (film, bonus)" if duration else "neznámý díl"])
                    continue
            if not planned:
                file_numbers = (season, list(episodes))
                season, episodes, why = _which_episode(os.path.basename(path), season, episodes, cat,
                                                       action if path == src and not extras else {}, [title])
                # a part of a two-part episode TMDB keeps as one ("Vítej v Koreji II"): "- pt2" next to the first part
                part = file_part(os.path.basename(path), cat, season, episodes, [title])
            dub_over = False
            owned_path = owned.get((season, episodes[0])) if season is not None and episodes else None
            if part is None and owned_path and _PT.search(owned_path) and _PT.search(owned_path).group(1) != "1":
                part = 1                                    # the 2nd part came first: this one is the 1st
            if (path != src or pack) and season is not None and episodes and not action.get("replace_owned", True) \
                    and all((season, ep) in owned for ep in episodes) and all((season, ep) in owned_foreign for ep in episodes):
                new_langs = {a["lang"] for a in _lang_from_release(await probe_async(path), action.get("release") or
                                                                   os.path.basename(path)).get("audio", []) if a.get("lang")}
                dub_over = bool({"cs", "sk"} & {lang.lower() for lang in new_langs})
                if dub_over:
                    logger.info("%s: S%02dE%02d owned only without CZ/SK — the dub takes its place", path, season, episodes[0])
            if (path != src or pack) and season is not None and episodes and not action.get("replace_owned", True) \
                    and all((season, ep) in owned for ep in episodes) and not dub_over and part is None:
                logger.info("%s: S%02dE%02d is owned already — left in downloads", path, season, episodes[0])
                left.append([os.path.basename(path), f"S{season:02d}E{episodes[0]:02d} už máš"])
                continue
            name, media = os.path.basename(path), None
            if names and season is not None and episodes:
                media = _lang_from_release(await probe_async(path), action.get("release") or os.path.basename(path))
                st = names["settings"]
                _r, season_rel, name = naming.episode_paths(
                    names["info"], season, episodes, media, os.path.basename(path), names["title"],
                    os.path.splitext(path)[1], await _episode_title(names, cat, tmdb_id, season, episodes[0]),
                    st["tv_folder_format"], st["tv_season_format"], st["tv_file_format"])
                folder = season_dirs.get(season) or os.path.join(show_root, season_rel)
            else:
                folder = season_dirs.get(season) or (os.path.join(show_root, f"Season {season:02d}") if season else show_root)
            if part and not _PT.search(name):
                name = f"{os.path.splitext(name)[0]} - pt{part}{os.path.splitext(name)[1]}"
            _ensure_dir(folder)
            picked = (_int(action.get("season")), [_int(action.get("episode"))]) if action.get("episode") else None
            # "replace" was for the episode the user picked: a file that turned out another one replaces nothing
            # (a file of more episodes holding the picked one replaces it: "S04E01-E02" for E01); "replace_owned"
            # is for every file of a pack (it imports them one by one as they complete) and a season's other files
            picked_here = picked is None or (picked[0] == season and picked[1][0] in episodes)
            replacing = bool(tmdb_id and season is not None and episodes) and (
                dub_over or (action.get("replace") and path == src and picked_here)
                or (action.get("replace_owned") and (pack or path != src)))
            if part and part >= 2 and owned_path and os.path.exists(owned_path) and not _PT.search(owned_path) \
                    and not replacing:
                # the first part was imported as the whole episode: it becomes "- pt1"
                stem, ext = os.path.splitext(owned_path)
                first = _unique_path(f"{stem} - pt1{ext}")
                _move_with_subtitles(owned_path, first)
                for table in ("library_episodes", "tv_media", "tv_episode_overrides", "tv_file_spans"):
                    try:
                        await db.execute(f"UPDATE {table} SET file_path = ? WHERE file_path = ?", (first, owned_path))
                    except Exception:  # noqa: BLE001 — a table of a module switched off
                        pass
                await db.execute("UPDATE library_episodes SET filename = ? WHERE file_path = ?", (os.path.basename(first), first))
                owned[(season, episodes[0])] = owned_path = first
                logger.info("S%02dE%02d: two parts — %s", season, episodes[0], os.path.basename(first))
            desired = os.path.join(folder, name)
            gone = _replaced(owned, season, episodes, part) if replacing else []
            if os.path.exists(desired) and desired in gone:
                # the new file takes the old one's very name: it waits aside while the old one goes
                aside = _unique_path(desired)
                _move_with_subtitles(path, aside)
                for old in gone:
                    logger.info("Replaced S%02dE%02d: deleted %s", season, episodes[0], _delete_version(old, aside))
                target = desired
                _move_with_subtitles(aside, target)
            else:
                target = _unique_path(desired)
                _move_with_subtitles(path, target)
                for old in gone:
                    logger.info("Replaced S%02dE%02d: deleted %s", season, episodes[0], _delete_version(old, target))
            targets.append(target)
            if not (tmdb_id and season is not None and episodes):
                logger.info("Imported %s (episode unknown — not in the library list)", target)
                continue
            if (season, episodes) != file_numbers and len(episodes) == 1:
                # the file's name keeps saying another number: the user's word (as the TV renamer's) holds it
                await tv_inventory.set_episode_override(db, target, season, episodes[0], why)
                logger.info("%s: S%02dE%02d — %s", os.path.basename(path), season, episodes[0], why)
            media = media if media is not None else _lang_from_release(await probe_async(target),
                                                                       action.get("release") or os.path.basename(path))
            stat = os.stat(target)
            # the TV renamer reads MediaInfo from here (no second probe on the next scan)
            await db.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, ?, ?, ?)",
                             (target, stat.st_size, stat.st_mtime, json.dumps(media or {})))
            values = (os.path.basename(target), target, stat.st_size, naming.resolution_label(media) or "",
                      ",".join(sorted({a["lang"].upper() for a in media.get("audio", []) if a.get("lang")})))
            for ep in episodes if not (part and part >= 2 and owned_path) else []:     # the 1st part stays the episode's file
                await db.execute(
                    "INSERT INTO library_episodes (show_tmdb_id, season, episode, filename, file_path, file_size, quality, "
                    "language, has_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) "
                    "ON CONFLICT(show_tmdb_id, season, episode) DO UPDATE SET filename = excluded.filename, "
                    "file_path = excluded.file_path, file_size = excluded.file_size, quality = excluded.quality, "
                    "language = excluded.language, has_file = 1",
                    (tmdb_id, season, ep, *values))
            logger.info("Imported episode S%02d%s → %s", season, "".join(f"E{e:02d}" for e in episodes), target)
            placed.append(f"S{season:02d}" + "".join(f"E{e:02d}" for e in episodes))
            if not (part and part >= 2):
                for ep in episodes:                     # the next files of this batch see it (its 2nd part)
                    owned[(season, ep)] = target
        if tmdb_id and targets:
            await _ensure_show(db, tmdb_id, title, year)
        await db.commit()
    finally:
        await db.close()
    payload["placed_episodes"], payload["left_files"] = placed, left
    if unsorted:
        payload["unsorted"] = [os.path.basename(u) for u in unsorted]
        payload["imported"] = True
        payload["placed"] = bool(targets)
    if targets:
        payload["path"] = targets[0]
        payload["imported"] = True
    if targets or unsorted:
        await events.emit("library.files_added", {"folders": sorted({os.path.dirname(t) for t in targets + unsorted})})


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
    imported = None
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
            payload["review"] = mismatch          # the notification tells the user
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
                deleted = _delete_version(old["file_path"], replacement=target)
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
        cursor = await db.execute("SELECT tmdb_id FROM library_movies WHERE id = ?", (new_id,))
        imported = await cursor.fetchone()
    finally:
        await client.close()
        await db.close()
    # a better version that was being downloaded is here now: what does the film look like now?
    from app.modules.library.upgrades import after_import
    await after_import(imported[0] if imported else None)
