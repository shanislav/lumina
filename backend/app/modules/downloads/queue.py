"""Download queue: at most ``max_concurrent_downloads`` of Lumina's downloads run at once, the rest wait here.

A waiting download keeps its request (DownloadRequest) — the source link is resolved only when it
starts, a WebShare/FastShare link would expire while waiting. The monitor starts the next ones
when a download finishes. 0 = no limit.
"""

import json
import logging
from datetime import datetime

from app.db import get_all_settings, get_db

logger = logging.getLogger(__name__)

DOWNLOAD_QUEUE = """
CREATE TABLE IF NOT EXISTS download_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request TEXT NOT NULL,          -- DownloadRequest as JSON
    requested_by TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
"""

DEFAULT_LIMIT = 3


async def limit() -> int:
    raw = (await get_all_settings()).get("max_concurrent_downloads")
    try:
        return max(0, int(raw)) if raw not in (None, "") else DEFAULT_LIMIT
    except ValueError:
        return DEFAULT_LIMIT


async def _counts() -> tuple[int, int]:
    """(running downloads Lumina tracks, waiting in the queue)"""
    db = await get_db()
    try:
        running = (await (await db.execute("SELECT COUNT(*) FROM download_tracker WHERE processed = 0")).fetchone())[0]
        waiting = (await (await db.execute("SELECT COUNT(*) FROM download_queue")).fetchone())[0]
        return running, waiting
    finally:
        await db.close()


async def must_wait() -> bool:
    """A new download waits when the limit is reached — or others already wait (first come, first served)."""
    n = await limit()
    if not n:
        return False
    running, waiting = await _counts()
    return running >= n or waiting > 0


async def add(request: dict, requested_by: str = "") -> int:
    db = await get_db()
    try:
        cursor = await db.execute("INSERT INTO download_queue (request, requested_by, created_at) VALUES (?, ?, ?)",
                                  (json.dumps(request), requested_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def items() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, request, requested_by, created_at FROM download_queue ORDER BY id")
        return [{"id": r[0], "request": json.loads(r[1]), "requested_by": r[2] or "", "created_at": r[3] or ""}
                for r in await cursor.fetchall()]
    finally:
        await db.close()


async def remove(queue_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM download_queue WHERE id = ?", (queue_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def clear() -> list[dict]:
    """Empty the queue; returns what waited."""
    waiting = await items()
    db = await get_db()
    try:
        await db.execute("DELETE FROM download_queue")
        await db.commit()
    finally:
        await db.close()
    return waiting


async def take(queue_id: int) -> dict | None:
    """Take one waiting download out of the queue (to start it now)."""
    item = next((q for q in await items() if q["id"] == queue_id), None)
    if item:
        await remove(queue_id)
    return item


async def pending() -> int:
    return (await _counts())[1]


async def drain() -> int:
    """Start waiting downloads while there is room. Returns how many started."""
    from app.models.schemas import DownloadRequest
    from app.modules.downloads.router import start_download

    started = 0
    while True:
        n = await limit()
        running, waiting = await _counts()
        if not waiting or (n and running >= n):
            return started
        first = (await items())[0]
        await remove(first["id"])
        title = first["request"].get("title") or "?"
        try:
            await start_download(DownloadRequest(**first["request"]), requested_by=first["requested_by"], queued=False)
            started += 1
            logger.info("Download queue: started %s", title)
        except Exception as e:  # a dead link must not block the rest
            logger.warning("Download queue: %s could not start: %s", title, e)
            from app.core import events
            if first["request"].get("tmdb_id"):
                await events.emit("download.cancelled", {"tmdb_ids": [first["request"]["tmdb_id"]], "stop_all": False})
