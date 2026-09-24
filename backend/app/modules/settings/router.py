import logging
from pathlib import Path

from fastapi import APIRouter

from app.clients.groq_scorer import DEFAULT_GROQ_MODEL, list_models
from app.db import get_all_settings, set_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])
SENSITIVE_KEYS = {"tmdb_api_key", "groq_api_key", "aria2_rpc_secret", "qbittorrent_password"}
DEFAULTS = {
    "tmdb_api_key": "",
    "groq_api_key": "",
    "groq_model": DEFAULT_GROQ_MODEL,
    "aria2_rpc_url": "http://aria2:6800/jsonrpc",
    "aria2_rpc_secret": "your_aria2_secret",
    "plex_media_dir": "/downloads/plex",
    "tv_media_dir": "",
    "movies_library_dir": "",
    "tv_library_dir": "",
    "qbittorrent_url": "",
    "qbittorrent_username": "admin",
    "qbittorrent_password": "",
    "min_relevance_score": "70",
    # quality of found files (app/core/quality.py)
    "quality_prefer_local": "true",   # CZ/SK audio first in "Doporučené"
    "quality_max_size_gb": "0",       # 0 = no limit
    "quality_hdr": "neutral",         # prefer | neutral | avoid
    "quality_weights": "",            # JSON, only values changed from core.quality.DEFAULT_WEIGHTS
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


# Groq is optional: without it file search falls back to name-based scoring.
REQUIRED_KEYS = {"tmdb_api_key"}


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


# Typical files the weights editor shows a live score for (media dict as the sources report it).
QUALITY_SAMPLES = [
    ("WEB-DL 1080p H.264 5 Mb/s, CZ 5.1", "Film.2020.1080p.WEB-DL.CZ.mkv",
     {"width": 1920, "height": 1080, "video_codec": "H264", "bitrate": 5_400_000, "audio": [{"lang": "cs", "codec": "AC3", "channels": 6}]}),
    ("WEB-DL 1080p H.265 3 Mb/s, CZ 5.1", "Film.2020.1080p.x265.CZ.mkv",
     {"width": 1920, "height": 1080, "video_codec": "HEVC", "bitrate": 3_400_000, "audio": [{"lang": "cs", "codec": "AC3", "channels": 6}]}),
    ("BluRay 1080p H.264 15 Mb/s, DTS 5.1", "Film.2020.1080p.BluRay.mkv",
     {"width": 1920, "height": 1080, "video_codec": "H264", "bitrate": 16_500_000, "audio": [{"lang": "en", "codec": "DTS", "channels": 6}]}),
    ("UHD 2160p H.265 HDR10 25 Mb/s, TrueHD 7.1", "Film.2020.2160p.UHD.HDR.mkv",
     {"width": 3840, "height": 2160, "video_codec": "HEVC", "bitrate": 29_000_000, "audio": [{"lang": "en", "codec": "TrueHD", "channels": 8}]}),
    ("„4K“ AI upscale H.265 4.5 Mb/s", "Film.2020.UP.AI.4K.mkv",
     {"width": 3840, "height": 2160, "video_codec": "HEVC", "bitrate": 4_900_000, "audio": [{"lang": "cs", "codec": "AAC", "channels": 2}]}),
    ("720p H.264 2.5 Mb/s, 2.0", "Film.2020.720p.mkv",
     {"width": 1280, "height": 720, "video_codec": "H264", "bitrate": 2_700_000, "audio": [{"lang": "cs", "codec": "AAC", "channels": 2}]}),
    ("SD XviD 1.2 Mb/s", "Film.2020.XviD.avi",
     {"width": 720, "height": 400, "video_codec": "XviD", "bitrate": 1_330_000, "audio": [{"lang": "cs", "codec": "MP3", "channels": 2}]}),
]


@router.get("/quality-weights")
async def quality_weights() -> dict:
    """Defaults and the weights in use now (defaults + the user's changes)."""
    from app.core.quality import DEFAULT_WEIGHTS, weights_from_setting

    stored = await get_all_settings()
    return {"defaults": DEFAULT_WEIGHTS, "current": weights_from_setting(stored.get("quality_weights", ""))}


@router.post("/quality-preview")
async def quality_preview(body: dict) -> list[dict]:
    """Score the sample files with the weights being edited (not saved yet)."""
    from app.config import get_effective_settings
    from app.core.quality import facts_from_media, merge_weights, prefs_from_settings, score

    prefs = prefs_from_settings(await get_effective_settings())
    prefs.weights = merge_weights(body.get("weights"))
    out = []
    for label, name, media in QUALITY_SAMPLES:
        size = int(media["bitrate"] * 6600 / 8)   # a 110-minute film
        result = score(facts_from_media({**media, "duration_s": 6600}, name, size), prefs)
        out.append({"label": label, "score": result.score, "parts": result.parts})
    return out


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


@router.get("/groq-models")
async def groq_models() -> dict:
    """Chat models available for the stored Groq key (the list changes as Groq retires models)."""
    stored = await get_all_settings()
    key = stored.get("groq_api_key", "")
    if not key:
        return {"models": [], "default": DEFAULT_GROQ_MODEL, "error": "Groq API key not configured"}
    try:
        return {"models": await list_models(key), "default": DEFAULT_GROQ_MODEL, "error": None}
    except Exception as e:
        logger.warning("Listing Groq models failed: %s", e)
        return {"models": [], "default": DEFAULT_GROQ_MODEL, "error": str(e)}
