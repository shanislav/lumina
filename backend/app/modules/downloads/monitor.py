import os
import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from app.config import get_effective_settings
from app.clients.aria2 import Aria2Client
from app.clients.qbittorrent import QBittorrentClient
from app.core import events
from app.core.paths import map_path
from app.db import DB_PATH

logger = logging.getLogger("app.modules.downloads.monitor")

SEEDING_DIR = ".lumina-import"


def _seeding_copy(path: str, torrent_hash: str) -> str:
    """A hard link of a finished torrent's video next to it (a copy across file systems) — the import
    takes that one, the torrent's own file stays for seeding (private trackers want a ratio)."""
    folder = os.path.join(os.path.dirname(path), SEEDING_DIR, torrent_hash[:16])
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    # the video and its subtitles ("Film.cs.srt")
    for name in os.listdir(os.path.dirname(path)):
        if name == os.path.basename(path) or (name.startswith(stem + ".") and name.lower().endswith((".srt", ".ass", ".ssa", ".sub", ".idx"))):
            src, dst = os.path.join(os.path.dirname(path), name), os.path.join(folder, name)
            if not os.path.exists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    import shutil
                    shutil.copy2(src, dst)
    return os.path.join(folder, os.path.basename(path))

_monitor_running = False


async def _monitor_loop():
    """Background loop that runs as long as there are unprocessed downloads."""
    global _monitor_running
    logger.info("Background monitor started (On-demand)")

    while True:
        try:
            cfg = await get_effective_settings()
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT id, tmdb_id, title, year, backend, content_type, intent FROM download_tracker WHERE processed = 0")
                tracked = cur.fetchall()

                if not tracked:
                    logger.info("No more downloads to process. Stopping monitor.")
                    _monitor_running = False
                    return

                for d in tracked:
                    did, tmdb_id, title, year, backend = d["id"], d["tmdb_id"], d["title"], d["year"], d["backend"]
                    content_type = d["content_type"] if "content_type" in d.keys() else "movie"
                    intent = json.loads(d["intent"]) if d["intent"] else None

                    try:
                        completed_path = None
                        if backend == "aria2":
                            aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
                            try:
                                s = await aria2.get_status(did)
                                if s.get("status") == "complete":
                                    files = s.get("files", [])
                                    if files: completed_path = files[0]["path"]
                            except Exception as ae:
                                if "not found" in str(ae):
                                    logger.warning("GID %s not found in Aria2, marking as processed to unblock", did)
                                    cur.execute("UPDATE download_tracker SET processed = 1, status = 'not_found' WHERE id = ?", (did,))
                                    conn.commit()
                                    continue
                                raise ae
                            finally:
                                await aria2.close()

                        elif backend == "qbittorrent":
                            qbt = QBittorrentClient(
                                cfg["qbittorrent_url"],
                                cfg["qbittorrent_username"],
                                cfg["qbittorrent_password"],
                            )
                            try:
                                t = await qbt.get_status(did)
                                state = t.get("state", "")
                                progress = t.get("progress", 0)
                                # Completed states or progress == 1.0
                                if state in ("uploading", "stalledUP", "pausedUP", "forcedUP", "queuedUP", "checkingUP") or progress >= 1.0:
                                    # content_path = the file or the torrent's folder, as qBittorrent sees it
                                    rule = cfg.get("qbittorrent_path_map", "")
                                    candidate = map_path(t.get("content_path", ""), rule, to_lumina=True)
                                    if not candidate and t.get("save_path") and t.get("name"):
                                        candidate = map_path(os.path.join(t["save_path"], t["name"]), rule, to_lumina=True)
                                    if candidate:
                                        if os.path.exists(candidate):
                                            if os.path.isdir(candidate):
                                                # Find largest video file in folder
                                                for root_d, _, fnames in os.walk(candidate):
                                                    for fn in fnames:
                                                        if os.path.splitext(fn)[1].lower() in ('.mkv', '.mp4', '.avi', '.ts', '.m4v'):
                                                            fp = os.path.join(root_d, fn)
                                                            if not completed_path or os.path.getsize(fp) > os.path.getsize(completed_path):
                                                                completed_path = fp
                                            else:
                                                completed_path = candidate
                                    if completed_path:
                                        # the library moves and renames what it imports — qBittorrent keeps
                                        # seeding its own file, the library gets a hard link (same data, no space)
                                        completed_path = _seeding_copy(completed_path, did)
                                    logger.info("qBittorrent %s complete: state=%s path=%s", did[:8], state, completed_path)
                            except Exception as qe:
                                logger.error("qBittorrent check failed for %s: %s", did[:8], qe)
                            finally:
                                await qbt.close()

                        if completed_path and Path(completed_path).exists():
                            logger.info("Download completed: %s. Emitting download.completed.", title)
                            await events.emit("download.completed", {
                                "download_id": did,
                                "tmdb_id": tmdb_id,
                                "title": title,
                                "year": year,
                                "content_type": content_type,
                                "path": completed_path,
                                "library_action": intent,
                            })
                            cur.execute("UPDATE download_tracker SET processed = 1, status = 'complete' WHERE id = ?", (did,))
                            conn.commit()
                    except Exception as sub_e:
                        logger.error("Error processing %s: %s", title, sub_e)

        except Exception as e:
            logger.error("Global monitor loop error: %s", e)

        await asyncio.sleep(20)

def ensure_monitor_running():
    """Triggers the background monitor task if not already running."""
    global _monitor_running
    if not _monitor_running:
        _monitor_running = True
        asyncio.create_task(_monitor_loop())
