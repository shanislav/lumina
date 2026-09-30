"""API: audio tracks of a library file, and the comparison of two versions (runs in the background)."""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import get_effective_settings
from app.core import events
from app.core.auth import User, require
from app.db import get_db
from app.modules.audiosync import analyze as engine
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

# one job at a time (it decodes audio / writes a whole film); _job is what the UI shows —
# a comparison, a transfer, or keeping the audio of a replaced version after a download
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


@router.get("/tracks/{movie_id}", dependencies=[Depends(require("audiosync"))])
async def tracks(movie_id: int) -> dict:
    f = await _file(movie_id)
    try:
        info = await asyncio.to_thread(engine.probe, f["file_path"])
    except Exception as e:  # noqa: BLE001 — broken file, missing ffprobe …
        raise HTTPException(500, f"Soubor nejde přečíst: {e}")
    return {"id": movie_id, "filename": f["filename"], **info}


class AnalyzeBody(BaseModel):
    reference_id: int          # the version whose video stays
    other_id: int              # the version the audio track comes from
    reference_track: int = 0
    other_track: int = 0


@router.post("/analyze", dependencies=[Depends(require("audiosync"))])
async def start_analysis(body: AnalyzeBody) -> dict:
    global _task
    if busy():
        raise HTTPException(409, "Už běží jiná práce se zvukem, počkej chvilku")
    if body.reference_id == body.other_id:
        raise HTTPException(400, "Vyber dvě různé verze")
    ref, other = await _file(body.reference_id), await _file(body.other_id)
    if ref["tmdb_id"] != other["tmdb_id"]:
        raise HTTPException(400, "Verze musí být stejného filmu")
    _job.clear()
    _job.update(running=True, kind="analyze", phase="start", done=0, total=0, request=body.model_dump(),
                title=ref["title"], result=None, error=None)
    _task = asyncio.create_task(_run(body, ref["file_path"], other["file_path"]))
    return _job


async def _run(body: AnalyzeBody, ref_path: str, other_path: str) -> None:
    async with _lock:
        await _analysis(body, ref_path, other_path)


async def _analysis(body: AnalyzeBody, ref_path: str, other_path: str) -> None:
    loop = asyncio.get_running_loop()

    def progress(phase: str, done: int, total: int) -> None:
        loop.call_soon_threadsafe(_job.update, {"phase": phase, "done": done, "total": total})

    try:
        result = await asyncio.to_thread(engine.analyze, ref_path, body.reference_track, other_path,
                                         body.other_track, progress)
        data = result.to_dict()
        db = await get_db()
        try:
            cursor = await db.execute(
                "INSERT INTO audiosync_results (reference_id, reference_track, other_id, other_track, verdict, result) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (body.reference_id, body.reference_track, body.other_id, body.other_track, result.verdict,
                 json.dumps(data)))
            await db.commit()
            result_id = cursor.lastrowid
        finally:
            await db.close()
        _job.update(result=data, result_id=result_id)
        logger.info("audiosync %s: %s speed %.5f offset %+.3f", _job.get("title"), result.verdict, result.speed,
                    result.offset)
    except Exception as e:  # noqa: BLE001 — shown to the user
        logger.exception("audiosync analysis failed")
        _job.update(error=str(e))
    finally:
        _job.update(running=False)


@router.get("/job", dependencies=[Depends(require("audiosync"))])
async def job() -> dict:
    return _job


@router.get("/results", dependencies=[Depends(require("audiosync"))])
async def results(reference_id: int, other_id: int) -> list[dict]:
    """Earlier comparisons of the pair, newest first."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM audiosync_results WHERE reference_id = ? AND other_id = ? ORDER BY id DESC LIMIT 5",
            (reference_id, other_id))
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [{**{k: r[k] for k in r.keys() if k != "result"}, "result": json.loads(r["result"])} for r in rows]


class TransferBody(BaseModel):
    result_id: int            # an analysis of the pair (its mapping is used)
    mode: str = "version"     # version = keep both files | replace = the new file replaces the reference
    other_tracks: list[int] | None = None   # tracks of the other version to add (None = the analysed one)
    drop_tracks: list[int] = []             # audio tracks of the reference to leave out


@router.post("/transfer")
async def start_transfer(body: TransferBody, user: User = Depends(require("audiosync"))) -> dict:
    global _task
    if body.mode not in ("version", "replace"):
        raise HTTPException(400, "Neznámý režim")
    # the replaced file is deleted once the new one is in the library
    if not user.can("library.delete" if body.mode == "replace" else "library.edit"):
        raise HTTPException(403, "Na tohle nemáš oprávnění (nahradit = mazat v knihovně, nová verze = upravovat knihovnu)")
    if busy():
        raise HTTPException(409, "Už běží jiná práce se zvukem, počkej chvilku")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM audiosync_results WHERE id = ?", (body.result_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(404, "Porovnání nenalezeno")
    analysis = clips.with_adjustment(json.loads(row["result"]))
    if analysis["verdict"] not in ("constant", "speed", "cuts") or not analysis.get("pieces"):
        raise HTTPException(400, "Zvuk k tomuto obrazu nesedí — není co přenést")
    ref, other = await _file(row["reference_id"]), await _file(row["other_id"])

    if body.drop_tracks and not user.can("library.delete"):
        raise HTTPException(403, "Odebrat stopy může jen uživatel s oprávněním mazat v knihovně")
    downloads = await _work_dir(ref)

    _job.clear()
    _job.update(running=True, kind="transfer", phase="start", done=0, total=0, title=ref["title"],
                request=body.model_dump(), result=None, error=None, imported=None)
    _task = asyncio.create_task(_run_transfer(body, dict(row), analysis, ref, other, downloads))
    return _job


async def _run_transfer(body: TransferBody, row: dict, analysis: dict, ref: dict, other: dict, downloads: Path) -> None:
    async with _lock:
        await _transfer(body, row, analysis, ref, other, downloads)


async def _transfer(body: TransferBody, row: dict, analysis: dict, ref: dict, other: dict, downloads: Path) -> None:
    loop = asyncio.get_running_loop()

    def progress(phase: str, done: int, total: int) -> None:
        loop.call_soon_threadsafe(_job.update, {"phase": phase, "done": done, "total": total})

    workdir = downloads / f".lumina-audiosync-{row['id']}"
    out_name = Path(ref["filename"]).stem + " [audio].mkv"
    report: dict = {}
    try:
        ref_keep = None
        if body.drop_tracks:
            count = len((await asyncio.to_thread(engine.probe, ref["file_path"]))["audio"])
            ref_keep = [i for i in range(count) if i not in body.drop_tracks]
        out = await asyncio.to_thread(muxer.transfer, ref["file_path"], row["reference_track"], other["file_path"],
                                      body.other_tracks or [row["other_track"]], analysis, workdir, out_name,
                                      progress, report, ref_keep)
        _job.update(report=report)
        await _hand_over(out, downloads / out_name, ref, body.mode, f"audiosync-{row['id']}", progress)
    except muxer.TransferError as e:
        _job.update(error=str(e))
    except Exception as e:  # noqa: BLE001 — shown to the user
        logger.exception("audiosync transfer failed")
        _job.update(error=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        _job.update(running=False)


# ── previews and manual correction (step 4) ──

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


class PreviewBody(BaseModel):
    result_id: int
    at: float                  # reference second where the clip starts
    adjust_ms: int = 0         # trying a correction: + = the other audio later
    other_track: int | None = None   # listen to another track of the other version (same timing)


@router.post("/preview", dependencies=[Depends(require("audiosync"))])
async def make_preview(body: PreviewBody) -> dict:
    row = await _result_row(body.result_id)
    if not row["reference_id"]:
        raise HTTPException(400, "Ukázka jde jen u porovnání dvou verzí v knihovně")
    ref, other = await _file(row["reference_id"]), await _file(row["other_id"])
    analysis = json.loads(row["result"])
    if analysis["verdict"] == "no_match":
        raise HTTPException(400, "Zvuk nesedí — není co ukázat")
    if not -10000 <= body.adjust_ms <= 10000:
        raise HTTPException(400, "Posun nejvýš ±10 s")
    at = max(0.0, min(body.at, (analysis.get("reference") or {}).get("duration", body.at + 30) - 25))
    track = row["other_track"] if body.other_track is None else body.other_track
    name = f"r{row['id']}x{track}-{int(at * 1000)}-{body.adjust_ms}"
    try:
        await asyncio.to_thread(clips.make_clip, ref["file_path"], other["file_path"], track, analysis,
                                at, body.adjust_ms, name)
    except Exception as e:  # noqa: BLE001
        logger.exception("audiosync preview failed")
        raise HTTPException(500, f"Ukázku se nepodařilo vyrobit: {e}")
    return {"name": f"{name}.mp4", "at": at, "adjust_ms": body.adjust_ms}


@router.get("/preview/{name}", dependencies=[Depends(require("audiosync"))])
async def get_preview(name: str) -> FileResponse:
    if not re.fullmatch(r"(r\d+x\d+|t\d+-\d+-(raw|fix\d+))-\d+--?\d+\.mp4", name):
        raise HTTPException(404)
    path = clips.PREVIEW_DIR / name
    if not path.is_file():
        raise HTTPException(404, "Ukázka už neexistuje")
    return FileResponse(path, media_type="video/mp4")


class AdjustBody(BaseModel):
    adjust_ms: int


@router.patch("/results/{result_id}", dependencies=[Depends(require("audiosync"))])
async def set_adjustment(result_id: int, body: AdjustBody) -> dict:
    """Keeps the user's correction; a transfer uses it."""
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



# ── the tracks of one file: check, remove, listen (single file) ──

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


async def _hand_over(built: str, final: Path, ref: dict, mode: str, download_id: str, progress) -> None:
    """The library takes the file over like a finished download (naming, NFO, replacing the old file)."""
    os.replace(built, final)
    progress("import", 0, 1)
    action = {"mode": "replace", "file_id": ref["id"]} if mode == "replace" else {"mode": "version"}
    payload = await events.emit("download.completed", {
        "download_id": download_id, "tmdb_id": ref["tmdb_id"], "title": ref["title"], "year": ref["year"],
        "content_type": "movie", "path": str(final), "library_action": action})
    _job.update(imported=bool(payload.get("imported")), path=payload.get("path"))


class CheckBody(BaseModel):
    movie_id: int
    reference_track: int = 0       # the track trusted to fit the picture


@router.post("/check", dependencies=[Depends(require("audiosync"))])
async def start_check(body: CheckBody) -> dict:
    """Does every audio track of one file line up with the reference track? (uploaders sometimes
    add a dub from another release that is shifted, at PAL speed or from another cut)"""
    global _task
    if busy():
        raise HTTPException(409, "Už běží jiná práce se zvukem, počkej chvilku")
    f = await _file(body.movie_id)
    info = await asyncio.to_thread(engine.probe, f["file_path"])
    others = [a["index"] for a in info["audio"] if a["index"] != body.reference_track]
    if body.reference_track >= len(info["audio"]) or not others:
        raise HTTPException(400, "Soubor má jen jednu zvukovou stopu — není s čím porovnat")
    _job.clear()
    _job.update(running=True, kind="check", phase="check", done=0, total=len(others), title=f["title"],
                request=body.model_dump(), checks=[], error=None)
    _task = asyncio.create_task(_run_check(body, f, others))
    return _job


async def _run_check(body: CheckBody, f: dict, others: list[int]) -> None:
    async with _lock:
        try:
            for k, t in enumerate(others):
                _job.update(phase="check", done=k, current=t)
                result = await asyncio.to_thread(engine.analyze, f["file_path"], body.reference_track,
                                                 f["file_path"], t)
                data = result.to_dict()
                db = await get_db()
                try:
                    cursor = await db.execute(
                        "INSERT INTO audiosync_results (reference_id, reference_track, other_id, other_track, verdict, "
                        "result) VALUES (?, ?, ?, ?, ?, ?)",
                        (f["id"], body.reference_track, f["id"], t, result.verdict, json.dumps(data)))
                    await db.commit()
                    rid = cursor.lastrowid
                finally:
                    await db.close()
                _job["checks"].append(_check_summary(rid, t, data))
            _job.update(done=len(others))
        except Exception as e:  # noqa: BLE001 — shown to the user
            logger.exception("audiosync track check failed")
            _job.update(error=str(e))
        finally:
            _job.update(running=False)


def _check_summary(result_id: int, track: int, data: dict) -> dict:
    fits = data["verdict"] == "constant" and abs(data["offset"]) <= 0.08
    return {"result_id": result_id, "track": track, "verdict": data["verdict"], "fits": fits,
            "offset": data["offset"], "speed": data["speed"], "confidence": data["confidence"],
            "pieces": data.get("pieces") or [], "reference_track": None}


@router.get("/checks/{movie_id}", dependencies=[Depends(require("audiosync"))])
async def checks(movie_id: int) -> list[dict]:
    """The latest check of every track of the file (reference track included in each row)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM audiosync_results WHERE reference_id = ? AND other_id = ? ORDER BY id DESC", (movie_id, movie_id))
        rows = await cursor.fetchall()
    finally:
        await db.close()
    seen, out = set(), []
    for r in rows:
        if r["other_track"] in seen:
            continue
        seen.add(r["other_track"])
        summary = _check_summary(r["id"], r["other_track"], json.loads(r["result"]))
        summary.update(reference_track=r["reference_track"], checked_at=r["created_at"])
        out.append(summary)
    return sorted(out, key=lambda x: x["track"])


class StripBody(BaseModel):
    movie_id: int
    drop_tracks: list[int]
    mode: str = "replace"


@router.post("/strip")
async def start_strip(body: StripBody, user: User = Depends(require("audiosync"))) -> dict:
    """A copy of the file without the chosen audio tracks (nothing re-encoded); replaces the file
    or is kept as a new version."""
    global _task
    if body.mode not in ("version", "replace"):
        raise HTTPException(400, "Neznámý režim")
    if not user.can("library.delete" if body.mode == "replace" else "library.edit"):
        raise HTTPException(403, "Na tohle nemáš oprávnění")
    if not body.drop_tracks:
        raise HTTPException(400, "Vyber stopy k odebrání")
    if busy():
        raise HTTPException(409, "Už běží jiná práce se zvukem, počkej chvilku")
    f = await _file(body.movie_id)
    downloads = await _work_dir(f)
    _job.clear()
    _job.update(running=True, kind="strip", phase="mux", done=0, total=100, title=f["title"],
                request=body.model_dump(), error=None, imported=None)
    _task = asyncio.create_task(_run_strip(body, f, downloads))
    return _job


async def _run_strip(body: StripBody, f: dict, downloads: Path) -> None:
    async with _lock:
        loop = asyncio.get_running_loop()

        def progress(phase: str, done: int, total: int) -> None:
            loop.call_soon_threadsafe(_job.update, {"phase": phase, "done": done, "total": total})

        workdir = downloads / f".lumina-strip-{f['id']}"
        out_name = Path(f["filename"]).stem + " [tracks].mkv"
        try:
            count = len((await asyncio.to_thread(engine.probe, f["file_path"]))["audio"])
            keep = [i for i in range(count) if i not in body.drop_tracks]
            out = await asyncio.to_thread(muxer.strip, f["file_path"], keep, workdir, out_name, progress)
            await _hand_over(out, downloads / out_name, f, body.mode, f"strip-{f['id']}", progress)
        except muxer.TransferError as e:
            _job.update(error=str(e))
        except Exception as e:  # noqa: BLE001 — shown to the user
            logger.exception("audiosync strip failed")
            _job.update(error=str(e))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            _job.update(running=False)


class TrackPreviewBody(BaseModel):
    movie_id: int
    track: int
    at: float
    result_id: int | None = None   # a check of this track: listen to it corrected (+ adjust_ms)
    adjust_ms: int = 0


@router.post("/preview-track", dependencies=[Depends(require("audiosync"))])
async def preview_track(body: TrackPreviewBody) -> dict:
    """20 s of the file's own picture with one of its tracks — as it is, or corrected by a check."""
    f = await _file(body.movie_id)
    info = await asyncio.to_thread(engine.probe, f["file_path"])
    if body.track >= len(info["audio"]):
        raise HTTPException(400, "Neznámá zvuková stopa")
    analysis = {"speed": 1.0, "offset": 0.0}
    tag = "raw"
    if body.result_id:
        row = await _result_row(body.result_id)
        if row["reference_id"] != body.movie_id or row["other_id"] != body.movie_id or row["other_track"] != body.track:
            raise HTTPException(400, "Kontrola nepatří k této stopě")
        analysis = json.loads(row["result"])
        tag = f"fix{row['id']}"
    at = max(0.0, min(body.at, info["duration"] - 25))
    name = f"t{f['id']}-{body.track}-{tag}-{int(at * 1000)}-{body.adjust_ms}"
    try:
        await asyncio.to_thread(clips.make_clip, f["file_path"], f["file_path"], body.track, analysis, at,
                                body.adjust_ms, name)
    except Exception as e:  # noqa: BLE001
        logger.exception("audiosync track preview failed")
        raise HTTPException(500, f"Ukázku se nepodařilo vyrobit: {e}")
    return {"name": f"{name}.mp4", "at": at}
