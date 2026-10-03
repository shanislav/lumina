"""Automation of TV shows (docs SERIALY, F3) — what each show wants, set per show on top of the defaults:

    auto_new   new episodes: off | notify (show what was found) | download
    auto_from  which missing episodes are new: "next" — after the last owned one (a show watched from now on),
               "all" — every missing episode that has aired
    auto_dub   episodes owned without Czech/Slovak sound (language "CZ/SK, else EN now"): off | notify |
               download the dubbed file and replace the English one
    auto_upgrade  a better version of an owned episode that does not meet the show's quality profile (below
               its minimums, or below its target score): off | notify | download and replace it — as films'
               "Hlídat lepší verzi": a higher score, never losing the Czech/Slovak sound

The scheduler's nightly run (event ``scheduler.run``, option "series") or "Zkontrolovat teď" checks the shows
with anything on: one season search per season with a wanted episode (the season's releases — the same
search as "Celá série", no AI), then for each episode the best file the show's quality profile allows: only
files sure to be that episode (``film == "yes"``), never a pack; a dub only from a file with Czech/Slovak
sound. What was found is kept in ``series_auto`` (found / downloading / dismissed) — the show page and the
overview list it, "notify" waits there for the user's click.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from app.core import events
from app.core.offers.search import upgrade_block
from app.core.profiles import block, load_profiles, pick_profile, reached_cutoff, row_from_media
from app.core.quality import prefs_from_settings
from app.db import get_db
from app.modules.series import store

logger = logging.getLogger(__name__)

SERIES_AUTO = """
CREATE TABLE IF NOT EXISTS series_auto (
    tmdb_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    episode INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- new | dub | upgrade
    status TEXT NOT NULL,               -- found | downloading | dismissed
    row TEXT NOT NULL DEFAULT '{}',     -- the file (an offer row)
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tmdb_id, season, episode, kind)
);
CREATE TABLE IF NOT EXISTS series_auto_checks (
    tmdb_id INTEGER PRIMARY KEY,
    checked_at TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}'   -- {wanted, found, downloading, note}
);
"""

PAUSE_S = 4                         # between two season searches (WebShare / FastShare / trackers)
DOWNLOADING_DAYS = 3                # an episode started this long ago is not searched again (a stuck download)
ROW_FIELDS = ("name", "source", "source_id", "ident", "size", "magnet_url", "quality_score", "quality_summary",
              "resolution", "codec", "hdr", "lang_tier", "audio_langs", "verified", "seeders")

_queue: list[int] = []
_state = {"running": False, "total": 0, "done": 0, "current": "", "found": 0, "downloading": 0}


def is_on(effective: dict) -> bool:
    return any(effective.get(k, "off") != "off" for k in ("auto_new", "auto_dub", "auto_upgrade"))


# ── what the show wants ──

def wanted_episodes(seasons: list[dict], effective: dict, below: dict | None = None) -> list[tuple[int, int, str]]:
    """(season, episode, kind) the automation looks for, from the show page's seasons (episode states).
    ``below``: owned episodes that do not meet the quality profile {(season, episode): …} (for "upgrade").
    Specials stay out (Top Gear has 120 of them)."""
    regular = [s for s in seasons if s.get("season_number") and not s.get("specials")]
    out: list[tuple[int, int, str]] = []
    if effective.get("auto_new", "off") != "off":
        owned = [(s["season_number"], e["episode"]) for s in regular for e in s["episodes"]
                 if e["state"] in ("owned", "temp", "unknown")]
        last = max(owned) if owned else None
        for s in regular:
            for e in s["episodes"]:
                key = (s["season_number"], e["episode"])
                if e["state"] == "missing" and (effective.get("auto_from") == "all" or (last and key > last)):
                    out.append((*key, "new"))
    if effective.get("auto_dub", "off") != "off" and effective.get("lang_mode") == "local_or_temp":
        out += [(s["season_number"], e["episode"], "dub") for s in regular for e in s["episodes"] if e["state"] == "temp"]
    if effective.get("auto_upgrade", "off") != "off" and below:
        out += [(s["season_number"], e["episode"], "upgrade") for s in regular for e in s["episodes"]
                if e["state"] in ("owned", "unknown") and (s["season_number"], e["episode"]) in below]
    return out


async def below_profile(seasons: list[dict], profile, prefs) -> dict[tuple[int, int], dict]:
    """Owned episodes whose file does not meet the profile (its minimums, or its target score when it has one),
    by the file's MediaInfo from the library scan: {(season, episode): {quality_score, language, file_size}}.
    A file without MediaInfo yet is left alone (the next scan reads it). Its sound's language unknown ("zvuk
    nezjištěn", an old AVI): ``language`` "?" — only a file with Czech/Slovak sound may replace it."""
    files = {(s["season_number"], e["episode"]): e["file"] for s in seasons if s.get("season_number")
             for e in s["episodes"] if e["state"] in ("owned", "unknown") and e.get("file") and e["file"].get("file_path")}
    if not files:
        return {}
    db = await get_db()
    try:
        paths = [f["file_path"] for f in files.values()]
        media = {}
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            for r in await (await db.execute(
                    f"SELECT file_path, media FROM tv_media WHERE file_path IN ({','.join('?' * len(chunk))})", chunk)).fetchall():
                media[r[0]] = json.loads(r[1] or "{}")
    except Exception:  # noqa: BLE001 — the library module off
        return {}
    finally:
        await db.close()
    out = {}
    for key, f in files.items():
        m = media.get(f["file_path"])
        if not m:
            continue
        row = row_from_media(m, f.get("filename") or "", f.get("size") or 0, prefs)
        if block(row, profile) is None and (not profile.cutoff or reached_cutoff(row, profile)):
            continue
        out[key] = {"quality_score": row["quality_score"], "file_size": f.get("size") or 0,
                    "language": ",".join(l.upper() for l in f.get("languages") or []) or "?"}
    return out


def pick(sets: list[dict], episode: int, profile, need_local: bool, skip: set[str] = frozenset(),
         owned: dict | None = None, prefs=None) -> dict | None:
    """The file for an episode: sure to be it, allowed by the profile, not a pack, Czech/Slovak sound when
    needed; ``owned`` (an upgrade): better than it and keeping its Czech/Slovak sound. Czech/Slovak first, then
    the release order (the most complete, best release — one uploader for the season), then the score."""
    candidates = []
    for i, st in enumerate(sets):
        row = st["episodes"].get(episode)
        if not row or row.get("pack") or row.get("film") != "yes" or row.get("ident") in skip:
            continue
        if need_local and (row.get("lang_tier") or 0) < 2:
            continue
        if block(row, profile) is not None:
            continue
        if owned is not None and upgrade_block(row, owned, prefs) is not None:
            continue
        candidates.append(((-(row.get("lang_tier", 0) >= 2), i, -(row.get("quality_score") or 0)), row))
    return min(candidates, key=lambda c: c[0])[1] if candidates else None


# ── records ──

async def records(tmdb_id: int | None = None) -> list[dict]:
    db = await get_db()
    try:
        sql, args = "SELECT * FROM series_auto", ()
        if tmdb_id is not None:
            sql, args = sql + " WHERE tmdb_id = ?", (tmdb_id,)
        rows = await (await db.execute(sql + " ORDER BY tmdb_id, season, episode", args)).fetchall()
    finally:
        await db.close()
    return [{**dict(r), "row": json.loads(r["row"] or "{}")} for r in rows]


async def _save(tmdb_id: int, season: int, episode: int, kind: str, status: str, row: dict) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO series_auto (tmdb_id, season, episode, kind, status, row, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(tmdb_id, season, episode, kind) DO UPDATE SET "
            "status = excluded.status, row = excluded.row, updated_at = excluded.updated_at",
            (tmdb_id, season, episode, kind, status, json.dumps({k: row.get(k) for k in ROW_FIELDS})))
        await db.commit()
    finally:
        await db.close()


async def _forget(tmdb_id: int, keep: set[tuple[int, int, str]]) -> None:
    """Records of episodes not wanted any more (owned now, dubbed now, the setting off) go away."""
    db = await get_db()
    try:
        for r in await (await db.execute("SELECT season, episode, kind FROM series_auto WHERE tmdb_id = ?",
                                         (tmdb_id,))).fetchall():
            if (r[0], r[1], r[2]) not in keep:
                await db.execute("DELETE FROM series_auto WHERE tmdb_id = ? AND season = ? AND episode = ? AND kind = ?",
                                 (tmdb_id, r[0], r[1], r[2]))
        await db.commit()
    finally:
        await db.close()


async def _note_check(tmdb_id: int, result: dict) -> None:
    db = await get_db()
    try:
        await db.execute("INSERT OR REPLACE INTO series_auto_checks (tmdb_id, checked_at, result) VALUES (?, ?, ?)",
                         (tmdb_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), json.dumps(result)))
        await db.commit()
    finally:
        await db.close()


async def checks() -> dict[int, dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM series_auto_checks")).fetchall()
    except Exception:  # noqa: BLE001 — before the migration
        rows = []
    finally:
        await db.close()
    return {r["tmdb_id"]: {"checked_at": r["checked_at"], **json.loads(r["result"] or "{}")} for r in rows}


async def _busy(tmdb_id: int) -> bool:
    """Something of the show is downloading or waiting for a slot (the user's download too): the show's
    episode states are not final until it lands — the next run checks it."""
    db = await get_db()
    try:
        since = (datetime.now() - timedelta(days=DOWNLOADING_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        row = await (await db.execute(
            "SELECT 1 FROM download_tracker WHERE tmdb_id = ? AND processed = 0 AND created_at > ? LIMIT 1",
            (tmdb_id, since))).fetchone()
        if not row:
            row = await (await db.execute(
                "SELECT 1 FROM download_queue WHERE json_extract(request, '$.tmdb_id') = ? LIMIT 1", (tmdb_id,))).fetchone()
        return bool(row)
    except Exception:  # noqa: BLE001 — the downloads module off: nothing is downloading
        return False
    finally:
        await db.close()


# ── downloading ──

async def _download(show: dict, tmdb_id: int, season: int, episode: int, kind: str, row: dict) -> str:
    """"" when started (or queued), else why not."""
    payload = await events.emit("download.request", {
        "file_ident": row["ident"], "source": row["source"], "source_id": row.get("source_id") or 0,
        "magnet_url": row.get("magnet_url"), "content_type": "tv", "tmdb_id": tmdb_id,
        "title": show.get("title") or "", "year": show.get("year") or 0, "file_name": row.get("name") or "",
        # a dub and a better version replace the owned episode
        "library_action": {"mode": "episode", "season": season, "episode": episode,
                           "replace": kind in ("dub", "upgrade"), "replace_owned": kind in ("dub", "upgrade")},
        "requested_by": "automatika seriálů",
    })
    if payload.get("error"):
        return payload["error"]
    return "" if payload.get("started") or payload.get("queued") else "stahování se nespustilo"


async def _show(tmdb_id: int) -> dict:
    """The show's title and year, as the library (or its settings) knows them; else TMDB's."""
    db = await get_db()
    try:
        for table in ("library_shows", "series_settings"):
            try:
                r = await (await db.execute(f"SELECT title, year FROM {table} WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
            except Exception:  # noqa: BLE001 — the library module off
                r = None
            if r and r["title"]:
                return {"title": r["title"], "year": int(r["year"]) if str(r["year"] or "").isdigit() else 0}
    finally:
        await db.close()
    from app.modules.series.router import show_with_seasons
    return (await show_with_seasons(tmdb_id))[0]


async def download_found(tmdb_id: int, keys: list[tuple[int, int, str]]) -> dict:
    """The user's click on what "notify" found."""
    show = await _show(tmdb_id)
    found = {(r["season"], r["episode"], r["kind"]): r for r in await records(tmdb_id) if r["status"] == "found"}
    started, errors = 0, []
    for key in keys:
        rec = found.get(tuple(key))
        if not rec:
            continue
        error = await _download(show, tmdb_id, *tuple(key), rec["row"])
        if error:
            errors.append(f"S{key[0]:02d}E{key[1]:02d}: {error}")
        else:
            started += 1
            await _save(tmdb_id, *tuple(key), "downloading", rec["row"])
    return {"started": started, "errors": errors}


async def dismiss(tmdb_id: int, season: int, episode: int, kind: str) -> None:
    """Not this file: the next check offers another one (if any)."""
    db = await get_db()
    try:
        await db.execute("UPDATE series_auto SET status = 'dismissed', updated_at = datetime('now') "
                         "WHERE tmdb_id = ? AND season = ? AND episode = ? AND kind = ?", (tmdb_id, season, episode, kind))
        await db.commit()
    finally:
        await db.close()


# ── the check ──

async def check_show(tmdb_id: int) -> dict:
    """Look for what the show wants; download it or keep it for the user. {wanted, found, downloading, note}"""
    from app.modules.series.router import search_season, series_detail

    settings = await store.get_settings(tmdb_id)
    eff = settings["effective"]
    if not is_on(eff):
        await _forget(tmdb_id, set())
        return {"wanted": 0, "found": 0, "downloading": 0, "note": "automatika vypnutá"}
    detail = await series_detail(tmdb_id)
    _state["current"] = detail["show"].get("title") or str(tmdb_id)
    profile = pick_profile(await load_profiles(), eff["profile_id"], "tv")
    from app.config import get_effective_settings
    prefs = prefs_from_settings(await get_effective_settings())
    below = await below_profile(detail["seasons"], profile, prefs) if eff.get("auto_upgrade", "off") != "off" else {}
    wanted = wanted_episodes(detail["seasons"], eff, below)
    await _forget(tmdb_id, set(wanted))
    result = {"wanted": len(wanted), "found": 0, "downloading": 0, "note": ""}
    if not wanted:
        if eff.get("auto_new") != "off" and eff.get("auto_from") == "next" and not detail.get("in_library"):
            result["note"] = "žádný díl v knihovně — „nové díly“ počítá od posledního, který máš"
        return result
    if await _busy(tmdb_id):
        result["note"] = "něco z tohoto seriálu se stahuje — příště"
        return result

    known = {(r["season"], r["episode"], r["kind"]): r for r in await records(tmdb_id)}
    recent = (datetime.now() - timedelta(days=DOWNLOADING_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    todo: dict[int, list[tuple[int, str]]] = {}
    for season, episode, kind in wanted:
        rec = known.get((season, episode, kind))
        if rec and rec["status"] == "downloading" and rec["updated_at"] > recent:
            result["downloading"] += 1
            continue
        todo.setdefault(season, []).append((episode, kind))

    news: dict[str, list] = {"found": [], "downloading": []}       # for the notification
    first = True
    for season, items in sorted(todo.items()):
        if not first:
            await asyncio.sleep(PAUSE_S)
        first = False
        try:
            offers = await search_season(tmdb_id, season, sorted({e for e, _ in items}), eff["torrent"])
        except Exception as e:  # noqa: BLE001 — one season must not stop the show
            logger.warning("Series automation: %s S%02d search failed: %s", tmdb_id, season, e)
            result["note"] = f"hledání S{season:02d} selhalo"
            continue
        for episode, kind in items:
            rec = known.get((season, episode, kind))
            skip = {rec["row"].get("ident")} if rec and rec["status"] == "dismissed" else set()
            need_local = kind == "dub" or eff["lang_mode"] == "local_only" or (
                kind == "upgrade" and below.get((season, episode), {}).get("language") == "?" and eff["lang_mode"] != "original")
            row = pick(offers.sets, episode, profile, need_local, skip,
                       owned=below.get((season, episode)) if kind == "upgrade" else None, prefs=prefs)
            if not row:
                continue
            mode = {"new": eff["auto_new"], "dub": eff["auto_dub"], "upgrade": eff["auto_upgrade"]}[kind]
            if mode == "download":
                error = await _download(detail["show"], tmdb_id, season, episode, kind, row)
                if not error:
                    await _save(tmdb_id, season, episode, kind, "downloading", row)
                    result["downloading"] += 1
                    _state["downloading"] += 1
                    news["downloading"].append([season, episode, kind])
                    continue
                logger.warning("Series automation: %s S%02dE%02d download failed: %s", tmdb_id, season, episode, error)
            if not (rec and rec["status"] == "found" and rec["row"].get("ident") == row.get("ident")):
                news["found"].append([season, episode, kind])           # new to the user
            await _save(tmdb_id, season, episode, kind, "found", row)
            result["found"] += 1
            _state["found"] += 1
    if news["found"] or news["downloading"]:
        await events.emit("series.found", {"tmdb_id": tmdb_id, "title": detail["show"].get("title") or "", **news})
    logger.info("Series automation '%s': %s", detail["show"].get("title"), result)
    return result


# ── the job ──

def job_status() -> dict:
    return {**_state, "queued": len(_queue)}


def enqueue(ids: list[int]) -> dict:
    new = [i for i in dict.fromkeys(ids) if i not in _queue]
    _queue.extend(new)
    if not _state["running"]:
        _state.update(running=True, total=len(_queue), done=0, found=0, downloading=0, current="")
        asyncio.create_task(_run())
    else:
        _state["total"] += len(new)
    return job_status()


async def _run() -> None:
    try:
        while _queue:
            tmdb_id = _queue.pop(0)
            try:
                result = await check_show(tmdb_id)
            except Exception as e:  # noqa: BLE001 — one show must not stop the job
                logger.warning("Series automation of %s failed: %s", tmdb_id, e)
                result = {"wanted": 0, "found": 0, "downloading": 0, "note": f"chyba: {e}"}
            await _note_check(tmdb_id, result)
            _state["done"] += 1
            if _queue:
                await asyncio.sleep(PAUSE_S)
    finally:
        _state.update(running=False, current="")


async def automated_shows() -> list[int]:
    """Shows with anything on: in the library (the defaults apply to them) or with their own settings."""
    defaults = await store.get_defaults()
    db = await get_db()
    try:
        own = {r["tmdb_id"]: dict(r) for r in await (await db.execute("SELECT * FROM series_settings")).fetchall()}
        try:
            library = [r[0] for r in await (await db.execute("SELECT tmdb_id FROM library_shows")).fetchall()]
        except Exception:  # noqa: BLE001 — the library module off
            library = []
    finally:
        await db.close()
    out = []
    for tmdb_id in dict.fromkeys([*library, *own]):
        mine = own.get(tmdb_id) or {}
        eff = {k: mine.get(k) if mine.get(k) is not None else defaults[k] for k in store.FIELDS}
        if is_on(eff):
            out.append(tmdb_id)
    return out


async def on_scheduler_run(payload: dict) -> None:
    if not payload.get("series", True):
        return
    ids = await automated_shows()
    if ids:
        logger.info("Series automation: %d shows", len(ids))
        enqueue(ids)


async def tasks() -> list[dict]:
    s = job_status()
    if not s.get("running"):
        return []
    return [{"id": "series-auto", "title": "Automatika seriálů",
             "detail": f"{s.get('current') or ''} · nalezeno {s.get('found', 0)} · stahuje {s.get('downloading', 0)}".strip(" ·"),
             "done": s.get("done"), "total": s.get("total"), "running": True, "link": "/library?tab=serialy"}]
