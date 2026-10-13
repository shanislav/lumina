"""Úpravy souborů: which files want a change, the queue, starting it (decisions/0009)."""

import json
import os
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import get_effective_settings, movies_library_dir, tv_library_dir
from app.core.auth import require
from app.db import get_db
from app.modules.convert import jobs, shrink

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
    return {"jobs": await jobs.recent(), "current": dict(jobs.current), "phases": jobs.PHASES, "kinds": jobs.KINDS,
            "server_encoders": list(shrink.encoders()[0])}


class Paths(BaseModel):
    paths: list[str]


# ── smaller files ──

# a bitrate that is plenty for the resolution (Mb/s, as HEVC); an older codec needs more for the same picture
ENOUGH_MBPS = ((2000, 14.0), (1000, 4.5), (700, 2.5), (0, 1.2))
CODEC_NEED = {"HEVC": 1.0, "AV1": 0.8, "AVC": 1.6, "MPEG-4 Visual": 2.2, "VC-1": 1.8, "MPEG Video": 2.5}


def _weight(media: dict) -> float:
    """How far above plenty the file's bitrate is (1.0 = just plenty; more = room to shrink)."""
    h = media.get("height") or 0
    enough = next(m for floor, m in ENOUGH_MBPS if h >= floor) * CODEC_NEED.get(media.get("video_codec") or "", 1.6)
    return round((media.get("bitrate") or 0) / 1e6 / enough, 2)


async def _library_files(roots: list[str]) -> list[dict]:
    db = await get_db()
    try:
        out = []
        for r in await (await db.execute("SELECT file_path, title, year, file_size, media, tmdb_id FROM library_movies "
                                         "WHERE status IN ('matched', 'manual')")).fetchall():
            media = json.loads(r["media"] or "{}")
            out.append({"path": r["file_path"], "kind": "movie", "group": "Filmy", "tmdb": r["tmdb_id"], "season": None,
                        "title": f"{r['title']} ({r['year']})" if r["year"] else r["title"], "size": r["file_size"] or 0,
                        "media": media})
        for r in await (await db.execute(
                "SELECT m.file_path, m.size, m.media, s.title AS show, e.show_tmdb_id, e.season, e.episode FROM tv_media m "
                "JOIN library_episodes e ON e.file_path = m.file_path "
                "LEFT JOIN library_shows s ON s.tmdb_id = e.show_tmdb_id")).fetchall():
            out.append({"path": r["file_path"], "kind": "episode", "group": f"{r['show']} — {r['season']}. série",
                        "tmdb": r["show_tmdb_id"], "season": r["season"],
                        "title": f"S{r['season']:02d}E{r['episode']:02d}", "size": r["size"] or 0,
                        "media": json.loads(r["media"] or "{}")})
    finally:
        await db.close()
    files = []
    for f in out:
        m = f.pop("media")
        if not any(os.path.normpath(f["path"]).startswith(r) for r in roots) or m.get("hdr") == "DV":
            continue                    # Dolby Vision would be lost in re-encoding
        files.append({**f, "codec": m.get("video_codec") or "", "height": m.get("height") or 0,
                      "mbps": round((m.get("bitrate") or 0) / 1e6, 1), "hdr": m.get("hdr") or "",
                      "weight": _weight(m)})
    return files


async def _roots() -> list[str]:
    cfg = await get_effective_settings()
    return [os.path.normpath(r) + os.sep for r in (movies_library_dir(cfg), tv_library_dir(cfg)) if r]


def _fold(text: str) -> str:
    """"Sběratelé kostí" → "sberatele kosti": the search needs no accents."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


@router.get("/shrink/candidates", dependencies=[Depends(require("convert"))])
async def shrink_candidates(q: str = "", by_weight: bool = False, movie: int | None = None, show: int | None = None,
                            season: int | None = None) -> dict:
    """Library files to shrink: without a search the ones well above a plenty bitrate; a search (title / show /
    file name, accents not needed) looks through the whole library; ``movie`` / ``show`` (+ ``season``) = one title's
    files, opened from its page."""
    files = await _library_files(await _roots())
    words = _fold(q).split()
    if movie or show:
        files = [f for f in files if f["tmdb"] == (movie or show) and f["kind"] == ("movie" if movie else "episode")
                 and (season is None or f["season"] == season)]
    if words:
        files = [f for f in files
                 if all(w in _fold(f"{f['group']} {f['title']} {os.path.basename(f['path'])}") for w in words)]
    elif not (movie or show):
        files = [f for f in files if f["weight"] >= 1.3]
    files.sort(key=(lambda f: -f["weight"]) if by_weight else (lambda f: (f["group"] != "Filmy", f["group"], f["title"])))
    return {"files": files[:600], "total": len(files)}


class PlanBody(BaseModel):
    paths: list[str]
    options: dict = {}


@router.post("/shrink/plan", dependencies=[Depends(require("convert"))])
async def shrink_plan(body: PlanBody) -> dict:
    """A preview (samples, VMAF, size, frames) for each chosen file; with options.go a file worth it is encoded."""
    roots = await _roots()
    paths = [p for p in body.paths if os.path.isfile(p) and any(os.path.normpath(p).startswith(r) for r in roots)]
    if not paths:
        raise HTTPException(400, "Žádný soubor z knihovny")
    opts = shrink.options(body.options)
    if opts["where"] == "server" and not shrink.can_encode(opts):
        raise HTTPException(400, "Server nemá grafickou kartu NVIDIA (NVENC) — zvol workera")
    return {"added": await jobs.enqueue("plan", paths, options=opts)}


class ShrinkBody(BaseModel):
    crf: float | None = None          # another than the plan found (the user's choice)
    force: bool = False               # the saving is under the user's limit — replace anyway


@router.post("/shrink/{plan_id}", dependencies=[Depends(require("convert"))])
async def shrink_from_plan(plan_id: int, body: ShrinkBody) -> dict:
    plan = await jobs.get(plan_id)
    if not plan or plan["kind"] != "plan" or plan["status"] != "done" or not plan["result"].get("crf"):
        raise HTTPException(400, "Náhled není hotový")
    crf = body.crf if body.crf and shrink.CRF_RANGE[0] <= body.crf <= shrink.CRF_RANGE[1] else plan["result"]["crf"]
    opts = {**plan["options"], "crf": crf, "plan_id": plan_id, "expect_vmaf": plan["result"].get("vmaf")}
    if body.force:
        opts["min_saving"] = 1          # still smaller than the original, or it stays
    added = await jobs.enqueue("shrink", [plan["path"]], options=opts)
    return {"added": added}


@router.delete("/jobs/{job_id}", dependencies=[Depends(require("convert"))])
async def drop_job(job_id: int) -> dict:
    await jobs.drop(job_id)
    return {"ok": True}


@router.get("/preview/{job_id}/{name}", dependencies=[Depends(require("convert"))])
async def preview_frame(job_id: int, name: str) -> FileResponse:
    if not re.fullmatch(r"\d[ab]\.jpg", name):
        raise HTTPException(404)
    path = jobs.PREVIEW_DIR / str(job_id) / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


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
