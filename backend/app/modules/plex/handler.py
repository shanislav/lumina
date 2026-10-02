"""Collect changed movie folders and scan them in Plex once things calm down — a batch rename
of hundreds of movies becomes one section scan instead of hundreds of requests (Plex then sees
the removed and the added file in the same scan and keeps the movie, see decisions/0008)."""

import asyncio
import logging
import os
import time

from app.clients.plex import PlexClient
from app.config import get_effective_settings, movies_library_dir, tv_library_dir
from app.db import get_automation
from app.modules.plex import migration
from app.modules.plex.paths import section_for, to_plex

logger = logging.getLogger(__name__)

QUIET_S = 15             # scan once no change came for this long …
MAX_WAIT_S = 120         # … or after this long with a few folders (not for a big batch: that waits for quiet)
MAX_FOLDERS = 20         # more changed folders in one section → scan the whole section once

_pending: set[str] = set()
_task: asyncio.Task | None = None
_first = 0.0
_last = 0.0


async def on_movie_updated(payload: dict) -> None:
    if not payload.get("folder") or payload.get("folder_is_library_root"):
        return
    await _queue(payload["folder"])


async def _roots() -> list[str]:
    cfg = await get_effective_settings()
    return [os.path.normpath(r) for r in (movies_library_dir(cfg), tv_library_dir(cfg)) if r]


def _root_of(folder: str, roots: list[str]) -> str | None:
    folder = os.path.normpath(folder)
    return next((r for r in roots if folder == r or folder.startswith(r + os.sep)), None)


async def on_files_removed(payload: dict) -> None:
    """A deleted version or a whole movie folder (a renamed show folder): scan the nearest folder still on
    disk (the movie's, or the year folder above a removed one; the TV library) — Plex then drops what is gone."""
    roots = await _roots()
    movies = os.path.normpath(movies_library_dir(await get_effective_settings()) or "")
    for folder in payload.get("folders", []):
        folder = os.path.normpath(folder)
        root = _root_of(folder, roots)
        if not root:
            continue
        while not os.path.isdir(folder) and folder != root:
            folder = os.path.dirname(folder)
        if folder != movies:                 # the whole TV library is fine (a show folder renamed), not all movies
            await _queue(folder)


async def _queue(folder: str) -> None:
    global _task, _first, _last
    automation = await get_automation("plex")
    if not automation or not automation["enabled"]:
        return
    if await migration.active():   # the migration scans the whole section after each batch itself
        return
    now = time.monotonic()
    if not _pending:
        _first = now
    _last = now
    _pending.add(folder)
    if _task is None or _task.done():
        _task = asyncio.create_task(_flush_later())


async def _flush_later() -> None:
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        if now - _last >= QUIET_S or (len(_pending) <= MAX_FOLDERS and now - _first >= MAX_WAIT_S):
            break
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
    roots = await _roots()
    client = PlexClient(cfg["url"], cfg["token"])
    done: list[str] = []
    try:
        sections = [s for s in await client.sections() if s["type"] in ("movie", "show")]
        locations = [loc for s in sections for loc in s["locations"]]
        targets: dict[str, list[str]] = {}
        for folder in folders:
            root = _root_of(folder, roots) or (roots[0] if roots else "")
            path = to_plex(folder, root, locations, cfg.get("path_map", ""))
            section = section_for(path, sections) if path else None
            if not section:
                logger.warning("Plex: no library contains %s (set the path mapping)", folder)
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
