from pydantic_settings import BaseSettings
from functools import lru_cache
from app.clients.groq_scorer import DEFAULT_GROQ_MODEL
from app.db import get_all_settings


class Settings(BaseSettings):
    tmdb_api_key: str = ""
    groq_api_key: str = ""
    aria2_rpc_url: str = "http://aria2:6800/jsonrpc"
    aria2_rpc_secret: str = "your_aria2_secret"
    plex_media_dir: str = "/downloads/plex"
    tv_media_dir: str = ""

    qbittorrent_url: str = ""
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = ""

    webshare_username: str = ""
    webshare_password: str = ""
    jackett_url: str = ""
    jackett_api_key: str = ""

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_effective_settings() -> dict[str, str]:
    """Load settings with DB overrides: DB values take priority over .env.

    This is the preferred way to get config at runtime.
    Returns a plain dict with all infra settings.

    """
    env = get_settings()
    db_settings = await get_all_settings()

    return {
        "tmdb_api_key": db_settings.get("tmdb_api_key") or env.tmdb_api_key,
        "groq_api_key": db_settings.get("groq_api_key") or env.groq_api_key,
        "groq_model": db_settings.get("groq_model") or DEFAULT_GROQ_MODEL,
        "aria2_rpc_url": db_settings.get("aria2_rpc_url") or env.aria2_rpc_url,
        "aria2_rpc_secret": db_settings.get("aria2_rpc_secret") or env.aria2_rpc_secret,
        "plex_media_dir": db_settings.get("plex_media_dir") or env.plex_media_dir,
        "tv_media_dir": db_settings.get("tv_media_dir") or env.tv_media_dir,
        "movies_library_dir": db_settings.get("movies_library_dir") or "",
        "tv_library_dir": db_settings.get("tv_library_dir") or "",
        "qbittorrent_url": db_settings.get("qbittorrent_url") or env.qbittorrent_url,
        "qbittorrent_username": db_settings.get("qbittorrent_username") or env.qbittorrent_username,
        "qbittorrent_password": db_settings.get("qbittorrent_password") or env.qbittorrent_password,
        "min_relevance_score": db_settings.get("min_relevance_score") or "70",
        "languages": db_settings.get("languages") or "cs",
        "quality_prefer_local": db_settings.get("quality_prefer_local") or "true",
        "quality_max_size_gb": db_settings.get("quality_max_size_gb") or "0",
        "quality_hdr": db_settings.get("quality_hdr") or "neutral",
        "radarr_api_key": db_settings.get("radarr_api_key") or "",
        "radarr_url": db_settings.get("radarr_url") or "",
        "radarr_root_folder": db_settings.get("radarr_root_folder") or "/data/movies",
        "radarr_profile_id": db_settings.get("radarr_profile_id") or "1",
        "radarr_blackhole_path": db_settings.get("radarr_blackhole_path") or "/downloads/radarr_inbox",
        "radarr_auto_add": db_settings.get("radarr_auto_add") or "false",
    }


def movies_library_dir(cfg: dict[str, str]) -> str:
    """Folder with the movie library. Falls back to the movie download folder."""
    return cfg.get("movies_library_dir") or cfg.get("plex_media_dir", "")


def tv_library_dir(cfg: dict[str, str]) -> str:
    """Folder with the TV library. Falls back to the TV download folder."""
    return cfg.get("tv_library_dir") or cfg.get("tv_media_dir", "")
