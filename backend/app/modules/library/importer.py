"""Library scan job: finds video files, identifies movies, stores them in the library.

Movies are identified from evidence in the file itself (duration, audio languages)
and its names (folder + file: title, year, {tmdb-ID}); NFO files and other modules
(event ``library.collect_hints``) only contribute hints. Unclear cases go to the
review queue instead of being guessed. See docs/decisions/0002.

The scan runs in the background; progress is exposed via ``job_status()``.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from app.clients.tmdb import TMDBClient
from app.config import get_effective_settings, movies_library_dir, tv_library_dir
from app.core import events
from app.core.mediainfo import probe_async
from app.db import get_db
from app.modules.library.files import (
    _detect_language,
    _detect_quality,
    _parse_episode_nfo,
    _parse_nfo_file,
    _scan_video_files,
)
from app.modules.library.matcher import FileEvidence, decide, score_candidate
from app.core.release_name import VIDEO_EXTS, NameFacts, parse_name
from app.modules.library.nfo import find_nfo, read_nfo
from app.modules.library.notify import emit_movie_updated
from app.utils.tv_parser import normalize_for_search, parse_tv_filename

logger = logging.getLogger(__name__)

TMDB_CACHE_DAYS = 30
MAX_SEARCHES_PER_FILE = 6
MAX_CANDIDATES = 8
SAMPLE_MAX_BYTES = 300 * 1024 * 1024
# Statuses the scanner must never overwrite: decided by the user.
USER_STATUSES = {"manual"}

_job: dict = {"running": False}
_lock = asyncio.Lock()


def job_status() -> dict:
    return dict(_job)


def start_scan(force: bool = False) -> bool:
    """Start a background scan. Returns False when one is already running."""
    if _job.get("running"):
        return False
    _job.clear()
    _job.update({
        "running": True, "force": force, "phase": "movies", "total": 0, "done": 0, "current": "",
        "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "error": None,
        "stats": {"movies_found": 0, "matched": 0, "manual": 0, "review": 0, "unmatched": 0, "skipped": 0,
                  "removed": 0, "shows_found": 0, "episodes_matched": 0},
    })
    asyncio.create_task(_run(force))
    return True


async def _run(force: bool) -> None:
    async with _lock:
        cfg = await get_effective_settings()
        client = TMDBClient(cfg.get("tmdb_api_key", ""))
        db = await get_db()
        try:
            if not cfg.get("tmdb_api_key"):
                raise RuntimeError("TMDB API key not configured")
            await _cleanup_missing(db)
            movie_dir = movies_library_dir(cfg)
            if movie_dir:
                await _scan_movies(client, db, movie_dir, force)
            tv_dir = tv_library_dir(cfg)
            _job["phase"] = "tv"
            stats = _job["stats"]
            await _scan_tv(client, db, tv_dir, stats)
            logger.info("Library scan complete: %s", stats)
        except Exception as e:
            logger.exception("Library scan failed")
            _job["error"] = str(e)
        finally:
            await client.close()
            await db.close()
            _job["running"] = False
            _job["phase"] = "done"
            _job["finished_at"] = datetime.now(timezone.utc).isoformat()


async def _cleanup_missing(db) -> None:
    """Remove DB entries whose files no longer exist."""
    cursor = await db.execute("SELECT id, file_path FROM library_movies")
    for row in await cursor.fetchall():
        if not os.path.exists(row[1]):
            await db.execute("DELETE FROM library_movies WHERE id = ?", (row[0],))
            _job["stats"]["removed"] += 1
    cursor = await db.execute("SELECT id, file_path FROM library_episodes WHERE has_file = 1")
    for row in await cursor.fetchall():
        if not os.path.exists(row[1]):
            await db.execute("UPDATE library_episodes SET has_file = 0, file_path = NULL, filename = NULL WHERE id = ?", (row[0],))
    await db.execute("DELETE FROM library_shows WHERE tmdb_id NOT IN (SELECT DISTINCT show_tmdb_id FROM library_episodes WHERE has_file = 1)")
    await db.commit()


# ─── MOVIES ───

def _find_movie_files(root: str) -> list[tuple[str, int]]:
    """(path, number of videos in the same folder) for every video, samples skipped."""
    found: list[tuple[str, int]] = []
    for folder, _dirs, files in os.walk(root, followlinks=True):
        videos = []
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                continue
            path = os.path.join(folder, name)
            if "sample" in name.lower():
                try:
                    if os.path.getsize(path) < SAMPLE_MAX_BYTES:
                        continue
                except OSError:
                    continue
            videos.append(path)
        found.extend((v, len(videos)) for v in videos)
    return sorted(found)


def _folder_facts(video_path: str, root: str) -> NameFacts | None:
    """Facts from the movie folder name — None for the library root or a bare year folder."""
    folder = os.path.dirname(video_path)
    if os.path.normpath(folder) == os.path.normpath(root):
        return None
    name = os.path.basename(folder)
    if re.fullmatch(r"(19|20)\d{2}", name):
        return None
    return parse_name(name)


def _quality_label(media: dict, filename: str) -> str:
    height, width = media.get("height") or 0, media.get("width") or 0
    if width >= 3200 or height >= 1600:
        return "2160p"
    if width >= 1800 or height >= 900:
        return "1080p"
    if width >= 1200 or height >= 650:
        return "720p"
    if width or height:
        return "480p"
    return _detect_quality(filename)


async def tmdb_details(client: TMDBClient, db, tmdb_id: int) -> dict | None:
    cursor = await db.execute("SELECT data, fetched_at FROM tmdb_movies WHERE tmdb_id = ?", (tmdb_id,))
    row = await cursor.fetchone()
    if row and time.time() - row[1] < TMDB_CACHE_DAYS * 86400:
        cached = json.loads(row[0])
        if "titles_by_lang" in cached:  # entries from before per-language titles are refetched
            return cached
    try:
        data = await client.get_movie_full(tmdb_id)
    except Exception as e:
        logger.warning("TMDB details for %s failed: %s", tmdb_id, e)
        return json.loads(row[0]) if row else None
    await db.execute(
        "INSERT OR REPLACE INTO tmdb_movies (tmdb_id, data, fetched_at) VALUES (?, ?, ?)",
        (tmdb_id, json.dumps(data), time.time()),
    )
    return data


async def _collect_candidates(client: TMDBClient, names: list[NameFacts], hint_ids: set[int]) -> list[int]:
    ids: list[int] = list(hint_ids)
    queries: list[tuple[str, int | None]] = []
    for facts in names:
        for title in [facts.title, *facts.extra_titles]:
            if title and (title, facts.year) not in queries:
                queries.append((title, facts.year))
    for title, year in queries[:MAX_SEARCHES_PER_FILE]:
        try:
            results = await client.search_movie_raw(title, year)
            if not results and year:
                results = await client.search_movie_raw(title)
        except Exception as e:
            logger.warning("TMDB search '%s' failed: %s", title, e)
            continue
        for r in results[:3]:
            if r["id"] not in ids:
                ids.append(r["id"])
    return ids[:MAX_CANDIDATES]


async def identify_movie(client: TMDBClient, db, video_path: str, videos_in_folder: int, root: str, media: dict) -> dict:
    """Gather evidence for one video file and score TMDB candidates."""
    file_facts = parse_name(os.path.basename(video_path))
    names = [file_facts]
    folder_facts = _folder_facts(video_path, root)
    if folder_facts and folder_facts.title:
        names.append(folder_facts)

    hints: dict[int, list[str]] = {}

    def add_hint(tmdb_id: int | None, source: str) -> None:
        if tmdb_id and source not in hints.setdefault(tmdb_id, []):
            hints[tmdb_id].append(source)

    for facts in names:
        add_hint(facts.tmdb_id, "name_tag")

    nfo_path = find_nfo(video_path, videos_in_folder)
    nfo = read_nfo(nfo_path) if nfo_path else None
    if nfo:
        add_hint(nfo.tmdb_id, "lumina_nfo" if nfo.by_lumina else "nfo")
        if nfo.imdb_id:
            try:
                add_hint(await client.find_by_imdb(nfo.imdb_id), "nfo_imdb")
            except Exception as e:
                logger.warning("TMDB find %s failed: %s", nfo.imdb_id, e)

    # Other modules (e.g. radarr) can add hints: payload["hints"] = [(tmdb_id, source), ...]
    extra = await events.emit("library.collect_hints", {"path": video_path, "hints": []})
    for tmdb_id, source in extra.get("hints", []):
        add_hint(tmdb_id, source)

    evidence = FileEvidence(
        titles=[t for f in names for t in [f.title, *f.extra_titles] if t],
        years={f.year for f in names if f.year} | ({nfo.year} if nfo and nfo.year else set()),
        duration_min=(media.get("duration_s") or 0) / 60 or None,
        audio_langs={a["lang"] for a in media.get("audio", []) if a.get("lang")},
        hints=hints,
    )

    candidate_ids = await _collect_candidates(client, names, set(hints))
    scored = []
    for tmdb_id in candidate_ids:
        details = await tmdb_details(client, db, tmdb_id)
        if details:
            scored.append(score_candidate(evidence, details))
    status, ranked = decide(scored)
    # Restoring from Lumina's own NFO (e.g. after losing the DB): the user's choice stays a user choice.
    if (nfo and nfo.by_lumina and nfo.lumina_status == "manual" and ranked
            and ranked[0].candidate["tmdb_id"] == nfo.tmdb_id):
        status = "manual"
    return {"status": status, "ranked": ranked}


async def _scan_movies(client: TMDBClient, db, root: str, force: bool) -> None:
    files = _find_movie_files(root)
    _job["total"] = len(files)
    stats = _job["stats"]
    stats["movies_found"] = len(files)

    for index, (path, videos_in_folder) in enumerate(files):
        _job["done"] = index
        _job["current"] = os.path.relpath(path, root)
        try:
            await _process_movie_file(client, db, path, videos_in_folder, root, force)
        except Exception:
            logger.exception("Library: failed to process %s", path)
        await db.commit()
    _job["done"] = len(files)


async def _process_movie_file(client: TMDBClient, db, path: str, videos_in_folder: int, root: str, force: bool) -> None:
    stats = _job["stats"]
    stat = os.stat(path)
    cursor = await db.execute(
        "SELECT id, status, file_size, file_mtime FROM library_movies WHERE file_path = ?", (path,)
    )
    existing = await cursor.fetchone()
    unchanged = existing and existing[2] == stat.st_size and abs((existing[3] or 0) - stat.st_mtime) < 1
    if existing and unchanged and (existing[1] in USER_STATUSES or (existing[1] == "matched" and not force)):
        stats["skipped"] += 1
        stats["matched"] += 1
        return

    media = await probe_async(path)
    result = await identify_movie(client, db, path, videos_in_folder, root, media)
    status, ranked = result["status"], result["ranked"]
    if existing and existing[1] in USER_STATUSES:
        status = existing[1]  # file changed, but the user's choice of movie stays
    stats[status if status in stats else "review"] += 1

    candidates = [
        {
            "tmdb_id": s.candidate["tmdb_id"], "title": s.candidate["title"],
            "original_title": s.candidate["original_title"], "year": s.candidate["year"],
            "runtime": s.candidate["runtime"], "poster_url": s.candidate["poster_url"],
            "score": s.score, "reasons": s.reasons,
        }
        for s in ranked[:5]
    ]
    best = ranked[0].candidate if ranked else None
    filename = os.path.basename(path)
    languages = ",".join(sorted({a["lang"].upper() for a in media.get("audio", []) if a.get("lang")}))
    values = {
        "filename": filename,
        "file_size": stat.st_size,
        "file_mtime": stat.st_mtime,
        "quality": _quality_label(media, filename),
        "language": languages or _detect_language(filename),
        "media": json.dumps(media),
        "duration_s": media.get("duration_s") or 0,
        "candidates": json.dumps(candidates),
        "confidence": ranked[0].score if ranked else 0,
        "status": status,
    }
    user_choice_kept = bool(existing and existing[1] in USER_STATUSES)
    if best and not user_choice_kept:
        values.update({
            "tmdb_id": best["tmdb_id"], "title": best["title"], "original_title": best["original_title"],
            "year": str(best["year"] or ""), "poster_url": best["poster_url"], "overview": best["overview"],
            "imdb_id": best.get("imdb_id", ""), "matched_by": "lumina_nfo" if status == "manual" else "auto",
        })
    elif not best and not user_choice_kept:
        values.update({"tmdb_id": None, "title": parse_name(filename).title or filename, "matched_by": "auto"})

    if existing:
        assignments = ", ".join(f"{k} = ?" for k in values)
        await db.execute(f"UPDATE library_movies SET {assignments}, scanned_at = datetime('now') WHERE id = ?",
                         (*values.values(), existing[0]))
        movie_id = existing[0]
    else:
        values["file_path"] = path
        values["added_at"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = await db.execute(f"INSERT INTO library_movies ({columns}) VALUES ({placeholders})", tuple(values.values()))
        movie_id = cursor.lastrowid
    await emit_movie_updated(db, movie_id)


# ─── TV SHOWS (unchanged logic, moved from router.py) ───

async def _scan_tv(client: TMDBClient, db, tv_dir: str, stats: dict) -> None:
    # --- Scan TV shows ---
    if tv_dir:
        tv_files = _scan_video_files(tv_dir)

        # Group by show name — try episode NFO first for season/episode info
        show_groups: dict[str, list[dict]] = {}
        for f in tv_files:
            # Try episode .nfo for accurate S/E numbers
            ep_nfo = _parse_episode_nfo(f["file_path"])
            parsed = parse_tv_filename(f["filename"], f["file_path"])

            if ep_nfo:
                # NFO has season/episode — merge with parsed or create new
                show_name = ep_nfo.get("show_name") or (parsed["show_name"] if parsed else "")
                if not show_name:
                    continue
                entry = {
                    **f,
                    "show_name": show_name,
                    "season": ep_nfo["season"],
                    "episode": ep_nfo["episode"],
                    "year": ep_nfo.get("year") or (parsed.get("year") if parsed else None),
                    "nfo_show_tmdb_id": ep_nfo.get("show_tmdb_id"),
                }
            elif parsed:
                entry = {**f, **parsed}
            else:
                continue

            key = normalize_for_search(entry["show_name"])
            if key not in show_groups:
                show_groups[key] = []
            show_groups[key].append(entry)

        stats["shows_found"] = len(show_groups)

        for show_key, episodes in show_groups.items():
            show_name = episodes[0]["show_name"]
            year = episodes[0].get("year")

            # Check if already in DB
            cursor = await db.execute(
                "SELECT tmdb_id FROM library_shows WHERE lower(title) = lower(?) OR lower(original_title) = lower(?)",
                (show_name, show_name),
            )
            existing = await cursor.fetchone()

            if existing:
                tmdb_id = existing[0]
            else:
                tmdb_id = None

                # 1. Try nfo_show_tmdb_id from episode NFO
                for ep in episodes:
                    if ep.get("nfo_show_tmdb_id"):
                        tmdb_id = ep["nfo_show_tmdb_id"]
                        logger.info("Show '%s' matched via episode NFO: tmdb=%d", show_name, tmdb_id)
                        break

                # 2. Try tvshow.nfo from show folder
                if not tmdb_id:
                    # Find show root folder from episode paths
                    for ep in episodes:
                        ep_path = ep.get("file_path", "")
                        # Walk up from episode to find tvshow.nfo
                        d = os.path.dirname(ep_path)
                        for _ in range(3):  # max 3 levels up
                            nfo_path = os.path.join(d, "tvshow.nfo")
                            if os.path.isfile(nfo_path):
                                nfo = _parse_nfo_file(nfo_path)
                                if nfo and nfo.get("tmdb_id"):
                                    tmdb_id = nfo["tmdb_id"]
                                    logger.info("Show '%s' matched via NFO: tmdb=%d", show_name, tmdb_id)
                                    break
                            parent = os.path.dirname(d)
                            if parent == d:
                                break
                            d = parent
                        if tmdb_id:
                            break

                # 3. Fallback: TMDB search
                if not tmdb_id:
                    search_q = f"{show_name} {year}" if year else show_name
                    try:
                        results = await client.search_tv(search_q)
                        if not results:
                            # Try without diacritics
                            stripped = normalize_for_search(show_name)
                            if stripped != show_name.lower():
                                results = await client.search_tv(stripped)
                        if not results:
                            logger.warning("No TMDB match for show '%s'", show_name)
                            continue
                        tmdb_id = results[0].tmdb_id
                    except Exception as e:
                        logger.warning("TMDB search failed for show '%s': %s", show_name, e)
                        continue

                # Fetch full details and populate episodes
                try:
                    details = await client.get_tv_details(tmdb_id)
                    await db.execute(
                        """INSERT OR REPLACE INTO library_shows
                        (tmdb_id, title, original_title, year, poster_url, overview,
                         total_seasons, total_episodes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (tmdb_id, details["title"], details["original_title"],
                         details["first_air_date"][:4] if details.get("first_air_date") else "",
                         details["poster_url"], details["overview"],
                         details["total_seasons"], details["total_episodes"]),
                    )

                    for season_info in details["seasons"]:
                        sn = season_info["season_number"]
                        try:
                            tmdb_episodes = await client.get_season(tmdb_id, sn)
                            for ep in tmdb_episodes:
                                await db.execute(
                                    """INSERT OR IGNORE INTO library_episodes
                                    (show_tmdb_id, season, episode, episode_title, air_date)
                                    VALUES (?, ?, ?, ?, ?)""",
                                    (tmdb_id, sn, ep["episode_number"],
                                     ep["name"], ep["air_date"]),
                                )
                        except Exception as e:
                            logger.warning("Failed to fetch S%02d for %s: %s", sn, show_name, e)
                except Exception as e:
                    logger.warning("TMDB details failed for show '%s': %s", show_name, e)
                    continue

            # Mark episodes we have on disk
            for ep_data in episodes:
                await db.execute(
                    """UPDATE library_episodes
                    SET has_file = 1, filename = ?, file_path = ?,
                        file_size = ?, quality = ?, language = ?
                    WHERE show_tmdb_id = ? AND season = ? AND episode = ?""",
                    (ep_data["filename"], ep_data["file_path"],
                     ep_data["file_size"], ep_data["quality"], ep_data["language"],
                     tmdb_id, ep_data["season"], ep_data["episode"]),
                )
                stats["episodes_matched"] += 1

        await db.commit()

