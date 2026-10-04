"""Wanted films: storage, the check (offers → profile → best) and the background job."""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.core import events
from app.core.cinema import before_digital
from app.core.cinema import last_remembered as cinema_last_seen
from app.core.offers.search import find_offers, verify_offers
from app.core.profiles import get_profile
from app.core.profiles import suitable as pick_suitable
from app.db import get_db

logger = logging.getLogger(__name__)

WANTED = """
CREATE TABLE IF NOT EXISTS wanted (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER,
    wikidata_id TEXT DEFAULT '',
    title TEXT NOT NULL,
    original_title TEXT DEFAULT '',
    year TEXT DEFAULT '',
    poster_url TEXT,
    profile_id INTEGER,              -- NULL = the default profile
    status TEXT DEFAULT 'wanted',    -- wanted | found | downloading | done
    matches INTEGER DEFAULT 0,       -- offers the profile allows
    best TEXT DEFAULT '{}',          -- the best of them
    note TEXT DEFAULT '',
    added_at TEXT,
    checked_at TEXT,
    done_at TEXT
);
"""

PAUSE_BETWEEN_FILMS_S = 5
RECORD_EVERY_DAYS = 7          # a film waiting with no digital date known: its recordings remembered this often
VERIFY_PER_FILM = 10
BEST_FIELDS = ("name", "source", "source_id", "ident", "size", "magnet_url", "quality_score", "quality_summary",
               "resolution", "codec", "hdr", "lang_tier", "audio_langs", "verified", "video_bitrate")

_queue: list[int] = []
_auto_download: set[int] = set()      # films the scheduler may download automatically when found
_state = {"running": False, "total": 0, "done": 0, "current": "", "found": 0}


def job_status() -> dict:
    return {**_state, "queued": len(_queue)}


def enqueue(ids: list[int], auto_download: bool = False) -> dict:
    if auto_download:
        _auto_download.update(ids)
    new = [i for i in dict.fromkeys(ids) if i not in _queue]
    _queue.extend(new)
    if not _state["running"]:
        _state.update(running=True, total=len(_queue), done=0, found=0, current="")
        asyncio.create_task(_run())
    else:
        _state["total"] += len(new)
    return job_status()


async def _run() -> None:
    try:
        while _queue:
            wanted_id = _queue.pop(0)
            try:
                if (await check(wanted_id) or {}).get("status") == "found":
                    _state["found"] += 1
                    if wanted_id in _auto_download:
                        await request_download(wanted_id)
                _auto_download.discard(wanted_id)
            except Exception as e:   # one film must not stop the job
                logger.warning("Wanted check %s failed: %s", wanted_id, e)
            _state["done"] += 1
            if _queue:
                await asyncio.sleep(PAUSE_BETWEEN_FILMS_S)
    finally:
        _state.update(running=False, current="")


async def on_download_cancelled(payload: dict) -> None:
    """Downloads cancelled: those films are found but not downloading; "Zastavit vše" stops the check job."""
    if payload.get("stop_all"):
        _queue.clear()
        _auto_download.clear()
    ids = [t for t in payload.get("tmdb_ids") or [] if t]
    if not ids:
        return
    db = await get_db()
    try:
        await db.execute(f"UPDATE wanted SET status = 'found' WHERE status = 'downloading' "
                         f"AND tmdb_id IN ({','.join('?' * len(ids))})", ids)
        await db.commit()
    finally:
        await db.close()


async def get(wanted_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM wanted WHERE id = ?", (wanted_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def check(wanted_id: int) -> dict | None:
    """Search the sources for the film, keep what the profile allows, remember the best."""
    item = await get(wanted_id)
    if not item or item["status"] == "done":
        return None
    _state["current"] = item["title"]
    cfg = await get_effective_settings()
    waiting = await _waiting(cfg, item["tmdb_id"])
    if waiting:
        # only cinema recordings exist yet: nothing to search for (WebShare / FastShare spared)
        db = await get_db()
        try:
            await db.execute("UPDATE wanted SET status = 'wanted', matches = 0, best = '{}', waiting = ?, checked_at = ? "
                             "WHERE id = ?", (waiting, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), wanted_id))
            await db.commit()
        finally:
            await db.close()
        logger.info("Wanted '%s': %s", item["title"], waiting)
        # the day before the digital release: what is out there then is a cinema recording (fakes named "WEB-DL"
        # too) — remembered for after the release day. No digital date known: once a week.
        if await _remember_now(cfg, item["tmdb_id"]):
            try:
                await find_offers(cfg, f"{item['title']} {item['year']}".strip(), original_title=item["original_title"] or "",
                                  tmdb_id=item["tmdb_id"], media_type="movie", use_ai=False)
            except Exception as e:  # noqa: BLE001
                logger.info("Wanted '%s': remembering the pre-release files failed: %s", item["title"], e)
        return {"status": "wanted", "matches": 0, "waiting": waiting}
    profile = await get_profile(item["profile_id"])
    query = f"{item['title']} {item['year']}".strip()
    offers = await find_offers(cfg, query, original_title=item["original_title"] or "",
                               tmdb_id=item["tmdb_id"] or None, media_type="movie",
                               wikidata_id=item["wikidata_id"] or None)
    await verify_offers(offers, limit=VERIFY_PER_FILM)
    suitable = pick_suitable(offers.rows, profile)
    best = {k: suitable[0].get(k) for k in BEST_FIELDS} if suitable else {}
    status = "found" if suitable else "wanted"
    db = await get_db()
    try:
        await db.execute("UPDATE wanted SET status = ?, matches = ?, best = ?, waiting = '', checked_at = ? WHERE id = ?",
                         (status, len(suitable), json.dumps(best), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          wanted_id))
        await db.commit()
    finally:
        await db.close()
    logger.info("Wanted '%s' (%s): %d suitable of %d offers", item["title"], profile.name, len(suitable), len(offers.rows))
    if suitable:
        await events.emit("offers.found", {"kind": "wanted", "wanted_id": wanted_id, "tmdb_id": item["tmdb_id"],
                                           "title": item["title"], "year": item["year"], "profile": profile.name,
                                           "matches": len(suitable), "best": best})
    return {"status": status, "matches": len(suitable)}


async def _remember_now(cfg: dict, tmdb_id: int) -> bool:
    """Search a waiting film to remember its pre-release files: on the day before its digital release (once), or
    weekly when TMDB knows no date."""
    client = TMDBClient(cfg.get("tmdb_api_key", ""))
    try:
        digital = ((await client.get_movie_full(tmdb_id)).get("releases") or {}).get("digital") or ""
    except Exception:  # noqa: BLE001
        return False
    finally:
        await client.close()
    last = await cinema_last_seen(tmdb_id)
    now = datetime.now()
    if digital:
        eve = (datetime.fromisoformat(digital) - timedelta(days=1)).strftime("%Y-%m-%d")
        return now.strftime("%Y-%m-%d") >= eve and (not last or last[:10] < eve)
    return not last or last < (now - timedelta(days=RECORD_EVERY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


async def _waiting(cfg: dict, tmdb_id: int | None) -> str:
    """"čeká na digitální vydání …" when the film has not come out digitally yet (core/cinema), else ""."""
    if not tmdb_id:
        return ""
    client = TMDBClient(cfg.get("tmdb_api_key", ""))
    try:
        full = await client.get_movie_full(tmdb_id)
    except Exception as e:  # noqa: BLE001 — TMDB down: search as before
        logger.info("Wanted: TMDB of %s failed: %s", tmdb_id, e)
        return ""
    finally:
        await client.close()
    releases = full.get("releases") or {}
    if not before_digital(releases):
        return ""
    when = releases.get("digital")
    cz = lambda d: f"{int(d[8:10])}. {int(d[5:7])}. {d[:4]}"
    return (f"čeká na digitální vydání ({cz(when)})" if when
            else f"čeká na digitální vydání (v kinech od {cz(releases['theatrical'])})")


async def request_download(wanted_id: int) -> None:
    """Scheduler with automatic downloads on: ask the downloads module for the best offer."""
    item = await get(wanted_id)
    best = json.loads((item or {}).get("best") or "{}")
    if not item or not best.get("ident"):
        return
    payload = await events.emit("download.request", {
        "file_ident": best["ident"], "source": best.get("source"), "source_id": best.get("source_id") or 0,
        "magnet_url": best.get("magnet_url"), "tmdb_id": item["tmdb_id"], "title": item["title"],
        "year": int(item["year"] or 0), "content_type": "movie", "requested_by": "wanted (plánovač)",
        "file_name": best.get("name") or "",
    })
    if payload.get("started"):
        db = await get_db()
        try:
            await db.execute("UPDATE wanted SET status = 'downloading' WHERE id = ?", (wanted_id,))
            await db.commit()
        finally:
            await db.close()


async def on_scheduler_run(payload: dict) -> None:
    """Nightly run: check every film that is not found-and-downloading or done yet."""
    if not payload.get("wanted"):
        return
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM wanted WHERE status IN ('wanted', 'found') "
                                  "ORDER BY checked_at IS NOT NULL, checked_at")
        ids = [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()
    if ids:
        enqueue(ids, auto_download=bool(payload.get("auto_download_wanted")))


async def on_movie_updated(payload: dict) -> None:
    """The film is in the library now → done."""
    tmdb_id = payload.get("tmdb_id")
    if not tmdb_id or payload.get("status") not in ("matched", "manual"):
        return
    db = await get_db()
    try:
        await db.execute("UPDATE wanted SET status = 'done', done_at = ? WHERE tmdb_id = ? AND status != 'done'",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tmdb_id))
        await db.commit()
    finally:
        await db.close()
