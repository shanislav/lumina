import logging
import os
from pathlib import Path

from app.core import naming
from app.core.mediainfo import probe_async
from app.db import get_automation

logger = logging.getLogger(__name__)


async def on_download_completed(payload: dict) -> None:
    automation = await get_automation("renamer")
    if not automation or not automation["enabled"]:
        return

    current_path = payload["path"]
    title, year, tmdb_id = payload["title"], payload["year"], payload["tmdb_id"]
    logger.info("Renaming file for: %s", title)

    cfg = automation["config"]
    media = await probe_async(current_path) if cfg.get("use_mediainfo") != "false" else {}
    # Same engine and template as the library, so a download is named like the rest of it.
    values = naming.movie_values({"tmdb_id": tmdb_id, "year": year}, media, Path(current_path).name, title or "")
    template = cfg.get("format") or naming.DEFAULT_FILE_FORMAT
    new_name = naming.sanitize(naming.render(template, values)) + Path(current_path).suffix.lower()
    new_path = Path(current_path).parent / new_name
    if new_path == Path(current_path):
        return
    if new_path.exists():
        logger.warning("Renaming skipped, %s already exists", new_path)
        return

    try:
        os.rename(current_path, new_path)
        os.chmod(new_path, 0o664)
        payload["path"] = str(new_path)
    except OSError as e:
        logger.error("Renaming failed for %s: %s", title, e)
