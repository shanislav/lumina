"""API: audio tracks of a library file, and the comparison of two versions (runs in the background)."""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require
from app.db import get_db
from app.modules.audiosync import analyze as engine

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

# one comparison at a time — it decodes audio for about a minute
_job: dict = {"running": False}
_task: asyncio.Task | None = None


async def _file(movie_id: int) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, tmdb_id, title, file_path, filename FROM library_movies WHERE id = ?",
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
    if _job.get("running"):
        raise HTTPException(409, "Už porovnávám jinou dvojici, počkej chvilku")
    if body.reference_id == body.other_id:
        raise HTTPException(400, "Vyber dvě různé verze")
    ref, other = await _file(body.reference_id), await _file(body.other_id)
    if ref["tmdb_id"] != other["tmdb_id"]:
        raise HTTPException(400, "Verze musí být stejného filmu")
    _job.clear()
    _job.update(running=True, phase="start", done=0, total=0, request=body.model_dump(),
                title=ref["title"], result=None, error=None)
    _task = asyncio.create_task(_run(body, ref["file_path"], other["file_path"]))
    return _job


async def _run(body: AnalyzeBody, ref_path: str, other_path: str) -> None:
    loop = asyncio.get_running_loop()

    def progress(phase: str, done: int, total: int) -> None:
        loop.call_soon_threadsafe(_job.update, {"phase": phase, "done": done, "total": total})

    try:
        result = await asyncio.to_thread(engine.analyze, ref_path, body.reference_track, other_path,
                                         body.other_track, progress)
        data = result.to_dict()
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO audiosync_results (reference_id, reference_track, other_id, other_track, verdict, result) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (body.reference_id, body.reference_track, body.other_id, body.other_track, result.verdict,
                 json.dumps(data)))
            await db.commit()
        finally:
            await db.close()
        _job.update(result=data)
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
