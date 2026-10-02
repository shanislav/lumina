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
                         content_type: str = "movie", intent: dict | None = None, source_label: str = "",
                         requested_by: str = ""):
    """Record a new download for background monitoring."""
    from datetime import datetime
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO download_tracker (id, tmdb_id, title, year, backend, status, target_dir, "
            "content_type, intent, source_label, requested_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, tmdb_id, title, year, backend, "active", target_dir, content_type,
             json.dumps(intent) if intent else "", source_label, requested_by,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()
    finally:
        await db.close()


async def tracked() -> dict[str, dict]:
    """gid / torrent hash -> what Lumina knows of the downloads it started (source, film, who, when)."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, source_label, tmdb_id, title, intent, requested_by, created_at, "
                                  "content_type FROM download_tracker")
        out = {}
        for r in await cursor.fetchall():
            intent = json.loads(r["intent"]) if r["intent"] else {}
            out[r["id"]] = {"source_label": r["source_label"] or "", "tmdb_id": r["tmdb_id"], "film": r["title"] or "",
                            "requested_by": r["requested_by"] or "", "created_at": r["created_at"] or "",
                            "mode": intent.get("mode") or "", "content_type": r["content_type"] or "movie",
                            # a TV episode: which one (the show page shows it as downloading)
                            "season": intent.get("season"), "episode": intent.get("episode")}
        return out
    finally:
        await db.close()


HISTORY_DAYS = 180


async def prune_history() -> None:
    """Finished downloads older than half a year leave the list."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM download_tracker WHERE processed = 1 AND "
                         "COALESCE(NULLIF(finished_at, ''), created_at) < datetime('now', ?)", (f"-{HISTORY_DAYS} days",))
        await db.commit()
    finally:
        await db.close()


async def history(offset: int = 0, limit: int = 10) -> tuple[list[dict], int]:
    """Finished / failed / cancelled downloads, the newest first, and how many there are."""
    db = await get_db()
    try:
        total = (await (await db.execute("SELECT COUNT(*) FROM download_tracker WHERE processed = 1")).fetchone())[0]
        cursor = await db.execute(
            "SELECT * FROM download_tracker WHERE processed = 1 "
            "ORDER BY COALESCE(NULLIF(finished_at, ''), created_at) DESC LIMIT ? OFFSET ?", (limit, offset))
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    out = []
    for r in rows:
        intent = json.loads(r["intent"]) if r.get("intent") else {}
        title = r["title"] or ""
        out.append({
            "id": r["id"], "backend": r["backend"], "status": r["status"] or "complete",
            "filename": r.get("file_name") or (f"{title} ({r['year']})" if r.get("year") else title),
            "total_length": r.get("size") or 0, "completed_length": r.get("size") or 0, "download_speed": 0,
            "source_label": r.get("source_label") or "", "tmdb_id": r["tmdb_id"], "film": title,
            "requested_by": r.get("requested_by") or "", "created_at": r.get("created_at") or "",
            "finished_at": r.get("finished_at") or "", "mode": intent.get("mode") or "",
            "content_type": r.get("content_type") or "movie",
        })
    return out, total


async def forget(download_id: str) -> bool:
    """Take a finished download off the list."""
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM download_tracker WHERE id = ? AND processed = 1", (download_id,))
        await db.commit()
        return cursor.rowcount > 0
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
