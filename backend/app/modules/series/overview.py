"""Přehled zdrojů: where a show can be downloaded from, season by season — before deciding to want it.

For each season with aired episodes the same search as the automation (WebShare / FastShare / torrents, its
releases grouped, one sample file of each of the best releases verified at its source — real resolution, codec,
sound), and the torrent packs of the season; besides that the packs of the whole show (their tracker pages read).
Summarised into a table (a few numbers per season, not every file) and kept, so the page shows it again.

It runs in the background, one show after another, gently to WebShare / FastShare (automation's pause between
seasons). "Chci" uses it: where a season has a pack that suits, the pack is downloaded, the rest episode by episode.
"""

import asyncio
import json
import logging
import statistics

from app.db import get_db

logger = logging.getLogger(__name__)

SERIES_OVERVIEW = """
CREATE TABLE IF NOT EXISTS series_overview (
    tmdb_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
SETS_SHOWN = 3
PACKS_SHOWN = 2

_queue: list[tuple[int, bool]] = []            # (tmdb_id, then act as "Chci")
_state: dict = {"running": False, "tmdb_id": 0, "done": 0, "total": 0, "current": ""}


def _mode(values: list[str]) -> str:
    values = [v for v in values if v]
    return statistics.mode(values) if values else ""


def set_summary(st: dict, aired: int, profile) -> dict:
    """One release of a season in a few words: how many episodes, resolution, codec, sound, size, whether its
    sample was verified and how many of its episodes the profile takes."""
    from app.core.profiles import block

    files = [st["episodes"][e] for e in st["covered"]]
    sources = sorted({f["source"] for f in files})
    return {"key": st["key"], "label": st["label"], "coverage": st["coverage"], "aired": aired,
            "resolution": st["resolution"], "codec": _mode([f.get("codec") or "" for f in files]),
            "langs": st["langs"], "local": st["local"], "local_subs": st["local_subs"], "score": st["score"],
            "size": st["size"], "episode_size": int(st["size"] / max(1, st["coverage"])), "sources": sources,
            "verified": any(f.get("verified") or f.get("sampled") for f in files),
            "fits": sum(1 for f in files if block(f, profile) is None)}


def pack_summary(p: dict, episodes: int, profile, lang_mode: str) -> dict:
    """A torrent pack in a few words, and whether "Chci" would take it (judged as one of its episodes)."""
    from app.modules.series.auto import PACK_MIN_SEEDERS
    from app.core.profiles import block

    per_episode = int((p.get("size") or 0) / max(1, episodes))
    ok_sound = lang_mode not in ("local_or_temp", "local_only") or (p.get("lang_tier") or 0) >= 2
    fits = ok_sound and (p.get("seeders") or 0) >= PACK_MIN_SEEDERS and block({**p, "size": per_episode, "pack": False}, profile) is None
    return {"ident": p["ident"], "name": p["name"], "size": p.get("size") or 0, "seeders": p.get("seeders") or 0,
            "resolution": p.get("resolution") or "", "codec": p.get("codec") or "", "langs": p.get("audio_langs") or [],
            "lang_tier": p.get("lang_tier") or 0, "verified": bool(p.get("verified")), "seasons": p.get("seasons") or [],
            "covers": p.get("covers"), "fits": fits}


def only_season(name: str, season: int) -> bool:
    """A pack of this one season ("Columbo 3. série", "Show S03") — not of more seasons or the whole show."""
    from app.core.episode_match import parse_episode

    info = parse_episode(name)
    held = info.seasons or ([info.season] if info.season is not None else [])
    return held == [season] and not (info.complete and len(info.seasons or []) > 1)


async def build(tmdb_id: int) -> dict:
    """The overview of one show (minutes for a long one: a season search each)."""
    from app.config import get_effective_settings
    from app.core.offers.season import find_show_packs
    from app.core.profiles import load_profiles, pick_profile
    from app.modules.series import auto, store
    from app.modules.series.router import _verify_packs, search_season, series_detail

    detail = await series_detail(tmdb_id)
    eff = (await store.get_settings(tmdb_id))["effective"]
    profile = pick_profile(await load_profiles(), eff.get("profile_id"), "tv")
    lang_mode = eff.get("lang_mode") or ""
    seasons = [s for s in detail["seasons"] if s.get("season_number") and not s.get("specials")]
    aired = {s["season_number"]: [e["episode"] for e in s["episodes"] if e["state"] != "upcoming"] for s in seasons}
    aired = {n: eps for n, eps in aired.items() if eps}
    counts = {n: len(eps) for n, eps in aired.items()}
    _state.update(total=len(aired) + 1, done=0, current=detail["show"].get("title") or str(tmdb_id))

    whole: list[dict] = []
    if eff.get("torrent"):
        try:
            found = await find_show_packs(await get_effective_settings(), tmdb_id)
            packs = await _verify_packs(found, counts)
            whole = [pack_summary(p, sum(counts[s] for s in (p.get("seasons") or list(counts)) if s in counts),
                                  profile, lang_mode) for p in packs if (p.get("covers") or 0) > 1][:3]
        except Exception as e:  # noqa: BLE001
            logger.info("Overview %s: show packs failed: %s", tmdb_id, e)
    _state["done"] = 1

    found_sets: dict[int, list[dict]] = {}
    rows: list[dict] = []
    for i, (n, eps) in enumerate(sorted(aired.items())):
        if i:
            await asyncio.sleep(auto.PAUSE_S)
        _state["current"] = f"{detail['show'].get('title') or ''} S{n:02d}"
        season = next(s for s in seasons if s["season_number"] == n)
        owned = sum(1 for e in season["episodes"] if e["state"] in ("owned", "temp", "unknown"))
        try:
            offers = await search_season(tmdb_id, n, eps, bool(eff.get("torrent")))
        except Exception as e:  # noqa: BLE001 — one season must not stop the overview
            logger.info("Overview %s S%02d failed: %s", tmdb_id, n, e)
            rows.append({"season": n, "aired": len(eps), "owned": owned, "error": str(e), "sets": [], "packs": []})
            _state["done"] += 1
            continue
        found_sets[n] = offers.sets
        packs = [{**p, "seasons": [n]} for p in offers.packs if p.get("source") in ("jackett", "prowlarr")
                 and only_season(p["name"], n)]
        rows.append({"season": n, "aired": len(eps), "owned": owned,
                     "sets": [set_summary(st, len(eps), profile) for st in offers.sets[:SETS_SHOWN]],
                     "packs": [pack_summary(p, len(eps), profile, lang_mode) for p in packs[:PACKS_SHOWN]],
                     "_pack_rows": packs[:PACKS_SHOWN]})
        _state["done"] += 1
    preferred = auto.preferred_release(found_sets)
    for r in rows:
        sets = r["sets"]
        # what the automation would take: the preferred release (one uploader) when it is among the season's best
        r["pick"] = next((st["key"] for st in sets if st["key"] == preferred), sets[0]["key"] if sets else None)
    from datetime import datetime
    data = {"tmdb_id": tmdb_id, "title": detail["show"].get("title") or "", "profile": profile.name,
            "lang_mode": lang_mode, "torrent": bool(eff.get("torrent")), "preferred": preferred,
            "whole": whole, "seasons": rows, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    db = await get_db()
    try:
        stored = {**data, "seasons": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]}
        await db.execute("INSERT OR REPLACE INTO series_overview (tmdb_id, data, created_at) VALUES (?, ?, ?)",
                         (tmdb_id, json.dumps(stored), data["created_at"]))
        await db.commit()
    finally:
        await db.close()
    return data


async def stored(tmdb_id: int) -> dict | None:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT data FROM series_overview WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
    except Exception:  # noqa: BLE001 — before the migration
        row = None
    finally:
        await db.close()
    return json.loads(row[0]) if row else None


async def act(data: dict) -> dict:
    """"Chci" after the overview: every season whose own pack suits is downloaded as that pack; the automation
    downloads the rest episode by episode (it waits while something of the show downloads — the packs first)."""
    from app.modules.series import auto, store
    from app.modules.series.router import PackDownload, pack_download

    tmdb_id = data["tmdb_id"]
    taken = []
    for r in data["seasons"]:
        if r.get("owned", 0) >= r.get("aired", 0):
            continue
        rows = r.get("_pack_rows") or []
        for summary, row in zip(r.get("packs") or [], rows):
            if summary["fits"]:
                try:
                    await pack_download(tmdb_id, PackDownload(row=row))
                    taken.append(r["season"])
                except Exception as e:  # noqa: BLE001
                    logger.warning("Overview %s S%02d: the pack failed: %s", tmdb_id, r["season"], e)
                break
    await store.save_settings(tmdb_id, {"auto_new": "download", "auto_from": "all"})
    auto.enqueue([tmdb_id])
    logger.info("Chci %s: season packs %s, the rest episode by episode", tmdb_id, taken)
    return {"packs": taken}


def job_status(tmdb_id: int | None = None) -> dict:
    s = {**_state, "queued": [t for t, _ in _queue]}
    if tmdb_id is not None:
        s["mine"] = _state["running"] and _state["tmdb_id"] == tmdb_id or tmdb_id in s["queued"]
    return s


def enqueue(tmdb_id: int, then_want: bool = False) -> dict:
    if not any(t == tmdb_id for t, _ in _queue) and not (_state["running"] and _state["tmdb_id"] == tmdb_id):
        _queue.append((tmdb_id, then_want))
    if not _state["running"]:
        _state["running"] = True
        asyncio.create_task(_run())
    return job_status(tmdb_id)


async def _run() -> None:
    try:
        while _queue:
            tmdb_id, then_want = _queue.pop(0)
            _state.update(tmdb_id=tmdb_id, done=0, total=0, current="")
            try:
                data = await build(tmdb_id)
                if then_want:
                    await act(data)
            except Exception as e:  # noqa: BLE001
                logger.warning("Overview of %s failed: %s", tmdb_id, e)
    finally:
        _state.update(running=False, tmdb_id=0, current="")
