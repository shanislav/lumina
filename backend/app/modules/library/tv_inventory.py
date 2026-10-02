"""What the TV library holds, as the last scan saw it — before the renamer tidies it (docs SERIALY).

Every show folder: which show it is and who says so (the user's fix, Plex, Lumina), and where Lumina
and Plex disagree. Every file: which episode, and what is wrong with it:

    ok           show and episode clear
    show         Lumina and Plex name a different show for the folder (Plex is used until the user decides)
    numbers      the file's SxxEyy is not what Plex shows (absolute / other ordering)
    not_in_tmdb  TMDB does not list the episode (another numbering than the user's files and Plex)
    not_in_plex  Plex does not know the file (not scanned yet, or it skipped it)
    unknown      neither the name nor Plex tell the episode
    unmatched    the show was found neither in TMDB nor in Plex
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

TV_INVENTORY = """
CREATE TABLE IF NOT EXISTS tv_folders (
    folder TEXT PRIMARY KEY,
    tmdb_id INTEGER,
    source TEXT DEFAULT '',
    lumina_tmdb_id INTEGER,
    lumina_title TEXT DEFAULT '',
    plex_tmdb_id INTEGER,
    plex_title TEXT DEFAULT '',
    files INTEGER DEFAULT 0,
    scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS tv_files (
    file_path TEXT PRIMARY KEY,
    folder TEXT DEFAULT '',
    show_tmdb_id INTEGER,
    season INTEGER,
    episodes TEXT DEFAULT '[]',
    status TEXT DEFAULT 'ok',
    note TEXT DEFAULT '',
    scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS tv_folder_overrides (
    folder TEXT PRIMARY KEY,
    tmdb_id INTEGER NOT NULL,
    updated_at TEXT
);
"""

# Plex's folders for a show's extras (bonus videos, not episodes) — and names users give them
EXTRA_DIRS = {"behind the scenes", "deleted scenes", "featurettes", "interviews", "scenes", "shorts", "trailers", "other"}
EXTRA_DIRS_OTHER_NAMES = {"bonus", "bonusy", "extra", "extras", "extras & bonus", "special features"}
EXTRA_SUFFIXES = ("-behindthescenes", "-deleted", "-featurette", "-interview", "-scene", "-short", "-trailer", "-other")


def extra_of(rel_parts: list[str]) -> str | None:
    """The extras folder a file lies in under its show folder ("Other", "Bonus"), or "" for a Plex extra
    suffix ("…-featurette.mkv"); None for an episode. rel_parts: path under the TV library."""
    for part in rel_parts[1:-1]:
        name = part.strip().lower()
        if name in EXTRA_DIRS or name in EXTRA_DIRS_OTHER_NAMES:
            return part
    stem = rel_parts[-1].rsplit(".", 1)[0].lower()
    return "" if stem.endswith(EXTRA_SUFFIXES) else None


STATUS_LABELS = {
    "extra": "bonus (ne díl)",
    "show": "Lumina a Plex se neshodnou na seriálu",
    "tmdb_other": "TMDB má pod tímto číslem jiný díl",
    "numbers": "jiné číslo dílu než v Plexu",
    "not_in_tmdb": "TMDB díl nezná",
    "not_in_plex": "Plex soubor nezná",
    "unknown": "neznámý díl",
    "unmatched": "seriál nenalezen",
}


@dataclass
class Inventory:
    folders: list[tuple] = field(default_factory=list)
    files: list[tuple] = field(default_factory=list)

    def show(self, folder: str, tmdb_id, source: str, lumina_id, lumina_title: str, plex_id, plex_title: str, files: int):
        self.folders.append((folder, tmdb_id, source, lumina_id, lumina_title or "", plex_id, plex_title or "", files))

    def file(self, path: str, folder: str, tmdb_id, season, episodes: list[int], status: str, note: str = ""):
        self.files.append((path, folder, tmdb_id, season, json.dumps(episodes), status, note))

    async def save(self, db) -> None:
        """The scan sees the whole TV library: what it saw replaces the last inventory."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await db.execute("DELETE FROM tv_folders")
        await db.execute("DELETE FROM tv_files")
        await db.executemany("INSERT OR REPLACE INTO tv_folders (folder, tmdb_id, source, lumina_tmdb_id, lumina_title, "
                             "plex_tmdb_id, plex_title, files, scanned_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [(*f, now) for f in self.folders])
        await db.executemany("INSERT OR REPLACE INTO tv_files (file_path, folder, show_tmdb_id, season, episodes, status, "
                             "note, scanned_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(*f, now) for f in self.files])


def same_episode(a: str, b: str) -> bool:
    """Two names of one episode (Plex's and TMDB's) — a word in common is enough; an empty or generic
    name ("Epizoda 3") says nothing, so it agrees."""
    from app.core.naming import episode_title
    from app.utils.tv_parser import normalize_for_search
    wa = {w for w in normalize_for_search(episode_title(a or "")).split() if len(w) > 2}
    wb = {w for w in normalize_for_search(episode_title(b or "")).split() if len(w) > 2}
    return not wa or not wb or bool(wa & wb)


async def overrides(db) -> dict[str, int]:
    rows = await (await db.execute("SELECT folder, tmdb_id FROM tv_folder_overrides")).fetchall()
    return {r[0]: r[1] for r in rows}


async def summary(db) -> dict:
    """The inventory for the UI: folders with a problem first, each with its problem files."""
    folders = [dict(r) for r in await (await db.execute(
        "SELECT f.*, s.title AS title, s.year AS year FROM tv_folders f LEFT JOIN library_shows s ON s.tmdb_id = f.tmdb_id "
        "ORDER BY f.folder")).fetchall()]
    counts: dict[str, dict[str, int]] = {}
    problems: dict[str, list[dict]] = {}
    for r in await (await db.execute("SELECT file_path, folder, season, episodes, status, note FROM tv_files")).fetchall():
        c = counts.setdefault(r["folder"], {})
        c[r["status"]] = c.get(r["status"], 0) + 1
        if r["status"] not in ("ok", "extra"):
            problems.setdefault(r["folder"], []).append(
                {"file": r["file_path"], "season": r["season"], "episodes": json.loads(r["episodes"] or "[]"),
                 "status": r["status"], "note": r["note"]})
    known = {f["folder"] for f in folders}
    for folder in counts:
        if folder not in known:          # files no show was found for at all
            folders.append({"folder": folder, "tmdb_id": None, "source": "", "files": sum(counts[folder].values())})
    for f in folders:
        f["counts"] = counts.get(f["folder"], {})
        f["problems"] = sorted(problems.get(f["folder"], []), key=lambda p: (p["season"] or 0, p["episodes"]))[:200]
        f["disagree"] = bool(f.get("lumina_tmdb_id") and f.get("plex_tmdb_id") and f["lumina_tmdb_id"] != f["plex_tmdb_id"])
    folders.sort(key=lambda f: (not (f["disagree"] or not f.get("tmdb_id")), -sum(v for k, v in f["counts"].items() if k not in ("ok", "extra")),
                                f["folder"].lower()))
    total = {}
    for c in counts.values():
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    scanned = await (await db.execute("SELECT MAX(scanned_at) FROM tv_folders")).fetchone()
    return {"folders": folders, "total": total, "labels": STATUS_LABELS, "scanned_at": scanned[0] if scanned else None}


async def set_override(db, folder: str, tmdb_id: int | None) -> None:
    if tmdb_id:
        await db.execute("INSERT OR REPLACE INTO tv_folder_overrides (folder, tmdb_id, updated_at) VALUES (?, ?, ?)",
                         (folder, tmdb_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    else:
        await db.execute("DELETE FROM tv_folder_overrides WHERE folder = ?", (folder,))
    await db.commit()
