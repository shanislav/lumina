import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import Depends, APIRouter

from app.core import events
from app.db import get_all_settings, get_automation, set_settings
from app.core.auth import require

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

DEFAULTS = {"time": "03:00", "wanted": "true", "upgrades": "true", "series": "true",
            "auto_download_wanted": "false", "auto_download_upgrades": "off"}
LAST_RUN_SETTING = "scheduler_last_run"
TICK_S = 60

_task: asyncio.Task | None = None


def _config(automation: dict | None) -> dict:
    cfg = {**DEFAULTS, **((automation or {}).get("config") or {})}
    return cfg


def _on(value) -> bool:
    return str(value).lower() == "true"


def _run_time(cfg: dict) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(cfg.get("time") or "03:00").split(":")[:2])
        return max(0, min(23, h)), max(0, min(59, m))
    except ValueError:
        return 3, 0


def next_run(cfg: dict, last_run: str, now: datetime | None = None) -> datetime:
    """Today at the configured time if it has not run today yet, otherwise tomorrow."""
    now = now or datetime.now()
    h, m = _run_time(cfg)
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    ran_today = (last_run or "")[:10] == now.strftime("%Y-%m-%d")
    return today if not ran_today and now <= today + timedelta(hours=12) else today + timedelta(days=1)


def is_due(cfg: dict, last_run: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    h, m = _run_time(cfg)
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # due from the time on, once a day; a server down at night catches up within 12 hours
    return (last_run or "")[:10] != now.strftime("%Y-%m-%d") and at <= now <= at + timedelta(hours=12)


async def run(reason: str = "plán") -> dict:
    automation = await get_automation("scheduler")
    cfg = _config(automation)
    payload = {
        "wanted": _on(cfg["wanted"]),
        "upgrades": _on(cfg["upgrades"]),
        "series": _on(cfg["series"]),
        "auto_download_wanted": _on(cfg["auto_download_wanted"]),
        "auto_download_upgrades": cfg["auto_download_upgrades"] if cfg["auto_download_upgrades"] in ("version", "replace") else "off",
        "reason": reason,
    }
    await set_settings({LAST_RUN_SETTING: datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    logger.info("Scheduler run (%s): %s", reason, payload)
    await events.emit("scheduler.run", payload)
    return payload


async def _loop() -> None:
    while True:
        try:
            automation = await get_automation("scheduler")
            if automation and automation["enabled"]:
                last = (await get_all_settings()).get(LAST_RUN_SETTING, "")
                if is_due(_config(automation), last):
                    await run()
        except Exception as e:   # the loop must survive anything
            logger.warning("Scheduler tick failed: %s", e)
        await asyncio.sleep(TICK_S)


async def start_loop() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop_loop() -> None:
    if _task and not _task.done():
        _task.cancel()


@router.get("")
async def scheduler_status() -> dict:
    automation = await get_automation("scheduler")
    cfg = _config(automation)
    last = (await get_all_settings()).get(LAST_RUN_SETTING, "")
    enabled = bool(automation and automation["enabled"])
    return {
        "enabled": enabled, "config": cfg, "last_run": last,
        "next_run": next_run(cfg, last).strftime("%Y-%m-%d %H:%M") if enabled else None,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


@router.post("/run", dependencies=[Depends(require("settings"))])
async def run_now() -> dict:
    """Run now (the same as the nightly run)."""
    return await run("ručně")
