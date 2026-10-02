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
from app.core.episode_match import parse_episode
from app.core.mediainfo import probe_async
from app.db import get_db
from app.modules.library.files import (
    _detect_language,
    _detect_quality,
    _parse_episode_nfo,
    _parse_nfo_file,
    _scan_video_files,
)
from app.modules.library.matcher import TRUSTED_HINTS, FileEvidence, decide, score_candidate
from app.core.release_name import VIDEO_EXTS, NameFacts, parse_name
from app.modules.library.nfo import find_nfo, read_nfo
from app.modules.library.notify import emit_movie_updated
from app.modules.library import episode_names, tv_inventory
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

    # Other modules can add hints: payload["hints"] = [(tmdb_id, source), ...]
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
    if status == "matched" and any(set(sources) & TRUSTED_HINTS and tmdb_id != ranked[0].candidate["tmdb_id"]
                                   for tmdb_id, sources in hints.items()):
        status = "review"
        ranked[0].reasons.append("Plex má jiný film")
    # Restoring from Lumina's own NFO (e.g. after losing the DB): the user's choice stays a user choice.
    if (nfo and nfo.by_lumina and nfo.lumina_status == "manual" and ranked
            and ranked[0].candidate["tmdb_id"] == nfo.tmdb_id):
        status = "manual"
    restored = {}
    if nfo and nfo.by_lumina and ranked and ranked[0].candidate["tmdb_id"] == nfo.tmdb_id:
        restored = nfo.lumina_files.get(os.path.basename(video_path), {})
    return {"status": status, "ranked": ranked, "restored": restored}


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
        restored = result.get("restored") or {}   # note / preferred from Lumina's NFO (lost DB)
        if restored:
            values["note"] = restored.get("note", "")
            values["preferred"] = int(bool(restored.get("preferred")))
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = await db.execute(f"INSERT INTO library_movies ({columns}) VALUES ({placeholders})", tuple(values.values()))
        movie_id = cursor.lastrowid
    # Commit first: other modules react on their own connections (an open write here locks them out)
    await db.commit()
    await emit_movie_updated(db, movie_id)


# ─── TV SHOWS (unchanged logic, moved from router.py) ───

_FOLDER_RELEASE = re.compile(r"(?i)[ ._-]+(?:s\d{1,2}(?:e\d{1,3})?|season ?\d|\d{1,2}\.? ?(?:s[eé]rie|serie|sezona)|complete|komplet)(?![a-z]).*$")
_FOLDER_YEAR = re.compile(r"[ ._(\[]*((?:19|20)\d{2})[)\]]?\s*$")


def _show_folder(file_path: str, tv_dir: str) -> tuple[str, str | None] | None:
    """(show name, year) from the first folder under the TV folder — None for a file lying right in it.
    "The.Book.of.Boba.Fett.S01.1080p.WEB-DL…" → "The Book of Boba Fett"; "Rodina Addamsovcov 1964" → year 1964."""
    try:
        rel = os.path.relpath(file_path, tv_dir)
    except ValueError:
        return None
    parts = rel.split(os.sep)
    if len(parts) < 2 or rel.startswith(".."):
        return None
    name = parts[0]
    if ("." in name and " " not in name) or "_" in name:
        name = re.sub(r"[._]+", " ", name)
    name = _FOLDER_RELEASE.sub("", name).strip(" -")
    year = None
    m = _FOLDER_YEAR.search(name)
    if m and m.start() > 0:
        year, name = m.group(1), name[:m.start()].strip(" -")
    return (name, year) if name else None


def _best_show(results: list, name: str, year: str | None, exact_only: bool = False):
    """The TMDB show whose title really is the name — not just the first hit ("SGA" found "Sgauth",
    "Bluey" found "Blue"); the right year wins among namesakes. None when nothing fits."""
    want = normalize_for_search(name)
    if not want:
        return None
    exact = [r for r in results if want in (normalize_for_search(r.title or ""), normalize_for_search(r.original_title or ""))]
    if exact:
        return next((r for r in exact if year and r.year == str(year)), exact[0])
    if exact_only:
        return None
    words = set(want.split())
    for r in results:        # "Mr Robot" ← "Mr. Robot", "Stargate Atlantis" ← "Stargate Atlantis: …"
        for t in (r.title or "", r.original_title or ""):
            have = normalize_for_search(t)
            if have.startswith(want + " ") or set(have.split()) == words:
                return r
    return None


def _episode_by_folder(f: dict, tv_dir: str) -> dict | None:
    """Names the old parser does not read ("chalupari-01-chudak-dedecek-hd-1975.mkv"): the episode from
    core/episode_match, the show from its folder in the library (the first one under the TV folder)."""
    from app.core.episode_match import parse_episode

    info = parse_episode(f["filename"])
    if len(info.episodes) > 1 and info.bare:
        info.episodes = info.episodes[:1]          # "Naruto_203-204-205": the file is listed under its first episode
    if not info.episodes and not info.is_pack:
        m = re.match(r"(\d{1,3})(?:[ ._-]|\.\w+$)", f["filename"])
        if m and not re.match(r"(19|20)\d{2}", f["filename"]):
            info.episodes = [int(m.group(1))]
    if len(info.episodes) != 1:
        return None
    rel = os.path.relpath(f["file_path"], tv_dir)
    parts = rel.split(os.sep)
    if len(parts) < 2 or rel.startswith(".."):
        return None
    season = info.season
    if season is None:
        folder = os.path.basename(os.path.dirname(f["file_path"])) if len(parts) > 2 else ""
        m = re.search(r"(?:season|s[eé]rie|sezona)\s*(\d{1,2})|^s(\d{1,2})$|^(\d{1,2})(?:\.|$| )", folder, re.I)
        season = int(next(g for g in m.groups() if g)) if m else 1
    year = re.search(r"\((19|20)\d{2}\)", parts[0])
    return {"show_name": re.sub(r"\s*\((19|20)\d{2}\)\s*$", "", parts[0]).strip(), "season": season,
            "episode": info.episodes[0], "year": year.group(0)[1:-1] if year else None}


_TITLE_AFTER = re.compile(r"(?i)(?:s\d{1,2}\s?e\d{1,3}(?:-?e\d{1,3})*|\b\d{1,2}x\d{2,3})[\s._-]*(.*)$")
_TITLE_NOISE = re.compile(r"(?i)\[[^\]]*\]|\([^)]*\)|\b(?:\d{3,4}p|x26[45]|h\.?26[45]|hevc|web-?dl|webrip|bluray|bdrip|hdtv|dvdrip|"
                          r"cz|sk|en|eng|cze|dab(?:ing)?|tit(?:ulky)?|multi|aac|ac3|dts|5\.1|"
                          r"ai-?upscale|upscale|dvb-?[ct]|sdtv|full ?hd|xvid|divx|dvd|fs)\b.*$")


def _title_in_name(filename: str) -> str:
    """The episode's name a file carries after its number ("S12E24 Stockholmský syndrom.mkv")."""
    stem = os.path.splitext(filename)[0]
    m = _TITLE_AFTER.search(stem)
    if not m:
        return ""
    title = _TITLE_NOISE.sub("", m.group(1).replace(".", " ").replace("_", " ")).strip(" -")
    return title if len(re.sub(r"[^A-Za-zÀ-ž]", "", title)) >= 3 else ""


def _other_by_name(file_title: str, plex_name: str, fits, cat: dict, here: tuple[int, int], duration: int
                   ) -> tuple[str, tuple[int, int], bool] | None:
    """(why, TMDB's episode, sure) when a name — the file's own, else Plex's — is another TMDB episode than
    the number says. Sure only by the file's own name, and only when Plex does not show the file's name
    (Plex then shows the right episode already)."""
    for who, name in (("soubor", file_title), ("Plex", plex_name)):
        if not name or fits(name):
            continue
        key, sure = episode_names.best(name, cat, here[0], duration)
        if not key or key == here:
            continue
        sure = sure and who == "soubor" and not (plex_name and tv_inventory.title_score(name, plex_name) >= tv_inventory.STRONG)
        t = cat[key].get("cs") or cat[key].get("en")
        own = cat.get(here, {}).get("cs") or cat.get(here, {}).get("en") or ""
        where = f"S{key[0]:02d}E{key[1]:02d}" if key[0] != here[0] else f"E{key[1]:02d}"
        return f"{who}: „{name}“ = v TMDB {where} „{t}“ (E{here[1]:02d} je „{own}“)", key, sure
    return None


def _bare_title(filename: str) -> str:
    """The episode's name after a bare number ("37.Davný protivník.avi" → "Davný protivník")."""
    m = re.match(r"\s*\d{1,3}\s*[._ -]+\s*(.+)$", os.path.splitext(filename)[0])
    if not m:
        return ""
    title = _TITLE_NOISE.sub("", m.group(1).replace(".", " ").replace("_", " ")).strip(" -")
    return title if len(re.sub(r"[^A-Za-zÀ-ž]", "", title)) >= 3 else ""


_ABSOLUTE = re.compile(r"(?<![0-9])(\d{3})(?![0-9]|p\b|i\b|\s?kbps)")


def _absolute_in_name(filename: str) -> int | None:
    """A three-digit absolute episode number in a name ("Naruto_CZ_027-02x01", "[CNT]_Naruto_153_[…]"),
    not a resolution or a CRC."""
    name = re.sub(r"\[[0-9A-Fa-f]{8}\]", "", os.path.splitext(filename)[0])
    for m in _ABSOLUTE.finditer(name):
        n = int(m.group(1))
        if n not in (480, 576, 720, 264, 265) and n > 0:
            return n
    return None


def _absolute_episode(order: list[tuple[int, int]], number: int) -> tuple[int, int] | None:
    """(season, episode) of an absolute episode number: the number-th of TMDB's episodes in order
    [(1, 1), (1, 2), …]. TMDB numbers some anime on through the seasons (Naruto S02 = E53–E104),
    others from 1 in every season — the order is the same."""
    return order[number - 1] if 0 < number <= len(order) else None


async def _lumina_show(client: TMDBClient, db, episodes: list[dict], show_name: str, year) -> tuple[int | None, str]:
    """(TMDB id, title) of the show by Lumina's own means: the library, NFO files, TMDB search by the
    folder's and the files' names (a title that really is the name first)."""
    counts: dict[str, int] = {}
    for ep in episodes:
        n = ep.get("file_show_name") or ""
        if n and normalize_for_search(n) != normalize_for_search(show_name):
            counts[n] = counts.get(n, 0) + 1
    names = [show_name, *sorted(counts, key=lambda n: -counts[n])[:2]]
    for n in names:
        row = await (await db.execute(
            "SELECT tmdb_id, title FROM library_shows WHERE lower(title) = lower(?) OR lower(original_title) = lower(?)",
            (n, n))).fetchone()
        if row:
            return row[0], row[1]
    for ep in episodes:
        if ep.get("nfo_show_tmdb_id"):
            return ep["nfo_show_tmdb_id"], ""
    for ep in episodes:
        d = os.path.dirname(ep.get("file_path", ""))
        for _ in range(3):  # max 3 levels up
            nfo_path = os.path.join(d, "tvshow.nfo")
            if os.path.isfile(nfo_path):
                nfo = _parse_nfo_file(nfo_path)
                if nfo and nfo.get("tmdb_id"):
                    return nfo["tmdb_id"], ""
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    first_hit = None
    found: list[tuple[str, list]] = []
    for n in names:
        try:
            results = await client.search_tv(f"{n} {year}" if year and n == show_name else n)
            if not results and year and n == show_name:
                results = await client.search_tv(n)
            if not results:
                stripped = normalize_for_search(n)
                if stripped != n.lower():
                    results = await client.search_tv(stripped)
        except Exception as e:
            logger.warning("TMDB search failed for show '%s': %s", n, e)
            continue
        first_hit = first_hit or (results[0] if results else None)
        best = _best_show(results, n, year, exact_only=True)
        if not best:
            # a show without a Czech name comes in its original script ("俺だけレベルアップな件")
            try:
                english = await client.search_tv(n, language="en-US")
            except Exception:
                english = []
            best = _best_show(english, n, year, exact_only=True)
            found.append((n, results + english))
        if best:
            return best.tmdb_id, best.title
    for n, results in found:
        best = _best_show(results, n, year)       # the name is the start of a title
        if best:
            return best.tmdb_id, best.title
    if first_hit and set(normalize_for_search(show_name).split()) & set(
            normalize_for_search(f"{first_hit.title} {first_hit.original_title}").split()):
        logger.info("Show '%s' matched loosely: %s", show_name, first_hit.title)
        return first_hit.tmdb_id, first_hit.title          # a word in common at least
    return None, ""


async def _scan_tv(client: TMDBClient, db, tv_dir: str, stats: dict) -> None:
    # --- Scan TV shows ---
    if tv_dir:
        tv_files = _scan_video_files(tv_dir)
        # what Plex knows of each file (which show, which episode) — the plex module answers, if on
        hints = (await events.emit("library.collect_tv_hints",
                                   {"paths": [f["file_path"] for f in tv_files], "hints": {}}))["hints"]
        overrides = await tv_inventory.overrides(db)
        manual = await tv_inventory.episode_overrides(db)
        unknown: list[dict] = []

        # Group by show name — try episode NFO first for season/episode info
        show_groups: dict[str, list[dict]] = {}
        extras: list[tuple[dict, str]] = []
        for f in tv_files:
            # bonus videos in the show's extras folder ("Hra o trůny/Other/…Reunion….mkv") are not episodes
            rel = os.path.relpath(f["file_path"], tv_dir).split(os.sep)
            extra = tv_inventory.extra_of(rel) if len(rel) > 1 else None
            if extra is not None:
                extras.append((f, extra))
                continue
            hint = hints.get(f["file_path"])
            # Try episode .nfo for accurate S/E numbers
            ep_nfo = _parse_episode_nfo(f["file_path"])
            parsed = parse_tv_filename(f["filename"], f["file_path"]) or _episode_by_folder(f, tv_dir)

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
            elif hint and hint.get("season") is not None and hint.get("episode"):
                # the name says nothing, Plex knows the episode
                entry = {**f, "show_name": hint.get("title") or "", "season": hint["season"],
                         "episode": hint["episode"], "year": None, "numbers_from": "plex"}
            elif f["file_path"] in manual:
                # the name says nothing, the user said which episode it is
                ms, me, _note = manual[f["file_path"]]
                entry = {**f, "show_name": "", "season": ms, "episode": me, "year": None, "numbers_from": "manual"}
            else:
                unknown.append(f)
                continue
            entry["hint"] = hint
            # the file's own numbers (an absolute mapping changes season/episode later); a file may hold
            # more episodes ("S07E21E22", "s04e01+02") — Plex lists such a file under one of them
            own = parse_episode(f["filename"])
            if not ep_nfo and own.season is not None and own.episodes and not own.bare \
                    and (own.season, own.episodes[0]) != (entry["season"], entry["episode"]) \
                    and re.search(r"(?i)s\d{1,2}\s?e\d{1,3}|\d{1,2}x\d{2,3}", f["filename"]):
                # the name's own SxxEyy beats the old parser (it read "Archer.S00E06" as S01E06)
                entry["season"], entry["episode"] = own.season, own.episodes[0]
            entry["file_episodes"] = own.episodes if len(own.episodes) > 1 and own.season == entry["season"] \
                and own.episodes[0] == entry["episode"] else [entry["episode"]]
            entry["file_season"] = entry["season"]
            # only a number in the name ("Naruto 104.mp4"): may be the absolute number of an anime
            entry["bare"] = not re.search(r"(?i)s\d{1,2}\s?e\d{1,3}|\d{1,2}x\d{2,3}", f["filename"]) \
                and entry.get("numbers_from") != "plex" and not ep_nfo

            # the show is its folder under the TV folder ("StarGate Atlantis/2. HD/SGA - S02E02…"): file
            # names use nicknames ("SGA") and the folder holds one show; the file's name stays a hint
            entry["file_show_name"] = entry["show_name"]
            folder_show = _show_folder(f["file_path"], tv_dir)
            entry["folder"] = os.path.relpath(f["file_path"], tv_dir).split(os.sep)[0] if folder_show else ""
            if folder_show:
                entry["show_name"], folder_year = folder_show
                entry["year"] = folder_year or entry.get("year")
            key = normalize_for_search(entry["show_name"])
            if key not in show_groups:
                show_groups[key] = []
            show_groups[key].append(entry)

        stats["shows_found"] = len(show_groups)
        # a file's first name (before Lumina renamed it): its own episode name is read from there
        origin_of = episode_names.origins(await (await db.execute(
            "SELECT src, dst, batch_id FROM file_operations WHERE status = 'done' AND dst != '' ORDER BY id")).fetchall())
        lengths = {r[0]: (json.loads(r[1] or "{}").get("duration_s") or 0)
                   for r in await (await db.execute("SELECT file_path, media FROM tv_media")).fetchall()}
        marked: set[tuple[int, int, int]] = set()       # (show, season, episode) this scan found
        matched_paths: set[str] = set()                 # files this scan put under a show
        inventory = tv_inventory.Inventory()
        for f, extra in extras:
            plex_named = extra.lower() in tv_inventory.EXTRA_DIRS or not extra
            inventory.file(f["file_path"], os.path.relpath(f["file_path"], tv_dir).split(os.sep)[0], None, None, [], "extra",
                           f"bonus ve složce „{extra}“" + ("" if plex_named else " — Plex ji nezná, renamer ji přejmenuje na Other")
                           if extra else "bonus (přípona Plexu)")
        for f in unknown:
            folder_show = _show_folder(f["file_path"], tv_dir)
            inventory.file(f["file_path"], os.path.relpath(f["file_path"], tv_dir).split(os.sep)[0] if folder_show else "",
                           None, None, [], "unknown", "z názvu nepoznám díl a Plex ho nezná")

        for show_key, episodes in show_groups.items():
            show_name = episodes[0]["show_name"]
            year = episodes[0].get("year")
            folder = episodes[0].get("folder") or ""

            # who says which show it is: the user (a fix in the inventory), Plex (its matches were often
            # fixed by hand), Lumina (folder and file names → TMDB)
            lumina_id, lumina_title = await _lumina_show(client, db, episodes, show_name, year)
            plex_votes: dict[int, int] = {}
            for ep in episodes:
                if (ep.get("hint") or {}).get("tmdb_id"):
                    plex_votes[ep["hint"]["tmdb_id"]] = plex_votes.get(ep["hint"]["tmdb_id"], 0) + 1
            plex_id = max(plex_votes, key=plex_votes.get) if plex_votes else None
            plex_title = next((ep["hint"].get("title") for ep in episodes
                               if (ep.get("hint") or {}).get("tmdb_id") == plex_id), "") if plex_id else ""
            override = overrides.get(folder) if folder else None
            tmdb_id = override or plex_id or lumina_id
            source = "user" if override else "plex" if plex_id else "lumina" if lumina_id else ""
            inventory.show(folder or show_name, tmdb_id, source, lumina_id, lumina_title, plex_id, plex_title, len(episodes))
            if not tmdb_id:
                logger.warning("No TMDB match for show '%s'", show_name)
                for ep in episodes:
                    inventory.file(ep["file_path"], folder or show_name, None, ep["season"], [ep["episode"]], "unmatched",
                                   "seriál nenalezen v TMDB ani v Plexu")
                continue

            if not await (await db.execute("SELECT 1 FROM library_shows WHERE tmdb_id = ?", (tmdb_id,))).fetchone():
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

            # TMDB's episodes in both languages, specials and seasons newer than the show's first scan too
            cat = await episode_names.catalog(client, db, tmdb_id)
            await db.executemany("INSERT OR IGNORE INTO library_episodes (show_tmdb_id, season, episode, episode_title, air_date) "
                                 "VALUES (?, ?, ?, ?, ?)", [(tmdb_id, s, e, v["cs"], v["air"]) for (s, e), v in cat.items()])
            own = episode_names.own_titles(
                [(ep["file_path"], origin_of.get(ep["file_path"], ep["file_path"])) for ep in episodes],
                lambda n: _title_in_name(n) or _bare_title(n))

            # Anime numbered through ("Naruto 104", "[CNT]_Naruto_153_"): a bare number its season does not
            # have is the absolute number — season and episode from the number of episodes in TMDB's seasons
            named = {(r[0], r[1]): r[2] or "" for r in await (await db.execute(
                "SELECT season, episode, episode_title FROM library_episodes WHERE show_tmdb_id = ? AND season > 0 "
                "ORDER BY season, episode", (tmdb_id,))).fetchall()}
            order = list(named)
            in_tmdb = set(order)
            sizes: dict[int, int] = {}
            for s, _ in order:
                sizes[s] = sizes.get(s, 0) + 1
            numbers = [_absolute_in_name(ep["filename"]) or (ep["episode"] if ep.get("bare") else None) for ep in episodes]
            known = [n for n in numbers if n]
            through = sizes and len(known) >= 0.8 * len(episodes) and len(known) >= 10 \
                and max(known) > max(sizes.values()) and len(set(known)) >= 0.9 * len(known)
            for ep_data, number in zip(episodes, numbers):
                # the whole show carries absolute numbers ("Naruto_CZ_027-02x01", "Naruto 104"): they are
                # the only consistent ones (the uploader's SxE follow another season split than TMDB's)
                if through and number:
                    absolute = _absolute_episode(order, number)
                elif ep_data.get("bare") and (ep_data["season"], ep_data["episode"]) not in in_tmdb:
                    # "Pokemon/2/37.Davný protivník.avi": TMDB's S02 has 36 — the name tells the episode (S02E36)
                    # before the number is taken as an absolute one
                    by_name, sure = episode_names.best(own.get(ep_data["file_path"], ""), cat, ep_data["season"],
                                                       lengths.get(ep_data["file_path"], 0))
                    if by_name and sure:
                        ep_data["season"], ep_data["episode"] = by_name
                        absolute = None
                    else:
                        absolute = _absolute_episode(order, ep_data["episode"])
                        number = ep_data["episode"]
                else:
                    absolute = None
                if absolute:
                    ep_data["absolute"] = number
                    ep_data["season"], ep_data["episode"] = absolute

            # the user's word on which episode a file is goes before everything else
            for ep_data in episodes:
                if ep_data["file_path"] in manual:
                    ms, me, mnote = manual[ep_data["file_path"]]
                    ep_data["season"], ep_data["episode"], ep_data["manual"] = ms, me, mnote
                    ep_data.pop("absolute", None)

            # Mark episodes we have on disk
            for ep_data in episodes:
                marked.add((tmdb_id, ep_data["season"], ep_data["episode"]))
                matched_paths.add(ep_data["file_path"])
                cursor = await db.execute(
                    """UPDATE library_episodes
                    SET has_file = 1, filename = ?, file_path = ?,
                        file_size = ?, quality = ?, language = ?
                    WHERE show_tmdb_id = ? AND season = ? AND episode = ?""",
                    (ep_data["filename"], ep_data["file_path"],
                     ep_data["file_size"], ep_data["quality"], ep_data["language"],
                     tmdb_id, ep_data["season"], ep_data["episode"]),
                )
                stats["episodes_matched"] += 1
                hint = ep_data.get("hint") or {}
                # the file's own name ("S01E02 Posilovač 4000", "02.Panika v Oblázkovém městě"), from its first name
                file_title = own.get(ep_data["file_path"], "")
                plex_name = hint.get("episode_title") or ""
                here = (ep_data["season"], ep_data["episode"])
                fits = lambda nm: any(tv_inventory.fits(nm, t)  # noqa: E731
                                      for t in (cat.get(here, {}).get("cs"), cat.get(here, {}).get("en")) if t)
                facts: dict = {"file": [ep_data.get("file_season", ep_data["season"]),
                                        ep_data.get("file_episodes") or [ep_data["episode"]]],
                               "title": file_title}
                if hint.get("season") is not None and hint.get("episode"):
                    facts["plex"] = [hint["season"], hint["episode"], hint.get("episode_title") or ""]
                if ep_data.get("absolute"):
                    facts["absolute"] = ep_data["absolute"]
                status, note = "ok", ""
                if "manual" in ep_data:
                    facts["manual"] = [ep_data["season"], ep_data["episode"], ep_data["manual"]]
                    status, note = "ok", "určeno ručně" + (f": {ep_data['manual']}" if ep_data["manual"] else "")
                elif lumina_id and plex_id and lumina_id != plex_id and not override:
                    status, note = "show", f"Plex: {plex_title} · Lumina: {lumina_title}"
                elif hint and (hint.get("season"), hint.get("episode")) != (ep_data["season"], ep_data["episode"]) \
                        and not (hint.get("season") == ep_data["season"]
                                 and hint.get("episode") in (ep_data.get("file_episodes") or [])) \
                        and ep_data.get("numbers_from") != "plex" and not ep_data.get("absolute"):
                    status, note = "numbers", f"soubor S{ep_data['season']:02d}E{ep_data['episode']:02d}, " \
                                              f"Plex S{(hint.get('season') or 0):02d}E{(hint.get('episode') or 0):02d}"
                elif (other := _other_by_name(file_title, plex_name, fits, cat, here, lengths.get(ep_data["file_path"], 0))) \
                        or not cursor.rowcount:
                    # Big Bang S12: TMDB has the two-part finale as E23 and the farewell special as E24; South
                    # Park S01 in a Czech airing order; Pokémon's 2nd season starting with TMDB's S01E82
                    status, note = ("tmdb_other", other[0]) if cursor.rowcount else (
                        "not_in_tmdb", "TMDB díl pod tímto číslem nemá" + (f" · {other[0]}" if other else ""))
                    if other:
                        facts["tmdb_season"], facts["tmdb_episode"] = other[1]
                        facts["tmdb_sure"] = other[2]
                elif not hint and plex_votes:
                    status, note = "not_in_plex", "Plex soubor nezná"
                inventory.file(ep_data["file_path"], folder or show_name, tmdb_id, ep_data["season"],
                               [ep_data["episode"]], status, note, facts)

        # a file this scan put under a show belongs to no other one (an earlier scan's wrong match:
        # "SGA" episodes under "Sgauth") — a show TMDB could not be asked about now keeps its files
        cursor = await db.execute("SELECT id, show_tmdb_id, season, episode, file_path FROM library_episodes WHERE has_file = 1")
        for row in await cursor.fetchall():
            if row[4] in matched_paths and (row[1], row[2], row[3]) not in marked:
                await db.execute("UPDATE library_episodes SET has_file = 0, file_path = NULL, filename = NULL WHERE id = ?", (row[0],))
        # shows left without a file
        await db.execute("DELETE FROM library_shows WHERE tmdb_id NOT IN "
                         "(SELECT DISTINCT show_tmdb_id FROM library_episodes WHERE has_file = 1)")
        await inventory.save(db)
        await db.commit()
        await _tv_media(db, tv_files)


async def _tv_media(db, files: list[dict]) -> None:
    """MediaInfo of every episode file for the renamer's names ([1080p x265] [CS+EN]) — read again only
    when the file changed (a first scan of a big library takes a while: ~0.1 s a file)."""
    known = {r[0]: (r[1], r[2]) for r in await (await db.execute("SELECT file_path, size, mtime FROM tv_media")).fetchall()}
    todo = [f for f in files if known.get(f["file_path"]) != (f["file_size"], f["added_at"])]
    _job.update(phase="tv_media", total=len(todo), done=0)
    for index, f in enumerate(todo, 1):
        _job["current"] = f["filename"]
        media = await probe_async(f["file_path"])
        await db.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, ?, ?, ?)",
                         (f["file_path"], f["file_size"], f["added_at"], json.dumps(media or {})))
        _job["done"] = index
        if index % 25 == 0:
            await db.commit()           # never hold the database for long (other modules write too)
    present = {f["file_path"] for f in files}
    await db.executemany("DELETE FROM tv_media WHERE file_path = ?", [(p,) for p in known if p not in present])
    await db.commit()

