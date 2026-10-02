"""Which episode a file is by its name — the file's own name against TMDB's names of the show's episodes.

The user's files often follow another order than TMDB (a Czech airing order, another season split,
specials numbered in a season), but the episode's name in the file is mostly right. This module answers
"which TMDB episode is this name", surely or not at all:

- TMDB's names of every episode in Czech and English, specials (S00) included, with runtime — a catalog
  per show kept in ``tmdb_episodes`` (refreshed after a week)
- a name matches when most of the shorter name's words are in the other (a small spelling difference in a
  longer word is fine: "Ikova" / "Ikeova"); names of other parts never ("Part I" / "Part II"); TMDB's
  generic names ("5. epizoda") are no name
- the file's season first, then the seasons next to it, then any season or the specials — a far one only
  when the length fits too; a match is sure only when no other episode is as good
- the file's own name: from its first name in Lumina's journal (a rename must not become the truth), junk
  is no name (release tags every file of the show carries: "DVB-C", "sdTV", "FULL HD"; broken encoding)
"""

import json
import os
import re
import time

from app.core import naming
from app.modules.library import tv_inventory

CATALOG_DAYS = 7
NEAR_RUNTIME = 0.35            # a far match: the file's length within 35 % of TMDB's runtime

TMDB_EPISODES = """
CREATE TABLE IF NOT EXISTS tmdb_episodes (
    show_tmdb_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    episode INTEGER NOT NULL,
    title_cs TEXT DEFAULT '',
    title_en TEXT DEFAULT '',
    runtime INTEGER DEFAULT 0,
    air_date TEXT DEFAULT '',
    fetched_at REAL NOT NULL,
    PRIMARY KEY (show_tmdb_id, season, episode)
);
"""

_MOJIBAKE = re.compile(r"[ÃÅÄ][\x80-\xbf¡-¿]|Ã|Å¾|Ä")


async def catalog(client, db, tmdb_id: int) -> dict[tuple[int, int], dict]:
    """(season, episode) → {cs, en, runtime, air} of every TMDB episode of the show, specials too."""
    rows = await (await db.execute("SELECT season, episode, title_cs, title_en, runtime, air_date, fetched_at "
                                   "FROM tmdb_episodes WHERE show_tmdb_id = ?", (tmdb_id,))).fetchall()
    if rows and time.time() - min(r[6] for r in rows) < CATALOG_DAYS * 86400:
        return {(r[0], r[1]): {"cs": r[2] or "", "en": r[3] or "", "runtime": r[4] or 0, "air": r[5] or ""} for r in rows}
    try:
        full = await client.get_tv_full(tmdb_id)
        seasons = [0] + [s["season_number"] for s in full.get("seasons", [])]
        out: dict[tuple[int, int], dict] = {}
        for sn in seasons:
            try:
                cs = await client.get_season(tmdb_id, sn)
            except Exception:  # noqa: BLE001 — a show without specials
                continue
            en = {e["episode_number"]: e.get("name") or "" for e in await client.get_season(tmdb_id, sn, language="en-US")}
            for e in cs:
                out[(sn, e["episode_number"])] = {"cs": e.get("name") or "", "en": en.get(e["episode_number"], ""),
                                                   "runtime": e.get("runtime") or 0, "air": e.get("air_date") or ""}
    except Exception:  # noqa: BLE001 — TMDB down: what we have
        return {(r[0], r[1]): {"cs": r[2] or "", "en": r[3] or "", "runtime": r[4] or 0, "air": r[5] or ""} for r in rows}
    now = time.time()
    await db.execute("DELETE FROM tmdb_episodes WHERE show_tmdb_id = ?", (tmdb_id,))
    await db.executemany("INSERT INTO tmdb_episodes (show_tmdb_id, season, episode, title_cs, title_en, runtime, air_date, fetched_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         [(tmdb_id, s, e, v["cs"], v["en"], v["runtime"], v["air"], now) for (s, e), v in out.items()])
    await db.commit()
    return out


def _names(entry: dict) -> list[str]:
    return [t for t in (entry.get("cs"), entry.get("en")) if t and naming.episode_title(t)]


def best(name: str, cat: dict[tuple[int, int], dict], season: int | None, duration_s: int = 0
         ) -> tuple[tuple[int, int] | None, bool]:
    """(the TMDB episode the name is, sure). Sure: a real match (``STRONG``), no other episode as good;
    the file's season first, then the ones next to it, then any (a far one with a fitting length)."""
    if not name or not naming.episode_title(name):
        return None, False
    norm = tv_inventory.normalized(name)
    scored = []
    for key, entry in cat.items():
        names = _names(entry)
        if not names:
            continue
        score, common = max(tv_inventory.title_match(name, t) for t in names)
        if score < tv_inventory.STRONG:
            continue
        exact = any(tv_inventory.normalized(t) == norm for t in names)
        tier = 0 if key[0] == season else 1 if season is not None and abs(key[0] - season) == 1 and key[0] > 0 else 2
        scored.append(((score, common, exact), tier, key, entry))
    if not scored:
        # no part in the name, the episodes are parts of it ("Space Race" → "Space Race: Part I / II"): which one is
        # not known — the file keeps its number (``tv_inventory.fits``)
        return None, False
    for tier in (0, 1, 2):
        here = sorted((s for s in scored if s[1] <= tier), key=lambda s: (s[0], -s[1]), reverse=True)
        if not here:
            continue
        top = here[0]
        if len(here) > 1 and here[1][0] == top[0]:
            return top[2], False                               # two equally good: not sure
        if top[1] == 2 and duration_s and top[3].get("runtime"):
            if abs(duration_s - top[3]["runtime"] * 60) > NEAR_RUNTIME * top[3]["runtime"] * 60:
                continue                                       # a far episode of another length
        return top[2], True
    return None, False


def origins(rows) -> dict[str, str]:
    """Current path → the file's first path in Lumina's journal (before any rename by Lumina)."""
    back: dict[str, str] = {}
    for src, dst in rows:
        back[dst] = back.pop(src, src)
    return back


def own_titles(files: list[tuple[str, str]], title_of) -> dict[str, str]:
    """path → the file's own episode name, read from its original name. Junk is no name: a "name" several
    files of the show carry ("DVB-C", "sdTV", "FULL HD", "INTERNAL" — release tags) and broken encoding."""
    raw = {path: (title_of(os.path.basename(origin)) or "") for path, origin in files}
    counts: dict[str, int] = {}
    for t in raw.values():
        if t:
            counts[tv_inventory.normalized(t)] = counts.get(tv_inventory.normalized(t), 0) + 1
    out = {}
    for path, t in raw.items():
        if not t or _MOJIBAKE.search(t) or counts.get(tv_inventory.normalized(t), 0) >= 3:
            out[path] = ""
        else:
            out[path] = t
    return out


def czech(text: str) -> bool:
    return bool(re.search(r"[ěščřžýáíéůúťďňĚŠČŘŽÝÁÍÉŮÚŤĎŇ]", text or ""))
