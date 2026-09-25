"""Fix movies on disk: rename folders and files to their verified identity.

A plan is computed per *group* = all library files of one movie in one folder.
When the folder holds nothing but that movie, the whole folder content moves
(subtitles, NFO, images, ...); otherwise only the movie's videos and their
sidecar files (same file stem, e.g. "Movie.cs.srt").

Safety rules (docs/decisions/0003):
- only movies with status matched/manual are organized
- nothing is ever overwritten — a conflict blocks the whole group
- renames stay on one filesystem (os.rename), no copying
- every operation is journaled in file_operations and a batch can be undone
- a failure in the middle rolls back what the batch already did
"""

import json
import logging
import os
import re
import uuid

from app.core import naming
from app.db import get_automation
from app.core.mediainfo import normalize_language
from app.core.release_name import SUBTITLE_EXTS, VIDEO_EXTS

logger = logging.getLogger(__name__)

ORGANIZABLE = ("matched", "manual")

FILE_OPERATIONS = """
CREATE TABLE IF NOT EXISTS file_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    movie_id INTEGER,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_file_operations_batch ON file_operations (batch_id);
"""


class OrganizeError(Exception):
    pass


async def naming_settings() -> dict:
    """Naming options live in the renamer automation config (shared with download renaming)."""
    automation = await get_automation("renamer")
    cfg = (automation or {}).get("config") or {}
    return {
        "language": cfg.get("language") or naming.DEFAULT_TITLE_LANGUAGE,
        "keep_local_original": cfg.get("keep_local_original", "true") != "false",
        "folder_format": cfg.get("folder_format") or naming.DEFAULT_FOLDER_FORMAT,
        "file_format": cfg.get("format") or naming.DEFAULT_FILE_FORMAT,
    }


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


# Two-letter codes accepted from a subtitle name; any other two letters ("hd", "up") are not a language.
_SUB_LANGS = {"cs", "sk", "en", "de", "fr", "pl", "hu", "es", "it", "ru", "uk", "pt", "nl", "ja", "ko", "zh"}


def subtitle_suffix(name: str) -> str:
    """Language/forced part for a subtitle renamed to the video's stem: "Parasite.CZE.forced.srt"
    -> ".cs.forced"; nothing recognisable -> "". Looks at the last words of the name only, and
    only after the last bracket: "[kor]" in "Film (2019) [WEBRip] [kor].ass" is the naming
    scheme's audio tag, not the subtitle language."""
    stem = os.path.splitext(name)[0].lower()
    tail = re.split(r"[\])]", stem)[-1]
    words = [w for w in re.split(r"[.\s_\-\[\]()]+", tail) if w][-3:]
    forced = "forced" in words
    lang = ""
    for w in reversed(words):
        code = "cs" if w == "cz" else normalize_language(w)
        if code in _SUB_LANGS and (len(w) > 2 or w in _SUB_LANGS or w == "cz"):
            lang = code
            break
    return (f".{lang}" if lang else "") + (".forced" if forced else "")


def _plan_group(rows: list[dict], details: dict, root: str, settings: dict) -> dict:
    """Plan for one movie (rows = its files in one folder). Pure except for listing the folder."""
    first = rows[0]
    folder = os.path.dirname(first["file_path"])
    title = naming.pick_title(
        details.get("titles_by_lang") or {}, details.get("original_language", ""),
        details.get("original_title", ""), settings["language"], settings["keep_local_original"],
    )
    info = {"tmdb_id": details["tmdb_id"], "imdb_id": details.get("imdb_id"), "year": details.get("year")}

    renames: dict[str, str] = {}  # src → dst for videos
    target_folder = None
    wanted: list[tuple[dict, str]] = []
    for row in rows:
        rel_folder, file_name = naming.movie_paths(
            info, row.get("media") or {}, row["filename"], title, os.path.splitext(row["filename"])[1],
            settings["folder_format"], settings["file_format"],
        )
        target_folder = os.path.normpath(os.path.join(root, *rel_folder.split("/")))
        wanted.append((row, os.path.join(target_folder, file_name)))
    # Two versions with the same name by the rules (same quality/codec/languages): the second one
    # keeps "(2)" — the way the import names it. A file already carrying its name goes first.
    wanted.sort(key=lambda item: os.path.normcase(item[0]["file_path"]) != os.path.normcase(item[1]))
    used: set[str] = set()
    for row, dst in wanted:
        base, ext = os.path.splitext(dst)
        n = 2
        while os.path.normcase(dst) in used:
            dst, n = f"{base} ({n}){ext}", n + 1
        used.add(os.path.normcase(dst))
        renames[row["file_path"]] = dst

    ops: list[dict] = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        entries = []
    group_paths = set(renames)
    other_videos = [
        e for e in entries
        if os.path.splitext(e)[1].lower() in VIDEO_EXTS and os.path.join(folder, e) not in group_paths
    ]
    whole_folder = not other_videos and os.path.normpath(folder) != os.path.normpath(root)

    for src, dst in renames.items():
        ops.append({"kind": "video", "src": src, "dst": dst})
        old_stem, new_stem = _stem(src), _stem(dst)
        for e in entries:
            path = os.path.join(folder, e)
            if path in group_paths or not os.path.isfile(path):
                continue
            if e.startswith(old_stem + "."):
                ops.append({"kind": "sidecar", "src": path, "dst": os.path.join(target_folder, new_stem + e[len(old_stem):])})

    # Subtitles named after something else ("Parasite (2021) [...].ass" next to a renamed video):
    # with one video in a folder of its own they clearly belong to it — take its stem + language.
    if whole_folder and len(renames) == 1:
        new_stem = _stem(next(iter(renames.values())))
        planned = {op["src"] for op in ops}
        taken = {op["dst"] for op in ops}
        for e in entries:
            path = os.path.join(folder, e)
            ext = os.path.splitext(e)[1].lower()
            if path in planned or ext not in SUBTITLE_EXTS or not os.path.isfile(path):
                continue
            base = os.path.join(target_folder, new_stem + subtitle_suffix(e))
            dst, n = base + ext, 2
            while dst in taken:
                dst, n = f"{base}.{n}{ext}", n + 1
            taken.add(dst)
            ops.append({"kind": "sidecar", "src": path, "dst": dst})

    if whole_folder:
        planned = {op["src"] for op in ops}
        for e in entries:
            path = os.path.join(folder, e)
            if path not in planned:
                ops.append({"kind": "other", "src": path, "dst": os.path.join(target_folder, e)})

    ops = [op for op in ops if op["src"] != op["dst"]]
    conflicts = _conflicts(ops)
    return {
        "movie_ids": [r["id"] for r in rows],
        "tmdb_id": details["tmdb_id"],
        "title": title,
        "year": details.get("year"),
        "folder": folder,
        "target_folder": target_folder,
        "ops": ops,
        "conflicts": conflicts,
        "remove_folder": whole_folder and os.path.normpath(folder) != os.path.normpath(target_folder or folder),
    }


def _conflicts(ops: list[dict]) -> list[str]:
    problems = []
    sources = {os.path.normcase(op["src"]) for op in ops}
    seen: set[str] = set()
    for op in ops:
        dst_key = os.path.normcase(op["dst"])
        if dst_key in seen:
            problems.append(f"Více souborů by dostalo stejné jméno: {op['dst']}")
        seen.add(dst_key)
        # an existing target is only fine when it is one of our own sources (case-only rename / swap)
        if os.path.exists(op["dst"]) and dst_key not in sources:
            problems.append(f"Cíl už existuje: {op['dst']}")
    return problems


async def _group_rows(db, movie_id: int) -> list[dict]:
    cursor = await db.execute("SELECT * FROM library_movies WHERE id = ?", (movie_id,))
    row = await cursor.fetchone()
    if not row:
        raise OrganizeError("Film nenalezen")
    row = dict(row)
    if row["status"] not in ORGANIZABLE or not row["tmdb_id"]:
        raise OrganizeError("Film není spárovaný — nejdřív ho potvrď")
    folder = os.path.dirname(row["file_path"])
    cursor = await db.execute(
        "SELECT * FROM library_movies WHERE tmdb_id = ? AND status IN ('matched', 'manual')", (row["tmdb_id"],)
    )
    rows = [dict(r) for r in await cursor.fetchall() if os.path.dirname(r["file_path"]) == folder]
    for r in rows:
        r["media"] = json.loads(r.get("media") or "{}")
    return rows


async def plan_movie(db, client, movie_id: int, root: str) -> dict:
    from app.modules.library.importer import tmdb_details

    rows = await _group_rows(db, movie_id)
    details = await tmdb_details(client, db, rows[0]["tmdb_id"])
    if not details:
        raise OrganizeError("Nelze načíst údaje z TMDB")
    return _plan_group(rows, details, root, await naming_settings())


async def plan_all(db, client, root: str) -> list[dict]:
    """Plans of all organizable movies that need a change."""
    cursor = await db.execute(
        "SELECT id, tmdb_id, file_path FROM library_movies WHERE status IN ('matched', 'manual') AND tmdb_id IS NOT NULL ORDER BY file_path"
    )
    seen: set[tuple] = set()
    plans = []
    for row in await cursor.fetchall():
        key = (row["tmdb_id"], os.path.dirname(row["file_path"]))
        if key in seen:
            continue
        seen.add(key)
        try:
            plan = await plan_movie(db, client, row["id"], root)
        except OrganizeError as e:
            logger.warning("Organize plan for %s failed: %s", row["file_path"], e)
            continue
        if plan["ops"]:
            plans.append(plan)
    return plans


def _ensure_dir(path: str) -> None:
    """Create a folder owned like its parent (Samba/Plex keep access), mode 775."""
    if os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    _ensure_dir(parent)
    os.mkdir(path)
    try:
        st = os.stat(parent)
        os.chown(path, st.st_uid, st.st_gid)
    except (OSError, AttributeError):
        pass
    try:
        os.chmod(path, 0o775)
    except OSError:
        pass


def _drop_lumina_leftovers(folder: str) -> None:
    """Remove NFO files written by Lumina from a folder that has nothing else left in it
    (after an undo the NFO module has already written a fresh one next to the restored video)."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return
    leftovers = [os.path.join(folder, e) for e in entries]
    if not leftovers or not all(p.lower().endswith(".nfo") and _written_by_lumina(p) for p in leftovers):
        return
    for p in leftovers:
        try:
            os.remove(p)
        except OSError:
            return


def _written_by_lumina(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "<lumina>" in f.read(65536)
    except OSError:
        return False


def _remove_empty_dirs(folder: str, root: str) -> None:
    """Remove folder and its empty parents up to (not including) the library root."""
    root = os.path.normpath(root)
    folder = os.path.normpath(folder)
    while folder != root and folder.startswith(root + os.sep):
        try:
            os.rmdir(folder)
        except OSError:
            return
        folder = os.path.dirname(folder)


async def apply_plan(db, plan: dict, root: str, batch_id: str | None = None) -> str:
    """Execute a plan. Returns batch id. Raises OrganizeError (after rollback) on failure."""
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

    movie_id = plan["movie_ids"][0] if plan["movie_ids"] else None
    for op in done:
        await db.execute(
            "INSERT INTO file_operations (batch_id, movie_id, src, dst) VALUES (?, ?, ?, ?)",
            (batch_id, movie_id, op["src"], op["dst"]),
        )
        if op["kind"] == "video":
            await db.execute(
                "UPDATE library_movies SET file_path = ?, filename = ?, file_mtime = ? WHERE file_path = ?",
                (op["dst"], os.path.basename(op["dst"]), os.stat(op["dst"]).st_mtime, op["src"]),
            )
    await db.commit()
    if plan["remove_folder"]:
        _remove_empty_dirs(plan["folder"], root)
    logger.info("Organized %s (%d operations, batch %s)", plan["title"], len(done), batch_id)
    return batch_id


async def undo_batch(db, batch_id: str, root: str) -> int:
    cursor = await db.execute(
        "SELECT id, src, dst FROM file_operations WHERE batch_id = ? AND status = 'done' ORDER BY id DESC", (batch_id,)
    )
    ops = await cursor.fetchall()
    undone = 0
    touched_folders: set[str] = set()
    for op in ops:
        if not os.path.exists(op["dst"]) or os.path.exists(op["src"]):
            logger.warning("Cannot undo %s → %s (file moved meanwhile)", op["dst"], op["src"])
            continue
        _ensure_dir(os.path.dirname(op["src"]))
        os.rename(op["dst"], op["src"])
        await db.execute(
            "UPDATE library_movies SET file_path = ?, filename = ?, file_mtime = ? WHERE file_path = ?",
            (op["src"], os.path.basename(op["src"]), os.stat(op["src"]).st_mtime, op["dst"]),
        )
        await db.execute("UPDATE file_operations SET status = 'undone' WHERE id = ?", (op["id"],))
        touched_folders.add(os.path.dirname(op["dst"]))
        undone += 1
    await db.commit()
    # Folders created by the batch may still hold an NFO the nfo module wrote there.
    for folder in touched_folders:
        _drop_lumina_leftovers(folder)
        _remove_empty_dirs(folder, root)
    return undone
