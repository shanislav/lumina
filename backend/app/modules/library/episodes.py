"""One episode of the library for its window on the show page: its file(s), MediaInfo, deleting a version.

A show has one row per episode (library_episodes); the files of it are what the last scan saw (tv_files —
two versions of an episode are two files there) plus the row's own file (an import after the scan).
"""

import json
import logging
import os
import re

from app.core import events
from app.modules.library.imports import _delete_version

logger = logging.getLogger(__name__)


async def episode(db, episode_id: int) -> dict | None:
    row = await (await db.execute(
        "SELECT e.*, s.title AS show_title, s.year AS show_year FROM library_episodes e "
        "LEFT JOIN library_shows s ON s.tmdb_id = e.show_tmdb_id WHERE e.id = ?", (episode_id,))).fetchone()
    return dict(row) if row else None


_PT = re.compile(r"^(.*) - pt(\d)(\.[^.]+)$", re.IGNORECASE)


def parts_of(path: str | None) -> list[tuple[int, str]]:
    """The parts of a two-part episode stored as "<name> - pt1.mkv", "<name> - pt2.mkv" (Plex plays them as one):
    [(1, path), (2, path)] — [] for a whole episode."""
    m = _PT.match(path or "")
    if not m or not os.path.isdir(os.path.dirname(path)):
        return []
    stem = os.path.basename(m.group(1))
    out = []
    for name in os.listdir(os.path.dirname(path)):
        n = _PT.match(name)
        if n and n.group(1) == stem and n.group(3).lower() == m.group(3).lower():
            out.append((int(n.group(2)), os.path.join(os.path.dirname(path), name)))
    return sorted(out)


def part_path(path: str | None, part: int | None) -> str | None:
    """The file of one part of the episode (its own file when it has no parts or part is None)."""
    if not part:
        return path
    return dict(parts_of(path)).get(part, path)


async def versions(db, ep: dict) -> list[dict]:
    """Every file of the episode: the scan's (tv_files) and the row's own, with MediaInfo (tv_media)."""
    paths: list[str] = []
    for r in await (await db.execute("SELECT file_path, episodes FROM tv_files WHERE show_tmdb_id = ? AND season = ?",
                                     (ep["show_tmdb_id"], ep["season"]))).fetchall():
        if ep["episode"] in json.loads(r["episodes"] or "[]"):
            paths.append(r["file_path"])
    if ep.get("file_path") and ep["file_path"] not in paths:
        paths.insert(0, ep["file_path"])
    parts = dict((path, n) for n, path in parts_of(ep.get("file_path")))
    for path in parts:
        if path not in paths:
            paths.append(path)
    out = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        m = await (await db.execute("SELECT media FROM tv_media WHERE file_path = ?", (path,))).fetchone()
        out.append({"file_path": path, "filename": os.path.basename(path), "size": os.path.getsize(path),
                    "media": json.loads(m[0] or "{}") if m else {}, "part": parts.get(path),
                    "current": path == ep.get("file_path") or path in parts})
    out.sort(key=lambda v: (not v["current"], v["part"] or 0, -v["size"]))
    return out


async def delete_file(db, ep: dict, path: str, root: str) -> list[str]:
    """Delete one file of the episode from disk (the user confirmed it; no trash) with its subtitles/NFO.
    The episode keeps another version if it has one."""
    files = await versions(db, ep)
    if path not in {v["file_path"] for v in files}:
        raise ValueError("Soubor k tomuto dílu nepatří")
    if not os.path.normpath(path).startswith(os.path.normpath(root) + os.sep):
        raise ValueError("Soubor není v knihovně seriálů")
    deleted = _delete_version(path)
    rest = [v for v in files if v["file_path"] != path]
    if ep.get("file_path") == path:
        if rest:
            await db.execute("UPDATE library_episodes SET file_path = ?, filename = ?, file_size = ? WHERE id = ?",
                             (rest[0]["file_path"], rest[0]["filename"], rest[0]["size"], ep["id"]))
        else:
            await db.execute("UPDATE library_episodes SET has_file = 0, file_path = NULL, filename = NULL WHERE id = ?", (ep["id"],))
    await db.execute("DELETE FROM tv_files WHERE file_path = ?", (path,))
    await db.execute("DELETE FROM tv_media WHERE file_path = ?", (path,))
    for p in deleted:
        await db.execute("INSERT INTO file_operations (batch_id, movie_id, src, dst, status) VALUES (?, NULL, ?, '', 'deleted')",
                         (f"delete-episode-{ep['id']}", p))
    await db.commit()
    logger.info("Deleted episode file %s (%d files)", path, len(deleted))
    await events.emit("library.files_removed", {"folders": [os.path.dirname(path)]})
    return deleted
