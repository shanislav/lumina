"""Plex's view of Lumina's movie library: the section that holds it and its movies."""

import re

from app.clients.plex import PlexClient
from app.config import get_effective_settings, movies_library_dir
from app.db import get_automation
from app.modules.plex.paths import section_for, to_plex


class PlexError(Exception):
    pass


async def connect() -> tuple[PlexClient, dict, dict, str]:
    """(client, config, the movie section holding Lumina's library, library root). The caller closes
    the client. Raises PlexError when Plex is not set up or the library is not in it."""
    automation = await get_automation("plex")
    cfg = (automation or {}).get("config") or {}
    if not (cfg.get("url") and cfg.get("token")):
        raise PlexError("Plex není nastavený (Nastavení → Integrace → Plex)")
    root = movies_library_dir(await get_effective_settings())
    if not root:
        raise PlexError("Knihovna filmů není nastavená")
    client = PlexClient(cfg["url"], cfg["token"])
    try:
        sections = [s for s in await client.sections() if s["type"] == "movie"]
        path = to_plex(root, root, [loc for s in sections for loc in s["locations"]], cfg.get("path_map", ""))
        section = section_for(path, sections) if path else None
        if not section:
            raise PlexError(f"Knihovnu {root} jsem v Plexu nenašel — nastav mapování cest u integrace Plex")
    except PlexError:
        await client.close()
        raise
    except Exception as e:
        await client.close()
        raise PlexError(f"Plex neodpovídá: {e or type(e).__name__}") from e
    return client, cfg, section, root


def _ids(meta: dict) -> tuple[int | None, str | None]:
    """(tmdb id, imdb id) from the new agent's Guid list or a legacy agent's guid."""
    tmdb = imdb = None
    for g in [x.get("id", "") for x in meta.get("Guid", [])] + [meta.get("guid", "")]:
        if m := re.match(r"(?:tmdb|com\.plexapp\.agents\.themoviedb)://(\d+)", g):
            tmdb = tmdb or int(m[1])
        elif m := re.match(r"(?:imdb|com\.plexapp\.agents\.imdb)://(tt\d+)", g):
            imdb = imdb or m[1]
    return tmdb, imdb


def movie(meta: dict) -> dict:
    """What Lumina keeps of a Plex movie."""
    tmdb, imdb = _ids(meta)
    return {
        "rating_key": str(meta["ratingKey"]),
        "title": meta.get("title", ""),
        "year": meta.get("year"),
        "tmdb_id": tmdb,
        "imdb_id": imdb,
        "files": sorted(p["file"] for m in meta.get("Media", []) for p in m.get("Part", []) if p.get("file")),
        "view_count": int(meta.get("viewCount") or 0),
        "last_viewed_at": meta.get("lastViewedAt"),
        "added_at": meta.get("addedAt"),
        "missing": bool(meta.get("deletedAt")),     # in the trash: its files are gone
    }


async def movies(client: PlexClient, section_key: str) -> list[dict]:
    return [movie(m) for m in await client.items(section_key)]
