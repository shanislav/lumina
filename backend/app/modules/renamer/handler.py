import logging
import os
import shutil
from pathlib import Path

from app.db import get_automation
from app.utils.media import format_filename, get_media_tags

logger = logging.getLogger(__name__)


async def on_download_completed(payload: dict) -> None:
    automation = await get_automation("renamer")
    if not automation or not automation["enabled"]:
        return

    current_path = payload["path"]
    title, year, tmdb_id = payload["title"], payload["year"], payload["tmdb_id"]
    logger.info("Renaming file for: %s", title)

    tags = {}
    if automation["config"].get("use_mediainfo") != "false":
        tags = get_media_tags(current_path)

    pattern = automation["config"].get("format", "")
    new_name = format_filename(current_path, tmdb_id, tags, title, year, pattern)
    new_path = Path(current_path).parent / new_name

    try:
        shutil.move(current_path, new_path)
        os.chmod(new_path, 0o664)
        payload["path"] = str(new_path)
    except Exception as e:
        logger.error("Renaming failed for %s: %s", title, e)
