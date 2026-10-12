"""The queue of file changes: one at a time in the background, kept in the DB (a restart picks it up again).

A job works in a folder next to its file (the same disk: the result is moved, not copied). Only a result that
passed its check takes the file's place; the library then knows the new file (path, MediaInfo) and Plex rescans."""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
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
WORKER_KINDS = ("plan", "shrink")        # what a worker may do (Dolby Vision remux stays on the server)

WORK_DIR = ".lumina-convert"
PREVIEW_DIR = Path("data/convert-previews")
POLL_S = 60
KINDS = {"dv": "Přebalení Dolby Vision do MP4", "plan": "Náhled zmenšení", "shrink": "Zmenšení souboru"}
PHASES = {"remux": "přebaluji", "verify": "kontroluji výsledek", "library": "předávám knihovně",
          "samples": "vybírám vzorky", "sample": "zkouším kvalitu na vzorcích", "stills": "připravuji snímky",
          "encode": "kóduji"}

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
        rows = await (await db.execute("SELECT * FROM convert_jobs ORDER BY CASE status WHEN 'running' THEN 0 "
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
        for job_id, path in await (await db.execute("SELECT id, path FROM convert_jobs WHERE status = 'running' "
                                                    "AND worker_id IS NULL")).fetchall():
            drop_work(path, job_id)
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
    """Who may run a job: the server, a worker, or whoever is free first (the user's choice; DV remux: server)."""
    w = (job.get("options") or {}).get("where")
    return w if job["kind"] in WORKER_KINDS and w in ("worker", "any") else "server"


async def release_expired() -> int:
    """Jobs whose worker stopped reporting (PC asleep, switched off) go back to the queue."""
    db = await get_db()
    try:
        cur = await db.execute("UPDATE convert_jobs SET status = 'queued', worker_id = NULL, lease_until = NULL, "
                               "progress = '{}' WHERE status = 'running' AND worker_id IS NOT NULL AND lease_until < ?",
                               (_now(),))
        await db.commit()
        if cur.rowcount:
            logger.info("Convert: %d job(s) of silent workers back in the queue", cur.rowcount)
        return cur.rowcount
    finally:
        await db.close()


async def _next() -> dict | None:
    await release_expired()
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM convert_jobs WHERE status = 'queued' ORDER BY id")).fetchall()
        for row in rows:
            if where(_row(row)) not in ("server", "any"):
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
