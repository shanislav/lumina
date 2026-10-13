"""Workers: a PC that encodes for Lumina (decisions/0009 §4) — the optional little app on the user's computer.

The PC has the server's share mounted and asks for work itself (Lumina never calls it): a long poll — the answer
comes as soon as a job is there, an idle worker sends about one small request a minute. A job it takes is leased:
its progress reports (every ~30 s) renew the lease; a worker gone silent (asleep, switched off) loses it and the
job goes back to the queue. The worker reads the file from the share and writes its result into the job's work
folder next to it — replacing the file, deleting the original and the library are the server's alone.

Pairing: the admin adds a worker and gets a token once (only its hash is kept); the token opens nothing but these
endpoints."""

import asyncio
import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import get_effective_settings, movies_library_dir, tv_library_dir
from app.core.auth import require
from app.db import get_db
from app.modules.convert import jobs, shrink

logger = logging.getLogger(__name__)

WORKERS = """
CREATE TABLE IF NOT EXISTS convert_workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    info TEXT NOT NULL DEFAULT '{}',     -- what the PC said of itself: system, CPU, encoders, version
    state TEXT NOT NULL DEFAULT '{}',    -- what it does now: working / waiting and why (outside its hours …)
    created_at TEXT NOT NULL,
    last_seen TEXT
);
"""

LEASE_S = 120               # a job stays a worker's this long after its last report
ONLINE_S = 150              # a worker heard from within this is online
LONG_POLL_S = 55
router = APIRouter(prefix="/api/convert", tags=["convert"])


def _now() -> datetime:
    return datetime.now()


def _fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── the worker's own endpoints (its token) ──

def worker_guard():
    """FastAPI dependency: the worker of the ``Authorization: Bearer <token>`` header."""
    async def dependency(request: Request) -> dict:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            raise HTTPException(401, "Chybí token pracovníka")
        db = await get_db()
        try:
            row = await (await db.execute("SELECT * FROM convert_workers WHERE token_hash = ?", (_hash(token),))).fetchone()
            if not row:
                raise HTTPException(401, "Neznámý pracovník (token byl smazán?)")
            await db.execute("UPDATE convert_workers SET last_seen = ? WHERE id = ?", (_fmt(_now()), row["id"]))
            await db.commit()
            return dict(row)
        finally:
            await db.close()

    dependency.token_guard = True  # type: ignore[attr-defined]  — read by the route guard test
    return dependency


class Hello(BaseModel):
    version: str = ""
    system: str = ""
    cpu: str = ""
    threads: int = 0
    gpu: str = ""
    encoders: list[str] = []          # ffmpeg's video encoders it has (libx265, hevc_nvenc …)
    x265: str = ""                    # its x265's version (parts of one file: the same major version as the server's)
    state: dict = {}                  # "waiting": why it does not ask for work now (outside its hours …)


@router.post("/worker/hello")
async def hello(body: Hello, worker: dict = Depends(worker_guard())) -> dict:
    """The worker says what it is (at start, then now and then with its state); Lumina says where its library is."""
    db = await get_db()
    try:
        if body.system or body.encoders:              # the app's settings window only checks the connection
            await db.execute("UPDATE convert_workers SET info = ? WHERE id = ?",
                             (json.dumps(body.model_dump(exclude={"state"})), worker["id"]))
        await db.execute("UPDATE convert_workers SET state = ? WHERE id = ?", (json.dumps(body.state), worker["id"]))
        await db.commit()
    finally:
        await db.close()
    cfg = await get_effective_settings()
    return {"name": worker["name"], "roots": [r for r in (movies_library_dir(cfg), tv_library_dir(cfg)) if r],
            "poll_s": LONG_POLL_S, "lease_s": LEASE_S, "phases": jobs.PHASES}


def _fits(job: dict, encoders: set[str]) -> bool:
    """A worker takes jobs meant for workers (or anyone) that its encoders can do."""
    if job["kind"] not in jobs.WORKER_KINDS or jobs.where(job) not in ("worker", "any"):
        return False
    encoder, _ = shrink.SPEEDS.get((job.get("options") or {}).get("speed") or "balanced", ("libx265", ""))
    return encoder in encoders


def parts_ok(info: dict) -> str:
    """Whether a worker may encode parts of the server's files → "" or why not (the same encoder: x265 of the
    same major version — the parts of one film must look alike)."""
    if "libx265" not in (info.get("encoders") or []):
        return "nemá x265"
    theirs = info.get("x265") or ""
    if not theirs:
        return "neznámá verze x265 (starý pracovník)"
    mine = shrink.x265_version()
    if mine.split(".")[0] != theirs.split(".")[0]:
        return f"jiná verze x265 ({theirs}, server {mine})"
    return ""


async def _claim(worker: dict) -> dict | None:
    await jobs.release_expired()
    info = json.loads(worker.get("info") or "{}")
    if not parts_ok(info):
        part = await jobs.claim_part(worker["id"], LEASE_S)
        if part:
            logger.info("Convert: worker %s took part %s of job %s", worker["name"], part["options"]["idx"],
                        part["options"]["parent"])
            folder = posixpath.join(posixpath.dirname(part["path"]), jobs.WORK_DIR, str(part["options"]["parent"]))
            return {**part, "work": folder, "out_name": jobs.part_name(part)}
    encoders = set(info.get("encoders") or [])
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM convert_jobs WHERE status = 'queued' AND kind != 'part' "
                                       "ORDER BY id")).fetchall()
        for row in rows:
            job = jobs._row(row)
            if not _fits(job, encoders):
                continue
            lease = _fmt(_now() + timedelta(seconds=LEASE_S))
            cur = await db.execute("UPDATE convert_jobs SET status = 'running', worker_id = ?, lease_until = ?, "
                                   "progress = '{}' WHERE id = ? AND status = 'queued'", (worker["id"], lease, job["id"]))
            await db.commit()
            if cur.rowcount:
                logger.info("Convert: worker %s took job %s (%s)", worker["name"], job["id"], job["path"])
                # the server's path (POSIX) — the worker translates it to its own
                return {**job, "work": posixpath.join(posixpath.dirname(job["path"]), jobs.WORK_DIR, f"w{job['id']}")}
        return None
    finally:
        await db.close()


@router.get("/worker/next")
async def next_job(wait: int = LONG_POLL_S, worker: dict = Depends(worker_guard())) -> dict:
    """A job for the worker — answered as soon as one is there, else empty after ``wait`` seconds."""
    deadline = _now() + timedelta(seconds=max(0, min(wait, LONG_POLL_S)))
    while True:
        job = await _claim(worker)
        if job or _now() >= deadline:
            return {"job": job}
        await asyncio.sleep(2)


async def _mine(job_id: int, worker: dict) -> dict:
    job = await jobs.get(job_id)
    if not job or job.get("worker_id") != worker["id"] or job["status"] != "running":
        raise HTTPException(409, "Úloha už není tvoje (vypršela, nebo ji někdo zrušil)")
    return job


class Progress(BaseModel):
    phase: str = ""
    value: float | None = None
    paused: str = ""                  # why it waits (another program runs, outside its hours …), "" = working


@router.post("/worker/jobs/{job_id}/progress")
async def progress(job_id: int, body: Progress, worker: dict = Depends(worker_guard())) -> dict:
    await _mine(job_id, worker)
    db = await get_db()
    try:
        await db.execute("UPDATE convert_jobs SET lease_until = ?, progress = ? WHERE id = ?",
                         (_fmt(_now() + timedelta(seconds=LEASE_S)), json.dumps(body.model_dump()), job_id))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


@router.post("/worker/jobs/{job_id}/still/{name}")
async def still(job_id: int, name: str, request: Request, worker: dict = Depends(worker_guard())) -> dict:
    """A frame of a plan's comparison (the original / the new one)."""
    await _mine(job_id, worker)
    if not re.fullmatch(r"\d[ab]\.jpg", name):
        raise HTTPException(400, "Špatný název snímku")
    data = await request.body()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Snímek je moc velký")
    folder = jobs.PREVIEW_DIR / str(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return {"ok": True}


class Done(BaseModel):
    data: dict = {}                   # plan: its estimate; shrink: the check (size, VMAF of the windows)
    out_name: str = ""                # shrink: the file the worker wrote into the job's work folder


@router.post("/worker/jobs/{job_id}/done")
async def done(job_id: int, body: Done, worker: dict = Depends(worker_guard())) -> dict:
    job = await _mine(job_id, worker)
    if job["kind"] == "part":
        return await _part_done(job, body)
    work = jobs.work_dir(job["path"], f"w{job_id}")
    try:
        if job["kind"] == "plan":
            result = await jobs.plan_done(job, body.data)
        else:
            out = os.path.join(work, os.path.basename(body.out_name))
            if not body.out_name or not os.path.isfile(out):
                raise shrink.ShrinkError("Výsledek pracovníka ve složce chybí")
            # the server's own quick check: the length and the tracks (the worker measured the quality)
            got = await asyncio.to_thread(shrink.probe, out)
            src = await asyncio.to_thread(shrink.probe, job["path"])
            if abs(got["duration"] - src["duration"]) > 2 or len(got["audio"]) != len(src["audio"]):
                raise shrink.ShrinkError("Výsledek pracovníka nesedí (délka nebo zvukové stopy)")
            result = await jobs.shrink_done(job, out, {**body.data, "size": os.path.getsize(out)})
        await jobs._finish(job, "done", result.get("note", ""), result.get("path", ""), result.get("data"))
        return {"ok": True}
    except (shrink.ShrinkError, OSError) as e:
        await jobs._finish(job, "error", str(e))
        return {"ok": False, "error": str(e)}
    finally:
        jobs.drop_work(job["path"], f"w{job_id}")


async def _part_done(part: dict, body: Done) -> dict:
    """A part from a worker: in the job's work folder with every frame it should have → it counts (unless the
    same part was done elsewhere first)."""
    path = os.path.join(jobs.part_dir(part), jobs.part_name(part))
    if os.path.basename(body.out_name) != jobs.part_name(part) or not os.path.isfile(path):
        await jobs.part_back(part, "Část od pracovníka ve složce chybí")
        return {"ok": False, "error": "Část ve složce chybí"}
    got = await asyncio.to_thread(shrink.count_frames, path)
    if got != part["options"]["frames"]:
        os.remove(path)
        await jobs.part_back(part, f"Část má {got} snímků místo {part['options']['frames']}")
        return {"ok": False, "error": "Špatný počet snímků"}
    counted = await jobs.part_done(part, path, float(body.data.get("seconds") or 0))
    return {"ok": True, "counted": counted}


class Failed(BaseModel):
    error: str = ""
    give_back: bool = False           # not an error: the worker returns the job (paused too long, closed)


@router.post("/worker/jobs/{job_id}/fail")
async def fail(job_id: int, body: Failed, worker: dict = Depends(worker_guard())) -> dict:
    job = await _mine(job_id, worker)
    if job["kind"] == "part":
        await jobs.part_back(job, "" if body.give_back else f"Pracovník {worker['name']}: {body.error or 'chyba'}")
        return {"ok": True}
    jobs.drop_work(job["path"], f"w{job_id}")
    if body.give_back:
        db = await get_db()
        try:
            await db.execute("UPDATE convert_jobs SET status = 'queued', worker_id = NULL, lease_until = NULL, "
                             "progress = '{}' WHERE id = ?", (job_id,))
            await db.commit()
        finally:
            await db.close()
        jobs._wake.set()
        return {"ok": True}
    await jobs._finish(job, "error", f"Pracovník {worker['name']}: {body.error or 'chyba'}")
    return {"ok": True}


# ── the admin: adding and removing workers ──

async def listing() -> list[dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM convert_workers ORDER BY id")).fetchall()
        busy = {r["worker_id"]: dict(r) for r in await (await db.execute(
            "SELECT id, kind, worker_id, path, progress, options FROM convert_jobs WHERE status = 'running' "
            "AND worker_id IS NOT NULL")).fetchall()}
    finally:
        await db.close()
    out = []
    for r in rows:
        seen = datetime.strptime(r["last_seen"], "%Y-%m-%d %H:%M:%S") if r["last_seen"] else None
        job = busy.get(r["id"])
        info = json.loads(r["info"] or "{}")
        part = None
        if job and job["kind"] == "part":
            o = json.loads(job["options"] or "{}")
            part = {"idx": o.get("idx", 0) + 1, "count": o.get("count", 0)}
        out.append({"id": r["id"], "name": r["name"], "info": info, "parts": parts_ok(info),
                    "state": json.loads(r["state"] or "{}"), "last_seen": r["last_seen"],
                    "online": bool(seen and (_now() - seen).total_seconds() < ONLINE_S),
                    "job": {"id": job["id"], "path": job["path"], "progress": json.loads(job["progress"] or "{}"),
                            "part": part} if job else None})
    return out


@router.get("/workers", dependencies=[Depends(require("convert"))])
async def list_workers() -> dict:
    return {"workers": await listing()}


class NewWorker(BaseModel):
    name: str


@router.post("/workers", dependencies=[Depends(require("convert"))])
async def add_worker(body: NewWorker) -> dict:
    """A new worker → its token, shown this once (only its hash is kept)."""
    name = body.name.strip()[:60] or "Pracovník"
    token = "lw_" + secrets.token_urlsafe(32)
    db = await get_db()
    try:
        cur = await db.execute("INSERT INTO convert_workers (name, token_hash, created_at) VALUES (?, ?, ?)",
                               (name, _hash(token), _fmt(_now())))
        await db.commit()
        return {"id": cur.lastrowid, "name": name, "token": token}
    finally:
        await db.close()


@router.delete("/workers/{worker_id}", dependencies=[Depends(require("convert"))])
async def remove_worker(worker_id: int) -> dict:
    """The token stops working; a job it had goes back to the queue."""
    db = await get_db()
    try:
        await db.execute("UPDATE convert_jobs SET status = 'queued', worker_id = NULL, lease_until = NULL, progress = '{}' "
                         "WHERE status = 'running' AND worker_id = ?", (worker_id,))
        await db.execute(jobs._SPARE_PARTS)
        await db.execute("DELETE FROM convert_workers WHERE id = ?", (worker_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}
