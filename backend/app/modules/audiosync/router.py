"""API of the audio editor: the reference track of a film, the map of all its dubs measured against
it, previews, and building the edited file (runs in the background, one job at a time)."""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.clients.tmdb import TMDBClient
from app.config import get_effective_settings
from app.core import events
from app.core.auth import User, require
from app.db import get_db
from app.modules.audiosync import analyze as engine
from app.modules.audiosync import filmmap
from app.modules.audiosync import preview as clips
from app.modules.audiosync import transfer as muxer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audiosync", tags=["audiosync"])

RESULTS = """
CREATE TABLE IF NOT EXISTS audiosync_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id INTEGER NOT NULL,
    reference_track INTEGER NOT NULL,
    other_id INTEGER NOT NULL,
    other_track INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS audiosync_results_pair ON audiosync_results(reference_id, other_id);
"""

MAPS = """
CREATE TABLE IF NOT EXISTS audiosync_maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    versions TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS audiosync_maps_film ON audiosync_maps(tmdb_id);
"""

# the track of a film trusted to fit the picture — chosen (and checked by watching) by the user
REFS = """
CREATE TABLE IF NOT EXISTS audiosync_refs (
    tmdb_id INTEGER PRIMARY KEY,
    movie_id INTEGER,
    track INTEGER,
    verified INTEGER NOT NULL DEFAULT 0,
    verified_by TEXT,
    original_language TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# one job at a time (it decodes audio / writes a whole film); _job is what the UI shows
_job: dict = {"running": False}
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def busy() -> bool:
    return bool(_job.get("running")) or _lock.locked()


async def _file(movie_id: int) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, tmdb_id, title, year, file_path, filename, file_size FROM library_movies "
                                  "WHERE id = ?",
                                  (movie_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row or not row["file_path"] or not os.path.isfile(row["file_path"]):
        raise HTTPException(404, "Soubor verze nenalezen")
    return dict(row)


async def _versions(tmdb_id: int) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, tmdb_id, title, year, file_path, filename, file_size, quality, language FROM library_movies "
            "WHERE tmdb_id = ? AND status IN ('matched', 'manual') ORDER BY file_size DESC", (tmdb_id,))
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    return [r for r in rows if r["file_path"] and os.path.isfile(r["file_path"])]


async def _save_result(ref_id: int, ref_track: int, other_id: int, other_track: int, data: dict) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO audiosync_results (reference_id, reference_track, other_id, other_track, verdict, result) "
            "VALUES (?, ?, ?, ?, ?, ?)", (ref_id, ref_track, other_id, other_track, data["verdict"], json.dumps(data)))
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def _result_row(result_id: int) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM audiosync_results WHERE id = ?", (result_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(404, "Porovnání nenalezeno")
    return dict(row)


@router.get("/job", dependencies=[Depends(require("audiosync"))])
async def job() -> dict:
    return _job


@router.get("/tracks/{movie_id}", dependencies=[Depends(require("audiosync"))])
async def tracks(movie_id: int) -> dict:
    f = await _file(movie_id)
    try:
        info = await asyncio.to_thread(engine.probe, f["file_path"])
    except Exception as e:  # noqa: BLE001 — broken file, missing ffprobe …
        raise HTTPException(500, f"Soubor nejde přečíst: {e}")
    return {"id": movie_id, "filename": f["filename"], **info}


# ── the reference track ──

def default_track(audio: list[dict], original_language: str) -> int:
    """The film's original language (usually what the picture was shot with), else English, else the first."""
    for lang in (engine.lang_code(original_language), "en"):
        for a in audio:
            if lang and engine.lang_code(a.get("language", "")) == lang:
                return a["index"]
    return audio[0]["index"] if audio else 0


async def _stored_ref(tmdb_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM audiosync_refs WHERE tmdb_id = ?", (tmdb_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    return dict(row) if row else None


async def _write_ref(tmdb_id: int, **fields) -> None:
    db = await get_db()
    try:
        await db.execute("INSERT OR IGNORE INTO audiosync_refs (tmdb_id) VALUES (?)", (tmdb_id,))
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(f"UPDATE audiosync_refs SET {sets}, updated_at = datetime('now') WHERE tmdb_id = ?",
                         (*fields.values(), tmdb_id))
        await db.commit()
    finally:
        await db.close()


async def _original_language(tmdb_id: int) -> str:
    stored = await _stored_ref(tmdb_id)
    if stored and stored.get("original_language") is not None:
        return stored["original_language"]
    cfg = await get_effective_settings()
    lang = ""
    if cfg.get("tmdb_api_key"):
        client = TMDBClient(cfg["tmdb_api_key"])
        try:
            lang = (await client.get_movie_full(tmdb_id)).get("original_language", "") or ""
        except Exception as e:  # noqa: BLE001 — without it the default is English
            logger.info("audiosync: original language of %s unknown: %s", tmdb_id, e)
            return ""
        finally:
            await client.close()
    await _write_ref(tmdb_id, original_language=lang)
    return lang


@router.get("/reference/{tmdb_id}", dependencies=[Depends(require("audiosync"))])
async def get_reference(tmdb_id: int) -> dict:
    """The chosen reference (if its version still exists) and the default track of every version."""
    versions = await _versions(tmdb_id)
    orig = await _original_language(tmdb_id)
    defaults = {}
    for v in versions:
        try:
            audio = (await asyncio.to_thread(engine.probe, v["file_path"]))["audio"]
        except Exception:  # noqa: BLE001 — a broken file has no default
            continue
        defaults[v["id"]] = default_track(audio, orig)
    stored = await _stored_ref(tmdb_id)
    chosen = None
    if stored and stored.get("movie_id") in defaults:
        chosen = {"movie_id": stored["movie_id"], "track": stored["track"], "verified": bool(stored["verified"]),
                  "verified_by": stored["verified_by"], "updated_at": stored["updated_at"]}
    return {"original_language": orig, "chosen": chosen, "defaults": defaults}


class ReferenceBody(BaseModel):
    movie_id: int
    track: int
    verified: bool = False


@router.put("/reference/{tmdb_id}")
async def set_reference(tmdb_id: int, body: ReferenceBody, user: User = Depends(require("audiosync"))) -> dict:
    f = await _file(body.movie_id)
    if f["tmdb_id"] != tmdb_id:
        raise HTTPException(400, "Verze nepatří k tomuto filmu")
    audio = (await asyncio.to_thread(engine.probe, f["file_path"]))["audio"]
    if not 0 <= body.track < len(audio):
        raise HTTPException(400, "Neznámá zvuková stopa")
    await _write_ref(tmdb_id, movie_id=body.movie_id, track=body.track, verified=int(body.verified),
                     verified_by=user.username if body.verified else None)
    return await get_reference(tmdb_id)


# ── previews and manual correction ──

class PreviewBody(BaseModel):
    result_id: int
    at: float                  # reference second where the clip starts
    adjust_ms: int = 0         # trying a correction on top of the saved one: + = the other audio later
    other_track: int | None = None   # another track of the other version (same timing)


@router.post("/preview", dependencies=[Depends(require("audiosync"))])
async def make_preview(body: PreviewBody) -> dict:
    """20 s of the reference picture with a track of another version placed by a measurement."""
    row = await _result_row(body.result_id)
    ref, other = await _file(row["reference_id"]), await _file(row["other_id"])
    analysis = json.loads(row["result"])
    if analysis["verdict"] == "no_match":
        raise HTTPException(400, "Zvuk nesedí — není co ukázat")
    if not -10000 <= body.adjust_ms <= 10000:
        raise HTTPException(400, "Posun nejvýš ±10 s")
    at = max(0.0, min(body.at, (analysis.get("reference") or {}).get("duration", body.at + 30) - 25))
    track = row["other_track"] if body.other_track is None else body.other_track
    name = f"r{row['id']}x{track}-{int(at * 1000)}-{body.adjust_ms}-{analysis.get('adjust_ms') or 0}"
    try:
        await asyncio.to_thread(clips.make_clip, ref["file_path"], other["file_path"], track,
                                clips.with_adjustment(analysis), at, body.adjust_ms, name)
    except Exception as e:  # noqa: BLE001
        logger.exception("audiosync preview failed")
        raise HTTPException(500, f"Ukázku se nepodařilo vyrobit: {e}")
    return {"name": f"{name}.mp4", "at": at, "adjust_ms": body.adjust_ms, "saved_ms": analysis.get("adjust_ms") or 0}


class TrackPreviewBody(BaseModel):
    movie_id: int
    track: int
    at: float


@router.post("/preview-track", dependencies=[Depends(require("audiosync"))])
async def preview_track(body: TrackPreviewBody) -> dict:
    """20 s of the file's own picture with one of its tracks as it is."""
    f = await _file(body.movie_id)
    info = await asyncio.to_thread(engine.probe, f["file_path"])
    if body.track >= len(info["audio"]):
        raise HTTPException(400, "Neznámá zvuková stopa")
    at = max(0.0, min(body.at, info["duration"] - 25))
    name = f"t{f['id']}-{body.track}-raw-{int(at * 1000)}-0"
    try:
        await asyncio.to_thread(clips.make_clip, f["file_path"], f["file_path"], body.track,
                                {"speed": 1.0, "offset": 0.0}, at, 0, name)
    except Exception as e:  # noqa: BLE001
        logger.exception("audiosync track preview failed")
        raise HTTPException(500, f"Ukázku se nepodařilo vyrobit: {e}")
    return {"name": f"{name}.mp4", "at": at, "adjust_ms": 0, "saved_ms": 0}


@router.get("/preview/{name}", dependencies=[Depends(require("audiosync"))])
async def get_preview(name: str) -> FileResponse:
    if not re.fullmatch(r"(r\d+x\d+-\d+--?\d+--?\d+|t\d+-\d+-raw-\d+-0)\.mp4", name):
        raise HTTPException(404)
    path = clips.PREVIEW_DIR / name
    if not path.is_file():
        raise HTTPException(404, "Ukázka už neexistuje")
    return FileResponse(path, media_type="video/mp4")


class AdjustBody(BaseModel):
    adjust_ms: int


@router.patch("/results/{result_id}", dependencies=[Depends(require("audiosync"))])
async def set_adjustment(result_id: int, body: AdjustBody) -> dict:
    """Keeps the user's correction of a measurement; building the file uses it."""
    if not -10000 <= body.adjust_ms <= 10000:
        raise HTTPException(400, "Posun nejvýš ±10 s")
    row = await _result_row(result_id)
    analysis = json.loads(row["result"])
    analysis["adjust_ms"] = body.adjust_ms
    db = await get_db()
    try:
        await db.execute("UPDATE audiosync_results SET result = ? WHERE id = ?", (json.dumps(analysis), result_id))
        await db.commit()
    finally:
        await db.close()
    return {"id": result_id, "adjust_ms": body.adjust_ms}


# ── the map: every track of every version measured against the reference track ──

async def _work_dir(ref: dict) -> Path:
    """The movie downloads folder (the result goes to the library from there) with room for a copy."""
    cfg = await get_effective_settings()
    downloads = Path(cfg.get("plex_media_dir") or "")
    if not downloads.is_dir():
        raise HTTPException(400, "Složka pro stahování filmů neexistuje")
    free = shutil.disk_usage(downloads).free
    if free < (ref["file_size"] or 0) * 1.05 + 2 * 1024**3:
        raise HTTPException(507, f"Málo místa ve složce stahování ({free / 1024**3:.0f} GB volno)")
    return downloads


async def _hand_over(built: str, final: Path, ref: dict, mode: str, download_id: str, progress) -> dict:
    """The library takes the file over like a finished download (naming, NFO, replacing the old file)."""
    os.replace(built, final)
    progress("import", 0, 1)
    action = {"mode": "replace", "file_id": ref["id"]} if mode == "replace" else {"mode": "version"}
    payload = await events.emit("download.completed", {
        "download_id": download_id, "tmdb_id": ref["tmdb_id"], "title": ref["title"], "year": ref["year"],
        "content_type": "movie", "path": str(final), "library_action": action})
    _job.update(imported=bool(payload.get("imported")), path=payload.get("path"))
    return payload


def _fits_reference(data: dict) -> bool:
    return data["verdict"] == "constant" and abs(data["offset"]) <= 0.08


class MapBody(BaseModel):
    tmdb_id: int
    target_id: int
    ref_track: int | None = None     # None = the chosen reference / the default


@router.post("/map", dependencies=[Depends(require("audiosync"))])
async def start_map(body: MapBody) -> dict:
    global _task
    if busy():
        raise HTTPException(409, "Už běží jiná práce se zvukem, počkej chvilku")
    versions = await _versions(body.tmdb_id)
    target = next((v for v in versions if v["id"] == body.target_id), None)
    if not target:
        raise HTTPException(404, "Cílová verze nenalezena")
    audio = (await asyncio.to_thread(engine.probe, target["file_path"]))["audio"]
    if not audio:
        raise HTTPException(400, "Cílová verze nemá zvuk")
    if body.ref_track is None:
        ref = await get_reference(body.tmdb_id)
        chosen = ref["chosen"]
        body.ref_track = chosen["track"] if chosen and chosen["movie_id"] == target["id"] else ref["defaults"][target["id"]]
    if not 0 <= body.ref_track < len(audio):
        raise HTTPException(400, "Neznámá referenční stopa")
    _job.clear()
    _job.update(running=True, kind="map", phase="target", done=0, total=len(audio) - 1 + len(versions) - 1,
                title=target["title"], request=body.model_dump(), error=None)
    _task = asyncio.create_task(_run_map(body, versions))
    return _job


async def _run_map(body: MapBody, versions: list[dict]) -> None:
    async with _lock:
        loop = asyncio.get_running_loop()

        def progress(phase: str, done: int, total: int) -> None:
            loop.call_soon_threadsafe(_job.update, {"phase": phase, "done": done, "total": total})

        try:
            await _map(body, versions, progress)
        except Exception as e:  # noqa: BLE001 — shown to the user
            logger.exception("audiosync film map failed")
            _job.update(error=str(e))
        finally:
            _job.update(running=False, finished_at=time.time())


async def _map(body: MapBody, versions: list[dict], progress) -> None:
    target = next(v for v in versions if v["id"] == body.target_id)
    infos = {v["id"]: await asyncio.to_thread(engine.probe, v["file_path"]) for v in versions}
    ref_track = body.ref_track
    tinfo = infos[target["id"]]
    duration = tinfo["duration"]
    ref_lang = engine.lang_code(tinfo["audio"][ref_track].get("language", ""))
    step = 0

    # 1) the target's own tracks: does each line up with the reference track? (uploaders mux in dubs
    #    from other releases — shifted, drifting, another cut). One that does not can be fixed.
    target_tracks: dict[int, dict] = {}
    own_fits: dict[int, dict] = {}
    for a in tinfo["audio"]:
        if a["index"] == ref_track:
            continue
        _job.update(phase="target", done=step, current=f"stopa {a['index'] + 1}")
        data = (await asyncio.to_thread(engine.analyze, target["file_path"], ref_track, target["file_path"],
                                        a["index"])).to_dict()
        rid = await _save_result(target["id"], ref_track, target["id"], a["index"], data)
        ok = _fits_reference(data)
        fixable = not ok and data["verdict"] in ("constant", "speed", "cuts") and bool(data.get("pieces"))
        target_tracks[a["index"]] = {"ok": ok, "fixable": fixable, "verdict": data["verdict"],
                                     "offset": data["offset"], "speed": data["speed"], "note": data.get("note"),
                                     "pieces": data.get("pieces") or [], "result_id": rid}
        own_fits[a["index"]] = {"delta": 0.0, "ok": ok, "own": data if fixable else None}
        step += 1
    placed = [{"id": target["id"], "path": target["file_path"], "analysis": None, "target": True,
               "audio": tinfo["audio"], "tracks": own_fits}]

    # 2) every other version: aligned through its track of the reference's language, then each of
    #    its tracks measured against the reference track on its own
    alignments = {}
    for v in versions:
        if v["id"] == target["id"]:
            continue
        audio = infos[v["id"]]["audio"]
        if not audio:
            continue
        other_track = next((a["index"] for a in audio if ref_lang and engine.lang_code(a.get("language", "")) == ref_lang), 0)
        _job.update(phase="align", done=step, current=v["filename"])
        data = (await asyncio.to_thread(engine.analyze, target["file_path"], ref_track, v["file_path"],
                                        other_track)).to_dict()
        rid = await _save_result(target["id"], ref_track, v["id"], other_track, data)
        usable = data["verdict"] != "no_match"
        fits: dict[int, dict] = {}
        if usable:
            for a in audio:
                _job.update(phase="tracks", current=f"{v['filename']} · stopa {a['index'] + 1}")
                delta, ok = await asyncio.to_thread(filmmap.track_delta, target["file_path"], ref_track,
                                                    v["file_path"], a["index"], data, duration)
                fits[a["index"]] = {"delta": delta, "ok": ok}
                if ok:
                    continue
                # not the version's timing (drifts, its own cut) — a full measurement of this track alone
                _job.update(phase="track", current=f"{v['filename']} · stopa {a['index'] + 1} zvlášť")
                own = (await asyncio.to_thread(engine.analyze, target["file_path"], ref_track, v["file_path"],
                                               a["index"])).to_dict()
                if own["verdict"] != "no_match" and own.get("pieces"):
                    orid = await _save_result(target["id"], ref_track, v["id"], a["index"], own)
                    fits[a["index"]] = {"delta": 0.0, "ok": True, "own": own, "result_id": orid,
                                        "verdict": own["verdict"]}
        alignments[v["id"]] = {"result_id": rid, "verdict": data["verdict"], "speed": data["speed"],
                               "offset": data["offset"], "pieces": data.get("pieces") or [],
                               "tracks": {i: {k: x for k, x in f.items() if k != "own"} for i, f in fits.items()}}
        placed.append({"id": v["id"], "path": v["file_path"], "audio": audio, "usable": usable, "tracks": fits,
                       # a version whose audio does not fit cannot be compared on the timeline
                       "analysis": data if usable else {"speed": 1.0, "offset": 0.0}})
        step += 1

    dubs = await asyncio.to_thread(filmmap.cluster, placed, duration, progress)
    result = {"target_id": target["id"], "ref_track": ref_track, "duration": duration,
              "target_tracks": target_tracks,
              "versions": [{"id": v["id"], "filename": v["filename"], "quality": v["quality"],
                            "file_size": v["file_size"], "audio": infos[v["id"]]["audio"],
                            "alignment": alignments.get(v["id"])} for v in versions],
              "dubs": dubs}
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO audiosync_maps (tmdb_id, target_id, versions, result) VALUES (?, ?, ?, ?)",
            (body.tmdb_id, target["id"], ",".join(str(v["id"]) for v in versions), json.dumps(result)))
        await db.commit()
        _job.update(map_id=cursor.lastrowid)
    finally:
        await db.close()


@router.get("/map/{tmdb_id}", dependencies=[Depends(require("audiosync"))])
async def get_map(tmdb_id: int) -> dict | None:
    """The latest map of the film; ``stale`` when the versions changed since."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM audiosync_maps WHERE tmdb_id = ? ORDER BY id DESC LIMIT 1", (tmdb_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        return None
    now = ",".join(str(v["id"]) for v in await _versions(tmdb_id))
    return {"id": row["id"], "created_at": row["created_at"], "stale": now != row["versions"], **json.loads(row["result"])}


class ApplyBody(BaseModel):
    map_id: int
    picks: list[dict] = []         # [{version_id, track}] — tracks of other versions to add
    fix_tracks: list[int] = []     # target tracks to replace by their copy moved onto the reference
    drop_tracks: list[int] = []    # target tracks to leave out
    mode: str = "replace"          # replace the target | keep it and add a new version


@router.post("/map/apply")
async def apply_map(body: ApplyBody, user: User = Depends(require("audiosync"))) -> dict:
    global _task
    if body.mode not in ("version", "replace"):
        raise HTTPException(400, "Neznámý režim")
    if not user.can("library.delete" if body.mode == "replace" else "library.edit"):
        raise HTTPException(403, "Na tohle nemáš oprávnění (upravit soubor = mazat v knihovně, nová verze = upravovat knihovnu)")
    if busy():
        raise HTTPException(409, "Už běží jiná práce se zvukem, počkej chvilku")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM audiosync_maps WHERE id = ?", (body.map_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(404, "Mapa nenalezena")
    fmap = json.loads(row["result"])
    target = await _file(fmap["target_id"])
    ref_track = fmap.get("ref_track", 0)
    if ref_track in body.drop_tracks or ref_track in body.fix_tracks:
        raise HTTPException(400, "Referenční stopa musí zůstat, jak je")
    by_version = {v["id"]: v for v in fmap["versions"]}
    sources: dict[tuple, dict] = {}
    for p in body.picks:
        v = by_version.get(p.get("version_id"))
        if not v or v["id"] == target["id"] or not v.get("alignment"):
            raise HTTPException(400, "Neplatný zdroj stopy")
        if v["alignment"]["verdict"] == "no_match":
            raise HTTPException(400, f"Zvuk verze {v['filename']} k cíli nesedí")
        track = int(p["track"])
        fit = (v["alignment"].get("tracks") or {}).get(str(track)) or {"delta": 0.0, "ok": True}
        if not fit["ok"]:
            raise HTTPException(400, f"Stopa {track + 1} verze {v['filename']} k referenci nesedí")
        if fit.get("result_id"):       # measured on its own
            key, src = (v["id"], f"r{fit['result_id']}"), {"result_id": fit["result_id"], "delta": 0.0}
        else:                          # the version's timing, shifted by the track's own delta
            key, src = (v["id"], fit["delta"]), {"result_id": v["alignment"]["result_id"], "delta": fit["delta"]}
        sources.setdefault(key, {"version_id": v["id"], **src, "tracks": []})["tracks"].append(track)
    checks = fmap.get("target_tracks") or {}
    for t in body.fix_tracks:
        c = checks.get(str(t))
        if not c or not c.get("fixable"):
            raise HTTPException(400, f"Stopu {t + 1} nejde opravit")
        sources[("fix", t)] = {"version_id": target["id"], "result_id": c["result_id"], "delta": 0.0, "tracks": [t]}
    if not sources and not body.drop_tracks:
        raise HTTPException(400, "Nic k přidání, opravě ani odebrání")
    downloads = await _work_dir(target)
    _job.clear()
    _job.update(running=True, kind="apply", phase="start", done=0, total=0, title=target["title"],
                request=body.model_dump(), error=None, imported=None)
    _task = asyncio.create_task(_run_apply(body, fmap, target, sources, downloads))
    return _job


async def _run_apply(body: ApplyBody, fmap: dict, target: dict, sources: dict, downloads: Path) -> None:
    async with _lock:
        loop = asyncio.get_running_loop()

        def progress(phase: str, done: int, total: int) -> None:
            loop.call_soon_threadsafe(_job.update, {"phase": phase, "done": done, "total": total})

        workdir = downloads / f".lumina-map-{target['id']}"
        report: dict = {}
        try:
            count = len((await asyncio.to_thread(engine.probe, target["file_path"]))["audio"])
            keep = [i for i in range(count) if i not in body.drop_tracks and i not in body.fix_tracks]
            ref_track = fmap.get("ref_track", 0)
            if sources:
                srcs = []
                for src in sources.values():
                    row = await _result_row(src["result_id"])
                    analysis = engine.shifted(clips.with_adjustment(json.loads(row["result"])), src["delta"])
                    srcs.append({"path": (await _file(src["version_id"]))["file_path"],
                                 "tracks": sorted(set(src["tracks"])), "analysis": analysis})
                out_name = Path(target["filename"]).stem + " [audio].mkv"
                out = await asyncio.to_thread(muxer.transfer_many, target["file_path"], ref_track, srcs, workdir,
                                              out_name, progress, report, keep, False)
            else:
                out_name = Path(target["filename"]).stem + " [tracks].mkv"
                out = await asyncio.to_thread(muxer.strip, target["file_path"], keep, workdir, out_name, progress)
            _job.update(report=report)
            payload = await _hand_over(out, downloads / out_name, target, body.mode, f"map-{target['id']}", progress)
            if body.mode == "replace" and payload.get("imported"):
                await _follow_reference(target, payload.get("path"), keep.index(ref_track))
        except muxer.TransferError as e:
            _job.update(error=str(e))
        except Exception as e:  # noqa: BLE001 — shown to the user
            logger.exception("audiosync map apply failed")
            _job.update(error=str(e))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            _job.update(running=False, finished_at=time.time())


async def _follow_reference(old: dict, new_path: str | None, new_track: int) -> None:
    """The edited file replaced the reference version: the reference (checked by the user) moves with it."""
    stored = await _stored_ref(old["tmdb_id"])
    if not stored or stored.get("movie_id") != old["id"] or not new_path:
        return
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM library_movies WHERE file_path = ?", (new_path,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if row:
        await _write_ref(old["tmdb_id"], movie_id=row["id"], track=new_track)
