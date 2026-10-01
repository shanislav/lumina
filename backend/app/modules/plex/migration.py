"""A big rename with Plex keeping its movies (decisions/0008).

Start: remember every movie of the section (Plex id, files, watched, added) and the server settings
that act on their own (watching the disk, scheduled scans, emptying the trash), then switch those off.
After each batch: one scan of the whole section — Plex sees the old file gone and the new one in the
same scan — and a check that every movie is still the same Plex item, now with the new files.
Finish: optionally give a movie Plex added anew its watched state and date back, empty the trash,
switch the settings back to what they were. The state is in the DB, so it survives a restart.
"""

import asyncio
import json
import logging
import os
import shutil
import time

from app.db import get_db
from app.modules.plex.library import PlexError, connect, movies

logger = logging.getLogger(__name__)

TABLES = """
CREATE TABLE IF NOT EXISTS plex_migration (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    section_key TEXT NOT NULL,
    section_title TEXT,
    prefs TEXT NOT NULL,            -- the server settings before the migration {id: value}
    report TEXT,                    -- the last check
    started_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS plex_snapshot (
    rating_key TEXT PRIMARY KEY,
    tmdb_id INTEGER,
    title TEXT,
    year INTEGER,
    files TEXT NOT NULL,
    view_count INTEGER,
    last_viewed_at INTEGER,
    added_at INTEGER
);
"""

# Server settings that act on their own while files are renamed
MANAGED = {
    "FSEventLibraryUpdatesEnabled": "Automaticky prohledávat knihovnu (sledování změn na disku)",
    "FSEventLibraryPartialScanEnabled": "Částečné prohledání při zjištění změn",
    "ScheduledLibraryUpdatesEnabled": "Pravidelně prohledávat knihovnu",
    "autoEmptyTrash": "Automaticky vysypat koš po každém prohledání",
}

POSTERS = "data/plex-posters"       # posters picked by hand, kept until the migration ends
SCAN_START_S = 15        # a scan that never showed up as running was quicker than the polling
SCAN_MAX_S = 3600

_job: dict = {}


def _on(value) -> bool:
    return str(value).lower() in ("true", "1")


async def state() -> dict | None:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM plex_migration WHERE id = 1")).fetchone()
        if not row:
            return None
        count = (await (await db.execute("SELECT COUNT(*) FROM plex_snapshot")).fetchone())[0]
        return {**dict(row), "prefs": json.loads(row["prefs"]),
                "report": json.loads(row["report"]) if row["report"] else None, "movies": count}
    finally:
        await db.close()


async def active() -> bool:
    return await state() is not None


async def overview() -> dict:
    """For the rename dialog: the migration (if one runs) and Plex's settings as they are now."""
    current = await state()
    out = {"configured": True, "active": current is not None, "job": job_view()}
    try:
        client, _cfg, section, _root = await connect()
    except PlexError as e:
        return {**out, "configured": "nastavený" not in str(e), "error": str(e)}
    try:
        prefs = await client.prefs()
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"Plex neodpovídá: {e or type(e).__name__}"}
    finally:
        await client.close()
    original = (current or {}).get("prefs", {})
    out["section"] = section["title"]
    out["settings"] = [{"id": k, "title": t, "on": _on(prefs[k]),
                        **({"was_on": _on(original[k])} if k in original else {})}
                       for k, t in MANAGED.items() if k in prefs]
    if current:
        out.update(started_at=current["started_at"], movies=current["movies"], report=current["report"])
    return out


async def _edits(client, rating_key: str) -> dict | None:
    """What the user changed by hand on a movie (locked fields; the poster saved as a file). A movie
    Plex gives a new id loses those — they are put back on the new item."""
    item = await client.item(rating_key)
    locked = [f["name"] for f in item.get("Field", []) if f.get("locked")]
    if not locked:
        return None
    out: dict = {"locked": locked, "values": {n: item[n] for n in locked if isinstance(item.get(n), (str, int, float))}}
    if "thumb" in locked:
        os.makedirs(POSTERS, exist_ok=True)
        path = os.path.join(POSTERS, f"{rating_key}.jpg")
        with open(path, "wb") as f:
            f.write(await client.poster(rating_key))
        out["poster"] = path
        out["values"].pop("thumb", None)
    return out


async def _all_edits(client, keys: list[str]) -> dict[str, dict]:
    sem = asyncio.Semaphore(8)

    async def one(key):
        async with sem:
            try:
                return key, await _edits(client, key)
            except Exception as e:  # noqa: BLE001 — one movie must not stop the migration
                logger.warning("Plex: edits of %s not read: %s", key, e)
                return key, None
    return {k: e for k, e in await asyncio.gather(*(one(k) for k in keys)) if e}


async def _put_edits_back(client, section_key: str, rating_key: str, edits: dict) -> None:
    if edits.get("poster") and os.path.exists(edits["poster"]):
        with open(edits["poster"], "rb") as f:
            await client.upload_poster(rating_key, f.read())
    await client.edit_fields(section_key, rating_key, edits.get("values", {}), edits.get("locked", []))


async def start() -> dict:
    if await active():
        raise PlexError("Migrace už běží")
    client, _cfg, section, _root = await connect()
    try:
        prefs = await client.prefs()
        found = await movies(client, section["key"])
        edits = await _all_edits(client, [m["rating_key"] for m in found])
        original = {k: prefs[k] for k in MANAGED if k in prefs}
        db = await get_db()
        try:
            await db.execute("DELETE FROM plex_snapshot")
            await db.executemany(
                "INSERT INTO plex_snapshot (rating_key, tmdb_id, imdb_id, title, year, files, view_count, last_viewed_at, "
                "added_at, edits) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(m["rating_key"], m["tmdb_id"], m["imdb_id"], m["title"], m["year"], json.dumps(m["files"]), m["view_count"],
                  m["last_viewed_at"], m["added_at"], json.dumps(edits[m["rating_key"]]) if m["rating_key"] in edits else None)
                 for m in found])
            await db.execute("INSERT INTO plex_migration (id, section_key, section_title, prefs) VALUES (1, ?, ?, ?)",
                             (section["key"], section["title"], json.dumps(original)))
            await db.commit()
        finally:
            await db.close()
        # Settings last: if this fails, the saved values bring them back on cancel
        await client.set_prefs({k: False for k in original})
    finally:
        await client.close()
    logger.info("Plex migration started: %d movies of %s (%d edited by hand), settings off: %s",
                len(found), section["title"], len(edits), list(original))
    return await overview()


def job_view() -> dict:
    return {k: _job.get(k) for k in ("running", "phase", "error", "finished_at")}


def start_check() -> bool:
    if _job.get("running"):
        return False
    _job.clear()
    _job.update(running=True, phase="scan", started=time.time())
    asyncio.create_task(_check_job())
    return True


async def _check_job() -> None:
    try:
        await check()
    except Exception as e:  # noqa: BLE001
        logger.exception("Plex migration check failed")
        _job["error"] = str(e) or type(e).__name__
    finally:
        _job.update(running=False, finished_at=time.time())


async def _wait_for_scan(client, key: str) -> None:
    began, seen = time.monotonic(), False
    while time.monotonic() - began < SCAN_MAX_S:
        await asyncio.sleep(0.5)
        section = next((s for s in await client.sections() if s["key"] == key), None)
        if section and section["refreshing"]:
            seen = True
        elif seen or time.monotonic() - began > SCAN_START_S:
            return
    raise PlexError("Plex prohledává knihovnu déle než hodinu")


def compare(snapshot: dict[str, dict], now: list[dict]) -> dict:
    """What happened to the movies since the snapshot. ``snapshot``: rating key → movie as saved."""
    current = {m["rating_key"]: m for m in now}
    moved, missing, gone = [], [], []
    for key, old in snapshot.items():
        m = current.get(key)
        if m is None:
            gone.append(old)
        elif m["missing"]:
            missing.append(old)
        elif m["files"] != old["files"]:
            moved.append({**old, "files": m["files"], "old_files": old["files"]})
    # the same film: by its TMDB id, or IMDb id (an old item matched by IMDb only)
    lost = {("tmdb", o["tmdb_id"]): o for o in missing + gone if o["tmdb_id"]}
    lost |= {("imdb", o["imdb_id"]): o for o in missing + gone if o.get("imdb_id")}
    # … or by its file name: a folder move keeps the names (a film Plex had wrong comes back as the right one)
    lost |= {("file", os.path.basename(f)): o for o in missing + gone for f in o["files"]}
    readded, new = [], []
    for m in now:
        if m["rating_key"] in snapshot:
            continue
        old = (lost.get(("tmdb", m["tmdb_id"])) or lost.get(("imdb", m.get("imdb_id")))
               or next((lost[("file", os.path.basename(f))] for f in m["files"] if ("file", os.path.basename(f)) in lost), None))
        if old and old not in [r["old"] for r in readded]:
            readded.append({"old": old, "new": m})
        else:
            new.append(m)
    return {"moved": moved, "missing": missing, "gone": gone, "readded": readded, "new": new,
            "unchanged": len(snapshot) - len(moved) - len(missing) - len(gone)}


def _brief(m: dict) -> dict:
    return {"rating_key": m["rating_key"], "title": m["title"], "year": m["year"]}


async def check() -> dict:
    current = await state()
    if not current:
        raise PlexError("Migrace neběží")
    client, _cfg, _section, _root = await connect()
    key = current["section_key"]
    try:
        await client.scan(key)
        await _wait_for_scan(client, key)
        _job["phase"] = "compare"
        now = await movies(client, key)
    finally:
        await client.close()
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM plex_snapshot")).fetchall()
        snapshot = {r["rating_key"]: {**dict(r), "files": json.loads(r["files"])} for r in rows}
        result = compare(snapshot, now)
        # Plex made a new item but carried the date added and the watched state over (by the film's id):
        # nothing to repair — the new item takes the old one's place
        renewed = [r for r in result["readded"] if r["new"]["added_at"] == r["old"]["added_at"]
                   and r["new"]["view_count"] >= r["old"]["view_count"]]
        result["readded"] = [r for r in result["readded"] if r not in renewed]
        for r in renewed:
            n = r["new"]
            await db.execute("UPDATE plex_snapshot SET rating_key = ?, files = ?, tmdb_id = ?, imdb_id = ? WHERE rating_key = ?",
                             (n["rating_key"], json.dumps(n["files"]), n["tmdb_id"], n["imdb_id"], r["old"]["rating_key"]))
        # the user's own poster and fields onto the new item
        restored = []
        for r in renewed:      # (a movie added anew gets them on finish, with the repair)
            if not r["old"].get("edits"):
                continue
            try:
                client = (await connect())[0]
                try:
                    await _put_edits_back(client, current["section_key"], r["new"]["rating_key"], json.loads(r["old"]["edits"]))
                finally:
                    await client.close()
                restored.append(r["old"]["title"])
            except Exception as e:  # noqa: BLE001
                logger.warning("Plex: edits of %s not put back: %s", r["old"]["title"], e)
        # Renamed files are the new normal; a film added meanwhile (a download) is no problem. A missing
        # movie stays in the snapshot, so every later check reports it again until it is dealt with.
        for m in result["moved"]:
            await db.execute("UPDATE plex_snapshot SET files = ? WHERE rating_key = ?", (json.dumps(m["files"]), m["rating_key"]))
        for m in result["new"]:
            await db.execute(
                "INSERT INTO plex_snapshot (rating_key, tmdb_id, imdb_id, title, year, files, view_count, last_viewed_at, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m["rating_key"], m["tmdb_id"], m["imdb_id"], m["title"], m["year"], json.dumps(m["files"]), m["view_count"],
                 m["last_viewed_at"], m["added_at"]))
        report = {
            "checked_at": time.time(),
            "moved": len(result["moved"]),
            "unchanged": result["unchanged"],
            "new": [_brief(m) for m in result["new"]],
            "renewed": [_brief(r["new"]) for r in renewed],
            "edits_restored": restored,
            "missing": [{**_brief(m), "files": m["files"]} for m in result["missing"] + result["gone"]
                        if m["rating_key"] not in {r["old"]["rating_key"] for r in result["readded"] + renewed}],
            "readded": [{"title": r["old"]["title"], "year": r["old"]["year"], "old_key": r["old"]["rating_key"],
                         "new_key": r["new"]["rating_key"], "watched": r["old"]["view_count"] > 0,
                         "added_at": r["old"]["added_at"]} for r in result["readded"]],
        }
        await db.execute("UPDATE plex_migration SET report = ? WHERE id = 1", (json.dumps(report),))
        await db.commit()
    finally:
        await db.close()
    logger.info("Plex migration check: %d moved, %d missing, %d added anew", report["moved"],
                len(report["missing"]), len(report["readded"]))
    return report


async def finish(empty_trash: bool, repair: bool) -> dict:
    """End the migration: settings back as they were; optionally repair movies Plex added anew and
    empty the trash first. Without both it is a plain cancel (nothing in Plex is deleted)."""
    current = await state()
    if not current:
        raise PlexError("Migrace neběží")
    if _job.get("running"):
        raise PlexError("Ještě běží kontrola Plexu")
    client, _cfg, _section, _root = await connect()
    repaired = 0
    try:
        if repair:
            readded = (current["report"] or {}).get("readded", [])
            # Plex often brings the watched state back itself (by the film's id) — mark only what it did not
            viewed = {m["rating_key"]: m["view_count"] for m in await movies(client, current["section_key"])} if readded else {}
            db = await get_db()
            try:
                edits = {row[0]: json.loads(row[1]) for row in await (await db.execute(
                    "SELECT rating_key, edits FROM plex_snapshot WHERE edits IS NOT NULL")).fetchall()}
            finally:
                await db.close()
            for r in readded:
                if r["old_key"] in edits:
                    await _put_edits_back(client, current["section_key"], r["new_key"], edits[r["old_key"]])
                if r["watched"] and not viewed.get(r["new_key"]):
                    await client.mark_watched(r["new_key"])
                if r["added_at"]:
                    await client.set_added_at(current["section_key"], r["new_key"], r["added_at"])
                repaired += 1
        if empty_trash:
            await client.empty_trash(current["section_key"])
        await client.set_prefs(current["prefs"])
    finally:
        await client.close()
    db = await get_db()
    try:
        await db.execute("DELETE FROM plex_migration")
        await db.execute("DELETE FROM plex_snapshot")
        await db.commit()
    finally:
        await db.close()
    shutil.rmtree(POSTERS, ignore_errors=True)
    _job.clear()
    logger.info("Plex migration finished (trash emptied: %s, repaired: %d), settings restored", empty_trash, repaired)
    return {"repaired": repaired, "emptied": empty_trash}
