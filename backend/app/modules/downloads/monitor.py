import os
import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from app.config import get_effective_settings
from app.clients.aria2 import Aria2Client
from app.clients.qbittorrent import QBittorrentClient
from app.core import events
from app.core.paths import map_path
from app.modules.downloads import queue
from app.db import DB_PATH

logger = logging.getLogger("app.modules.downloads.monitor")

SEEDING_DIR = ".lumina-import"
VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.ts', '.m4v')
FIRST_EPISODES = 2       # a show pack: these of the wanted episodes get the highest priority (watch while it downloads)


def pack_order(files: list[dict], skip: list[int], pack_season: int | None) -> list[int]:
    """The indexes of a pack's wanted episode files, the first episode first (a file of no episode last)."""
    from app.modules.library.imports import pack_episodes

    order = []
    for f in files:
        if f["index"] in skip or os.path.splitext(f["name"])[1].lower() not in VIDEO_EXTS:
            continue
        season, episodes = pack_episodes(f["name"].replace("\\", "/").replace("/", os.sep), pack_season)
        order.append(((season is None or not episodes, season or 0, (episodes or [0])[0]), f["index"]))
    return [i for _, i in sorted(order)]


def pack_priorities(files: list[dict], skip: list[int], pack_season: int | None) -> dict[int, list[int]]:
    """qBittorrent's priorities of a pack's files: the first wanted episodes the highest (7), the rest of their
    season high (6); the other seasons stay normal (1)."""
    from app.modules.library.imports import pack_episodes

    order = pack_order(files, skip, pack_season)
    if not order:
        return {}
    names = {f["index"]: f["name"] for f in files}
    season_of = {i: pack_episodes(names[i].replace("\\", "/").replace("/", os.sep), pack_season)[0] for i in order}
    first = season_of[order[0]]
    top = order[:FIRST_EPISODES]
    high = [i for i in order if first is not None and season_of[i] == first and i not in top]
    return {7: top, 6: high} if high else {7: top}


def _seeding_copy(path: str, torrent_hash: str) -> str:
    """A hard link of a finished torrent's video next to it (a copy across file systems) — the import
    takes that one, the torrent's own file stays for seeding (private trackers want a ratio)."""
    folder = os.path.join(os.path.dirname(path), SEEDING_DIR, torrent_hash[:16])
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    # the video and its subtitles ("Film.cs.srt")
    for name in os.listdir(os.path.dirname(path)):
        if name == os.path.basename(path) or (name.startswith(stem + ".") and name.lower().endswith((".srt", ".ass", ".ssa", ".sub", ".idx"))):
            src, dst = os.path.join(os.path.dirname(path), name), os.path.join(folder, name)
            if not os.path.exists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    import shutil
                    shutil.copy2(src, dst)
    return os.path.join(folder, os.path.basename(path))

_monitor_running = False


async def _removed(tmdb_id, content_type: str, intent: dict | None) -> None:
    """A download removed in Aria2 / qBittorrent while it ran (the user cancelled it there or in Lumina): the
    modules that started it hear it — it is not downloading any more."""
    if tmdb_id:
        await events.emit("download.cancelled", queue.cancelled(
            [{"tmdb_id": tmdb_id, "content_type": content_type, "library_action": intent or {}}]))


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _monitor_loop():
    """Background loop that runs as long as there are unprocessed downloads."""
    global _monitor_running
    logger.info("Background monitor started (On-demand)")

    while True:
        try:
            cfg = await get_effective_settings()
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT id, tmdb_id, title, year, backend, content_type, intent FROM download_tracker WHERE processed = 0")
                tracked = cur.fetchall()

                if not tracked and not await queue.pending():
                    logger.info("No more downloads to process. Stopping monitor.")
                    _monitor_running = False
                    return

                for d in tracked:
                    did, tmdb_id, title, year, backend = d["id"], d["tmdb_id"], d["title"], d["year"], d["backend"]
                    content_type = d["content_type"] if "content_type" in d.keys() else "movie"
                    intent = json.loads(d["intent"]) if d["intent"] else None

                    try:
                        completed_path = None
                        extra_paths: list[str] = []      # a torrent of several episodes: the others
                        pack_files: dict[str, str] = {}   # a pack's file here → its path in the torrent (the plan's key)
                        if backend == "aria2":
                            aria2 = Aria2Client(cfg["aria2_rpc_url"], cfg["aria2_rpc_secret"])
                            try:
                                s = await aria2.get_status(did)
                                if s.get("status") == "complete":
                                    files = s.get("files", [])
                                    if files: completed_path = files[0]["path"]
                                elif s.get("status") in ("error", "removed"):
                                    # failed or cancelled — it no longer takes a slot of the queue
                                    logger.warning("Download %s ended as %s", title, s.get("status"))
                                    cur.execute("UPDATE download_tracker SET processed = 1, status = ? WHERE id = ?", (s.get("status"), did))
                                    conn.commit()
                                    if s.get("status") == "error":         # "removed" = the user cancelled it
                                        await events.emit("download.failed", {
                                            "download_id": did, "tmdb_id": tmdb_id, "title": title, "year": year,
                                            "content_type": content_type, "library_action": intent,
                                            "reason": s.get("errorMessage") or ""})
                                    else:
                                        await _removed(tmdb_id, content_type, intent)
                                    continue
                            except Exception as ae:
                                if "not found" in str(ae):
                                    logger.warning("GID %s not found in Aria2, marking as processed to unblock", did)
                                    cur.execute("UPDATE download_tracker SET processed = 1, status = 'not_found' WHERE id = ?", (did,))
                                    conn.commit()
                                    await _removed(tmdb_id, content_type, intent)
                                    continue
                                raise ae
                            finally:
                                await aria2.close()

                        elif backend == "qbittorrent":
                            qbt = QBittorrentClient(
                                cfg["qbittorrent_url"],
                                cfg["qbittorrent_username"],
                                cfg["qbittorrent_password"],
                            )
                            try:
                                t = await qbt.get_status(did)
                                if t.get("status") == "not_found":
                                    # removed from qBittorrent — it no longer takes a slot of the queue
                                    logger.warning("Torrent %s of %s not in qBittorrent, marking as processed", did[:8], title)
                                    cur.execute("UPDATE download_tracker SET processed = 1, status = 'not_found' WHERE id = ?", (did,))
                                    conn.commit()
                                    await _removed(tmdb_id, content_type, intent)
                                    continue
                                if intent and intent.get("mode") == "pack" and not intent.get("files_chosen") and tmdb_id:
                                    # a whole-show pack: the episodes the user has are not downloaded at all (unless
                                    # they are to be replaced), the first wanted ones first
                                    files = await qbt.files(did)
                                    if files:          # a magnet's metadata is in
                                        from app.modules.library.imports import pack_skip
                                        owned = {(r[0], r[1]) for r in cur.execute(
                                            "SELECT season, episode FROM library_episodes WHERE show_tmdb_id = ? AND has_file = 1",
                                            (tmdb_id,)).fetchall()}
                                        # the whole pack placed at once (library.pack_plan): which file is which
                                        # episode, two-part episodes, bonuses — before anything downloads
                                        plan = None
                                        try:
                                            from app.modules.library.pack_plan import first_indexes, make_plan, skip_indexes
                                            plan = await make_plan(tmdb_id, files, intent.get("pack_season"),
                                                                   bool(intent.get("replace_owned")), title)
                                        except Exception as e:  # noqa: BLE001 — the file by file way then
                                            logger.warning("Pack %s: no plan: %s", title, e)
                                        if plan:
                                            skip = skip_indexes(plan)
                                            top, high = first_indexes(plan)
                                            prios = {7: top, 6: high}
                                        else:
                                            skip = [] if intent.get("replace_owned") else pack_skip(files, owned, intent.get("pack_season"))
                                            prios = pack_priorities(files, skip, intent.get("pack_season"))
                                        if skip:
                                            await qbt.set_file_priority(did, skip, 0)
                                        # the first episodes first, in order — each goes to the library once it is
                                        # complete (below), so watching can start before the whole pack is in
                                        try:
                                            for priority, indexes in prios.items():
                                                if indexes:
                                                    await qbt.set_file_priority(did, indexes, priority)
                                            await qbt.set_sequential(did, True)
                                        except Exception as e:  # noqa: BLE001 — only the order
                                            logger.info("Pack %s: the order not set: %s", title, e)
                                        intent.update(files_chosen=True, skipped=len(skip))
                                        if plan:
                                            intent["plan"] = plan
                                            await events.emit("download.planned", {"tmdb_id": tmdb_id, "title": title,
                                                                                   "summary": plan["summary"],
                                                                                   "unknown": [os.path.basename(k) for k, v in plan["files"].items()
                                                                                               if v.get("kind") == "unknown"][:20]})
                                        cur.execute("UPDATE download_tracker SET intent = ? WHERE id = ?", (json.dumps(intent), did))
                                        conn.commit()
                                        logger.info("Pack %s: %d of %d files not downloaded (owned episodes)", title, len(skip), len(files))
                                state = t.get("state", "")
                                progress = t.get("progress", 0)
                                if intent and intent.get("mode") == "pack" and content_type == "tv" and progress < 1.0 \
                                        and intent.get("files_chosen") and t.get("save_path"):
                                    # a pack's episode complete: to the library now (a hard link — it keeps seeding)
                                    rule = cfg.get("qbittorrent_path_map", "")
                                    done_before = set(intent.get("imported_paths") or [])
                                    for f in await qbt.files(did):
                                        if f.get("priority", 1) == 0 or (f.get("progress") or 0) < 1 \
                                                or os.path.splitext(f["name"])[1].lower() not in VIDEO_EXTS:
                                            continue
                                        full = map_path(os.path.join(t["save_path"], f["name"]), rule, to_lumina=True)
                                        if not full or full in done_before or not os.path.exists(full) \
                                                or "sample" in os.path.basename(full).lower():
                                            continue
                                        await events.emit("download.completed", {
                                            "download_id": did, "tmdb_id": tmdb_id, "title": title, "year": year,
                                            "content_type": content_type, "path": _seeding_copy(full, did),
                                            "extra_paths": [], "library_action": intent, "partial": True,
                                            "pack_file": f["name"].replace("\\", "/"),
                                        })
                                        done_before.add(full)
                                        intent["imported_paths"] = sorted(done_before)
                                        cur.execute("UPDATE download_tracker SET intent = ? WHERE id = ?", (json.dumps(intent), did))
                                        conn.commit()
                                        logger.info("Pack %s: %s in the library before the rest", title, os.path.basename(full))
                                # Completed states or progress == 1.0
                                if state in ("uploading", "stalledUP", "pausedUP", "forcedUP", "queuedUP", "checkingUP") or progress >= 1.0:
                                    # content_path = the file or the torrent's folder, as qBittorrent sees it
                                    rule = cfg.get("qbittorrent_path_map", "")
                                    candidate = map_path(t.get("content_path", ""), rule, to_lumina=True)
                                    if not candidate and t.get("save_path") and t.get("name"):
                                        candidate = map_path(os.path.join(t["save_path"], t["name"]), rule, to_lumina=True)
                                    if candidate:
                                        if os.path.exists(candidate):
                                            if os.path.isdir(candidate):
                                                # Find largest video file in folder
                                                videos = []
                                                for root_d, _, fnames in os.walk(candidate):
                                                    if SEEDING_DIR in root_d:
                                                        continue
                                                    for fn in fnames:
                                                        if os.path.splitext(fn)[1].lower() in ('.mkv', '.mp4', '.avi', '.ts', '.m4v'):
                                                            fp = os.path.join(root_d, fn)
                                                            if "sample" not in fn.lower():
                                                                videos.append(fp)
                                                            if not completed_path or os.path.getsize(fp) > os.path.getsize(completed_path):
                                                                completed_path = fp
                                                if content_type == "tv":
                                                    # a season pack: every episode goes to the library — but the ones
                                                    # imported while it downloaded (above) not again
                                                    done_before = set((intent or {}).get("imported_paths") or [])
                                                    videos = [fp for fp in videos if fp not in done_before]
                                                    if done_before and completed_path in done_before:
                                                        completed_path = max(videos, key=os.path.getsize) if videos else None
                                                    rels = {fp: os.path.relpath(fp, os.path.dirname(candidate)).replace(os.sep, "/")
                                                            for fp in videos}
                                                    extra_paths = []
                                                    for fp in videos:
                                                        if fp != completed_path:
                                                            copy = _seeding_copy(fp, did)
                                                            extra_paths.append(copy)
                                                            pack_files[copy] = rels[fp]
                                                    if not completed_path and done_before:
                                                        cur.execute("UPDATE download_tracker SET processed = 1, status = 'complete', "
                                                                    "finished_at = ? WHERE id = ?", (_now(), did))
                                                        conn.commit()
                                                        logger.info("Pack %s complete: every episode imported on the way", title)
                                            else:
                                                completed_path = candidate
                                    if completed_path:
                                        # the library moves and renames what it imports — qBittorrent keeps
                                        # seeding its own file, the library gets a hard link (same data, no space)
                                        original = completed_path
                                        completed_path = _seeding_copy(completed_path, did)
                                        if content_type == "tv" and t.get("content_path"):
                                            rel_root = os.path.dirname(candidate) if os.path.isdir(candidate) else os.path.dirname(original)
                                            pack_files[completed_path] = os.path.relpath(original, rel_root).replace(os.sep, "/")
                                    logger.info("qBittorrent %s complete: state=%s path=%s", did[:8], state, completed_path)
                            except Exception as qe:
                                logger.error("qBittorrent check failed for %s: %s", did[:8], qe)
                            finally:
                                await qbt.close()

                        if completed_path and Path(completed_path).exists():
                            logger.info("Download completed: %s. Emitting download.completed.", title)
                            done_size = os.path.getsize(completed_path)     # before the library moves it
                            done = await events.emit("download.completed", {
                                "download_id": did,
                                "tmdb_id": tmdb_id,
                                "title": title,
                                "year": year,
                                "content_type": content_type,
                                "path": completed_path,
                                "extra_paths": extra_paths,
                                "library_action": intent,
                                "pack_files": pack_files,
                            })
                            cur.execute("UPDATE download_tracker SET processed = 1, status = 'complete', finished_at = ?, file_name = ?, size = ? "
                                        "WHERE id = ?", (_now(), os.path.basename(done.get("path") or completed_path), done_size, did))
                            conn.commit()
                    except Exception as sub_e:
                        logger.error("Error processing %s: %s", title, sub_e)

        except Exception as e:
            logger.error("Global monitor loop error: %s", e)

        # finished ones made room — start what waits
        try:
            await queue.drain()
        except Exception as e:
            logger.error("Download queue error: %s", e)

        await asyncio.sleep(20)

def ensure_monitor_running():
    """Triggers the background monitor task if not already running."""
    global _monitor_running
    if not _monitor_running:
        _monitor_running = True
        asyncio.create_task(_monitor_loop())
