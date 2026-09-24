import json

from app.db import get_db

DOWNLOAD_TRACKER_V1 = """
CREATE TABLE IF NOT EXISTS download_tracker (
    id TEXT PRIMARY KEY,
    tmdb_id INTEGER,
    title TEXT,
    year INTEGER,
    backend TEXT,
    status TEXT,
    target_dir TEXT,
    content_type TEXT DEFAULT 'movie',
    processed INTEGER DEFAULT 0
);
"""


async def track_download(id: str, tmdb_id: int, title: str, year: int, backend: str, target_dir: str,
                         content_type: str = "movie", intent: dict | None = None, source_label: str = ""):
    """Record a new download for background monitoring."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO download_tracker (id, tmdb_id, title, year, backend, status, target_dir, "
            "content_type, intent, source_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, tmdb_id, title, year, backend, "active", target_dir, content_type,
             json.dumps(intent) if intent else "", source_label)
        )
        await db.commit()
    finally:
        await db.close()


async def source_labels() -> dict[str, str]:
    """gid / torrent hash -> source label of the downloads Lumina started."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, source_label FROM download_tracker WHERE source_label != ''")
        return {row[0]: row[1] for row in await cursor.fetchall()}
    finally:
        await db.close()
