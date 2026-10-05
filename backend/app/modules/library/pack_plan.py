"""The plan of a show pack — every file of a torrent placed at once, as soon as its list is known (a magnet's
metadata: seconds after it is added), before anything downloads.

One file at a time (as it completes) can not see the others: an old show's pack splits a two-part episode TMDB
keeps as one ("Vítej v Koreji" + "Vítej v Koreji.II"), numbers through the seasons or by the airing order, and the
second part took the next episode's place (M*A*S*H). With the whole list the plan sees it:

1. each video → its episode: the episode name against TMDB's (the season's first; two-part episodes; letters with
   dots / stars alike), else its numbers (SxxExx, the folders, a bare number, an absolute one) — how sure: name >
   number > order
2. two files on one episode: the parts of a two-part episode TMDB keeps as one ("- pt1" / "- pt2"), else the surer
   one wins and the other is unknown
3. the size against the others: a file much bigger than its episode should be (a film in the pack) or tiny (a bonus)
   is no episode unless its name says it
4. bonuses (a folder "Extras", "Bonus", "Featurettes", "… -trailer") go to the show's Plex extras; a "sample" is not
   downloaded at all; an episode the user has (with Czech / Slovak sound) is not downloaded either

The import follows the plan (``plan_for``); the real length (MediaInfo) is checked there — a "1 episode" of 90
minutes goes to "Nezařazeno" rather than to a wrong place.
"""

import os
import re
import statistics

from app.core.release_name import SUBTITLE_EXTS, VIDEO_EXTS
from app.modules.library import episode_names, tv_inventory

SURE = {"name": 3, "number": 2, "order": 1}
_SAMPLE = re.compile(r"(?i)(?:^|[\s._\-\[(])sample(?:$|[\s._\-\])])")
_EXTRA_WORDS = {"trailer": "Trailers", "teaser": "Trailers", "making of": "Behind The Scenes", "behind the scenes": "Behind The Scenes",
                "deleted": "Deleted Scenes", "vystřižen": "Deleted Scenes", "interview": "Interviews", "rozhovor": "Interviews",
                "featurette": "Featurettes", "blooper": "Other", "gag reel": "Other", "bonus": "Other", "extra": "Other"}
_PLEX_DIRS = {d.lower(): d for d in ("Behind The Scenes", "Deleted Scenes", "Featurettes", "Interviews", "Scenes", "Shorts",
                                     "Trailers", "Other")}


def extra_folder(rel: str) -> str | None:
    """The Plex extras folder of a pack's file that is a bonus ("Bonus/Making of.mkv" → "Behind The Scenes"), None
    for an episode."""
    parts = [p for p in rel.replace("\\", "/").split("/")]
    low = rel.lower()
    for part in parts[:-1]:
        name = part.strip().lower()
        if name in _PLEX_DIRS:
            return _PLEX_DIRS[name]
        if name in tv_inventory.EXTRA_DIRS_OTHER_NAMES:
            return next((d for w, d in _EXTRA_WORDS.items() if w in low), "Other")
    stem = os.path.splitext(parts[-1])[0].lower()
    for suffix in tv_inventory.EXTRA_SUFFIXES:
        if stem.endswith(suffix):
            return {"-behindthescenes": "Behind The Scenes", "-deleted": "Deleted Scenes", "-featurette": "Featurettes",
                    "-interview": "Interviews", "-scene": "Scenes", "-short": "Shorts", "-trailer": "Trailers"}.get(suffix, "Other")
    return None


def _numbers(rel: str, pack_season: int | None) -> tuple[int | None, list[int]]:
    from app.modules.library.imports import pack_episodes
    return pack_episodes(rel.replace("\\", "/").replace("/", os.sep), pack_season)


def plan_pack(files: list[dict], cat: dict, owned_local: set[tuple[int, int]] = frozenset(), show_names=(),
              pack_season: int | None = None) -> dict:
    """files: qBittorrent's [{index, name (the path in the torrent), size}]; cat: TMDB's episodes (episode_names
    .catalog); owned_local: episodes the user has with Czech / Slovak sound (not downloaded again).
    → {"files": {path: entry}, "summary": {...}}; entry {"kind": episode | extra | sample | unknown | owned |
    other, "season", "episodes", "part", "how", "why", "index", "extra": Plex folder}."""
    regular = {k: v for k, v in cat.items() if k[0] > 0}
    runtimes = sorted(v.get("runtime") or 0 for v in regular.values() if v.get("runtime"))
    typical_rt = runtimes[len(runtimes) // 2] if runtimes else 0
    order = sorted(regular)
    entries: dict[str, dict] = {}
    videos = [f for f in files if os.path.splitext(f["name"])[1].lower() in VIDEO_EXTS]
    for f in files:
        if f not in videos and os.path.splitext(f["name"])[1].lower() not in SUBTITLE_EXTS:
            entries[f["name"]] = {"kind": "other", "index": f["index"]}

    for f in videos:
        rel, base = f["name"], os.path.basename(f["name"].replace("\\", "/"))
        e = {"index": f["index"], "rel": rel, "size": f.get("size") or 0, "kind": "unknown", "season": None, "episodes": [],
             "part": None, "how": "", "why": ""}
        entries[rel] = e
        if _SAMPLE.search(os.path.splitext(base)[0]):
            e.update(kind="sample", why="ukázka (sample)")
            continue
        folder = extra_folder(rel)
        season, eps = _numbers(rel, pack_season)
        hit = episode_names.release_episode(base, cat, season, show_names) if cat else None
        if hit and hit[1]:
            (s, ep), _sure, title = hit
            e.update(kind="episode", season=s, episodes=[ep], how="name", why=f"podle názvu „{title}“")
            e["part"] = _part(base, cat, s, ep, show_names)
        elif folder:
            e.update(kind="extra", extra=folder, why=f"bonus ({folder})")
            continue
        elif season is not None and eps:
            e.update(kind="episode", season=season, episodes=list(eps), how="number", why="podle čísla")
        elif cat:
            titles = episode_names.release_titles(base, show_names)
            absolute = episode_names.absolute_in_name(base)
            m = re.match(r"(?i)^\s*(?:e|ep|díl|dil)?\s*(\d{1,3})(?!\d)", os.path.splitext(base)[0])
            n = absolute or (int(m.group(1)) if m and not titles else None)
            if n and 0 < n <= len(order):
                e.update(kind="episode", season=order[n - 1][0], episodes=[order[n - 1][1]], how="order",
                         why=f"{n}. díl seriálu v pořadí TMDB")
        if folder and e["kind"] == "episode" and e["how"] != "name":
            e.update(kind="extra", extra=folder, season=None, episodes=[], why=f"bonus ({folder})")

    eps_files = [e for e in entries.values() if e.get("kind") == "episode"]
    _sizes(eps_files, cat, typical_rt)
    _conflicts(eps_files, cat, typical_rt)
    for e in eps_files:
        if e["kind"] == "episode" and all((e["season"], ep) in owned_local for ep in e["episodes"]) and not e["part"]:
            e.update(kind="owned", why=f"S{e['season']:02d}E{e['episodes'][0]:02d} už máš (s CZ/SK)")

    summary: dict[str, int] = {}
    for e in entries.values():
        summary[e["kind"]] = summary.get(e["kind"], 0) + 1
    summary["episodes"] = len({(e["season"], ep) for e in entries.values() if e.get("kind") == "episode" for ep in e["episodes"]})
    summary["parts"] = sum(1 for e in entries.values() if e.get("kind") == "episode" and (e.get("part") or 0) >= 2)
    summary["tmdb"] = len(regular)
    summary["missing"] = len([k for k in regular if k not in {(e["season"], ep) for e in entries.values()
                                                                if e.get("kind") in ("episode", "owned") for ep in e["episodes"]}])
    return {"files": entries, "summary": summary}


def _part(base: str, cat: dict, season: int, episode: int, show_names) -> int | None:
    for title in episode_names.release_titles(base, show_names):
        key, part = episode_names.part_episode(title, cat, season)
        if key == (season, episode) and part:
            return part
    return None


def _sizes(eps: list[dict], cat: dict, typical_rt: int) -> None:
    """A file far bigger than its episode should be (a film in the pack) or tiny (a bonus), placed only by its
    number: no episode. Its episode's runtime is the yardstick (a double-length episode is twice as big)."""
    per_minute = [e["size"] / (cat.get((e["season"], e["episodes"][0]), {}).get("runtime") or typical_rt or 1)
                  for e in eps if e["size"] and len(e["episodes"]) == 1]
    if len(per_minute) < 4:
        return
    usual = statistics.median(per_minute)
    for e in eps:
        if e["how"] == "name" or not e["size"]:
            continue
        rt = sum(cat.get((e["season"], ep), {}).get("runtime") or typical_rt or 0 for ep in e["episodes"]) or typical_rt
        ratio = e["size"] / (usual * rt) if rt else 1
        if ratio > 2.5:
            e.update(kind="unknown", why=f"na díl moc velký ({ratio:.1f}×) — film / něco jiného?")
        elif ratio < 0.25:
            e.update(kind="extra", extra="Other", season=None, episodes=[], why="na díl moc malý — bonus")


def _conflicts(eps: list[dict], cat: dict, typical_rt: int) -> None:
    """Two files on one episode: a two-part episode TMDB keeps as one (parts "- pt1" / "- pt2" — the episode is
    long, or a file says its part), else the surer file wins, the other is unknown."""
    by_key: dict[tuple, list[dict]] = {}
    for e in eps:
        if e["kind"] == "episode":
            for ep in e["episodes"]:
                by_key.setdefault((e["season"], ep), []).append(e)
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        rt = cat.get(key, {}).get("runtime") or 0
        double = (typical_rt and rt >= 1.5 * typical_rt) or any(g.get("part") for g in group)
        if double and len(group) <= 4:
            for i, g in enumerate(sorted(group, key=lambda g: (g.get("part") or 1, g["index"])), 1):
                g["part"] = i
            continue
        group.sort(key=lambda g: (SURE.get(g["how"], 0), -abs(len(g["episodes"]) - 1)), reverse=True)
        for loser in group[1:]:
            loser.update(kind="unknown", why=f"S{key[0]:02d}E{key[1]:02d} má už „{os.path.basename(group[0].get('rel', '')) or 'jiný soubor'}“")


def plan_for(plan: dict | None, rel: str) -> dict | None:
    """The plan's entry of a pack's file (its path in the torrent)."""
    return ((plan or {}).get("files") or {}).get(rel)


def skip_indexes(plan: dict) -> list[int]:
    """Not downloaded: samples, episodes the user has (with their subtitles)."""
    files = plan.get("files") or {}
    skip_stems = {os.path.splitext(rel)[0] for rel, e in files.items() if e.get("kind") in ("sample", "owned")}
    out = [e["index"] for e in files.values() if e.get("kind") in ("sample", "owned")]
    out += [e["index"] for rel, e in files.items() if e.get("kind") == "other" and
            any(rel.startswith(stem + ".") for stem in skip_stems)]
    return sorted(set(out))


def first_indexes(plan: dict, n: int = 2) -> tuple[list[int], list[int]]:
    """(the first n wanted episodes, the rest of their season) — qBittorrent's priorities 7 / 6."""
    eps = sorted((e for e in (plan.get("files") or {}).values() if e.get("kind") == "episode"),
                 key=lambda e: (e["season"], e["episodes"][0], e.get("part") or 0))
    if not eps:
        return [], []
    top = [e["index"] for e in eps[:n]]
    season = eps[0]["season"]
    return top, [e["index"] for e in eps if e["season"] == season and e["index"] not in top]


async def make_plan(tmdb_id: int, files: list[dict], pack_season: int | None, replace_owned: bool, title: str) -> dict | None:
    """The plan of a pack being downloaded: TMDB's episodes of the show, the episodes owned with Czech / Slovak
    sound (not downloaded again — unless they are to be replaced)."""
    from app.config import get_effective_settings
    from app.db import get_db
    cfg = await get_effective_settings()
    _check, cat = await episode_names.release_checker(cfg.get("tmdb_api_key", ""), tmdb_id, None)
    if not cat:
        return None
    owned_local: set[tuple[int, int]] = set()
    if not replace_owned:
        db = await get_db()
        try:
            for s, e, lang in await (await db.execute(
                    "SELECT season, episode, language FROM library_episodes WHERE show_tmdb_id = ? AND has_file = 1",
                    (tmdb_id,))).fetchall():
                if {"CS", "SK"} & set((lang or "").upper().split(",")):
                    owned_local.add((s, e))
        finally:
            await db.close()
    return plan_pack([{"index": f["index"], "name": f["name"].replace("\\", "/"), "size": f.get("size") or 0} for f in files],
                     cat, owned_local, [title], pack_season)
