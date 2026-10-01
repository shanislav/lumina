"""Which movie Plex thinks a file is — a hint for the library import (event ``library.collect_hints``).
Plex's matches were often fixed by hand over the years, so they are a good guess; the file itself
(length, language) still decides."""

import logging
import time

from app.modules.plex.library import PlexError, connect, movies
from app.modules.plex.paths import to_plex

logger = logging.getLogger(__name__)

TTL_S = 600          # one library scan reads Plex once
RETRY_S = 60         # Plex down or not set up → ask again after this long, not for every file

_index: dict = {"at": 0.0, "ok": False}


async def _load() -> dict:
    if time.monotonic() - _index["at"] < (TTL_S if _index["ok"] else RETRY_S):
        return _index
    _index.update(at=time.monotonic(), ok=False)
    try:
        client, cfg, section, root = await connect()
    except PlexError as e:
        logger.debug("Plex hints off: %s", e)
        return _index
    try:
        files = {f: m["tmdb_id"] for m in await movies(client, section["key"]) if m["tmdb_id"] for f in m["files"]}
    except Exception as e:  # noqa: BLE001 — Plex being down must never break the import
        logger.warning("Plex hints: %s", e)
        return _index
    finally:
        await client.close()
    _index.update(ok=True, files=files, root=root, locations=section["locations"], rule=cfg.get("path_map", ""))
    logger.info("Plex hints: %d files of section %s", len(files), section["title"])
    return _index


async def on_collect_hints(payload: dict) -> None:
    index = await _load()
    if not index["ok"]:
        return
    path = to_plex(payload["path"], index["root"], index["locations"], index["rule"])
    tmdb_id = index["files"].get(path) if path else None
    if tmdb_id:
        payload["hints"].append((tmdb_id, "plex"))


def forget() -> None:
    _index.update(at=0.0, ok=False)
