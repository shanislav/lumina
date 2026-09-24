"""Radarr as a hint source for library identification (``library.collect_hints``).

Used once while migrating away from Radarr: Radarr's tmdb id for a file is only a
hint — it is wrong for some movies (docs/decisions/0002). Works whenever Radarr
URL + API key are configured, independent of the post-processing switch.
"""

import logging
import time

from app.clients.radarr import RadarrClient
from app.db import get_automation

logger = logging.getLogger(__name__)

CACHE_SECONDS = 600
_cache: dict = {"at": 0.0, "by_path": {}}


async def _files_by_path(cfg: dict) -> dict[str, int]:
    if time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["by_path"]
    client = RadarrClient(cfg["url"], cfg["api_key"])
    try:
        movies = await client.get_movies()
    finally:
        await client.close()
    by_path = {}
    for m in movies:
        f = m.get("movieFile")
        if m.get("hasFile") and f:
            by_path[f"{m['path'].rstrip('/')}/{f['relativePath']}"] = m["tmdbId"]
    _cache.update({"at": time.time(), "by_path": by_path})
    return by_path


async def on_collect_hints(payload: dict) -> None:
    automation = await get_automation("radarr")
    cfg = (automation or {}).get("config") or {}
    if not (cfg.get("url") and cfg.get("api_key")):
        return
    try:
        tmdb_id = (await _files_by_path(cfg)).get(payload["path"])
    except Exception as e:
        logger.warning("Radarr hints unavailable: %s", e)
        return
    if tmdb_id:
        payload["hints"].append((tmdb_id, "radarr"))
