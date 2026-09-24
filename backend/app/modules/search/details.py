"""Technical details of found files, straight from the sources (WebShare file_info,
FastShare file page) — real audio/subtitle languages instead of guessing from names.

Loaded lazily for the rows the user actually sees, and cached per file: the content of
an uploaded file never changes, so each file is asked for only once.
"""

import asyncio
import json
import logging
import time

from app.db import get_db
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

CACHE_DAYS = 90
MAX_FILES_PER_REQUEST = 20
# Parallel detail requests per source — the clients throttle further (app/core/throttle.py).
PER_SOURCE_CONCURRENCY = 2

SOURCE_FILE_DETAILS = """
CREATE TABLE IF NOT EXISTS source_file_details (
    source_type TEXT NOT NULL,
    ident TEXT NOT NULL,
    data TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (source_type, ident)
);
"""


async def get_details(files: list[dict]) -> dict[str, dict | None]:
    """files: [{source_id, ident, name}] → {"<source_id>:<ident>": details | None}."""
    registry = SourceRegistry.get()
    result: dict[str, dict | None] = {}
    todo: list[tuple[str, object, dict]] = []

    db = await get_db()
    try:
        for f in files[:MAX_FILES_PER_REQUEST]:
            key = f"{f['source_id']}:{f['ident']}"
            source = registry.get_source_by_id(int(f["source_id"]))
            if not source:
                result[key] = None
                continue
            cursor = await db.execute(
                "SELECT data, fetched_at FROM source_file_details WHERE source_type = ? AND ident = ?",
                (source.source_type.value, f["ident"]),
            )
            row = await cursor.fetchone()
            if row and time.time() - row[1] < CACHE_DAYS * 86400:
                result[key] = json.loads(row[0])
            else:
                todo.append((key, source, f))

        limits: dict[int, asyncio.Semaphore] = {}

        async def fetch(key: str, source, f: dict) -> None:
            sem = limits.setdefault(source.source_id, asyncio.Semaphore(PER_SOURCE_CONCURRENCY))
            async with sem:
                try:
                    details = await source.get_details(f["ident"], f["name"])
                except Exception as e:
                    logger.warning("Details of %s %s failed: %s", source.source_type.value, f["ident"], e)
                    details = None
            result[key] = details
            if details is not None:
                await db.execute(
                    "INSERT OR REPLACE INTO source_file_details (source_type, ident, data, fetched_at) VALUES (?, ?, ?, ?)",
                    (source.source_type.value, f["ident"], json.dumps(details), time.time()),
                )

        await asyncio.gather(*(fetch(*item) for item in todo))
        await db.commit()
        if todo:
            logger.info("Fetched details of %d files (%d from cache)", len(todo), len(result) - len(todo))
    finally:
        await db.close()
    return result
