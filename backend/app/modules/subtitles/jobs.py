"""Subtitle sync jobs, one at a time in the background (a 2-hour film takes a minute or two: the whole
file is read for its sound). The result per subtitle file is kept, the panel shows it."""

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime

from app.db import get_db
from app.modules.subtitles import files, sync

logger = logging.getLogger(__name__)

SUBTITLE_SYNC = """
CREATE TABLE IF NOT EXISTS subtitle_sync (
    path TEXT PRIMARY KEY,
    result TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT
);
"""

_queue: list[tuple[str, str, str]] = []      # (video, subtitle file, title)
_state = {"running": False, "current": "", "done": 0}
_task: asyncio.Task | None = None


def status() -> dict:
    return {**_state, "queued": [s for _, s, _ in _queue]}


def enqueue(video: str, subtitle: str, title: str = "") -> None:
    global _task
    if any(s == subtitle for _, s, _ in _queue) or _state["current"] == subtitle:
        return
    _queue.append((video, subtitle, title))
    if not _state["running"]:
        _state.update(running=True, done=0)
        _task = asyncio.create_task(_run())


def _audio_tracks(video: str) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", video], capture_output=True, text=True, timeout=60)
    return len([l for l in out.stdout.split() if l.strip()])


def _sync_file(video: str, subtitle: str) -> dict:
    with open(subtitle, "rb") as f:
        text = files.decode(f.read())
    result = sync.fit(video, text, _audio_tracks(video))
    if result.get("changed"):
        with open(subtitle, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(sync.apply(text, result["scale"], result["shift"]).replace("\r\n", "\n"))
    return {k: v for k, v in result.items() if k != "sp"}


async def _run() -> None:
    try:
        while _queue:
            video, subtitle, title = _queue.pop(0)
            _state["current"] = subtitle
            try:
                result = await asyncio.to_thread(_sync_file, video, subtitle)
            except Exception as e:
                logger.warning("Subtitle sync of %s failed: %s", subtitle, e)
                result = {"ok": False, "reason": f"chyba: {e}"}
            logger.info("Subtitle sync %s: %s", os.path.basename(subtitle),
                        {k: result.get(k) for k in ("scale_name", "shift", "score", "as_is", "parts", "changed", "reason")})
            db = await get_db()
            try:
                await db.execute("INSERT OR REPLACE INTO subtitle_sync (path, result, synced_at) VALUES (?, ?, ?)",
                                 (subtitle, json.dumps(result), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                await db.commit()
            finally:
                await db.close()
            _state["done"] += 1
    finally:
        _state.update(running=False, current="")


async def results(paths: list[str]) -> dict[str, dict]:
    if not paths:
        return {}
    db = await get_db()
    try:
        cursor = await db.execute(f"SELECT path, result, synced_at FROM subtitle_sync WHERE path IN ({','.join('?' * len(paths))})",
                                  paths)
        return {r[0]: {**json.loads(r[1] or "{}"), "synced_at": r[2]} for r in await cursor.fetchall()}
    finally:
        await db.close()


async def read_tasks() -> list[dict]:
    """For the task list."""
    if not _state["running"]:
        return []
    left = len(_queue)
    return [{"id": "subsync", "title": "Synchronizace titulků se zvukem",
             "detail": os.path.basename(_state["current"]) + (f" · ve frontě {left}" if left else ""),
             "done": 0, "total": 0, "running": True, "unit": ""}]
