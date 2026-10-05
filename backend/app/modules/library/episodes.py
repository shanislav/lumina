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
    # a file of more episodes ("S04E01-E02") was the other episode's file too: it takes its own other version
    for row in await (await db.execute("SELECT * FROM library_episodes WHERE file_path = ? AND id != ?",
                                       (path, ep["id"]))).fetchall():
        other = [v for v in await versions(db, dict(row)) if v["file_path"] != path]
        if other:
            await db.execute("UPDATE library_episodes SET file_path = ?, filename = ?, file_size = ? WHERE id = ?",
                             (other[0]["file_path"], other[0]["filename"], other[0]["size"], row["id"]))
        else:
            await db.execute("UPDATE library_episodes SET has_file = 0, file_path = NULL, filename = NULL WHERE id = ?",
                             (row["id"],))
    await db.execute("DELETE FROM tv_files WHERE file_path = ?", (path,))
    await db.execute("DELETE FROM tv_media WHERE file_path = ?", (path,))
    for p in deleted:
        await db.execute("INSERT INTO file_operations (batch_id, movie_id, src, dst, status) VALUES (?, NULL, ?, '', 'deleted')",
                         (f"delete-episode-{ep['id']}", p))
    await db.commit()
    logger.info("Deleted episode file %s (%d files)", path, len(deleted))
    await events.emit("library.files_removed", {"folders": [os.path.dirname(path)]})
    return deleted


async def apply_span(db, path: str, count: int | None) -> list[int]:
    """The user's word on how many episodes a file holds, in the library now (the next scan says the same):
    every one of them owned with this file, one it no longer holds takes its other version (or none). Returns
    the episodes the file holds."""
    from app.core.episode_match import parse_episode
    from app.modules.library import tv_inventory
    row = await (await db.execute("SELECT show_tmdb_id, season, episodes, status, facts FROM tv_files WHERE file_path = ?",
                                  (path,))).fetchone()
    if not row or row["season"] is None:
        return []
    before = json.loads(row["episodes"] or "[]")
    if not before:
        return []
    name = parse_episode(os.path.basename(path))
    held = tv_inventory.file_episodes(before[0], name.episodes if name.season == row["season"] else [], count)
    src = await (await db.execute("SELECT filename, file_size, quality, language FROM library_episodes "
                                  "WHERE show_tmdb_id = ? AND season = ? AND episode = ?",
                                  (row["show_tmdb_id"], row["season"], before[0]))).fetchone()
    values = (src["filename"], src["file_size"], src["quality"], src["language"]) if src else \
        (os.path.basename(path), os.path.getsize(path), "", "")
    for e in held:
        # an episode with a file of its own keeps it (this one is another version of it)
        await db.execute("UPDATE library_episodes SET has_file = 1, file_path = ?, filename = ?, file_size = ?, "
                         "quality = ?, language = ? WHERE show_tmdb_id = ? AND season = ? AND episode = ? "
                         "AND (episode = ? OR has_file = 0 OR file_path IS NULL OR file_path = ?)",
                         (path, *values, row["show_tmdb_id"], row["season"], e, held[0], path))
    for e in before:
        if e in held:
            continue
        ep = await (await db.execute("SELECT * FROM library_episodes WHERE show_tmdb_id = ? AND season = ? AND episode = ? "
                                     "AND file_path = ?", (row["show_tmdb_id"], row["season"], e, path))).fetchone()
        if not ep:
            continue
        other = [v for v in await versions(db, {**dict(ep), "file_path": None}) if v["file_path"] != path]
        if other:
            await db.execute("UPDATE library_episodes SET file_path = ?, filename = ?, file_size = ? WHERE id = ?",
                             (other[0]["file_path"], other[0]["filename"], other[0]["size"], ep["id"]))
        else:
            await db.execute("UPDATE library_episodes SET has_file = 0, file_path = NULL, filename = NULL WHERE id = ?",
                             (ep["id"],))
    facts = json.loads(row["facts"] or "{}")
    facts.pop("two_parts", None)
    if count:
        facts["span"] = count
    else:
        facts.pop("span", None)
    status = "ok" if row["status"] == "two_parts" else row["status"]
    await db.execute("UPDATE tv_files SET episodes = ?, facts = ?, status = ?, note = CASE WHEN ? = 'ok' AND status = "
                     "'two_parts' THEN '' ELSE note END WHERE file_path = ?",
                     (json.dumps(held), json.dumps(facts), status, status, path))
    await db.commit()
    logger.info("%s holds S%02d%s (the user's word)", os.path.basename(path), row["season"],
                "".join(f"E{e:02d}" for e in held))
    return held


async def in_file(db, ep: dict) -> dict | None:
    """Which episodes the episode's current file holds, for "Díly v tomto souboru" in its window: {first,
    episodes, said (the user's count or None), suggested (the scan's next-episode guess), choices: [{count, label}]}."""
    from app.modules.library import tv_inventory
    path = ep.get("file_path")
    row = await (await db.execute("SELECT episodes, facts FROM tv_files WHERE file_path = ?", (path,))).fetchone() \
        if path else None
    if not row:
        return None
    held = json.loads(row["episodes"] or "[]") or [ep["episode"]]
    facts = json.loads(row["facts"] or "{}")
    first = held[0]
    titles = {r[0]: r[1] or "" for r in await (await db.execute(
        "SELECT episode, episode_title FROM library_episodes WHERE show_tmdb_id = ? AND season = ? AND episode BETWEEN ? AND ?",
        (ep["show_tmdb_id"], ep["season"], first, first + tv_inventory.MAX_SPAN - 1))).fetchall()}
    choices = []
    for n in range(1, tv_inventory.MAX_SPAN):
        last = first + n - 1
        if n > 1 and last not in titles:
            break
        label = f"jen E{first:02d}" if n == 1 else f"E{first:02d}–E{last:02d}"
        choices.append({"count": n, "label": label + (f" ({titles[last]})" if n > 1 and titles.get(last) else "")})
    spans = await tv_inventory.file_spans(db)
    return {"first": first, "episodes": held, "said": spans.get(path), "suggested": facts.get("two_parts"),
            "choices": choices}
