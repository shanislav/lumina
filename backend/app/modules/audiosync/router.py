"""API: audio tracks of a library file, and the comparison of two versions (runs in the background)."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import get_effective_settings
from app.core import events
from app.core.auth import User, require
from app.db import get_db
from app.modules.audiosync import analyze as engine
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
    analysis = json.loads(row["result"])
    if analysis["verdict"] not in ("constant", "speed", "cuts") or not analysis.get("pieces"):
        raise HTTPException(400, "Zvuk k tomuto obrazu nesedí — není co přenést")
    ref, other = await _file(row["reference_id"]), await _file(row["other_id"])

    cfg = await get_effective_settings()
    downloads = Path(cfg.get("plex_media_dir") or "")
    if not downloads.is_dir():
        raise HTTPException(400, "Složka pro stahování filmů neexistuje")
    free = shutil.disk_usage(downloads).free
    if free < (ref["file_size"] or 0) * 1.05 + 2 * 1024**3:
        raise HTTPException(507, f"Málo místa ve složce stahování ({free / 1024**3:.0f} GB volno)")

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
    try:
        out = await asyncio.to_thread(muxer.transfer, ref["file_path"], row["reference_track"], other["file_path"],
                                      row["other_track"], analysis, workdir, out_name, progress)
        # the library takes the file over like a finished download (naming, NFO, replacing the old file)
        final = downloads / out_name
        os.replace(out, final)
        progress("import", 0, 1)
        action = {"mode": "replace", "file_id": ref["id"]} if body.mode == "replace" else {"mode": "version"}
        payload = await events.emit("download.completed", {
            "download_id": f"audiosync-{row['id']}", "tmdb_id": ref["tmdb_id"], "title": ref["title"],
            "year": ref["year"], "content_type": "movie", "path": str(final), "library_action": action})
        _job.update(imported=bool(payload.get("imported")), path=payload.get("path"))
    except muxer.TransferError as e:
        _job.update(error=str(e))
    except Exception as e:  # noqa: BLE001 — shown to the user
        logger.exception("audiosync transfer failed")
        _job.update(error=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        _job.update(running=False)
