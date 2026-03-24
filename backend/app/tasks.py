import os
import asyncio
import logging
import shutil
import sqlite3
import json
from pathlib import Path

from app.config import get_effective_settings
from app.clients.aria2 import Aria2Client
from app.clients.qbittorrent import QBittorrentClient
from app.clients.radarr import RadarrClient
from app.utils.media import get_media_tags, format_filename

logger = logging.getLogger("app.tasks")
DB_PATH = Path("data/lumina.db")

_monitor_running = False

def get_local_automations_sync():
    """Fetch automation settings using synchronous sqlite3 for stability in background tasks."""
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Check if table exists first
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='automations'")
        if not cur.fetchone():
            return []
        cur.execute("SELECT type, name, enabled, config FROM automations")
        rows = cur.fetchall()
        return [
            {"type": r["type"], "name": r["name"], "enabled": bool(r["enabled"]), "config": json.loads(r["config"])}
            for r in rows
        ]

async def run_post_processing(download_id: str, tmdb_id: int, title: str, year: int, local_path: str):
    """Run Renamer and Radarr workflows based on enabled automations."""
    automations = get_local_automations_sync()
    radarr_cfg = next((a for a in automations if a["type"] == "radarr"), None)
    renamer_cfg = next((a for a in automations if a["type"] == "renamer"), None)
    
    current_path = local_path
    
    # 1. RENAMER
    if renamer_cfg and renamer_cfg.get("enabled"):
        logger.info("Renaming file for: %s", title)
        tags = {}
        if renamer_cfg["config"].get("use_mediainfo") != "false":
            tags = get_media_tags(current_path)
        
        pattern = renamer_cfg["config"].get("format", "")
        new_name = format_filename(current_path, tmdb_id, tags, title, year, pattern)
        new_path = Path(current_path).parent / new_name
        
        try:
            shutil.move(current_path, new_path)
            os.chmod(new_path, 0o664)
            current_path = str(new_path)
        except Exception as e:
            logger.error("Renaming failed for %s: %s", title, e)

    # 2. RADARR
    if radarr_cfg and radarr_cfg.get("enabled"):
        cfg = radarr_cfg["config"]
        if cfg.get("api_key") and cfg.get("url"):
            logger.info("Adding movie to Radarr: %s", title)
            radarr = RadarrClient(cfg["url"], cfg["api_key"])
            try:
                movie = await radarr.get_movie_by_tmdb_id(tmdb_id)
                if not movie and cfg.get("auto_add") == "true":
                    await radarr.add_movie(tmdb_id, title, year, cfg.get("root_folder", "/data/movies"))
                
                if cfg.get("blackhole_path"):
                    dest = Path(cfg["blackhole_path"]) / Path(current_path).name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try: os.chmod(dest.parent, 0o775)
                    except: pass
                    shutil.move(current_path, dest)
                    os.chmod(dest, 0o664)
                    await radarr.trigger_blackhole_scan(cfg["blackhole_path"])
            except Exception as e:
                logger.error("Radarr integration failed for %s: %s", title, e)
            finally:
                await radarr.close()

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
                cur.execute("SELECT id, tmdb_id, title, year, backend FROM download_tracker WHERE processed = 0")
                tracked = cur.fetchall()
                
                if not tracked:
                    logger.info("No more downloads to process. Stopping monitor.")
                    _monitor_running = False
                    return

                for d in tracked:
                    did, tmdb_id, title, year, backend = d["id"], d["tmdb_id"], d["title"], d["year"], d["backend"]
                    
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
                        
                        if completed_path and Path(completed_path).exists():
                            logger.info("Download completed: %s. Starting post-processing.", title)
                            await run_post_processing(did, tmdb_id, title, year, completed_path)
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
