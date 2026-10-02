import asyncio
import logging
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.clients.tmdb import TMDBClient
from app.config import get_effective_settings
from app.core import events
from app.core.auth import require
from app.core.profiles import load_profiles, pick_profile
from app.core.quality import prefs_from_settings
from app.modules.series import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/series", tags=["series"])

TMDB_TTL_S = 6 * 3600
_cache: dict[tuple, tuple[float, object]] = {}


async def _cached(key: tuple, fetch):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TMDB_TTL_S:
        return hit[1]
    value = await fetch()
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.time(), value)
    return value


async def show_with_seasons(tmdb_id: int, fresh: bool = False) -> tuple[dict, dict[int, list[dict]]]:
    """The show and the episodes of every season from TMDB (cached for 6 hours)."""
    cfg = await get_effective_settings()
    if fresh:
        for key in [k for k in _cache if k[1] == tmdb_id]:
            _cache.pop(key, None)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        locale = {"cs": "cs-CZ", "sk": "sk-SK", "en": "en-US"}.get(cfg.get("metadata_language") or "cs", "cs-CZ")
        show = await _cached(("show", tmdb_id, locale), lambda: client.get_tv_full(tmdb_id, language=locale))

        async def season(n: int) -> list[dict]:
            try:
                return await _cached(("season", tmdb_id, n, locale), lambda: client.get_season(tmdb_id, n, language=locale))
            except Exception as e:
                logger.info("TMDB season %s of %s failed: %s", n, tmdb_id, e)
                return []

        numbers = [s["season_number"] for s in show["seasons"]]
        episodes = dict(zip(numbers, await asyncio.gather(*(season(n) for n in numbers))))
    finally:
        await client.close()
    return show, episodes


@router.get("/{tmdb_id}", dependencies=[Depends(require("search"))])
async def series_detail(tmdb_id: int, fresh: bool = False) -> dict:
    """A TV show's page: the show, its settings, and every season with the state of each episode
    (owned / temp = owned without CZ/SK, waits for the dub / missing / upcoming)."""
    try:
        show, episodes = await show_with_seasons(tmdb_id, fresh)
    except Exception as e:
        raise HTTPException(502, f"TMDB: {e}")
    settings = await store.get_settings(tmdb_id)
    owned = await store.owned_episodes(tmdb_id)
    local = list(prefs_from_settings(await get_effective_settings()).local_langs)
    today = date.today()
    mode = settings["effective"]["lang_mode"]
    seasons = [store.season_view(s, episodes.get(s["season_number"], []), owned, mode, local, today)
               for s in show["seasons"]]
    totals = {k: sum(s["counts"][k] for s in seasons) for k in ("owned", "temp", "missing", "upcoming")}
    profiles = await load_profiles()
    profile = pick_profile(profiles, settings["effective"]["profile_id"], "tv")
    return {"show": show, "settings": settings, "profile": {"id": profile.id, "name": profile.name},
            "seasons": seasons, "totals": totals, "local_langs": local,
            "in_library": bool(owned)}


class SettingsBody(BaseModel):
    values: dict              # only the keys to change: profile_id, lang_mode, torrent, monitor (None = default)


@router.put("/{tmdb_id}/settings", dependencies=[Depends(require("library.edit"))])
async def series_settings(tmdb_id: int, body: SettingsBody) -> dict:
    show = {}
    try:
        show, _ = await show_with_seasons(tmdb_id)
    except Exception as e:
        logger.info("TMDB of show %s failed: %s", tmdb_id, e)
    return await store.save_settings(tmdb_id, body.values, show)


@router.get("/defaults/settings", dependencies=[Depends(require("search"))])
async def get_series_defaults() -> dict:
    return await store.get_defaults()


@router.put("/defaults/settings", dependencies=[Depends(require("settings"))])
async def put_series_defaults(body: SettingsBody) -> dict:
    return await store.save_defaults(body.values)


@router.get("/{tmdb_id}/season/{season}/offers", dependencies=[Depends(require("search"))])
async def season_offers(tmdb_id: int, season: int, episodes: str = "") -> dict:
    """Files of a whole season grouped into releases, torrent packs, and a plan: a file for each wanted
    episode (default: the missing ones and the ones waiting for a dub)."""
    from app.core.offers.season import find_season_offers

    wanted = [int(x) for x in episodes.split(",") if x.strip().isdigit()]
    settings = await store.get_settings(tmdb_id)
    if not wanted:
        detail = await series_detail(tmdb_id)
        wanted = [e["episode"] for s in detail["seasons"] if s["season_number"] == season
                  for e in s["episodes"] if e["state"] in ("missing", "temp")]
        if not wanted:
            return {"season": season, "wanted": [], "sets": [], "packs": [], "plan": [], "movie": None}
    try:
        offers = await find_season_offers(await get_effective_settings(), tmdb_id, season, wanted,
                                          torrent=settings["effective"]["torrent"])
    except Exception as e:
        logger.warning("Season offers of %s S%s failed: %s", tmdb_id, season, e)
        raise HTTPException(502, f"Hledání selhalo: {e}")
    sets = [{**s, "episodes": {str(k): v for k, v in s["episodes"].items()}} for s in offers.sets[:12]]
    return {"season": season, "wanted": offers.episodes, "sets": sets, "packs": offers.packs[:8],
            "plan": offers.plan, "movie": offers.ctx.as_dict()}


class SeasonDownload(BaseModel):
    items: list[dict]          # [{"episode": 3, "row": <offer row>}] — a pack: any episode of it
    replace_owned: bool = True # owned episodes (EN waiting for the dub) are replaced by the new files


@router.post("/{tmdb_id}/season/{season}/download", dependencies=[Depends(require("download"))])
async def season_download(tmdb_id: int, season: int, body: SeasonDownload) -> dict:
    """Download the chosen files of a season (the plan, or a pack) — each like "Download" in the file table."""
    show, _ = await show_with_seasons(tmdb_id)
    owned = await store.owned_episodes(tmdb_id)
    started, errors = 0, []
    for item in body.items:
        row, episode = item.get("row") or {}, int(item.get("episode") or 0)
        if not row.get("ident"):
            continue
        payload = await events.emit("download.request", {
            "file_ident": row["ident"], "source": row["source"], "source_id": row.get("source_id") or 0,
            "magnet_url": row.get("magnet_url"), "content_type": "tv", "tmdb_id": tmdb_id,
            "title": show.get("title") or "", "year": show.get("year") or 0, "file_name": row.get("name") or "",
            "library_action": {"mode": "episode", "season": season, "episode": episode,
                               "replace": bool(body.replace_owned and (season, episode) in owned),
                               "replace_owned": body.replace_owned},
            "requested_by": "series",
        })
        if payload.get("error"):
            errors.append(f"E{episode:02d}: {payload['error']}")
        elif payload.get("started"):
            started += 1
    return {"started": started, "errors": errors}
