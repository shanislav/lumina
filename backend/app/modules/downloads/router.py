import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.clients.aria2 import Aria2Client
from app.clients.qbittorrent import QBittorrentClient
from app.config import get_effective_settings
from app.core.auth import User, require
from app.models.schemas import DownloadRequest
from app.sources.base import DownloadBackend
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["download"])

SOURCE_LABELS: dict[str, str] = {
    "webshare": "WebShare",
    "fastshare": "FastShare",
    "jackett": "Torrent",
    "prowlarr": "Torrent",
}


def _resolve_target_dir(cfg: dict, req: DownloadRequest) -> str:
    """Pick the right download folder based on content type."""
    if req.target_folder:
        return req.target_folder
    if req.content_type == "tv" and cfg.get("tv_media_dir"):
        return cfg["tv_media_dir"]
    return cfg["plex_media_dir"]


@router.post("/download")
async def download(req: DownloadRequest, user: User = Depends(require("download"))) -> dict:
    # replacing a version deletes the old file once the new one is imported
    if (req.library_action or {}).get("mode") == "replace" and not user.can("library.delete"):
        raise HTTPException(403, "Nahradit verzi (smaže starou) může jen uživatel s oprávněním mazat v knihovně")
    # an own target folder writes anywhere the backend can — only for who manages the settings
    if req.target_folder and not user.can("settings"):
        raise HTTPException(403, "Vlastní cílovou složku může zvolit jen správce nastavení")
    return await start_download(req, requested_by=user.username)


async def start_download(req: DownloadRequest, requested_by: str = "", queued: bool = True) -> dict:
    """Starts a download — from the UI (above) or from other modules via event download.request.
    Over the limit of concurrent downloads it waits in the queue (queued=False: the queue starting it)."""
    from app.modules.downloads import queue
    from app.modules.downloads.monitor import ensure_monitor_running

    cfg = await get_effective_settings()
    target_dir = _resolve_target_dir(cfg, req)

    if queued and await queue.must_wait():
        queue_id = await queue.add(req.model_dump(), requested_by)
        ensure_monitor_running()
        return {"queued": queue_id, "status": "queued", "target_dir": target_dir, "source": req.source}

    registry = SourceRegistry.get()
    source = registry.get_source_by_id(req.source_id) if req.source_id else None
    action = dict(req.library_action or {})
    if (req.content_type or "") == "tv" and req.file_name:
        action.setdefault("release", req.file_name)      # what the uploader called it ("Chalupáři S01 (CZ)")
    source_label = SOURCE_LABELS.get(req.source, req.source)

    if source and source.download_backend == DownloadBackend.ARIA2:
        download_info = await source.get_download_info(req.file_ident, req.file_name or "")
        aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
        try:
            gid = await aria2.add_uri(
                download_info["url"],
                directory=target_dir,
                single_connection=True,
                headers=download_info.get("headers"),
            )
            from app.modules.downloads.store import track_download
            await track_download(gid, req.tmdb_id, req.title, req.year, "aria2", target_dir, req.content_type or "movie", action or req.library_action, source_label, requested_by)
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
            # torrents go where qBittorrent keeps them (its own folder, seeding goes on there);
            # the finished file is found by its path and imported from there
            torrent_hash = await qbt.add_torrent(req.magnet_url, save_path="")
            from app.modules.downloads.store import track_download
            await track_download(torrent_hash, req.tmdb_id, req.title, req.year, "qbittorrent", target_dir, req.content_type or "movie", action or req.library_action, source_label, requested_by)
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


async def on_download_request(payload: dict) -> None:
    """Event download.request — another module (wanted / library upgrades via the scheduler) asks for
    a download; started exactly like the UI does it. payload: DownloadRequest fields."""
    fields = {k: payload[k] for k in DownloadRequest.model_fields if k in payload}
    try:
        payload["started"] = await start_download(DownloadRequest(**fields), requested_by=payload.get("requested_by") or "")
        logger.info("Download requested by %s: %s", payload.get("requested_by", "?"), fields.get("title"))
    except Exception as e:
        payload["error"] = str(e)
        logger.warning("Requested download of %s failed: %s", fields.get("title"), e)


@router.get("/downloads")
async def list_downloads(offset: int = 0, limit: int = 10) -> dict:
    """Lumina's downloads: what downloads now (live state from aria2 / qBittorrent), what waits in the
    queue, then the history — finished first, the newest on top, a page at a time."""
    import sqlite3

    from app.db import DB_PATH
    from app.modules.downloads import queue
    from app.modules.downloads.store import history, tracked

    cfg = await get_effective_settings()
    known = await tracked()
    with sqlite3.connect(DB_PATH) as conn:
        running = conn.execute("SELECT id, backend FROM download_tracker WHERE processed = 0 ORDER BY created_at DESC").fetchall()

    active: list[dict] = []
    aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"]) if any(b == "aria2" for _, b in running) else None
    qbt = (QBittorrentClient(cfg["qbittorrent_url"], cfg["qbittorrent_username"], cfg["qbittorrent_password"])
           if cfg.get("qbittorrent_url") and any(b == "qbittorrent" for _, b in running) else None)
    try:
        for did, backend in running:
            info = known.get(did) or {}
            item = {"backend": backend, "status": "active", "total_length": 0, "completed_length": 0,
                    "download_speed": 0, "filename": info.get("film") or did}
            try:
                if backend == "aria2" and aria2:
                    item.update(await aria2.get_status(did))
                elif backend == "qbittorrent" and qbt:
                    t = await qbt.get_status(did)
                    item.update({"hash": did, "status": t.get("state", "unknown"), "total_length": t.get("total_size", 0),
                                 "completed_length": t.get("downloaded", 0), "download_speed": t.get("download_speed", 0),
                                 "filename": t.get("name") or item["filename"], "progress": t.get("progress", 0)})
            except Exception as e:     # polled every few seconds — a stopped client must not flood the log
                logger.debug("Live state of %s unavailable: %s", did, e)
            item.setdefault("gid" if backend == "aria2" else "hash", did)
            item.update({"source_label": info.get("source_label") or ("Torrent" if backend == "qbittorrent" else ""),
                         **{k: info.get(k) for k in ("tmdb_id", "film", "requested_by", "created_at", "mode", "content_type",
                                                    "season", "episode")}})
            active.append(item)
    finally:
        if aria2:
            await aria2.close()
        if qbt:
            await qbt.close()

    # waiting for a free slot — in the order they will start
    waiting = []
    for i, q in enumerate(await queue.items()):
        r = q["request"]
        title = r.get("title") or "?"
        waiting.append({
            "queue_id": q["id"], "queue_pos": i + 1, "status": "queued", "backend": "queue",
            "filename": f"{title} ({r['year']})" if r.get("year") else title,
            "source_label": SOURCE_LABELS.get(r.get("source") or "", r.get("source") or ""),
            "total_length": 0, "completed_length": 0, "download_speed": 0,
            "tmdb_id": r.get("tmdb_id"), "film": title, "requested_by": q["requested_by"], "created_at": q["created_at"],
            "mode": (r.get("library_action") or {}).get("mode") or "", "content_type": r.get("content_type") or "movie",
            "season": (r.get("library_action") or {}).get("season"), "episode": (r.get("library_action") or {}).get("episode"),
        })
    done, total = await history(max(0, offset), max(1, min(limit, 200)))
    return {"downloads": active + waiting, "history": done, "history_total": total, "limit": await queue.limit()}


@router.delete("/download/{identifier}", dependencies=[Depends(require("download"))])
async def remove_download(
    identifier: str, backend: str = "aria2", active: bool = False,
) -> dict:
    """Remove/cancel a download. Use active=true to cancel an in-progress download."""
    cfg = await get_effective_settings()

    if backend == "queue":
        from app.core import events
        from app.modules.downloads import queue
        try:
            item = await queue.take(int(identifier))
        except ValueError:
            raise HTTPException(400, "Neplatné id fronty")
        if item and item["request"].get("tmdb_id"):
            await events.emit("download.cancelled", {"tmdb_ids": [item["request"]["tmdb_id"]], "stop_all": False})
        return {"ok": bool(item)}
    if backend == "history":
        # off the list only — the file is in the library (and a torrent keeps seeding)
        from app.modules.downloads.store import forget
        return {"ok": await forget(identifier)}
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


@router.post("/download/queue/{queue_id}/start")
async def start_queued_now(queue_id: int, user: User = Depends(require("download"))) -> dict:
    """Skip the queue: start this waiting download right away (over the limit)."""
    from app.modules.downloads import queue
    item = await queue.take(queue_id)
    if not item:
        raise HTTPException(404, "Ve frontě už není")
    if (item["request"].get("library_action") or {}).get("mode") == "replace" and not user.can("library.delete"):
        await queue.add(item["request"], item["requested_by"])
        raise HTTPException(403, "Nahradit verzi (smaže starou) může jen uživatel s oprávněním mazat v knihovně")
    return await start_download(DownloadRequest(**item["request"]), requested_by=item["requested_by"], queued=False)


async def cancel_tracked(did: str, backend: str) -> None:
    """Cancel a running download Lumina started (its unfinished files are deleted) and mark it cancelled."""
    import sqlite3

    from app.db import DB_PATH
    cfg = await get_effective_settings()
    try:
        if backend == "qbittorrent":
            qbt = QBittorrentClient(cfg["qbittorrent_url"], cfg["qbittorrent_username"], cfg["qbittorrent_password"])
            try:
                await qbt.delete_torrent(did, delete_files=True)
            finally:
                await qbt.close()
        else:
            aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
            try:
                await aria2.force_remove(did)
                await aria2.remove_result(did)
            finally:
                await aria2.close()
    except Exception as e:
        logger.warning("Cancelling %s failed: %s", did, e)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE download_tracker SET processed = 1, status = 'cancelled' WHERE id = ?", (did,))


class StopAll(BaseModel):
    cancel_running: bool = False     # also cancel what downloads now (else it finishes)


@router.post("/downloads/stop-all", dependencies=[Depends(require("download"))])
async def stop_all(body: StopAll) -> dict:
    """"Zastavit vše": empty the queue, stop background checks from adding more, optionally cancel
    the running downloads Lumina started (their unfinished files are deleted)."""
    import sqlite3

    from app.core import events
    from app.db import DB_PATH
    from app.modules.downloads import queue

    tmdb_ids = [q["request"].get("tmdb_id") for q in await queue.clear()]
    dropped = len(tmdb_ids)
    cancelled = 0
    if body.cancel_running:
        with sqlite3.connect(DB_PATH) as conn:
            running = conn.execute("SELECT id, backend, tmdb_id FROM download_tracker WHERE processed = 0").fetchall()
        for did, backend, tmdb_id in running:
            await cancel_tracked(did, backend)
            tmdb_ids.append(tmdb_id)
            cancelled += 1
    await events.emit("download.cancelled", {"tmdb_ids": [t for t in tmdb_ids if t], "stop_all": True})
    logger.info("Stop all: %d taken out of the queue, %d running cancelled", dropped, cancelled)
    return {"dropped": dropped, "cancelled": cancelled}


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
