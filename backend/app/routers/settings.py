import logging
from pathlib import Path

from fastapi import APIRouter

from app.db import get_all_settings, set_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])
SENSITIVE_KEYS = {"tmdb_api_key", "groq_api_key", "aria2_rpc_secret", "qbittorrent_password"}
DEFAULTS = {
    "tmdb_api_key": "",
    "groq_api_key": "",
    "aria2_rpc_url": "http://aria2:6800/jsonrpc",
    "aria2_rpc_secret": "",
    "plex_media_dir": "/downloads/plex",
    "qbittorrent_url": "",
    "qbittorrent_username": "admin",
    "qbittorrent_password": "",
    "min_relevance_score": "70",
    "languages": "cs",
}


def _mask(settings: dict[str, str]) -> dict[str, str]:
    masked = {}
    for k, v in settings.items():
        if k in SENSITIVE_KEYS and v:
            masked[k] = "********"
        else:
            masked[k] = v
    return masked


REQUIRED_KEYS = {"tmdb_api_key", "groq_api_key"}


@router.get("/setup-status")
async def setup_status() -> dict:
    """Check if initial setup is complete."""
    stored = await get_all_settings()
    missing = [k for k in REQUIRED_KEYS if not stored.get(k)]

    from app.db import get_db
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM sources WHERE enabled = 1")
        row = await cursor.fetchone()
        has_sources = row[0] > 0 if row else False
    finally:
        await db.close()

    if not has_sources:
        missing.append("sources")

    return {"complete": len(missing) == 0, "missing": missing}


@router.get("")
async def list_settings() -> dict[str, str]:
    """Return all settings (sensitive values masked)."""
    stored = await get_all_settings()
    # Merge with defaults so frontend always sees all keys
    merged = {**DEFAULTS, **stored}
    return _mask(merged)


@router.put("")
async def update_settings(body: dict[str, str]) -> dict[str, str]:
    """Update settings. Masked values (********) are skipped to preserve existing."""
    stored = await get_all_settings()

    updates: dict[str, str] = {}
    for key, value in body.items():
        if key not in DEFAULTS:
            continue
        if value == "********":
            continue
        updates[key] = value

    if updates:
        await set_settings(updates)
        logger.info("Updated settings: %s", list(updates.keys()))

    new_stored = await get_all_settings()
    merged = {**DEFAULTS, **new_stored}
    return _mask(merged)


@router.get("/browse")
async def browse_directories(path: str = "/") -> dict:
    """List subdirectories for the folder picker."""
    target = Path(path).resolve()
    if not target.is_dir():
        return {"path": str(target), "parent": str(target.parent), "dirs": []}

    dirs: list[dict] = []
    try:
        for entry in sorted(target.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                dirs.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        pass

    return {
        "path": str(target),
        "parent": str(target.parent) if target != target.parent else None,
        "dirs": dirs,
    }


@router.get("/languages")
async def available_languages() -> list[dict]:
    """Return all supported languages with their codes and labels."""
    from app.clients.groq_scorer import LANGUAGE_CONFIG

    stored = await get_all_settings()
    enabled_codes = {
        c.strip() for c in stored.get("languages", "cs").split(",") if c.strip()
    }
    return [
        {"code": code, "name": cfg["name"], "label": cfg["label"], "enabled": code in enabled_codes}
        for code, cfg in LANGUAGE_CONFIG.items()
    ]
