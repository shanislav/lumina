"""What Plex knows of each file of the TV library — for the library scan (event
``library.collect_tv_hints``): which show (TMDB id) and which episode. Plex's matches were often
fixed by hand, so the scan prefers them and shows where Lumina sees it differently."""

import logging

from app.modules.plex.library import PlexError, connect_tv, tv_files
from app.modules.plex.paths import to_plex

logger = logging.getLogger(__name__)


async def on_collect_tv_hints(payload: dict) -> None:
    """payload: {"paths": [file as Lumina sees it, ...], "hints": {}} → hints[path] = {tmdb_id, tvdb_id,
    title, season, episode, episode_title, show_key}."""
    try:
        client, cfg, section, root = await connect_tv()
    except PlexError as e:
        logger.info("Plex TV hints off: %s", e)
        return
    try:
        files = await tv_files(client, section["key"])
    except Exception as e:  # noqa: BLE001 — Plex being down must never break the scan
        logger.warning("Plex TV hints: %s", e)
        return
    finally:
        await client.close()
    rule = cfg.get("path_map", "")
    found = 0
    for path in payload.get("paths") or []:
        plex_path = to_plex(path, root, section["locations"], rule)
        hint = files.get(plex_path) if plex_path else None
        if hint:
            payload["hints"][path] = hint
            found += 1
    logger.info("Plex TV hints: %d of %d files known to Plex (section %s)", found, len(payload.get("paths") or []),
                section["title"])
