"""Collect changed movie folders and scan them in Plex a moment later — a batch rename of
hundreds of movies becomes one section scan instead of hundreds of requests."""

import asyncio
import logging

from app.clients.plex import PlexClient
from app.config import get_effective_settings, movies_library_dir
from app.db import get_automation
from app.modules.plex.paths import section_for, to_plex

logger = logging.getLogger(__name__)

DELAY_S = 5
MAX_FOLDERS = 20          # more changed folders in one section → scan the whole section once

_pending: set[str] = set()
_task: asyncio.Task | None = None


async def on_movie_updated(payload: dict) -> None:
    global _task
    automation = await get_automation("plex")
    if not automation or not automation["enabled"]:
        return
    if not payload.get("folder") or payload.get("folder_is_library_root"):
        return
    _pending.add(payload["folder"])
    if _task is None or _task.done():
        _task = asyncio.create_task(_flush_later())


async def _flush_later() -> None:
    await asyncio.sleep(DELAY_S)
    folders = sorted(_pending)
    _pending.clear()
    try:
        await scan_folders(folders)
    except Exception as e:  # Plex being down must never break the library
        logger.warning("Plex scan failed: %s", e)


async def scan_folders(folders: list[str]) -> list[str]:
    """Scan folders in Plex; returns what was asked ("<section>: <path>" or "<section>")."""
    automation = await get_automation("plex")
    cfg = (automation or {}).get("config") or {}
    if not (cfg.get("url") and cfg.get("token")):
        return []
    root = movies_library_dir(await get_effective_settings())
    client = PlexClient(cfg["url"], cfg["token"])
    done: list[str] = []
    try:
        sections = [s for s in await client.sections() if s["type"] == "movie"]
        locations = [loc for s in sections for loc in s["locations"]]
        targets: dict[str, list[str]] = {}
        for folder in folders:
            path = to_plex(folder, root, locations, cfg.get("path_map", ""))
            section = section_for(path, sections) if path else None
            if not section:
                logger.warning("Plex: no movie library contains %s (set the path mapping)", folder)
                continue
            targets.setdefault(section["key"], []).append(path)
        for key, paths in targets.items():
            if len(paths) > MAX_FOLDERS:
                await client.scan(key)
                done.append(key)
            else:
                for path in paths:
                    await client.scan(key, path)
                    done.append(f"{key}: {path}")
    finally:
        await client.close()
    logger.info("Plex scan: %s", "; ".join(done) or "nothing")
    return done
