import logging
import os
import shutil
from pathlib import Path

from app.clients.radarr import RadarrClient
from app.db import get_automation

logger = logging.getLogger(__name__)


async def on_download_completed(payload: dict) -> None:
    if payload.get("imported"):  # the library already placed the file
        return
    if payload.get("content_type") != "movie":
        return
    automation = await get_automation("radarr")
    if not automation or not automation["enabled"]:
        return
    cfg = automation["config"]
    if not (cfg.get("api_key") and cfg.get("url")):
        return

    current_path = payload["path"]
    title, year, tmdb_id = payload["title"], payload["year"], payload["tmdb_id"]
    logger.info("Radarr post-processing: %s (tmdb=%s)", title, tmdb_id)

    radarr = RadarrClient(cfg["url"], cfg["api_key"])
    try:
        movie = await radarr.get_movie_by_tmdb_id(tmdb_id)
        if not movie and cfg.get("auto_add") == "true":
            await radarr.add_movie(tmdb_id, title, year, cfg.get("root_folder", "/data/movies"))

        if cfg.get("blackhole_path"):
            # Move file to blackhole — Radarr picks it up and does the rest
            blackhole = Path(cfg["blackhole_path"])
            blackhole.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(blackhole, 0o775)
            except OSError:
                pass
            dest = blackhole / Path(current_path).name
            if str(Path(current_path).resolve()) != str(dest.resolve()):
                shutil.move(current_path, dest)
                payload["path"] = str(dest)
                logger.info("Moved to blackhole: %s", dest)
            await radarr.trigger_blackhole_scan(cfg["blackhole_path"])
            # Re-ensure blackhole dir exists (Radarr may delete it)
            blackhole.mkdir(parents=True, exist_ok=True)
            logger.info("Radarr scan triggered: %s", cfg["blackhole_path"])
        else:
            # No blackhole — just trigger scan on download dir
            scan_path = str(Path(current_path).parent)
            await radarr.trigger_blackhole_scan(scan_path)
            logger.info("Radarr scan triggered: %s", scan_path)
    except Exception as e:
        logger.error("Radarr integration failed for %s: %s", title, e)
    finally:
        await radarr.close()
