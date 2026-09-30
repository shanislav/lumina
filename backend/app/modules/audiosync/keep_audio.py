"""Upgrade that keeps the local dub: "replace, but keep my CZ/SK audio" (decisions/0007, step 5).

The user downloads a better file (e.g. UHD with English only) to replace a version that has CZ/SK
audio, with ``library_action = {"mode": "replace", "file_id": <old>, "keep_audio": true}``.
On ``download.completed`` this module holds the file back from the library (``held_by``), and in
the background: compares the audio, puts the CZ/SK tracks the new file lacks into it, checks the
result — and only then hands the new file to the library to replace the old version.
If the audio does not fit (other cut, other film, failed check), nothing is deleted: the downloaded
file goes into the library as another version.
A held download is remembered in ``audiosync_pending`` — after a backend restart the work resumes.
"""

import asyncio
import importlib
import json
import logging
import os
import shutil
from pathlib import Path

from app.config import get_effective_settings
from app.core import events
from app.db import get_db
from app.modules.audiosync import analyze as engine
from app.modules.audiosync import transfer as muxer

logger = logging.getLogger(__name__)

PENDING = """
CREATE TABLE IF NOT EXISTS audiosync_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ISO 639-1/2 codes of the local languages → one code
LOCAL = {"cs": "cs", "cze": "cs", "ces": "cs", "sk": "sk", "slo": "sk", "slk": "sk"}


def _router():
    # the package exports the APIRouter as "router", so the module is reached by its full name
    return importlib.import_module("app.modules.audiosync.router")


def missing_local_tracks(new_audio: list[dict], old_audio: list[dict]) -> list[int]:
    """Indexes of the old file's CZ/SK tracks whose language the new file does not have
    (one per language — the first, usually the main mix)."""
    have = {LOCAL.get(a.get("language", "")) for a in new_audio} - {None}
    picked, langs = [], set()
    for a in old_audio:
        lang = LOCAL.get(a.get("language", ""))
        if lang and lang not in have and lang not in langs:
            picked.append(a["index"])
            langs.add(lang)
    return picked


async def on_download_completed(payload: dict) -> None:
    action = payload.get("library_action") or {}
    if (payload.get("imported") or payload.get("held_by") or not action.get("keep_audio")
            or (payload.get("content_type") or "movie") != "movie" or not action.get("file_id")):
        return
    payload["held_by"] = "audiosync"       # the library leaves it alone; we emit again when done
    held = {k: v for k, v in payload.items() if k != "held_by"}
    db = await get_db()
    try:
        cursor = await db.execute("INSERT INTO audiosync_pending (payload) VALUES (?)", (json.dumps(held),))
        await db.commit()
        pending_id = cursor.lastrowid
    finally:
        await db.close()
    asyncio.create_task(_keep_audio(held, pending_id))


async def resume_pending() -> None:
    """Startup: finish the downloads held back before a restart."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, payload FROM audiosync_pending ORDER BY id")
        rows = await cursor.fetchall()
    finally:
        await db.close()
    for row in rows:
        payload = json.loads(row["payload"])
        if os.path.isfile(payload.get("path") or ""):
            logger.info("keep audio: resuming %s after a restart", payload.get("path"))
            asyncio.create_task(_keep_audio(payload, row["id"]))
        else:
            await _forget(row["id"])


async def _forget(pending_id: int | None) -> None:
    if pending_id is None:
        return
    db = await get_db()
    try:
        await db.execute("DELETE FROM audiosync_pending WHERE id = ?", (pending_id,))
        await db.commit()
    finally:
        await db.close()


async def _release(payload: dict, path: str, action: dict, suffix: str) -> dict:
    return await events.emit("download.completed", {
        **{k: v for k, v in payload.items() if k not in ("held_by", "imported")},
        "download_id": f"{payload.get('download_id', '')}{suffix}", "path": path, "library_action": action})


async def _old_file(file_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, title, file_path FROM library_movies WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    return dict(row) if row and row["file_path"] and os.path.isfile(row["file_path"]) else None


async def _keep_audio(payload: dict, pending_id: int | None = None) -> None:
    r = _router()
    action = payload["library_action"]
    new_path = payload["path"]
    plain_replace = {"mode": "replace", "file_id": action["file_id"]}
    as_version = {"mode": "version"}
    async with r._lock:
        r._job.clear()
        r._job.update(running=True, kind="upgrade", phase="start", done=0, total=0, title=payload.get("title"),
                      result=None, error=None)
        loop = asyncio.get_running_loop()

        def progress(phase: str, done: int, total: int) -> None:
            loop.call_soon_threadsafe(r._job.update, {"phase": phase, "done": done, "total": total})

        try:
            old = await _old_file(action["file_id"])
            if not old:
                logger.warning("keep audio: old version %s is gone — importing %s as a new version",
                               action["file_id"], new_path)
                await _release(payload, new_path, as_version, "")
                return
            new_info, old_info = await asyncio.gather(asyncio.to_thread(engine.probe, new_path),
                                                      asyncio.to_thread(engine.probe, old["file_path"]))
            tracks = missing_local_tracks(new_info["audio"], old_info["audio"])
            if not tracks:
                logger.info("keep audio: %s already has the local audio — plain replace", new_path)
                await _release(payload, new_path, plain_replace, "")
                return

            result = await asyncio.to_thread(engine.analyze, new_path, 0, old["file_path"], tracks[0], progress)
            data = result.to_dict()
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO audiosync_results (reference_id, reference_track, other_id, other_track, verdict, "
                    "result) VALUES (0, 0, ?, ?, ?, ?)", (old["id"], tracks[0], result.verdict, json.dumps(data)))
                await db.commit()
            finally:
                await db.close()
            r._job.update(result=data)
            if result.verdict not in ("constant", "speed", "cuts") or not result.pieces:
                raise muxer.TransferError(f"Zvuk staré verze k novému obrazu nesedí ({result.verdict})")

            cfg = await get_effective_settings()
            downloads = Path(cfg.get("plex_media_dir") or os.path.dirname(new_path))
            workdir = downloads / f".lumina-keepaudio-{os.getpid()}-{id(payload)}"
            out_name = Path(new_path).stem + " [audio].mkv"
            try:
                built = await asyncio.to_thread(muxer.transfer, new_path, 0, old["file_path"], tracks, data,
                                                workdir, out_name, progress)
                final = Path(new_path).with_name(out_name)
                os.replace(built, final)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
            os.remove(new_path)            # its video and tracks are all in the new file
            progress("import", 0, 1)
            done = await _release(payload, str(final), plain_replace, "-audio")
            r._job.update(imported=bool(done.get("imported")), path=done.get("path"))
            logger.info("keep audio: %s — %d track(s) kept, old version replaced", final.name, len(tracks))
        except Exception as e:  # noqa: BLE001 — never lose anything: keep both versions
            logger.warning("keep audio failed for %s: %s — importing it as a new version", new_path, e)
            r._job.update(error=f"{e} — nový soubor uložen jako další verze, nic se nesmazalo")
            if os.path.isfile(new_path):
                done = await _release(payload, new_path, as_version, "")
                r._job.update(imported=bool(done.get("imported")), path=done.get("path"))
        finally:
            await _forget(pending_id)
            r._job.update(running=False)
