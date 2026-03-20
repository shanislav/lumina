from fastapi import APIRouter, HTTPException

from app.config import get_effective_settings
from app.clients.aria2 import Aria2Client
from app.clients.qbittorrent import QBittorrentClient
from app.models.schemas import DownloadRequest
from app.sources.base import DownloadBackend
from app.sources.registry import SourceRegistry

router = APIRouter(prefix="/api", tags=["download"])


@router.post("/download")
async def start_download(req: DownloadRequest) -> dict:
    cfg = await get_effective_settings()
    target_dir = req.target_folder or cfg["plex_media_dir"]

    registry = SourceRegistry.get()
    source = registry.get_source_by_id(req.source_id) if req.source_id else None

    if source and source.download_backend == DownloadBackend.ARIA2:
        download_info = await source.get_download_info(req.file_ident)
        aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
        try:
            gid = await aria2.add_uri(download_info["url"], directory=target_dir)
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
            downloads.extend(active)

            stopped = await aria2.tell_stopped(0, 10)
            for d in stopped:
                d["backend"] = "aria2"
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
                    downloads.append({
                        "hash": t.get("hash", ""),
                        "status": t.get("state", "unknown"),
                        "total_length": t.get("total_size", 0),
                        "completed_length": t.get("downloaded", 0),
                        "download_speed": t.get("dlspeed", 0),
                        "filename": t.get("name", ""),
                        "backend": "qbittorrent",
                        "progress": t.get("progress", 0),
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
                # Cancel active download, then clean up the result entry
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
