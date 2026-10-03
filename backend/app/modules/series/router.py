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
from app.db import get_db
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

        numbers = [s["season_number"] for s in show["seasons"]] + [0]       # the specials too (if any)
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
    cfg = await get_effective_settings()
    local = list(prefs_from_settings(cfg).local_langs)
    # TMDB's generic names ("7. epizoda") — the English one when there is one (the catalog, kept for a week)
    from app.core import naming
    from app.modules.library import episode_names
    try:
        _check, cat = await episode_names.release_checker(cfg.get("tmdb_api_key", ""), tmdb_id, None)
    except Exception as e:  # noqa: BLE001
        logger.info("Episode catalog of %s: %s", tmdb_id, e)
        cat = {}
    for n, eps in episodes.items():
        for ep in eps:
            en = (cat.get((n, ep.get("episode_number"))) or {}).get("en") or ""
            if not naming.episode_title(ep.get("name") or "") and naming.episode_title(en):
                ep["name"] = en
    today = date.today()
    mode = settings["effective"]["lang_mode"]
    never = await store.no_dub(tmdb_id)
    seasons = [store.season_view(s, episodes.get(s["season_number"], []), owned, mode, local, today, never)
               for s in show["seasons"]]
    totals = {k: sum(s["counts"][k] for s in seasons) for k in ("owned", "temp", "unknown", "missing", "upcoming")}
    if episodes.get(0):
        # the specials last, out of the totals (Top Gear has 120 of them — "missing" would mean nothing)
        seasons.append(store.season_view({"season_number": 0, "name": "Speciály", "episode_count": len(episodes[0]),
                                          "air_date": "", "poster_url": None, "specials": True},
                                         episodes[0], owned, mode, local, today, never))
    profiles = await load_profiles()
    profile = pick_profile(profiles, settings["effective"]["profile_id"], "tv")
    return {"show": show, "settings": settings, "profile": {"id": profile.id, "name": profile.name},
            "seasons": seasons, "totals": totals, "local_langs": local,
            "in_library": bool(owned)}


class NoDub(BaseModel):
    on: bool = True


@router.put("/{tmdb_id}/episode/{season}/{episode}/no-dub", dependencies=[Depends(require("library.edit"))])
async def episode_no_dub(tmdb_id: int, season: int, episode: int, body: NoDub) -> dict:
    """The user knows the episode never got a dub (South Park S14E05–06): it waits for none."""
    await store.set_no_dub(tmdb_id, season, episode, body.on)
    return {"ok": True}


class SettingsBody(BaseModel):
    values: dict              # only the keys to change: profile_id, lang_mode, torrent, auto_new, auto_from, auto_dub (None = default)


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


async def search_season(tmdb_id: int, season: int, wanted: list[int], torrent: bool):
    """The season's files grouped into releases, each judged by its numbers and its episode's own name
    (and the other numberings of the season: a split season, anime's absolute numbers)."""
    from app.core.offers.season import find_season_offers
    from app.modules.library import episode_names

    cfg = await get_effective_settings()
    by_name, cat = await episode_names.release_checker(cfg.get("tmdb_api_key", ""), tmdb_id, season)
    alt = {tuple(o): e for e in wanted for o in episode_names.other_numbers(cat, season, e)["alt"]} if cat else {}
    return await find_season_offers(cfg, tmdb_id, season, wanted, torrent=torrent, by_name=by_name, alt=alt)


@router.get("/{tmdb_id}/season/{season}/offers", dependencies=[Depends(require("search"))])
async def season_offers(tmdb_id: int, season: int, episodes: str = "") -> dict:
    """Files of a whole season grouped into releases, torrent packs, and a plan: a file for each wanted
    episode (default: the missing ones and the ones waiting for a dub)."""
    wanted = [int(x) for x in episodes.split(",") if x.strip().isdigit()]
    settings = await store.get_settings(tmdb_id)
    if not wanted:
        detail = await series_detail(tmdb_id)
        wanted = [e["episode"] for s in detail["seasons"] if s["season_number"] == season
                  for e in s["episodes"] if e["state"] in ("missing", "temp", "unknown")]
        if not wanted:
            return {"season": season, "wanted": [], "sets": [], "packs": [], "plan": [], "movie": None}
    try:
        offers = await search_season(tmdb_id, season, wanted, settings["effective"]["torrent"])
    except Exception as e:
        logger.warning("Season offers of %s S%s failed: %s", tmdb_id, season, e)
        raise HTTPException(502, f"Hledání selhalo: {e}")
    sets = [{**s, "episodes": {str(k): v for k, v in s["episodes"].items()}} for s in offers.sets[:12]]
    return {"season": season, "wanted": offers.episodes, "sets": sets, "packs": offers.packs[:8],
            "plan": offers.plan, "movie": offers.ctx.as_dict()}


@router.get("/{tmdb_id}/packs", dependencies=[Depends(require("search"))])
async def show_packs(tmdb_id: int) -> dict:
    """Torrents of the whole show ("komplet", "1-26. série", "S01-S10") — beside the season search."""
    from app.core.offers.season import find_show_packs

    try:
        return await find_show_packs(await get_effective_settings(), tmdb_id)
    except Exception as e:
        logger.warning("Show packs of %s failed: %s", tmdb_id, e)
        raise HTTPException(502, f"Hledání selhalo: {e}")


class PackDownload(BaseModel):
    row: dict                    # the pack (a row of /packs)
    replace_owned: bool = False  # episodes the user has go for the pack's (else the pack's copies stay out)


@router.post("/{tmdb_id}/pack/download", dependencies=[Depends(require("download"))])
async def pack_download(tmdb_id: int, body: PackDownload) -> dict:
    """Download a pack of the whole show: every episode goes to its season; what the user has stays, unless
    ``replace_owned``; files that are no episode (a film, extras) stay in the downloads."""
    show, _ = await show_with_seasons(tmdb_id)
    row = body.row
    if not row.get("ident"):
        raise HTTPException(400, "Chybí soubor")
    held = row.get("seasons") or []
    numbers = [s["season_number"] for s in show.get("seasons", [])]
    pack_season = held[0] if len(held) == 1 else numbers[0] if len(numbers) == 1 else None
    payload = await events.emit("download.request", {
        "file_ident": row["ident"], "source": row["source"], "source_id": row.get("source_id") or 0,
        "magnet_url": row.get("magnet_url"), "content_type": "tv", "tmdb_id": tmdb_id,
        "title": show.get("title") or "", "year": show.get("year") or 0, "file_name": row.get("name") or "",
        # the pack's one season: files named only "01 - Name" inside it ("Chalupáři S01")
        "library_action": {"mode": "pack", "replace_owned": body.replace_owned, "pack_season": pack_season},
        "requested_by": "series",
    })
    if payload.get("error"):
        raise HTTPException(502, payload["error"])
    return {"started": bool(payload.get("started"))}


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


# ── automation (auto.py) ──

@router.get("/automation/overview", dependencies=[Depends(require("search"))])
async def automation_overview() -> dict:
    """Every show of the library (and every show with its own settings): its automation, the last check,
    what was found, how many episodes are owned and how many only without Czech/Slovak sound."""
    from app.db import get_all_settings, get_automation
    from app.modules.series import auto

    defaults = await store.get_defaults()
    cfg = await get_effective_settings()
    local = set(prefs_from_settings(cfg).local_langs)
    db = await get_db()
    try:
        own = {r["tmdb_id"]: dict(r) for r in await (await db.execute("SELECT * FROM series_settings")).fetchall()}
        try:
            shows = {r["tmdb_id"]: dict(r) for r in await (await db.execute(
                "SELECT tmdb_id, title, year, poster_url FROM library_shows")).fetchall()}
            counts: dict[int, dict] = {}
            for r in await (await db.execute(
                    "SELECT show_tmdb_id, language FROM library_episodes WHERE has_file = 1 AND season > 0")).fetchall():
                c = counts.setdefault(r[0], {"owned": 0, "foreign": 0})
                c["owned"] += 1
                langs = store.languages_of(r[1])
                if langs and not local & set(langs):
                    c["foreign"] += 1
        except Exception:  # noqa: BLE001 — the library module off
            shows, counts = {}, {}
    finally:
        await db.close()
    checked = await auto.checks()
    found: dict[int, list[dict]] = {}
    for r in await auto.records():
        if r["status"] in ("found", "downloading"):
            found.setdefault(r["tmdb_id"], []).append(r)
    out = []
    for tmdb_id in dict.fromkeys([*shows, *own]):
        base = shows.get(tmdb_id) or own.get(tmdb_id) or {}
        mine = {k: (own.get(tmdb_id) or {}).get(k) for k in store.FIELDS}
        if mine["torrent"] is not None:
            mine["torrent"] = bool(mine["torrent"])
        out.append({"tmdb_id": tmdb_id, "title": base.get("title") or "", "year": base.get("year") or "",
                    "poster_url": base.get("poster_url"), "in_library": tmdb_id in shows,
                    "own": mine, "effective": {k: mine[k] if mine[k] is not None else defaults[k] for k in store.FIELDS},
                    **(counts.get(tmdb_id) or {"owned": 0, "foreign": 0}),
                    "checked": checked.get(tmdb_id), "found": found.get(tmdb_id, [])})
    out.sort(key=lambda s: s["title"].lower())
    scheduler = await get_automation("scheduler")
    sched_cfg = (scheduler or {}).get("config") or {}
    return {"shows": out, "defaults": defaults, "job": auto.job_status(),
            "scheduler": {"enabled": bool(scheduler and scheduler["enabled"]),
                          "series": str(sched_cfg.get("series", "true")).lower() == "true",
                          "time": sched_cfg.get("time") or "03:00",
                          "last_run": (await get_all_settings()).get("scheduler_last_run", "")}}


class BulkSettings(BaseModel):
    tmdb_ids: list[int]
    values: dict              # as SettingsBody: only the keys to change (None = default)


@router.put("/automation/bulk", dependencies=[Depends(require("library.edit"))])
async def automation_bulk(body: BulkSettings) -> dict:
    """The same settings for many shows at once (the overview's checked rows)."""
    db = await get_db()
    try:
        shows = {r[0]: dict(r) for r in await (await db.execute(
            "SELECT tmdb_id, title, year, poster_url FROM library_shows")).fetchall()}
    except Exception:  # noqa: BLE001
        shows = {}
    finally:
        await db.close()
    for tmdb_id in body.tmdb_ids:
        await store.save_settings(tmdb_id, body.values, shows.get(tmdb_id))
    return {"saved": len(body.tmdb_ids)}


class AutoRun(BaseModel):
    tmdb_ids: list[int] = []  # empty: every show with anything on


@router.post("/automation/run", dependencies=[Depends(require("library.edit"))])
async def automation_run(body: AutoRun) -> dict:
    """Check now (as the nightly run, for these shows) — in the background, the task list shows it."""
    from app.modules.series import auto
    ids = body.tmdb_ids or await auto.automated_shows()
    return auto.enqueue(ids) if ids else auto.job_status()


@router.get("/{tmdb_id}/automation", dependencies=[Depends(require("search"))])
async def show_automation(tmdb_id: int) -> dict:
    from app.modules.series import auto
    return {"records": [r for r in await auto.records(tmdb_id) if r["status"] != "dismissed"],
            "checked": (await auto.checks()).get(tmdb_id), "job": auto.job_status()}


class FoundItems(BaseModel):
    keys: list[list]          # [[season, episode, kind]]


@router.post("/{tmdb_id}/automation/download", dependencies=[Depends(require("download"))])
async def automation_download(tmdb_id: int, body: FoundItems) -> dict:
    from app.modules.series import auto
    return await auto.download_found(tmdb_id, [(int(k[0]), int(k[1]), str(k[2])) for k in body.keys])


@router.post("/{tmdb_id}/automation/dismiss", dependencies=[Depends(require("library.edit"))])
async def automation_dismiss(tmdb_id: int, body: FoundItems) -> dict:
    from app.modules.series import auto
    for k in body.keys:
        await auto.dismiss(tmdb_id, int(k[0]), int(k[1]), str(k[2]))
    return {"ok": True}
