"""Fix TV shows on disk: every show folder to the naming rules (docs SERIALY — renamer).

A plan is computed per *show folder* (the first folder under the TV library, as the scan sees it):

- every episode file goes to ``{show folder}/{season folder}/{name}`` by the TV templates
  (core/naming.episode_paths); its subtitles, NFO and thumbnails (same file stem) go with it
- the numbers stay the user's: what Plex shows (it reads them from the names), else the file's own;
  an anime numbered through keeps the scan's mapping. Per show the user may choose TMDB's numbering
  instead (``tv_naming``): two files TMDB made one episode become its parts (" - pt1", " - pt2")
- the episode's name: Plex's (clean, in the user's language), the file's own when TMDB/Plex call that
  number another episode, else TMDB's
- extras folders (Plex's Other, Featurettes …) move with the show unchanged; "Bonus", "Extra" … become
  "Other" (a name Plex knows)
- anything else in the folder keeps its place under the new show folder; nothing is overwritten,
  a conflict blocks the show, every batch can be undone (file_operations, as with movies)
"""

import json
import logging
import os
import time
import uuid

from app.core import naming
from app.core.release_name import SUBTITLE_EXTS, VIDEO_EXTS
from app.modules.library import tv_inventory
from app.modules.library.organize import (
    OrganizeError,
    _conflicts,
    _ensure_dir,
    naming_settings,
    path_moved,
    subtitle_suffix,
)

logger = logging.getLogger(__name__)

TMDB_CACHE_DAYS = 30
NUMBERINGS = ("files", "tmdb")
# files the renamer leaves where they are (it cannot tell the episode)
SKIPPED = {"unknown": "neznámý díl — zůstane, kde je", "unmatched": "seriál nenalezen — zůstane, kde je"}


class ShowNotFound(OrganizeError):
    pass


async def show_details(client, db, tmdb_id: int) -> dict | None:
    row = await (await db.execute("SELECT data, fetched_at FROM tmdb_shows WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
    if row and time.time() - row[1] < TMDB_CACHE_DAYS * 86400:
        return json.loads(row[0])
    try:
        data = await client.get_tv_full(tmdb_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("TMDB details of show %s failed: %s", tmdb_id, e)
        return json.loads(row[0]) if row else None
    await db.execute("INSERT OR REPLACE INTO tmdb_shows (tmdb_id, data, fetched_at) VALUES (?, ?, ?)",
                     (tmdb_id, json.dumps(data), time.time()))
    await db.commit()
    return data


async def numbering(db, tmdb_id: int) -> str:
    row = await (await db.execute("SELECT numbering FROM tv_naming WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
    return row[0] if row and row[0] in NUMBERINGS else "files"


async def set_numbering(db, tmdb_id: int, value: str) -> None:
    if value not in NUMBERINGS:
        raise OrganizeError(f"Neznámé číslování: {value}")
    await db.execute("INSERT OR REPLACE INTO tv_naming (tmdb_id, numbering, updated_at) VALUES (?, ?, datetime('now'))",
                     (tmdb_id, value))
    await db.commit()


def _generic(title: str) -> bool:
    return not naming.episode_title(title or "")


def episode_target(row: dict, mode: str, tmdb_titles: dict[tuple[int, int], str]) -> tuple[int, list[int], str, tuple]:
    """(season, episodes, the episode's name, order key) of an inventory file. row: tv_files row with
    "facts" parsed and "episodes" a list."""
    facts = row["facts"]
    plex = facts.get("plex")
    file_season, file_eps = facts.get("file") or [row["season"], row["episodes"]]
    scan = (row["season"], row["episodes"])                         # the scan's numbers (TMDB's, if it mapped)
    file_title = facts.get("title") or ""
    plex_title = plex[2] if plex and not _generic(plex[2]) else ""
    tmdb_title = lambda s, e: tmdb_titles.get((s, e), "")  # noqa: E731
    order = (file_season or 0, file_eps[0] if file_eps else 0)

    if mode == "tmdb":
        if facts.get("tmdb_episode"):
            season, episodes = row["season"], [facts["tmdb_episode"]]
        else:
            season, episodes = scan[0], list(scan[1])
            if len(file_eps) > 1 and file_season == season and file_eps[0] == episodes[0]:
                episodes = list(file_eps)
        title = tmdb_title(season, episodes[0]) or (file_title if row["status"] == "tmdb_other" else plex_title) or file_title
        return season, episodes, title, order

    if facts.get("absolute"):
        season, episodes = scan[0], list(scan[1])                   # numbered through: TMDB's season split
    elif plex:
        season, episodes = plex[0], [plex[1]]
        if season == file_season and plex[1] in file_eps:
            episodes = list(file_eps)                               # a file of more episodes ("S07E21E22")
    else:
        season, episodes = file_season, list(file_eps)
    if row["status"] == "tmdb_other" and file_title:
        title = file_title          # the name in the file is TMDB's other episode — Plex's name is not this one's
    elif plex_title:
        title = plex_title
    elif (season, episodes[0]) == (scan[0], scan[1][0] if scan[1] else None) and row["status"] == "ok":
        title = tmdb_title(season, episodes[0]) or file_title
    else:
        title = file_title
    return season, episodes, title, order


def _sidecars(folder_entries: list[str], folder: str, old_stem: str) -> list[str]:
    """Files belonging to a video by name: "S01E01.avi" → "S01E01.srt", "S01E01.cs.srt", "S01E01-thumb.jpg"."""
    out = []
    for e in folder_entries:
        path = os.path.join(folder, e)
        if os.path.splitext(e)[1].lower() in VIDEO_EXTS or not e.startswith(old_stem) or e == old_stem:
            continue
        if e[len(old_stem)] in ".-_ [(" and os.path.isfile(path):
            out.append(e)
    return out


def plan_folder(rows: list[dict], show: dict, media: dict[str, dict], tmdb_titles: dict[tuple[int, int], str],
                root: str, settings: dict, mode: str = "files", blocked: str = "") -> dict:
    """Plan for one show folder. rows: its tv_files rows (facts parsed, episodes a list). show: {tmdb_id, title,
    year}. Pure except for listing the folder."""
    folder = rows[0]["folder"]
    old_root = os.path.normpath(os.path.join(root, folder))
    info = {"tmdb_id": show["tmdb_id"], "year": show.get("year")}
    show_rel, _season, _file = naming.episode_paths(info, 1, [1], {}, "", show["title"], ".mkv",
                                                    folder_format=settings["tv_folder_format"])
    new_root = os.path.normpath(os.path.join(root, *show_rel.split("/")))

    ops: list[dict] = []
    skipped: list[dict] = []
    wanted: list[tuple[dict, int, list[int], str, tuple]] = []
    for row in rows:
        if row["status"] == "extra":
            continue
        if row["status"] in SKIPPED or row["season"] is None:
            skipped.append({"file": row["file_path"], "why": SKIPPED.get(row["status"], "neznámý díl")})
            continue
        season, episodes, title, order = episode_target(row, mode, tmdb_titles)
        wanted.append((row, season, episodes, title, order))

    # TMDB's numbering may give one episode two files: TMDB's two-part episode = the user's two episodes
    by_target: dict[tuple, list] = {}
    for item in wanted:
        by_target.setdefault((item[1], tuple(item[2])), []).append(item)
    candidates: list[tuple[dict, str]] = []
    for (season, episodes), items in sorted(by_target.items()):
        items.sort(key=lambda it: it[4])
        # with the user's numbers two files of one number are versions of it, not parts
        orders = sorted({it[4] for it in items}) if mode == "tmdb" else []
        for row, _s, _e, title, order in items:
            ext = os.path.splitext(row["file_path"])[1]
            show_rel, season_rel, name = naming.episode_paths(
                info, season, list(episodes), media.get(row["file_path"]) or {}, os.path.basename(row["file_path"]),
                show["title"], ext, title, settings["tv_folder_format"], settings["tv_season_format"], settings["tv_file_format"])
            stem = os.path.splitext(name)[0]
            if len(orders) > 1:                                       # parts of one episode (Plex stacks them)
                stem += f" - pt{orders.index(order) + 1}"
            candidates.append((row, os.path.join(root, *show_rel.split("/"), season_rel, stem + ext.lower())))
    # two versions of an episode named alike: the second keeps "(2)" (as movies do); a file already
    # carrying its name goes first
    candidates.sort(key=lambda c: os.path.normcase(c[0]["file_path"]) != os.path.normcase(c[1]))
    renames: dict[str, str] = {}
    used: set[str] = set()
    for row, dst in candidates:
        base, ext = os.path.splitext(dst)
        n = 2
        while os.path.normcase(dst) in used:
            dst, n = f"{base} ({n}){ext}", n + 1
        used.add(os.path.normcase(dst))
        renames[row["file_path"]] = dst

    listing: dict[str, list[str]] = {}

    def entries(d: str) -> list[str]:
        if d not in listing:
            try:
                listing[d] = sorted(os.listdir(d))
            except OSError:
                listing[d] = []
        return listing[d]

    planned: set[str] = set()
    for src, dst in renames.items():
        ops.append({"kind": "video", "src": src, "dst": dst})
        planned.add(src)
        d, old_stem, new_stem = os.path.dirname(src), os.path.splitext(os.path.basename(src))[0], os.path.splitext(os.path.basename(dst))[0]
        for e in _sidecars(entries(d), d, old_stem):
            path = os.path.join(d, e)
            if path in planned:
                continue
            ext = os.path.splitext(e)[1].lower()
            rest = subtitle_suffix(e) + ext if ext in SUBTITLE_EXTS else e[len(old_stem):]
            ops.append({"kind": "sidecar", "src": path, "dst": os.path.join(os.path.dirname(dst), new_stem + rest)})
            planned.add(path)

    # extras folders and everything else: the same place under the new show folder
    for d, subs, files in os.walk(old_root):
        subs.sort()
        for f in sorted(files):
            path = os.path.join(d, f)
            if path in planned:
                continue
            rel = os.path.relpath(path, old_root).split(os.sep)
            extra = tv_inventory.extra_of([folder, *rel])
            if extra:
                rel = ["Other" if p == extra and p.strip().lower() in tv_inventory.EXTRA_DIRS_OTHER_NAMES else p for p in rel]
            ops.append({"kind": "extra" if extra is not None else "other", "src": path, "dst": os.path.join(new_root, *rel)})
            planned.add(path)

    ops = [op for op in ops if op["src"] != op["dst"]]
    return {
        "folder": folder,
        "tmdb_id": show["tmdb_id"],
        "title": show["title"],
        "year": show.get("year"),
        "numbering": mode,
        "source_folder": old_root,
        "target_folder": new_root,
        "ops": ops,
        "blocked": blocked,
        "conflicts": ([blocked] if blocked else []) + _conflicts(ops),
        "skipped": skipped,
    }


def names_only(plan: dict) -> dict:
    """New file names, each file staying in its folder — the first of the two steps Plex needs (decisions/0008)."""
    ops = [{**op, "dst": os.path.join(os.path.dirname(op["src"]), os.path.basename(op["dst"]))} for op in plan["ops"]]
    ops = [op for op in ops if op["src"] != op["dst"]]
    return {**plan, "ops": ops, "conflicts": ([plan["blocked"]] if plan.get("blocked") else []) + _conflicts(ops),
            "target_folder": plan["source_folder"]}


def tips(rows: list[dict]) -> list[str]:
    """What the user may want to do in Plex. A file named as another TMDB episode while Plex shows TMDB's name
    for its number: Plex orders the show by TMDB, the files by TheTVDB (or as aired)."""
    other = [r for r in rows if r["status"] == "tmdb_other" and (r.get("note") or "").startswith("soubor")]
    if not other:
        return []
    return [f"Plex u {len(other)} dílů ukazuje název jiného dílu (řadí podle TMDB, soubory podle TheTVDB nebo vysílání). "
            "V Plexu: Upravit seriál → Pokročilé → Pořadí dílů = TheTVDB. Nebo tady zvol „čísla podle TMDB“."]


async def _rows(db, folder: str | None = None) -> list[dict]:
    sql = "SELECT * FROM tv_files" + (" WHERE folder = ?" if folder is not None else "") + " ORDER BY file_path"
    out = []
    for r in await (await db.execute(sql, (folder,) if folder is not None else ())).fetchall():
        r = dict(r)
        r["facts"] = json.loads(r.get("facts") or "{}")
        r["episodes"] = json.loads(r.get("episodes") or "[]")
        out.append(r)
    return out


async def plan_show(db, client, folder: str, root: str, settings: dict | None = None) -> dict:
    rows = await _rows(db, folder)
    if not rows or not folder:
        raise ShowNotFound("Složka seriálu nenalezena — spusť sken knihovny")
    f = await (await db.execute("SELECT * FROM tv_folders WHERE folder = ?", (folder,))).fetchone()
    if not f or not f["tmdb_id"]:
        raise ShowNotFound(f"Seriál ve složce „{folder}“ není určený — vyber ho v Kontrole knihovny")
    tmdb_id = f["tmdb_id"]
    details = await show_details(client, db, tmdb_id)
    if not details:
        raise OrganizeError("Nelze načíst údaje seriálu z TMDB")
    settings = settings or await naming_settings()
    title = naming.pick_title(details.get("titles_by_lang") or {}, details.get("original_language", ""),
                              details.get("original_title", ""), settings["language"], settings["keep_local_original"]) \
        or details.get("title", "")
    media = {r[0]: json.loads(r[1] or "{}") for r in await (await db.execute(
        "SELECT file_path, media FROM tv_media WHERE file_path IN (SELECT file_path FROM tv_files WHERE folder = ?)",
        (folder,))).fetchall()}
    tmdb_titles = {(r[0], r[1]): r[2] or "" for r in await (await db.execute(
        "SELECT season, episode, episode_title FROM library_episodes WHERE show_tmdb_id = ?", (tmdb_id,))).fetchall()}
    override = await (await db.execute("SELECT 1 FROM tv_folder_overrides WHERE folder = ?", (folder,))).fetchone()
    blocked = ""
    if not override and f["lumina_tmdb_id"] and f["plex_tmdb_id"] and f["lumina_tmdb_id"] != f["plex_tmdb_id"]:
        blocked = "Lumina a Plex se neshodnou, který seriál to je — rozhodni v Kontrole knihovny seriálů"
    mode = await numbering(db, tmdb_id)
    plan = plan_folder(rows, {"tmdb_id": tmdb_id, "title": title, "year": details.get("year")}, media, tmdb_titles,
                       root, settings, mode, blocked)
    plan["tips"] = tips(rows)
    plan["media_missing"] = sum(1 for r in rows if r["status"] != "extra" and r["file_path"] not in media)
    return plan


async def plan_all(db, client, root: str) -> list[dict]:
    """Plans of all show folders that need a change (or are blocked)."""
    settings = await naming_settings()
    plans = []
    for (folder,) in await (await db.execute("SELECT folder FROM tv_folders WHERE folder != '' ORDER BY folder")).fetchall():
        try:
            plan = await plan_show(db, client, folder, root, settings)
        except OrganizeError as e:
            logger.info("TV organize plan for %s: %s", folder, e)
            continue
        if plan["ops"] or plan["conflicts"]:
            plans.append(plan)
    return plans


def _remove_empty_tree(folder: str, root: str) -> None:
    """Empty folders left under a show folder (old season folders), the show folder too when empty."""
    root = os.path.normpath(root)
    folder = os.path.normpath(folder)
    if folder == root or not folder.startswith(root + os.sep) or not os.path.isdir(folder):
        return
    for d, _subs, _files in sorted(os.walk(folder, topdown=False), key=lambda w: -len(w[0])):
        try:
            os.rmdir(d)
        except OSError:
            pass


async def apply_plan(db, plan: dict, root: str, batch_id: str | None = None) -> str:
    """Execute a plan (one show). Raises OrganizeError (after rollback) on failure."""
    if plan["conflicts"]:
        raise OrganizeError("; ".join(plan["conflicts"]))
    batch_id = batch_id or uuid.uuid4().hex[:12]
    done: list[dict] = []
    try:
        for op in plan["ops"]:
            if os.path.exists(op["dst"]) and os.path.normcase(op["dst"]) != os.path.normcase(op["src"]):
                raise OrganizeError(f"Cíl mezitím vznikl: {op['dst']}")
            _ensure_dir(os.path.dirname(op["dst"]))
            os.rename(op["src"], op["dst"])
            done.append(op)
    except (OSError, OrganizeError) as e:
        for op in reversed(done):
            try:
                os.rename(op["dst"], op["src"])
            except OSError:
                logger.exception("Rollback of %s failed", op["dst"])
        raise OrganizeError(f"Oprava selhala, vráceno zpět: {e}") from e

    for op in done:
        await db.execute("INSERT INTO file_operations (batch_id, movie_id, src, dst) VALUES (?, NULL, ?, ?)",
                         (batch_id, op["src"], op["dst"]))
        if op["kind"] == "video":
            await path_moved(db, op["src"], op["dst"])
    # the user's choice of the folder's show goes with the folder
    new_folder = os.path.relpath(plan["target_folder"], root).split(os.sep)[0]
    if new_folder != plan["folder"]:
        await db.execute("UPDATE OR REPLACE tv_folder_overrides SET folder = ? WHERE folder = ?", (new_folder, plan["folder"]))
        await db.execute("UPDATE tv_files SET folder = ? WHERE folder = ?", (new_folder, plan["folder"]))
        await db.execute("UPDATE OR REPLACE tv_folders SET folder = ? WHERE folder = ?", (new_folder, plan["folder"]))
    await db.commit()
    if os.path.normcase(plan["source_folder"]) != os.path.normcase(plan["target_folder"]):
        _remove_empty_tree(plan["source_folder"], root)
    else:
        _remove_empty_tree_inside(plan["source_folder"])
    logger.info("Organized show %s (%d operations, batch %s)", plan["title"], len(done), batch_id)
    return batch_id


def _remove_empty_tree_inside(folder: str) -> None:
    """Old season folders left empty inside a show folder that keeps its name."""
    for d, _subs, _files in sorted(os.walk(folder, topdown=False), key=lambda w: -len(w[0])):
        if os.path.normpath(d) != os.path.normpath(folder):
            try:
                os.rmdir(d)
            except OSError:
                pass
