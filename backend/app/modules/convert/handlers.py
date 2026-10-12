"""A finished download with Dolby Vision in an MKV goes to the queue on its own (setting convert_dv_auto)."""

import asyncio
import logging
import os

from app.config import get_effective_settings
from app.modules.convert import dv, jobs

logger = logging.getLogger(__name__)


async def on_download_completed(payload: dict) -> None:
    """After the library took the file (priority behind the import): its MKVs with DV are queued."""
    if not payload.get("imported") or payload.get("held_by"):
        return
    if (await get_effective_settings()).get("convert_dv_auto") == "false":
        return
    paths = payload.get("placed_paths") or [payload.get("path")]
    wanted = []
    for path in paths:
        if not path or os.path.splitext(path)[1].lower() != ".mkv" or not os.path.isfile(path):
            continue
        try:
            if await asyncio.to_thread(dv.needs_remux, path):
                wanted.append(path)
        except dv.RemuxError as e:
            logger.info("DV check of %s: %s", path, e)
    if wanted:
        added = await jobs.enqueue("dv", wanted, auto=True)
        logger.info("Dolby Vision in MKV after a download: %d file(s) queued for MP4", added)
