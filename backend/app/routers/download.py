import logging

from fastapi import APIRouter, HTTPException

from app.config import get_effective_settings
from app.clients.aria2 import Aria2Client
from app.clients.qbittorrent import QBittorrentClient
from app.models.schemas import DownloadRequest
from app.sources.base import DownloadBackend
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["download"])

# In-memory mapping: gid/hash → source label (e.g. "FastShare", "WebShare", "Torrent")
_download_sources: dict[str, str] = {}

SOURCE_LABELS: dict[str, str] = {
    "webshare": "WebShare",
    "fastshare": "FastShare",
    "jackett": "Torrent",
}


def _resolve_target_dir(cfg: dict, req: DownloadRequest) -> str:
    """Pick the right download folder based on content type."""
    if req.target_folder:
        return req.target_folder
    if req.content_type == "tv" and cfg.get("tv_media_dir"):
        return cfg["tv_media_dir"]
    return cfg["plex_media_dir"]


@router.post("/download")
async def start_download(req: DownloadRequest) -> dict:
    cfg = await get_effective_settings()
    target_dir = _resolve_target_dir(cfg, req)

    registry = SourceRegistry.get()
    source = registry.get_source_by_id(req.source_id) if req.source_id else None
    source_label = SOURCE_LABELS.get(req.source, req.source)

    if source and source.download_backend == DownloadBackend.ARIA2:
        download_info = await source.get_download_info(req.file_ident)
        aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
        try:
            gid = await aria2.add_uri(
                download_info["url"],
                directory=target_dir,
                single_connection=True,
                headers=download_info.get("headers"),
            )
            _download_sources[gid] = source_label
            from app.db import track_download
            from app.tasks import ensure_monitor_running
            await track_download(gid, req.tmdb_id, req.title, req.year, "aria2", target_dir)
            ensure_monitor_running()
            return {
                "gid": gid,
                "status": "active",
                "target_dir": target_dir,
                "source": source.source_type.value,
            }
        finally:
            await aria2.close()

    elif source and source.download_backend == DownloadBackend.QBITTORRENT:
        if not req.magnet_url:
            raise HTTPException(400, "magnet_url is required for torrent downloads")
        if not cfg["qbittorrent_url"]:
            raise HTTPException(503, "qBittorrent is not configured")

        qbt = QBittorrentClient(
            cfg["qbittorrent_url"],
            cfg["qbittorrent_username"],
            cfg["qbittorrent_password"],
        )
        try:
            torrent_hash = await qbt.add_torrent(req.magnet_url, save_path=target_dir)
            _download_sources[torrent_hash] = source_label
            from app.db import track_download
            from app.tasks import ensure_monitor_running
            await track_download(torrent_hash, req.tmdb_id, req.title, req.year, "qbittorrent", target_dir)
            ensure_monitor_running()
            return {
                "hash": torrent_hash,
                "status": "active",
                "target_dir": target_dir,
                "source": source.source_type.value,
            }
        finally:
            await qbt.close()

    raise HTTPException(400, f"Source not found (source_id={req.source_id})")


@router.get("/downloads")
async def list_downloads() -> dict:
    """List all active + recent downloads from Aria2 and qBittorrent."""
    cfg = await get_effective_settings()
    downloads: list[dict] = []

    # Aria2
    try:
        aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
        try:
            active = await aria2.tell_active()
            for d in active:
                d["backend"] = "aria2"
                d["source_label"] = _download_sources.get(d.get("gid", ""), "")
            downloads.extend(active)

            stopped = await aria2.tell_stopped(0, 10)
            for d in stopped:
                d["backend"] = "aria2"
                d["source_label"] = _download_sources.get(d.get("gid", ""), "")
            downloads.extend(stopped)
        finally:
            await aria2.close()
    except Exception:
        pass

    # qBittorrent
    if cfg.get("qbittorrent_url"):
        try:
            qbt = QBittorrentClient(
                cfg["qbittorrent_url"],
                cfg["qbittorrent_username"],
                cfg["qbittorrent_password"],
            )
            try:
                await qbt.login()
                resp = await qbt._http.get(
                    f"{qbt._base_url}/api/v2/torrents/info",
                    params={"sort": "added_on", "reverse": "true", "limit": "20"},
                )
                resp.raise_for_status()
                for t in resp.json():
                    h = t.get("hash", "")
                    downloads.append({
                        "hash": h,
                        "status": t.get("state", "unknown"),
                        "total_length": t.get("total_size", 0),
                        "completed_length": t.get("downloaded", 0),
                        "download_speed": t.get("dlspeed", 0),
                        "filename": t.get("name", ""),
                        "backend": "qbittorrent",
                        "progress": t.get("progress", 0),
                        "source_label": _download_sources.get(h, "Torrent"),
                    })
            finally:
                await qbt.close()
        except Exception:
            pass

    return {"downloads": downloads}


@router.delete("/download/{identifier}")
async def remove_download(
    identifier: str, backend: str = "aria2", active: bool = False,
) -> dict:
    """Remove/cancel a download. Use active=true to cancel an in-progress download."""
    cfg = await get_effective_settings()

    # Clean up source tracking
    _download_sources.pop(identifier, None)

    if backend == "qbittorrent":
        if not cfg.get("qbittorrent_url"):
            raise HTTPException(503, "qBittorrent is not configured")
        qbt = QBittorrentClient(
            cfg["qbittorrent_url"],
            cfg["qbittorrent_username"],
            cfg["qbittorrent_password"],
        )
        try:
            ok = await qbt.delete_torrent(identifier, delete_files=True)
            return {"ok": ok}
        finally:
            await qbt.close()
    else:
        aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
        try:
            if active:
                await aria2.force_remove(identifier)
                await aria2.remove_result(identifier)
                return {"ok": True}
            else:
                ok = await aria2.remove_result(identifier)
                return {"ok": ok}
        finally:
            await aria2.close()


@router.get("/download/{gid}/status")
async def download_status(gid: str) -> dict:
    cfg = await get_effective_settings()
    aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
    try:
        return await aria2.get_status(gid)
    finally:
        await aria2.close()


@router.get("/download/torrent/{torrent_hash}/status")
async def torrent_status(torrent_hash: str) -> dict:
    cfg = await get_effective_settings()
    if not cfg["qbittorrent_url"]:
        raise HTTPException(503, "qBittorrent is not configured")
    qbt = QBittorrentClient(
        cfg["qbittorrent_url"],
        cfg["qbittorrent_username"],
        cfg["qbittorrent_password"],
    )
    try:
        return await qbt.get_status(torrent_hash)
    finally:
        await qbt.close()
