"""Background check: is there a better version of a library movie on the sources?

For each movie: the owned version to beat (the preferred one, else the best score) → all
offers of the film (app/core/offers, same as the search UI) → the likely ones verified at the
source → offers that are an upgrade by the agreed rule (core.offers.search.upgrade_block).
The result per movie is kept in upgrade_checks, so the library can show "a better version
exists" without searching again.

One job at a time, movies one after another with a pause — WebShare/FastShare must not see
a burst (the clients throttle as well). A scheduler (nightly checks, wanted quality) will
call run_checks() the same way the API does.
"""

import asyncio
import json
import logging
from datetime import datetime

from app.config import get_effective_settings
from app.core import quality
from app.core.offers.search import find_offers, upgrade_block, verify_offers
from app.core.profiles import block, get_profile, reached_cutoff, row_from_media
from app.db import get_db

logger = logging.getLogger(__name__)

UPGRADE_CHECKS = """
CREATE TABLE IF NOT EXISTS upgrade_checks (
    tmdb_id INTEGER PRIMARY KEY,
    owned_id INTEGER,
    owned_score INTEGER DEFAULT 0,
    status TEXT DEFAULT '',          -- better | none | error
    upgrades INTEGER DEFAULT 0,      -- how many offers are an upgrade
    best TEXT DEFAULT '{}',          -- the best upgrade offer
    error TEXT DEFAULT '',
    checked_at TEXT
);
"""

# Film-level settings (decisions/0004, 0005): the quality profile of a film and whether Lumina
# watches for a better version of it (the scheduler checks those).
LIBRARY_FILMS = """
CREATE TABLE IF NOT EXISTS library_films (
    tmdb_id INTEGER PRIMARY KEY,
    profile_id INTEGER,              -- NULL = the default profile
    watch_upgrades INTEGER NOT NULL DEFAULT 0
);
"""

PAUSE_BETWEEN_MOVIES_S = 5
VERIFY_PER_MOVIE = 10
MAX_PER_JOB = 200

_queue: list[int] = []
_auto_download: dict[int, str] = {}     # tmdb_id → "version" | "replace" (scheduler with auto downloads)
_state = {"running": False, "total": 0, "done": 0, "current": "", "found": 0}
_task: asyncio.Task | None = None


def status() -> dict:
    return {**_state, "queued": len(_queue)}


def enqueue(tmdb_ids: list[int]) -> dict:
    """Add movies to the running job (or start one)."""
    global _task
    new = [t for t in dict.fromkeys(tmdb_ids) if t and t not in _queue][:MAX_PER_JOB]
    _queue.extend(new)
    if not _state["running"]:
        _state.update(running=True, total=len(_queue), done=0, found=0, current="")
        _task = asyncio.create_task(_run())
    else:
        _state["total"] += len(new)
    return status()


async def _run() -> None:
    try:
        while _queue:
            tmdb_id = _queue.pop(0)
            try:
                result = await check_movie(tmdb_id)
                if result and result["status"] == "better":
                    _state["found"] += 1
                    if tmdb_id in _auto_download:
                        await request_download(tmdb_id, _auto_download[tmdb_id])
                _auto_download.pop(tmdb_id, None)
            except Exception as e:  # one movie must not stop the job
                logger.warning("Upgrade check of tmdb %s failed: %s", tmdb_id, e)
                await _save(tmdb_id, None, 0, "error", [], str(e))
            _state["done"] += 1
            if _queue:
                await asyncio.sleep(PAUSE_BETWEEN_MOVIES_S)
    finally:
        _state.update(running=False, current="")
        logger.info("Upgrade check finished: %d movies, %d with a better version", _state["done"], _state["found"])


async def _owned_version(db, tmdb_id: int, prefs: quality.Prefs) -> dict | None:
    cursor = await db.execute(
        "SELECT id, title, original_title, year, filename, file_size, language, media, preferred "
        "FROM library_movies WHERE tmdb_id = ? AND status IN ('matched', 'manual')", (tmdb_id,))
    versions = []
    for r in await cursor.fetchall():
        media = json.loads(r["media"] or "{}")
        q = quality.score(quality.facts_from_media(media, r["filename"] or "", r["file_size"] or 0), prefs)
        versions.append({**dict(r), "quality_score": q.score})
    if not versions:
        return None
    return max(versions, key=lambda v: (v["preferred"] or 0, v["quality_score"]))


async def film_settings(db, tmdb_id: int) -> dict:
    cursor = await db.execute("SELECT profile_id, watch_upgrades FROM library_films WHERE tmdb_id = ?", (tmdb_id,))
    row = await cursor.fetchone()
    return {"profile_id": row[0], "watch_upgrades": bool(row[1])} if row else {"profile_id": None, "watch_upgrades": False}


async def check_movie(tmdb_id: int) -> dict | None:
    """Better versions of an owned film: higher score (the agreed upgrade rule) AND the film's
    profile allows it. An owned version that already reached the profile's cutoff is done."""
    cfg = await get_effective_settings()
    prefs = quality.prefs_from_settings(cfg)
    db = await get_db()
    try:
        owned = await _owned_version(db, tmdb_id, prefs)
        settings = await film_settings(db, tmdb_id)
    finally:
        await db.close()
    if not owned:
        return None
    profile = await get_profile(settings["profile_id"])
    owned_row = row_from_media(json.loads(owned["media"] or "{}"), owned["filename"] or "", owned["file_size"] or 0, prefs)
    if reached_cutoff(owned_row, profile):
        await _save(tmdb_id, owned, owned["quality_score"], "done", [], note=f"cíl profilu {profile.name} splněn")
        return {"status": "done", "upgrades": 0}
    _state["current"] = owned["title"]
    offers = await find_offers(cfg, owned["title"], original_title=owned["original_title"] or "",
                               tmdb_id=tmdb_id, media_type="movie")
    await verify_offers(offers, limit=VERIFY_PER_MOVIE)
    owned_cmp = {"quality_score": owned["quality_score"], "language": owned["language"] or "",
                 "file_size": owned["file_size"]}
    better = [r for r in offers.rows
              if upgrade_block(r, owned_cmp, offers.prefs) is None and block(r, profile) is None]
    # prefer verified offers, then the score — an unverified name can promise too much
    better.sort(key=lambda r: (not r.get("verified"), -r["quality_score"]))
    status_ = "better" if better else "none"
    await _save(tmdb_id, owned, owned["quality_score"], status_, better, note=f"profil {profile.name}")
    return {"status": status_, "upgrades": len(better)}


async def _save(tmdb_id: int, owned: dict | None, owned_score: int, status_: str, better: list[dict],
                error: str = "", note: str = "") -> None:
    best = {}
    if better:
        b = better[0]
        best = {k: b.get(k) for k in ("name", "source", "source_id", "ident", "size", "magnet_url", "quality_score",
                                      "quality_summary", "lang_tier", "audio_langs", "verified")}
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO upgrade_checks (tmdb_id, owned_id, owned_score, status, upgrades, best, error, "
            "checked_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tmdb_id, owned["id"] if owned else None, owned_score, status_, len(better), json.dumps(best), error,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note),
        )
        await db.commit()
    finally:
        await db.close()


async def request_download(tmdb_id: int, mode: str) -> None:
    """Scheduler with automatic upgrades: download the best better version as a new version or as a
    replacement of the compared one (the import deletes the old one only when the lengths agree)."""
    from app.core import events

    check = (await results()).get(str(tmdb_id)) or {}
    best = check.get("best") or {}
    if check.get("status") != "better" or not best.get("ident") or not check.get("owned_id"):
        return
    db = await get_db()
    try:
        cursor = await db.execute("SELECT title, year FROM library_movies WHERE id = ?", (check["owned_id"],))
        owned = await cursor.fetchone()
    finally:
        await db.close()
    if not owned:
        return
    action = {"mode": "replace", "file_id": check["owned_id"]} if mode == "replace" else {"mode": "version"}
    payload = await events.emit("download.request", {
        "file_ident": best["ident"], "source": best.get("source"), "source_id": best.get("source_id") or 0,
        "magnet_url": best.get("magnet_url"), "tmdb_id": tmdb_id, "title": owned["title"],
        "year": int((owned["year"] or "0")[:4] or 0), "content_type": "movie", "library_action": action,
        "requested_by": "upgrade (plánovač)",
    })
    if payload.get("started"):
        db = await get_db()
        try:
            await db.execute("UPDATE upgrade_checks SET status = 'downloading' WHERE tmdb_id = ?", (tmdb_id,))
            await db.commit()
        finally:
            await db.close()


async def on_scheduler_run(payload: dict) -> None:
    """Nightly run: films with "watch for a better version" (not already downloading one)."""
    if not payload.get("upgrades"):
        return
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT f.tmdb_id FROM library_films f LEFT JOIN upgrade_checks u ON u.tmdb_id = f.tmdb_id "
            "WHERE f.watch_upgrades = 1 AND (u.status IS NULL OR u.status != 'downloading' "
            "OR u.checked_at < datetime('now', '-3 days'))")
        ids = [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()
    mode = payload.get("auto_download_upgrades") or "off"
    if mode in ("version", "replace"):
        _auto_download.update({i: mode for i in ids})
    if ids:
        enqueue(ids)


async def results() -> dict[str, dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM upgrade_checks")
        return {
            str(r["tmdb_id"]): {
                "owned_id": r["owned_id"], "owned_score": r["owned_score"], "status": r["status"],
                "upgrades": r["upgrades"], "best": json.loads(r["best"] or "{}"), "error": r["error"],
                "checked_at": r["checked_at"], "note": r["note"] or "",
            }
            for r in await cursor.fetchall()
        }
    finally:
        await db.close()
