"""Úpravy souborů: which files want a change, the queue, starting it (decisions/0009)."""

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import get_effective_settings, movies_library_dir, tv_library_dir
from app.core.auth import require
from app.db import get_db
from app.modules.convert import jobs

router = APIRouter(prefix="/api/convert", tags=["convert"])

DV_IN_MKV = "file_path LIKE '%.mkv' AND media LIKE '%\"hdr\": \"DV\"%'"


async def candidates() -> list[dict]:
    """Library files with Dolby Vision in an MKV (by the library's MediaInfo) — films and episodes."""
    db = await get_db()
    try:
        out = []
        for r in await (await db.execute(
                f"SELECT file_path, title, year, file_size FROM library_movies WHERE {DV_IN_MKV} ORDER BY title")).fetchall():
            out.append({"path": r["file_path"], "kind": "movie", "title": f"{r['title']} ({r['year']})" if r["year"] else r["title"],
                        "size": r["file_size"] or 0})
        for r in await (await db.execute(
                f"SELECT m.file_path, m.size, s.title AS show_title, e.season, e.episode FROM tv_media m "
                f"LEFT JOIN library_episodes e ON e.file_path = m.file_path "
                f"LEFT JOIN library_shows s ON s.tmdb_id = e.show_tmdb_id WHERE m.{DV_IN_MKV} "
                f"ORDER BY s.title, e.season, e.episode")).fetchall():
            title = (f"{r['show_title']} S{r['season']:02d}E{r['episode']:02d}" if r["show_title"]
                     else os.path.basename(r["file_path"]))
            out.append({"path": r["file_path"], "kind": "episode", "title": title, "size": r["size"] or 0})
        return [c for c in out if os.path.isfile(c["path"])]
    finally:
        await db.close()


@router.get("/candidates", dependencies=[Depends(require("convert"))])
async def list_candidates() -> dict:
    queued = {j["path"] for j in await jobs.recent(500) if j["status"] in ("queued", "running")}
    return {"dv": [{**c, "queued": c["path"] in queued} for c in await candidates()]}


@router.get("/jobs", dependencies=[Depends(require("convert"))])
async def list_jobs() -> dict:
    return {"jobs": await jobs.recent(), "current": dict(jobs.current), "phases": jobs.PHASES, "kinds": jobs.KINDS}


class Paths(BaseModel):
    paths: list[str]


@router.post("/dv", dependencies=[Depends(require("convert"))])
async def start_dv(body: Paths) -> dict:
    """Queue the chosen files — only library files that want it (DV in MKV)."""
    cfg = await get_effective_settings()
    roots = [os.path.normpath(r) + os.sep for r in (movies_library_dir(cfg), tv_library_dir(cfg)) if r]
    allowed = {c["path"] for c in await candidates()}
    paths = [p for p in body.paths if p in allowed and any(os.path.normpath(p).startswith(r) for r in roots)]
    if not paths:
        raise HTTPException(400, "Žádný z vybraných souborů nepotřebuje přebalit")
    return {"added": await jobs.enqueue("dv", paths)}
