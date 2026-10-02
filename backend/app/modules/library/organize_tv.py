"""Fix TV shows on disk: every show folder to the naming rules (docs SERIALY — renamer).

A plan is computed per *show folder* (the first folder under the TV library, as the scan sees it):

- every episode file goes to ``{show folder}/{season folder}/{name}`` by the TV templates
  (core/naming.episode_paths); its subtitles, NFO and thumbnails (same file stem) go with it
- the numbers stay the user's: what Plex shows (it reads them from the names), else the file's own;
  an anime numbered through keeps the scan's mapping. Per show the user may choose numbering by the
  episode's name instead (``tv_naming`` "tmdb"): a file whose own name is surely TMDB's other episode
  (South Park S01: "S01E02 Posilovač 4000" = TMDB E03, a Czech airing order) takes that number; two files
  TMDB made one episode become its parts (" - pt1", " - pt2"). Episodes may trade numbers (E02 ↔ E03);
  one that would land on a number another episode keeps is left as it is and shown as unsure. Suggested
  (not chosen) for a show where the scan found such files.
- the episode's name: Plex's (clean, in the user's language), else the file's own (also when Plex shows
  another episode's name for that number), else TMDB's; a generic one ("Epizoda 3") is no name
- the show's name: TMDB's in the renamer's language — or Plex's when it is one of TMDB's names of the
  show (TMDB's Czech "Blue" for Bluey; Plex shows what the user picked or fixed)
- extras folders (Plex's Other, Featurettes …) move with the show unchanged; "Bonus", "Extra" … become
  "Other" (a name Plex knows)
- anything else in the folder keeps its place under the new show folder; nothing is overwritten,
  a conflict blocks the show, every batch can be undone (file_operations, as with movies)
"""

import json
import logging
import os
import re
import time
import uuid

from app.core import naming
from app.core.release_name import SUBTITLE_EXTS, VIDEO_EXTS
from app.modules.library import tv_inventory
from app.modules.library.episode_names import catalog, czech
from app.modules.library.organize import (
    OrganizeError,
    _conflicts,
    _ensure_dir,
    move_many,
    naming_settings,
    paths_moved,
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


async def numbering(db, tmdb_id: int) -> str | None:
    """The user's choice for the show, None when there is none."""
    row = await (await db.execute("SELECT numbering FROM tv_naming WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
    return row[0] if row and row[0] in NUMBERINGS else None


async def set_numbering(db, tmdb_id: int, value: str) -> None:
    if value not in NUMBERINGS:
        raise OrganizeError(f"Neznámé číslování: {value}")
    await db.execute("INSERT OR REPLACE INTO tv_naming (tmdb_id, numbering, updated_at) VALUES (?, ?, datetime('now'))",
                     (tmdb_id, value))
    await db.commit()


def _named(title: str) -> str:
    """The name, or "" for none or a generic one ("Epizoda 3", "Episode 25")."""
    return title if naming.episode_title(title or "") else ""


def show_title(details: dict, settings: dict, plex_title: str = "") -> str:
    tmdb = naming.pick_title(details.get("titles_by_lang") or {}, details.get("original_language", ""),
                             details.get("original_title", ""), settings["language"], settings["keep_local_original"]) \
        or details.get("title", "")
    known = {t.casefold() for t in [*(details.get("titles_by_lang") or {}).values(), details.get("original_title") or "",
                                    details.get("title") or "", *(details.get("titles") or [])] if t}
    return plex_title if plex_title and plex_title.casefold() in known else tmdb


_PT = re.compile(r"(?i)[ ._-]+(?:pt|part|cd)\s?(\d)\s*$")


def _pt(path: str) -> int:
    """The part a file already is ("… - pt2.mkv" → 2), 0 when none."""
    m = _PT.search(os.path.splitext(os.path.basename(path))[0])
    return int(m.group(1)) if m else 0


def episode_target(row: dict, mode: str, tmdb_titles: dict[tuple[int, int], str]) -> tuple[int, list[int], str, tuple]:
    """(season, episodes, the episode's name, order key) of an inventory file. row: tv_files row with
    "facts" parsed and "episodes" a list."""
    facts = row["facts"]
    plex = facts.get("plex")
    file_season, file_eps = facts.get("file") or [row["season"], row["episodes"]]
    scan = (row["season"], row["episodes"])                         # the scan's numbers (TMDB's, if it mapped)
    file_title = _named(facts.get("title") or "")
    plex_title = _named(plex[2]) if plex else ""
    tmdb_title = lambda s, e: _named(tmdb_titles.get((s, e), ""))  # noqa: E731
    order = (file_season or 0, file_eps[0] if file_eps else 0, _pt(row["file_path"]))

    if facts.get("manual"):                                        # the user's word: any numbering
        season, episode = facts["manual"][0], facts["manual"][1]
        if not order[2] and (m := re.search(r"(?<![\d.])(\d)\s*$", os.path.splitext(os.path.basename(row["file_path"]))[0])):
            order = (order[0], order[1], int(m.group(1)))      # "Nejhorší auto všech dob 2": its 2nd part
        # TMDB's Czech name, else the file's own (the user's language — TMDB may have only English ones)
        tmdb = tmdb_title(season, episode)
        # Plex's name only when Plex shows this episode (else it is the name of the number the file had)
        plex_here = plex_title if plex and (plex[0], plex[1]) == (season, episode) else ""
        return season, [episode], (tmdb if tmdb and (czech(tmdb) or not file_title) else file_title) or plex_here, order

    if mode == "tmdb":
        if facts.get("tmdb_episode") and facts.get("tmdb_sure"):
            # the file's own name is surely TMDB's episode there — TMDB's name is the file's
            season = facts["tmdb_season"] if facts.get("tmdb_season") is not None else row["season"]  # next one, specials
            return season, [facts["tmdb_episode"]], tmdb_title(season, facts["tmdb_episode"]) or file_title, order
        # an episode keeping its number keeps its name too (never TMDB's name of that number: South Park
        # "S02E04 Ikova obřízka" is not TMDB's E04 "Milovník slepic")
        return episode_target(row, "files", tmdb_titles)

    if facts.get("absolute"):
        season, episodes = scan[0], list(scan[1])                   # numbered through: TMDB's season split
    elif plex:
        season, episodes = plex[0], [plex[1]]
        if season == file_season and plex[1] in file_eps:
            episodes = list(file_eps)                               # a file of more episodes ("S07E21E22")
    else:
        season, episodes = file_season, list(file_eps)
    # Plex's name is another episode's: the file is named as TMDB's other one, names another part
    # ("Part I" / Plex "Part II"), or TMDB does not know the episode and the two names have nothing in common
    parts = (tv_inventory._part(file_title), tv_inventory._part(plex_title))
    other = file_title and plex_title and (
        row["status"] == "tmdb_other"
        or (None not in parts and parts[0] != parts[1])
        or (row["status"] == "not_in_tmdb" and not tv_inventory.same_episode(file_title, plex_title, strict=True)))
    if len(episodes) > 1:
        # a file of more episodes: the first one's name (Plex shows the last one's)
        title = tmdb_title(season, episodes[0]) or file_title or plex_title
    elif row["status"] == "tmdb_other" and file_title or other:
        title = file_title
    elif file_title and plex_title and czech(file_title) and not czech(plex_title) and plex_title.isascii() \
            and tv_inventory.title_score(file_title, plex_title) < tv_inventory.STRONG:
        title = file_title          # the file's Czech name before Plex's English one (TMDB without a Czech name)
    elif plex_title or file_title:
        title = plex_title or file_title
    elif (season, episodes[0]) == (scan[0], scan[1][0] if scan[1] else None) and row["status"] == "ok":
        title = tmdb_title(season, episodes[0])
    else:
        title = ""
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


_SUB_WORD = re.compile(r"(cz|cze|cs|sk|slo|en|eng)(?:tit|titulky|sub|subs)")
_SUB_LANG = {"cz": "cs", "cze": "cs", "cs": "cs", "sk": "sk", "slo": "sk", "en": "en", "eng": "en"}


def subtitle_rest(name: str, old_stem: str) -> str:
    """What a subtitle file keeps after the video's new stem: its language (".cs", also from "-CZtit"),
    forced and SDH (".en.sdh.srt") — Plex reads those."""
    ext = os.path.splitext(name)[1].lower()
    suffix = subtitle_suffix(name)
    words = [w for w in re.split(r"[.\s_\-\[\]()]+", os.path.splitext(name[len(old_stem):])[0].lower()) if w]
    if not suffix.startswith(".") or suffix.startswith(".forced"):
        lang = next((_SUB_LANG[m.group(1)] for w in words if (m := _SUB_WORD.fullmatch(w))), "")
        suffix = (f".{lang}" if lang else "") + suffix
    if any(w in ("sdh", "cc", "hi") for w in words):
        suffix += ".sdh"
    return suffix + ext


def _own_numbers(row: dict) -> tuple[int, list[int]]:
    """The numbers the episode has now — Plex's (what the user sees), else the file's."""
    plex = row["facts"].get("plex")
    file_season, file_eps = row["facts"].get("file") or [row["season"], row["episodes"]]
    if plex:
        return plex[0], (list(file_eps) if plex[0] == file_season and plex[1] in file_eps else [plex[1]])
    return file_season, list(file_eps)


def _gap(wanted: list, i: int, season: int, claims: dict, tmdb_titles: dict) -> bool:
    """Move the i-th episode to the free number of the season its own name fits best (a word in common:
    another translation) — only when that number is the clear best. Returns whether it moved."""
    row = wanted[i][0]
    name = row["facts"].get("title") or ""
    free = [k for k, t in tmdb_titles.items() if k[0] == season and k not in claims and t]
    scored = sorted(((tv_inventory.title_match(name, tmdb_titles[k]), k) for k in free), reverse=True)
    if not name or not scored or scored[0][0][0] <= 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return False
    key = scored[0][1]
    wanted[i] = (row, key[0], [key[1]], tmdb_titles[key], wanted[i][4])
    return True


def _settle(wanted: list[tuple], mode: str, tmdb_titles: dict) -> tuple[list[tuple], list[dict]]:
    """Numbering by names, checked for the whole show: no two episodes may end on one number — unless
    TMDB's episode there names them both (its two parts). Episodes trading numbers are fine. An episode
    surely belonging to a number another one keeps: the one staying goes to a free number its name fits
    (another translation of it — Pokémon S01E54 "Obtížný test" = TMDB E56 "Obtížná zkouška"); else the
    renumbered one goes back to its own number (listed as unsure)."""
    unsure: list[dict] = []
    wanted = list(wanted)
    while True:
        claims: dict[tuple[int, int], list[int]] = {}
        for i, (_row, s, e, _t, _o) in enumerate(wanted):
            claims.setdefault((s, e[0]), []).append(i)
        back: set[int] = set()
        moved = False
        for (s, n), idx in claims.items():
            if len({wanted[i][4] for i in idx}) < 2:
                continue                                   # one episode (or versions of it)
            title = tmdb_titles.get((s, n), "")
            names = [wanted[i][0]["facts"].get("title") or "" for i in idx]
            two_part = title and all(tv_inventory.title_score(nm, title) >= tv_inventory.STRONG for nm in names) \
                and all(tv_inventory.title_score(x, y) < tv_inventory.STRONG for j, x in enumerate(names) for y in names[j + 1:])
            if two_part:
                continue                                   # TMDB's two-part episode: both its names, each another
            stay = [i for i in idx if (wanted[i][1], wanted[i][2]) == _own_numbers(wanted[i][0])
                    and not wanted[i][0]["facts"].get("manual")]
            if len(stay) == 1 and len(idx) == 2 and _gap(wanted, stay[0], s, claims, tmdb_titles):
                moved = True
                break
            # the user's word stays; the others give way
            back |= {i for i in idx if (wanted[i][1], wanted[i][2]) != _own_numbers(wanted[i][0])
                     and not wanted[i][0]["facts"].get("manual")}
        if moved:
            continue
        if not back:
            return wanted, unsure
        for i in back:
            row = wanted[i][0]
            s, e, t, o = episode_target(row, "files", tmdb_titles)
            unsure.append({"file": row["file_path"], "why": f"podle názvu {naming.episode_label(wanted[i][1], wanted[i][2])}, "
                                                           f"to číslo ale zůstává jinému dílu — nechávám {naming.episode_label(s, e)}"})
            wanted[i] = (row, s, e, t, o)


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

    unsure: list[dict] = []
    if mode == "tmdb" or any(row["facts"].get("manual") for row in rows):
        wanted, unsure = _settle(wanted, mode, tmdb_titles)

    # TMDB's numbering may give one episode two files: TMDB's two-part episode = the user's two episodes
    by_target: dict[tuple, list] = {}
    for item in wanted:
        by_target.setdefault((item[1], tuple(item[2])), []).append(item)
    candidates: list[tuple[dict, str]] = []
    for (season, episodes), items in sorted(by_target.items()):
        items.sort(key=lambda it: it[4])
        # with the user's numbers two files of one number are versions of it, not parts — unless they are
        # named as parts already ("… - pt1", "… - pt2": what an earlier rename by TMDB's numbering made)
        parts = mode == "tmdb" or (len(items) > 1 and all(it[4][2] for it in items))
        # two copies of one episode (the same name) are versions of it, not its parts
        names = [it[0]["facts"].get("title") or "" for it in items]
        if not all(it[4][2] for it in items) and                 any(tv_inventory.title_score(x, y) >= tv_inventory.STRONG for j, x in enumerate(names) for y in names[j + 1:]):
            parts = False
        orders = sorted({it[4] for it in items}) if parts else []
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
    ours = {os.path.normcase(r["file_path"]) for r in rows}
    for row, dst in candidates:
        base, ext = os.path.splitext(dst)
        n = 2
        # taken by another file of this plan, or one already lying there (another version of the episode)
        while os.path.normcase(dst) in used or (os.path.exists(dst) and os.path.normcase(dst) not in ours):
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
    taken = {os.path.normcase(d) for d in renames.values()}
    for src, dst in renames.items():
        ops.append({"kind": "video", "src": src, "dst": dst})
        planned.add(src)
        d, old_stem, new_stem = os.path.dirname(src), os.path.splitext(os.path.basename(src))[0], os.path.splitext(os.path.basename(dst))[0]
        for e in _sidecars(entries(d), d, old_stem):
            path = os.path.join(d, e)
            if path in planned:
                continue
            ext = os.path.splitext(e)[1].lower()
            rest = subtitle_rest(e, old_stem) if ext in SUBTITLE_EXTS else e[len(old_stem):]
            target = os.path.join(os.path.dirname(dst), new_stem + rest)
            base, tail = target[:-len(ext)] if ext else target, ext
            n = 2
            while os.path.normcase(target) in taken:            # two subtitles of one language: ".2.srt"
                target, n = f"{base}.{n}{tail}", n + 1
            taken.add(os.path.normcase(target))
            ops.append({"kind": "sidecar", "src": path, "dst": target})
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
        # episodes whose number changes against Plex's (an anime numbered through, TMDB's numbering): Plex
        # makes them new items — the migration pairs them by file and gives back watched / date added
        "renumbered": sum(1 for row, s, e, _t, _o in wanted
                          if row["facts"].get("plex") and [s, e[0]] != row["facts"]["plex"][:2]),
        # what the numbering by names changes, and what it left as it was
        "renumber": [{"file": row["file_path"], "from": naming.episode_label(*_own_numbers(row)), "to": naming.episode_label(s, e),
                      "title": t} for row, s, e, t, _o in wanted if (s, e) != _own_numbers(row)] if mode == "tmdb" else [],
        "unsure": unsure,
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
            "V Plexu: Upravit seriál → Pokročilé → Pořadí dílů = TheTVDB. Nebo tady zvol „čísla podle názvu dílu“."]


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
    title = show_title(details, settings, f["plex_title"] or "")
    media = {r[0]: json.loads(r[1] or "{}") for r in await (await db.execute(
        "SELECT file_path, media FROM tv_media WHERE file_path IN (SELECT file_path FROM tv_files WHERE folder = ?)",
        (folder,))).fetchall()}
    tmdb_titles = {(r[0], r[1]): r[2] or "" for r in await (await db.execute(
        "SELECT season, episode, episode_title FROM library_episodes WHERE show_tmdb_id = ?", (tmdb_id,))).fetchall()}
    # TMDB's names: Czech, else English (Chernobyl has no Czech names of its episodes)
    for key, entry in (await catalog(client, db, tmdb_id)).items():
        tmdb_titles[key] = entry["cs"] if naming.episode_title(entry["cs"]) else (entry["en"] or entry["cs"])
    override = await (await db.execute("SELECT 1 FROM tv_folder_overrides WHERE folder = ?", (folder,))).fetchone()
    blocked = ""
    if not override and f["lumina_tmdb_id"] and f["plex_tmdb_id"] and f["lumina_tmdb_id"] != f["plex_tmdb_id"]:
        blocked = "Lumina a Plex se neshodnou, který seriál to je — rozhodni v Kontrole knihovny seriálů"
    chosen = await numbering(db, tmdb_id)
    # numbering by names suggested where the scan found files surely named as TMDB's other episodes
    sure = sum(1 for r in rows if r["facts"].get("tmdb_sure"))
    mode = chosen or ("tmdb" if sure else "files")
    show = {"tmdb_id": tmdb_id, "title": title, "year": details.get("year")}
    plan = plan_folder(rows, show, media, tmdb_titles, root, settings, mode, blocked)
    if chosen is None and mode == "tmdb" and not plan["renumber"]:
        mode = "files"                                     # nothing sure to renumber: no suggestion
        plan = plan_folder(rows, show, media, tmdb_titles, root, settings, mode, blocked)
    plan["suggested"] = chosen is None and mode == "tmdb"
    plan["sure_names"] = sure
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
    done = plan["ops"]
    move_many([(op["src"], op["dst"]) for op in done])          # episodes trading numbers wait on a temporary name
    for op in done:
        await db.execute("INSERT INTO file_operations (batch_id, movie_id, src, dst) VALUES (?, NULL, ?, ?)",
                         (batch_id, op["src"], op["dst"]))
    await paths_moved(db, [(op["src"], op["dst"]) for op in done if op["kind"] == "video"])
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
