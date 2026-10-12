"""The queue of file changes: one at a time in the background, kept in the DB (a restart picks it up again).

A job works in a folder next to its file (the same disk: the result is moved, not copied). Only a result that
passed its check takes the file's place; the library then knows the new file (path, MediaInfo) and Plex rescans."""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime

from app.core import events
from app.db import get_db
from app.modules.convert import dv

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

WORK_DIR = ".lumina-convert"
POLL_S = 60
KINDS = {"dv": "Přebalení Dolby Vision do MP4"}
PHASES = {"remux": "přebaluji", "verify": "kontroluji výsledek", "library": "předávám knihovně"}

_wake = asyncio.Event()
_task: asyncio.Task | None = None
current: dict = {}                       # the running job, for the task list


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def enqueue(kind: str, paths: list[str], auto: bool = False) -> int:
    """Queue ``paths`` (not the ones queued or running already). → how many were added."""
    db = await get_db()
    added = 0
    try:
        for path in paths:
            if await (await db.execute("SELECT 1 FROM convert_jobs WHERE path = ? AND kind = ? AND status IN "
                                       "('queued', 'running')", (path, kind))).fetchone():
                continue
            await db.execute("INSERT INTO convert_jobs (kind, path, auto) VALUES (?, ?, ?)", (kind, path, int(auto)))
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
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def start() -> None:
    """A job left running by a restart goes again (its work folder is thrown away first)."""
    global _task
    db = await get_db()
    try:
        for (path,) in await (await db.execute("SELECT path FROM convert_jobs WHERE status = 'running'")).fetchall():
            shutil.rmtree(os.path.join(os.path.dirname(path), WORK_DIR), ignore_errors=True)
        await db.execute("UPDATE convert_jobs SET status = 'queued' WHERE status = 'running'")
        await db.commit()
    finally:
        await db.close()
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    if _task:
        _task.cancel()


async def _next() -> dict | None:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM convert_jobs WHERE status = 'queued' ORDER BY id LIMIT 1")).fetchone()
        if row:
            await db.execute("UPDATE convert_jobs SET status = 'running' WHERE id = ?", (row["id"],))
            await db.commit()
        return dict(row) if row else None
    finally:
        await db.close()


async def _finish(job: dict, status: str, note: str = "", result: str = "") -> None:
    db = await get_db()
    try:
        await db.execute("UPDATE convert_jobs SET status = ?, note = ?, result_path = ?, finished_at = ? WHERE id = ?",
                         (status, note, result, _now(), job["id"]))
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
            await _finish(job, "done", result.get("note", ""), result["path"])
        except (dv.RemuxError, OSError) as e:
            await _finish(job, "error", str(e))
        except Exception as e:  # noqa: BLE001 — a job must never stop the queue
            logger.exception("Convert job %s failed", job["id"])
            await _finish(job, "error", f"Neočekávaná chyba: {e}")
        finally:
            current.clear()


def _progress(phase: str) -> None:
    current["phase"] = phase


async def run(job: dict) -> dict:
    """One job: the change in the work folder, its check, then the file's place and the library."""
    src = job["path"]
    if not os.path.isfile(src):
        raise dv.RemuxError("Soubor zmizel")
    folder = os.path.dirname(src)
    if shutil.disk_usage(folder).free < os.path.getsize(src) * 1.1 + 1024**3:
        raise dv.RemuxError("Málo místa na disku")
    stem = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(folder, WORK_DIR)
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
        shutil.rmtree(work, ignore_errors=True)
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
