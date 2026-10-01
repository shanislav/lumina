"""Wanted films: storage, the check (offers → profile → best) and the background job."""

import asyncio
import json
import logging
from datetime import datetime

from app.config import get_effective_settings
from app.core import events
from app.core.offers.search import find_offers, verify_offers
from app.core.profiles import get_profile
from app.core.profiles import suitable as pick_suitable
from app.db import get_db

logger = logging.getLogger(__name__)

WANTED = """
CREATE TABLE IF NOT EXISTS wanted (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER,
    wikidata_id TEXT DEFAULT '',
    title TEXT NOT NULL,
    original_title TEXT DEFAULT '',
    year TEXT DEFAULT '',
    poster_url TEXT,
    profile_id INTEGER,              -- NULL = the default profile
    status TEXT DEFAULT 'wanted',    -- wanted | found | downloading | done
    matches INTEGER DEFAULT 0,       -- offers the profile allows
    best TEXT DEFAULT '{}',          -- the best of them
    note TEXT DEFAULT '',
    added_at TEXT,
    checked_at TEXT,
    done_at TEXT
);
"""

PAUSE_BETWEEN_FILMS_S = 5
VERIFY_PER_FILM = 10
BEST_FIELDS = ("name", "source", "source_id", "ident", "size", "magnet_url", "quality_score", "quality_summary",
               "resolution", "codec", "hdr", "lang_tier", "audio_langs", "verified", "video_bitrate")

_queue: list[int] = []
_auto_download: set[int] = set()      # films the scheduler may download automatically when found
_state = {"running": False, "total": 0, "done": 0, "current": "", "found": 0}


def job_status() -> dict:
    return {**_state, "queued": len(_queue)}


def enqueue(ids: list[int], auto_download: bool = False) -> dict:
    if auto_download:
        _auto_download.update(ids)
    new = [i for i in dict.fromkeys(ids) if i not in _queue]
    _queue.extend(new)
    if not _state["running"]:
        _state.update(running=True, total=len(_queue), done=0, found=0, current="")
        asyncio.create_task(_run())
    else:
        _state["total"] += len(new)
    return job_status()


async def _run() -> None:
    try:
        while _queue:
            wanted_id = _queue.pop(0)
            try:
                if (await check(wanted_id) or {}).get("status") == "found":
                    _state["found"] += 1
                    if wanted_id in _auto_download:
                        await request_download(wanted_id)
                _auto_download.discard(wanted_id)
            except Exception as e:   # one film must not stop the job
                logger.warning("Wanted check %s failed: %s", wanted_id, e)
            _state["done"] += 1
            if _queue:
                await asyncio.sleep(PAUSE_BETWEEN_FILMS_S)
    finally:
        _state.update(running=False, current="")


async def get(wanted_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM wanted WHERE id = ?", (wanted_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def check(wanted_id: int) -> dict | None:
    """Search the sources for the film, keep what the profile allows, remember the best."""
    item = await get(wanted_id)
    if not item or item["status"] == "done":
        return None
    _state["current"] = item["title"]
    cfg = await get_effective_settings()
    profile = await get_profile(item["profile_id"])
    query = f"{item['title']} {item['year']}".strip()
    offers = await find_offers(cfg, query, original_title=item["original_title"] or "",
                               tmdb_id=item["tmdb_id"] or None, media_type="movie",
                               wikidata_id=item["wikidata_id"] or None)
    await verify_offers(offers, limit=VERIFY_PER_FILM)
    suitable = pick_suitable(offers.rows, profile)
    best = {k: suitable[0].get(k) for k in BEST_FIELDS} if suitable else {}
    status = "found" if suitable else "wanted"
    db = await get_db()
    try:
        await db.execute("UPDATE wanted SET status = ?, matches = ?, best = ?, checked_at = ? WHERE id = ?",
                         (status, len(suitable), json.dumps(best), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          wanted_id))
        await db.commit()
    finally:
        await db.close()
    logger.info("Wanted '%s' (%s): %d suitable of %d offers", item["title"], profile.name, len(suitable), len(offers.rows))
    if suitable:
        await events.emit("offers.found", {"kind": "wanted", "wanted_id": wanted_id, "tmdb_id": item["tmdb_id"],
                                           "title": item["title"], "year": item["year"], "profile": profile.name,
                                           "matches": len(suitable), "best": best})
    return {"status": status, "matches": len(suitable)}


async def request_download(wanted_id: int) -> None:
    """Scheduler with automatic downloads on: ask the downloads module for the best offer."""
    item = await get(wanted_id)
    best = json.loads((item or {}).get("best") or "{}")
    if not item or not best.get("ident"):
        return
    payload = await events.emit("download.request", {
        "file_ident": best["ident"], "source": best.get("source"), "source_id": best.get("source_id") or 0,
        "magnet_url": best.get("magnet_url"), "tmdb_id": item["tmdb_id"], "title": item["title"],
        "year": int(item["year"] or 0), "content_type": "movie", "requested_by": "wanted (plánovač)",
    })
    if payload.get("started"):
        db = await get_db()
        try:
            await db.execute("UPDATE wanted SET status = 'downloading' WHERE id = ?", (wanted_id,))
            await db.commit()
        finally:
            await db.close()


async def on_scheduler_run(payload: dict) -> None:
    """Nightly run: check every film that is not found-and-downloading or done yet."""
    if not payload.get("wanted"):
        return
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM wanted WHERE status IN ('wanted', 'found') "
                                  "ORDER BY checked_at IS NOT NULL, checked_at")
        ids = [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()
    if ids:
        enqueue(ids, auto_download=bool(payload.get("auto_download_wanted")))


async def on_movie_updated(payload: dict) -> None:
    """The film is in the library now → done."""
    tmdb_id = payload.get("tmdb_id")
    if not tmdb_id or payload.get("status") not in ("matched", "manual"):
        return
    db = await get_db()
    try:
        await db.execute("UPDATE wanted SET status = 'done', done_at = ? WHERE tmdb_id = ? AND status != 'done'",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tmdb_id))
        await db.commit()
    finally:
        await db.close()
