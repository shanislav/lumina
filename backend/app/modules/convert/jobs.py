"""The queue of file changes: one at a time in the background, kept in the DB (a restart picks it up again).

A job works in a folder next to its file (the same disk: the result is moved, not copied). Only a result that
passed its check takes the file's place; the library then knows the new file (path, MediaInfo) and Plex rescans."""

import asyncio
import json
import logging
import os
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app.core import events
from app.core.migrations import add_column
from app.db import get_db
from app.modules.convert import dv, shrink

logger = logging.getLogger(__name__)

JOBS = """
CREATE TABLE IF NOT EXISTS convert_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                  -- dv: Dolby Vision MKV → MP4
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | error
    note TEXT NOT NULL DEFAULT '',
    result_path TEXT NOT NULL DEFAULT '',
    auto INTEGER NOT NULL DEFAULT 0,     -- started on its own after a download
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_convert_jobs_status ON convert_jobs(status);
"""

# later columns: the job's choices (shrink: quality, speed …) and what came out (a plan's estimate)
OPTIONS = add_column("convert_jobs", "options", "TEXT NOT NULL DEFAULT '{}'")
RESULT = add_column("convert_jobs", "result", "TEXT NOT NULL DEFAULT '{}'")
# a job a worker (a PC, workers.py) took: whose, until when (renewed by its progress reports) and how far it is
WORKER = add_column("convert_jobs", "worker_id", "INTEGER")
LEASE = add_column("convert_jobs", "lease_until", "TEXT")
PROGRESS = add_column("convert_jobs", "progress", "TEXT NOT NULL DEFAULT '{}'")
WORKER_KINDS = ("plan", "shrink", "part")   # what a worker may do (Dolby Vision remux stays on the server)
# where = "together": the server leads the job and encodes its parts with the workers (shrink.split) — each part
# is a job row of kind "part" (options: parent, idx, start, frames), taken like any job; a part may run twice at the
# end (whoever finishes first wins, the other is stopped), the leader joins them

WORK_DIR = ".lumina-convert"
PREVIEW_DIR = Path("data/convert-previews")
POLL_S = 60
KINDS = {"dv": "Přebalení Dolby Vision do MP4", "plan": "Náhled zmenšení", "shrink": "Zmenšení souboru",
         "part": "Část souboru"}
PHASES = {"remux": "přebaluji", "verify": "kontroluji výsledek", "library": "předávám knihovně",
          "samples": "vybírám vzorky", "sample": "zkouším kvalitu na vzorcích", "stills": "připravuji snímky",
          "encode": "kóduji", "upload": "nahrávám výsledek na server", "split": "dělím na části",
          "parts": "kódujeme po částech", "join": "spojuji části"}
PART_FAILS = 3                           # a part failing this often fails its job
WORKER_FIRST_S = 30                      # "whoever is free first": a free worker's head start before the server
WORKER_ONLINE_S = 150

_wake = asyncio.Event()
_task: asyncio.Task | None = None
current: dict = {}                       # the running job, for the task list


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def enqueue(kind: str, paths: list[str], auto: bool = False, options: dict | None = None) -> int:
    """Queue ``paths`` (not the ones queued or running already). → how many were added."""
    db = await get_db()
    added = 0
    try:
        for path in paths:
            if await (await db.execute("SELECT 1 FROM convert_jobs WHERE path = ? AND kind = ? AND status IN "
                                       "('queued', 'running')", (path, kind))).fetchone():
                continue
            await db.execute("INSERT INTO convert_jobs (kind, path, auto, created_at, options) VALUES (?, ?, ?, ?, ?)",
                             (kind, path, int(auto), _now(), json.dumps(options or {})))  # local time like finished_at
            added += 1
        await db.commit()
    finally:
        await db.close()
    if added:
        _wake.set()
    return added


async def recent(limit: int = 50) -> list[dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM convert_jobs WHERE kind != 'part' ORDER BY CASE status WHEN 'running' THEN 0 "
                                       "WHEN 'queued' THEN 1 ELSE 2 END, id DESC LIMIT ?", (limit,))).fetchall()
        return [_row(r) for r in rows]
    finally:
        await db.close()


def _row(r) -> dict:
    d = dict(r)
    for k in ("options", "result", "progress"):
        try:
            d[k] = json.loads(d.get(k) or "{}")
        except ValueError:
            d[k] = {}
    return d


async def get(job_id: int) -> dict | None:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM convert_jobs WHERE id = ?", (job_id,))).fetchone()
        return _row(row) if row else None
    finally:
        await db.close()


async def drop(job_id: int) -> None:
    """A finished job out of the list (a plan the user does not want) with its frames."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM convert_jobs WHERE id = ? AND status IN ('done', 'error')", (job_id,))
        await db.commit()
    finally:
        await db.close()
    shutil.rmtree(PREVIEW_DIR / str(job_id), ignore_errors=True)


async def start() -> None:
    """A job left running by a restart goes again (its work folder is thrown away first)."""
    global _task
    db = await get_db()
    try:
        for row in await (await db.execute("SELECT * FROM convert_jobs WHERE status = 'running' "
                                           "AND worker_id IS NULL")).fetchall():
            if not _together(_row(row)) and row["kind"] != "part":      # a together job's parts done are kept
                drop_work(row["path"], row["id"])
        await db.execute("UPDATE convert_jobs SET status = 'queued' WHERE status = 'running' AND worker_id IS NULL")
        await db.commit()
    finally:
        await db.close()
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    if _task:
        _task.cancel()


def work_dir(path: str, job_id: int | str) -> str:
    """The job's own work folder next to its file (the same disk) — jobs of one folder (a season) never meet."""
    return os.path.join(os.path.dirname(path), WORK_DIR, str(job_id))


def drop_work(path: str, job_id: int | str) -> None:
    shutil.rmtree(work_dir(path, job_id), ignore_errors=True)
    try:
        os.rmdir(os.path.join(os.path.dirname(path), WORK_DIR))       # the last one out
    except OSError:
        pass


def where(job: dict) -> str:
    """Who may run a job: the server, a worker, or whoever is free first (the user's choice; DV remux: server).
    "together": its plan goes to whoever is free, the job itself to the server (it leads), its parts to anyone."""
    w = (job.get("options") or {}).get("where")
    if job["kind"] == "part" or (job["kind"] == "plan" and w == "together"):
        return "any"
    return w if job["kind"] in WORKER_KINDS and w in ("worker", "any") else "server"


def _together(job: dict) -> bool:
    return job["kind"] == "shrink" and (job.get("options") or {}).get("where") == "together"


async def release_expired() -> int:
    """Jobs whose worker stopped reporting (PC asleep, switched off) go back to the queue."""
    db = await get_db()
    try:
        cur = await db.execute("UPDATE convert_jobs SET status = 'queued', worker_id = NULL, lease_until = NULL, "
                               "progress = '{}' WHERE status = 'running' AND worker_id IS NOT NULL AND lease_until < ?",
                               (_now(),))
        await db.execute(_SPARE_PARTS)
        await db.commit()
        if cur.rowcount:
            logger.info("Convert: %d job(s) of silent workers back in the queue", cur.rowcount)
        return cur.rowcount
    finally:
        await db.close()


# a part back in the queue (its worker went silent) that runs (or ran) in another row too is not needed twice
_SPARE_PARTS = """DELETE FROM convert_jobs WHERE kind = 'part' AND status = 'queued' AND EXISTS (
    SELECT 1 FROM convert_jobs o WHERE o.kind = 'part' AND o.id != convert_jobs.id
    AND json_extract(o.options, '$.parent') = json_extract(convert_jobs.options, '$.parent')
    AND json_extract(o.options, '$.idx') = json_extract(convert_jobs.options, '$.idx')
    AND (o.status != 'queued' OR o.id < convert_jobs.id))"""


async def _next() -> dict | None:
    await release_expired()
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM convert_jobs WHERE status = 'queued' AND kind != 'part' "
                                       "ORDER BY id")).fetchall()     # parts: their leader takes them (_run_together)
        idle = None
        for row in rows:
            job = _row(row)
            if where(job) not in ("server", "any"):
                continue
            if where(job) == "any":
                # "whoever is free first": a free worker (a PC — faster than the server) gets a head start
                if idle is None:
                    idle = await _idle_workers(db)
                age = (datetime.now() - datetime.strptime(job["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
                if idle and age < WORKER_FIRST_S:
                    continue
            # taken only if still queued — a worker may have taken it a moment ago
            cur = await db.execute("UPDATE convert_jobs SET status = 'running' WHERE id = ? AND status = 'queued'",
                                   (row["id"],))
            await db.commit()
            if cur.rowcount:
                return _row(row)
        return None
    finally:
        await db.close()


async def _idle_workers(db) -> int:
    """Workers heard from lately, free (no job of theirs running) and not waiting (outside their hours …)."""
    since = (datetime.now() - timedelta(seconds=WORKER_ONLINE_S)).strftime("%Y-%m-%d %H:%M:%S")
    rows = await (await db.execute(
        "SELECT state FROM convert_workers w WHERE last_seen >= ? AND NOT EXISTS (SELECT 1 FROM convert_jobs j "
        "WHERE j.status = 'running' AND j.worker_id = w.id)", (since,))).fetchall()
    return sum(1 for r in rows if not (json.loads(r[0] or "{}").get("waiting")))


async def _finish(job: dict, status: str, note: str = "", result: str = "", data: dict | None = None) -> None:
    db = await get_db()
    try:
        await db.execute("UPDATE convert_jobs SET status = ?, note = ?, result_path = ?, finished_at = ?, result = ?, "
                         "lease_until = NULL WHERE id = ?", (status, note, result, _now(), json.dumps(data or {}), job["id"]))
        await db.commit()
    finally:
        await db.close()
    await events.emit("convert.finished", {"kind": job["kind"], "path": job["path"], "ok": status == "done",
                                           "note": note, "result_path": result, "auto": bool(job["auto"])})


async def _loop() -> None:
    while True:
        job = await _next()
        if not job:
            _wake.clear()
            try:                         # a job queued by another process (a script) is found within a minute
                await asyncio.wait_for(_wake.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass
            continue
        current.clear()
        current.update(id=job["id"], kind=job["kind"], path=job["path"], phase="")
        try:
            result = await run(job)
            await _finish(job, "done", result.get("note", ""), result.get("path", ""), result.get("data"))
        except (dv.RemuxError, shrink.ShrinkError, OSError) as e:
            await _finish(job, "error", str(e))
        except Exception as e:  # noqa: BLE001 — a job must never stop the queue
            logger.exception("Convert job %s failed", job["id"])
            await _finish(job, "error", f"Neočekávaná chyba: {e}")
        finally:
            current.clear()


def _progress(phase: str, value: float | None = None) -> None:
    current["phase"] = phase
    current["value"] = value


async def run(job: dict) -> dict:
    """One job by its kind → {path, note, data}."""
    if job["kind"] == "plan":
        return await _run_plan(job)
    if _together(job):
        return await _run_together(job)
    if job["kind"] == "shrink":
        return await _run_shrink(job)
    return await _run_dv(job)


async def _run_plan(job: dict) -> dict:
    """The estimate (samples, VMAF, frames to compare); with "go" a file worth it goes on to be encoded."""
    src = job["path"]
    if not os.path.isfile(src):
        raise shrink.ShrinkError("Soubor zmizel")
    opts = shrink.options(job.get("options"))
    work = work_dir(src, job["id"])
    preview = PREVIEW_DIR / str(job["id"])
    try:
        data = await asyncio.to_thread(shrink.plan, src, opts, work, str(preview), _progress)
    finally:
        drop_work(src, job["id"])
    return await plan_done(job, data)


async def plan_done(job: dict, data: dict) -> dict:
    """A plan's estimate came (from the server or a worker): its note; with "go" a file worth it is queued."""
    opts = shrink.options(job.get("options"))
    note = data.get("note") or (f"{data['src_size'] / 1024**3:.2f} GB → ~{data['est_size'] / 1024**3:.2f} GB "
                                f"(−{data['saving']:g} %), obraz {data.get('src_mbps', 0):g} → {data.get('mbps', 0):g} Mb/s, "
                                f"VMAF {data['vmaf']:g}")
    if opts["go"] and data.get("decision") == "ok":
        await enqueue("shrink", [job["path"]], bool(job["auto"]),
                      {**(job.get("options") or {}), "crf": data["crf"], "plan_id": job["id"], "expect_vmaf": data.get("vmaf")})
        note += " · zařazeno ke kódování"
    return {"note": note, "data": data}


async def _run_shrink(job: dict) -> dict:
    """The whole file encoded, checked, and only when it saves enough it takes the file's place."""
    src = job["path"]
    if not os.path.isfile(src):
        raise shrink.ShrinkError("Soubor zmizel")
    opts = shrink.options(job.get("options"))
    crf = float((job.get("options") or {}).get("crf") or 0)
    if not crf:
        raise shrink.ShrinkError("Chybí náhled (CRF) — nejdřív udělej náhled")
    folder = os.path.dirname(src)
    if shutil.disk_usage(folder).free < os.path.getsize(src) + 1024**3:
        raise shrink.ShrinkError("Málo místa na disku")
    work = work_dir(src, job["id"])
    try:
        out, check = await asyncio.to_thread(shrink.encode, src, crf, opts, work, _progress)
        return await shrink_done(job, out, check)
    finally:
        drop_work(src, job["id"])


async def shrink_done(job: dict, out: str, check: dict) -> dict:
    """An encoded file (from the server or a worker, in the work folder next to the original) takes the
    original's place — only when it saves enough; the library follows and the name the new codec."""
    src = job["path"]
    opts = shrink.options(job.get("options"))
    crf = float((job.get("options") or {}).get("crf") or 0)
    folder = os.path.dirname(src)
    old_size = os.path.getsize(src)
    saving = round(100 * (1 - check["size"] / old_size), 1)
    if saving < opts["min_saving"]:
        raise shrink.ShrinkError(f"Ušetřilo by jen {saving:g} % — soubor nechávám")
    _progress("library")
    final = os.path.join(folder, os.path.basename(out))
    if final != src and os.path.exists(final):
        raise shrink.ShrinkError(f"{os.path.basename(final)} už ve složce je")
    _same_owner(out, src)
    os.replace(out, final)
    if final != src:
        os.remove(src)
    await _library(src, final)
    final = await _rename(final)
    note = (f"{old_size / 1024**3:.2f} GB → {check['size'] / 1024**3:.2f} GB (−{saving:g} %), "
            f"VMAF kontrola {', '.join(f'{v:g}' for v in check['vmaf_check'])}")
    logger.info("Shrunk %s → %s (%s)", src, final, note)
    return {"path": final, "note": note, "data": {**check, "saving": saving, "old_size": old_size, "crf": crf}}


# ── one file in parts (where = "together") ──

_spf: dict[int | None, float] = {}       # seconds per frame of each runner (None: the server), from its parts


async def parts_of(parent: int) -> list[dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM convert_jobs WHERE kind = 'part' AND json_extract(options, "
                                       "'$.parent') = ? ORDER BY json_extract(options, '$.idx'), id", (parent,))).fetchall()
        return [_row(r) for r in rows]
    finally:
        await db.close()


def part_dir(part: dict) -> str:
    """The parts of a job lie in the job's work folder (next to the file: the leader joins them there)."""
    return work_dir(part["path"], part["options"]["parent"])


def part_name(part: dict) -> str:
    return f"part-{part['options']['idx']:04d}-{part['id']}.mkv"


def _left(part: dict) -> float | None:
    """How long the part still runs where it runs (s) — infinite when its worker is paused, None when unknown."""
    prog = part.get("progress") or {}
    if prog.get("paused"):
        return float("inf")
    started = float(part["options"].get("started") or 0)
    elapsed = max(1.0, datetime.now().timestamp() - started) if started else 0.0
    share = float(prog.get("value") or 0) / 100
    if share >= 0.03 and elapsed:
        return elapsed * (1 - share) / share
    spf = _spf.get(part.get("worker_id"))
    return max(0.0, part["options"]["frames"] * spf - elapsed) if spf else None


async def claim_part(runner: int | None, lease_s: int = 0, parent: int | None = None) -> dict | None:
    """A part for the server (``runner`` None — its own job's parts) or a worker: the first nobody has; when none is
    left, one running elsewhere that this runner would clearly finish sooner (a paused worker, a slow machine at the
    end) — the same part twice, whoever finishes first wins (part_done)."""
    await release_expired()
    lease = (datetime.now() + timedelta(seconds=lease_s)).strftime("%Y-%m-%d %H:%M:%S") if lease_s else None
    started = datetime.now().timestamp()
    db = await get_db()
    try:
        sql = "SELECT * FROM convert_jobs WHERE kind = 'part' AND status IN ('queued', 'running')"
        args: tuple = ()
        if parent is not None:
            sql, args = sql + " AND json_extract(options, '$.parent') = ?", (parent,)
        rows = [_row(r) for r in await (await db.execute(sql + " ORDER BY id", args)).fetchall()]
        for part in sorted((r for r in rows if r["status"] == "queued"),
                           key=lambda r: (r["options"]["parent"], r["options"]["idx"])):
            cur = await db.execute("UPDATE convert_jobs SET status = 'running', worker_id = ?, lease_until = ?, "
                                   "progress = '{}', options = json_set(options, '$.started', ?) "
                                   "WHERE id = ? AND status = 'queued'", (runner, lease, started, part["id"]))
            await db.commit()
            if cur.rowcount:
                part["options"]["started"] = started
                return {**part, "status": "running", "worker_id": runner, "progress": {}}
        # nothing left to start: the same part a second time where it pays
        mine = _spf.get(runner)
        runs: dict[tuple, int] = {}
        for r in rows:
            key = (r["options"]["parent"], r["options"]["idx"])
            runs[key] = runs.get(key, 0) + 1
        best, gain = None, 0.0
        for r in rows:
            if r["status"] != "running" or r["worker_id"] == runner or runs[(r["options"]["parent"], r["options"]["idx"])] > 1:
                continue
            left = _left(r)
            if left is None or (mine is None and left != float("inf")):
                continue
            ours = r["options"]["frames"] * (mine or 0)
            if left == float("inf") or ours < left * 0.7:
                g = 1e12 if left == float("inf") else left - ours
                if g > gain:
                    best, gain = r, g
        if not best:
            return None
        options = {**best["options"], "started": started}
        cur = await db.execute("INSERT INTO convert_jobs (kind, path, status, auto, created_at, options, worker_id, "
                               "lease_until) VALUES ('part', ?, 'running', 0, ?, ?, ?, ?)",
                               (best["path"], _now(), json.dumps(options), runner, lease))
        await db.commit()
        logger.info("Convert: part %s of job %s once more (%s)", options["idx"], options["parent"],
                    "server" if runner is None else f"worker {runner}")
        return {**best, "id": cur.lastrowid, "options": options, "worker_id": runner, "progress": {}}
    finally:
        await db.close()


async def part_done(part: dict, path: str, seconds: float) -> bool:
    """A part encoded (by the server or a worker): the first of its runs is the one — another still running is
    dropped (its runner is told at its next report). → whether this one counts."""
    o = part["options"]
    db = await get_db()
    try:
        same = await (await db.execute(
            "SELECT id, status FROM convert_jobs WHERE kind = 'part' AND json_extract(options, '$.parent') = ? AND "
            "json_extract(options, '$.idx') = ? AND id != ?", (o["parent"], o["idx"], part["id"]))).fetchall()
        mine = await (await db.execute("SELECT status FROM convert_jobs WHERE id = ?", (part["id"],))).fetchone()
        if not mine or mine[0] != "running" or any(r[1] == "done" for r in same):
            await db.execute("DELETE FROM convert_jobs WHERE id = ? AND status != 'done'", (part["id"],))
            await db.commit()
            if os.path.exists(path):
                os.remove(path)
            return False
        await db.execute("UPDATE convert_jobs SET status = 'done', result_path = ?, result = ?, lease_until = NULL, "
                         "finished_at = ? WHERE id = ?", (path, json.dumps({"seconds": round(seconds, 1)}), _now(), part["id"]))
        if same:
            await db.execute(f"DELETE FROM convert_jobs WHERE id IN ({','.join('?' * len(same))})", [r[0] for r in same])
        await db.commit()
    finally:
        await db.close()
    if o.get("frames"):
        spf = seconds / o["frames"]
        old = _spf.get(part.get("worker_id"))
        _spf[part.get("worker_id")] = spf if old is None else old * 0.5 + spf * 0.5
    return True


async def part_back(part: dict, error: str = "") -> None:
    """A part its runner gives back (``error``: it failed there) — to the queue again, unless it runs elsewhere
    too; a part failing again and again fails its job (the leader sees it)."""
    o = dict(part["options"])
    fails = int(o.get("fails") or 0) + (1 if error else 0)
    o["fails"] = fails
    db = await get_db()
    try:
        if fails >= PART_FAILS:
            await db.execute("UPDATE convert_jobs SET status = 'error', note = ?, options = ?, worker_id = NULL, "
                             "lease_until = NULL WHERE id = ?", (error, json.dumps(o), part["id"]))
        else:
            await db.execute("UPDATE convert_jobs SET status = 'queued', options = ?, worker_id = NULL, "
                             "lease_until = NULL, progress = '{}' WHERE id = ?", (json.dumps(o), part["id"]))
            await db.execute(_SPARE_PARTS)
        await db.commit()
    finally:
        await db.close()


async def _drop_parts(parent: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM convert_jobs WHERE kind = 'part' AND json_extract(options, '$.parent') = ?", (parent,))
        await db.commit()
    finally:
        await db.close()


async def _overall(parts: list[dict]) -> tuple[int, int]:
    """The leader's progress: frames done (running parts by their share) of all, who encodes now → (done, count)."""
    frames_of = {p["options"]["idx"]: p["options"]["frames"] for p in parts}
    total = sum(frames_of.values())
    done_idx = {p["options"]["idx"] for p in parts if p["status"] == "done"}
    share: dict[int, float] = {}
    for p in parts:
        i = p["options"]["idx"]
        if p["status"] == "running" and i not in done_idx:
            share[i] = max(share.get(i, 0.0), float((p.get("progress") or {}).get("value") or 0) / 100)
    frames = sum(frames_of[i] for i in done_idx) + sum(frames_of[i] * v for i, v in share.items())
    names = await _runner_names({p["worker_id"] for p in parts if p["status"] == "running"})
    current.update(phase="parts", value=round(100 * frames / max(1, total), 1),
                   detail=f"{len(done_idx)}/{len(frames_of)} částí" + (f" · kóduje {', '.join(names)}" if names else ""))
    return len(done_idx), len(frames_of)


async def _runner_names(ids: set) -> list[str]:
    names = ["server"] if None in ids else []
    ids = sorted(i for i in ids if i is not None)
    if ids:
        db = await get_db()
        try:
            names += [r[0] for r in await (await db.execute(
                f"SELECT name FROM convert_workers WHERE id IN ({','.join('?' * len(ids))})", ids)).fetchall()]
        finally:
            await db.close()
    return names


async def _run_together(job: dict) -> dict:
    """The server leads: cuts the file into parts, encodes them with the workers (each takes the next one), joins
    them, checks the whole and only then it takes the file's place. A restart keeps the parts done."""
    src = job["path"]
    if not os.path.isfile(src):
        raise shrink.ShrinkError("Soubor zmizel")
    opts = shrink.options(job.get("options"))
    crf = float((job.get("options") or {}).get("crf") or 0)
    if not crf:
        raise shrink.ShrinkError("Chybí náhled (CRF) — nejdřív udělej náhled")
    if shrink.SPEEDS[opts["speed"]][0] != "libx265":
        raise shrink.ShrinkError("Po částech kóduje jen procesor (x265) — grafická karta má jiný obraz")
    info = await asyncio.to_thread(shrink.probe, src)
    if info["dv"]:
        raise shrink.ShrinkError("Dolby Vision by zaniklo — soubor nechávám")
    work = work_dir(src, job["id"])
    keep = False
    try:
        parts = await parts_of(job["id"])
        if not parts:
            if shutil.disk_usage(os.path.dirname(src)).free < os.path.getsize(src) * 1.5 + 1024**3:
                raise shrink.ShrinkError("Málo místa na disku (části a výsledek vedle originálu)")
            _progress("split")
            cut = await asyncio.to_thread(shrink.split, src)
            if not cut:                                   # cannot be cut frame-exact: the whole file here
                return await _run_shrink(job)
            os.makedirs(work, exist_ok=True)
            for folder in (os.path.dirname(work), work):     # the workers write their parts there (the share's user,
                _same_owner(folder, os.path.dirname(src))    # whoever it is — the folder is gone after the job)
                try:
                    os.chmod(folder, 0o777)
                except OSError:
                    pass
            db = await get_db()
            try:
                for p in cut:
                    await db.execute("INSERT INTO convert_jobs (kind, path, auto, created_at, options) VALUES "
                                     "('part', ?, 0, ?, ?)", (src, _now(), json.dumps(
                                         {**(job.get("options") or {}), "parent": job["id"], "count": len(cut), **p})))
                await db.commit()
            finally:
                await db.close()
            logger.info("Convert: job %s in %d parts", job["id"], len(cut))
        while True:
            parts = await parts_of(job["id"])
            broken = next((p for p in parts if p["status"] == "error"), None)
            if broken:
                raise shrink.ShrinkError(f"Část {broken['options']['idx'] + 1} selhala: {broken['note']}")
            done, count = await _overall(parts)
            if done == count:
                break
            mine = await claim_part(None, parent=job["id"])
            if mine:
                await _encode_part_here(job, mine, info, opts, crf)
            else:
                await asyncio.sleep(3)
        done = {p["options"]["idx"]: p for p in parts if p["status"] == "done"}
        order = sorted(done)
        files = [done[i]["result_path"] for i in order]
        counts = [done[i]["options"]["frames"] for i in order]
        seams = [done[order[k]]["options"]["start"] for k in sorted({len(order) // 3, 2 * len(order) // 3}) if k > 0]
        current.pop("detail", None)
        out, check = await asyncio.to_thread(shrink.assemble, src, files, counts, info, opts, work, seams, _progress)
        by = await _runner_names({p["worker_id"] for p in done.values()})
        result = await shrink_done(job, out, {**check, "parts": len(order)})
        result["note"] += f" · {len(order)} částí ({', '.join(by)})"
        return result
    except asyncio.CancelledError:
        keep = True                                       # a restart: the parts done wait for it
        raise
    finally:
        current.pop("detail", None)
        if not keep:
            await _drop_parts(job["id"])
            drop_work(src, job["id"])


async def _encode_part_here(job: dict, part: dict, info: dict, opts: dict, crf: float) -> None:
    """The server encodes one part itself; stopped when a worker finished the same part first."""
    o = part["options"]
    out = os.path.join(part_dir(part), part_name(part))
    cancel = threading.Event()
    state = {"value": 0.0}

    def prog(phase: str, value: float | None = None) -> None:
        state["value"] = value or 0.0

    task = asyncio.ensure_future(asyncio.to_thread(shrink.encode_part, part["path"], o["start"], o["frames"], info,
                                                   opts, crf, out, prog, cancel))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=5)
            if task.done():
                break
            db = await get_db()
            try:
                cur = await db.execute("UPDATE convert_jobs SET progress = ? WHERE id = ? AND status = 'running'",
                                       (json.dumps({"phase": "encode", "value": state["value"]}), part["id"]))
                await db.commit()
            finally:
                await db.close()
            if not cur.rowcount:
                cancel.set()                              # done elsewhere first
            await _overall(await parts_of(job["id"]))
        seconds = await task
    except asyncio.CancelledError:
        cancel.set()
        raise
    except shrink.ShrinkError as e:
        if not cancel.is_set():
            logger.warning("Convert: part %s of job %s failed on the server: %s", o["idx"], o["parent"], e)
            await part_back(part, str(e))
        return
    await part_done(part, out, seconds)


async def _rename(path: str) -> str:
    """The name follows the new codec ("x264" → "x265") — by the naming rules; the folder stays (Plex keeps the
    item). Episodes through the library's own rename, films by their plan's names only."""
    from app.config import get_effective_settings, movies_library_dir
    from app.modules.library import organize
    from app.modules.library.notify import emit_movie_updated
    db = await get_db()
    try:
        movie = await (await db.execute("SELECT id FROM library_movies WHERE file_path = ? AND status IN "
                                        "('matched', 'manual')", (path,))).fetchone()
        if not movie:
            from app.modules.library.router import _rename_now
            return (await _rename_now(db, [path])).get(path, path)
        from app.clients.tmdb import TMDBClient
        cfg = await get_effective_settings()
        client = TMDBClient(cfg.get("tmdb_api_key", ""))
        try:
            plan = organize.names_only(await organize.plan_movie(db, client, movie[0], movies_library_dir(cfg)))
            if plan["ops"]:
                await organize.apply_plan(db, plan, movies_library_dir(cfg))
                await emit_movie_updated(db, movie[0])
        except organize.OrganizeError as e:
            logger.info("Rename after shrinking %s: %s", path, e)
        finally:
            await client.close()
        row = await (await db.execute("SELECT file_path FROM library_movies WHERE id = ?", (movie[0],))).fetchone()
        return row[0] if row else path
    finally:
        await db.close()


async def _run_dv(job: dict) -> dict:
    """Dolby Vision MKV → MP4: the change in the work folder, its check, then the file's place and the library."""
    src = job["path"]
    if not os.path.isfile(src):
        raise dv.RemuxError("Soubor zmizel")
    folder = os.path.dirname(src)
    if shutil.disk_usage(folder).free < os.path.getsize(src) * 1.1 + 1024**3:
        raise dv.RemuxError("Málo místa na disku")
    stem = os.path.splitext(os.path.basename(src))[0]
    work = work_dir(src, job["id"])
    try:
        out, subs = await asyncio.to_thread(dv.remux, src, work, stem, set(os.listdir(folder)), _progress)
        _progress("library")
        final = os.path.join(folder, os.path.basename(out))
        if os.path.exists(final):
            raise dv.RemuxError(f"{os.path.basename(final)} už ve složce je")
        moved = []
        for p in subs:
            target = os.path.join(folder, os.path.basename(p))
            if not os.path.exists(target):
                os.replace(p, target)
                moved.append(os.path.basename(p))
                _same_owner(target, src)
        os.replace(out, final)
        _same_owner(final, src)
    finally:
        drop_work(src, job["id"])
    os.remove(src)                      # its sidecars (subtitles, NFO) share the stem — they stay with the MP4
    await _library(src, final)
    note = f"titulky vedle: {len(moved)}" if moved else ""
    logger.info("Converted %s → %s%s", src, final, f" ({note})" if note else "")
    return {"path": final, "note": note}


def _same_owner(path: str, like: str) -> None:
    """The new file belongs to whom the original did (the backend runs as root; the share's users edit it)."""
    try:
        st = os.stat(like)
        os.chown(path, st.st_uid, st.st_gid)
        os.chmod(path, st.st_mode & 0o777)
    except (OSError, AttributeError):        # not on Windows, not on a share that refuses it
        pass


async def _library(old: str, new: str) -> None:
    """Every row that knew the MKV knows the MP4 now (with its MediaInfo); NFO and Plex follow."""
    from app.core.mediainfo import probe_async
    from app.modules.library.notify import emit_movie_updated
    from app.modules.library.organize import path_moved
    media = await probe_async(new)
    st = os.stat(new)
    movie = None
    db = await get_db()
    try:
        await path_moved(db, old, new)
        await db.execute("UPDATE library_movies SET media = ?, file_size = ?, file_mtime = ? WHERE file_path = ?",
                         (json.dumps(media), st.st_size, st.st_mtime, new))
        await db.execute("UPDATE library_episodes SET file_size = ? WHERE file_path = ?", (st.st_size, new))
        if await (await db.execute("SELECT 1 FROM tv_media WHERE file_path = ?", (new,))).fetchone() or \
                await (await db.execute("SELECT 1 FROM library_episodes WHERE file_path = ?", (new,))).fetchone():
            await db.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, ?, ?, ?)",
                             (new, st.st_size, st.st_mtime, json.dumps(media)))
        await db.commit()
        movie = await (await db.execute("SELECT id FROM library_movies WHERE file_path = ?", (new,))).fetchone()
        if movie:
            await emit_movie_updated(db, movie[0])
    finally:
        await db.close()
    if not movie:
        await events.emit("library.files_added", {"folders": [os.path.dirname(new)]})
