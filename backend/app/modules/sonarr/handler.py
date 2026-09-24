import logging
import os
import shutil
from pathlib import Path

from app.clients.sonarr import SonarrClient
from app.db import get_automation

logger = logging.getLogger(__name__)


async def on_download_completed(payload: dict) -> None:
    if payload.get("content_type") != "tv":
        return
    automation = await get_automation("sonarr")
    if not automation or not automation["enabled"]:
        return
    cfg = automation["config"]
    if not (cfg.get("api_key") and cfg.get("url")):
        return

    current_path = payload["path"]
    title = payload["title"]
    logger.info("Processing TV download with Sonarr: %s", title)

    sonarr = SonarrClient(cfg["url"], cfg["api_key"])
    try:
        # Auto-add series if configured
        if cfg.get("auto_add") == "true":
            existing = await sonarr.lookup_series(title)
            if existing:
                tvdb_id = existing.get("tvdbId", 0)
                in_sonarr = await sonarr.get_series_by_tvdb_id(tvdb_id)
                if not in_sonarr:
                    await sonarr.add_series(
                        title, tvdb_id,
                        cfg.get("root_folder", "/downloads/tv"),
                        int(cfg.get("profile_id", "1")),
                    )

        # Move to blackhole or trigger scan
        if cfg.get("blackhole_path"):
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
                logger.info("Moved to Sonarr blackhole: %s", dest)
            try:
                os.chmod(dest, 0o664)
            except OSError:
                pass
            await sonarr.trigger_import_scan(cfg["blackhole_path"])
            # Re-ensure blackhole dir exists (Sonarr may delete it)
            blackhole.mkdir(parents=True, exist_ok=True)
        else:
            # No blackhole — just trigger scan on the download dir
            await sonarr.trigger_import_scan(str(Path(current_path).parent))
    except Exception as e:
        logger.error("Sonarr integration failed for %s: %s", title, e)
    finally:
        await sonarr.close()
