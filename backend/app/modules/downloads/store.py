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


async def track_download(id: str, tmdb_id: int, title: str, year: int, backend: str, target_dir: str, content_type: str = "movie"):
    """Record a new download for background monitoring."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO download_tracker (id, tmdb_id, title, year, backend, status, target_dir, content_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id, tmdb_id, title, year, backend, "active", target_dir, content_type)
        )
        await db.commit()
    finally:
        await db.close()
