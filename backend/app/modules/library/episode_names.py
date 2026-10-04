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


async def catalog(client, db, tmdb_id: int, max_age_s: float = CATALOG_DAYS * 86400) -> dict[tuple[int, int], dict]:
    """(season, episode) → {cs, en, runtime, air} of every TMDB episode of the show, specials too.
    ``max_age_s``: older than this is read again (a new episode gets its name shortly before it airs)."""
    rows = await (await db.execute("SELECT season, episode, title_cs, title_en, runtime, air_date, fetched_at "
                                   "FROM tmdb_episodes WHERE show_tmdb_id = ?", (tmdb_id,))).fetchall()
    if rows and time.time() - min(r[6] for r in rows) < max_age_s:
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
    """Current path → the file's first path in Lumina's journal (before any rename by Lumina). rows: (src, dst)
    or (src, dst, batch) in the journal's order; a batch moves its files at once (two files trading places
    within one batch must not take each other's past)."""
    back: dict[str, str] = {}
    batch: list[tuple[str, str]] = []
    current = object()

    def flush():
        moved = [(src, dst, back.get(src, src)) for src, dst in batch]
        for src, _dst, _o in moved:
            back.pop(src, None)
        for _src, dst, origin in moved:
            back[dst] = origin
        batch.clear()

    for row in rows:
        src, dst = row[0], row[1]
        key = row[2] if len(row) > 2 else None
        if key != current or key is None:
            flush()
            current = key
        batch.append((src, dst))
    flush()
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


_LEAD_NUMBER = re.compile(r"(?i)^\s*(?:e|ep|díl|dil)?\s*\d{1,3}\s*(?:-|\.)\s+(?=\D)")
_SE_AT = re.compile(r"(?i)\bs\d{1,2}\s?e\d{1,3}(?:\s?-?\s?e\d{1,3})*|\b\d{1,2}x\d{2,3}\b")
_TAGS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_JUNK = {"cz", "sk", "en", "eng", "cze", "czech", "dab", "dabing", "dabbing", "tit", "titulky", "multi", "aac", "ac3",
         "dts", "xvid", "divx", "dvd", "dvdrip", "bdrip", "brrip", "bluray", "webrip", "web", "dl", "webdl", "hdtv",
         "hevc", "avc", "x264", "x265", "h264", "h265", "fullhd", "full", "hd", "fhd", "uhd", "sd", "sdtv", "remux",
         "hdr", "dv", "upscale", "aiupscale", "proper", "repack", "internal", "komplet", "mkv", "mp4", "avi", "ddp5",
         "dd5", "atmos", "amzn", "nf", "cr", "a", "by", "web-dl", "vostfr", "dub", "subs", "multisubs", "multiaudios",
         "bdrip", "10bit", "8bit"}
_TOKEN_JUNK = re.compile(r"(?i)^(?:-\S+|repack\d*|proper\d*|\d{3,4}p|\d{1,2}bit|(?:19|20)\d{2}|5 ?1|[a-z]{2}\+[a-z]{2}(?:\+[a-z]{2})?)$")


def _after_show(stem: str, show_names) -> str | None:
    """The rest of a name without an episode number after the show's name ("Top Gear - Polární speciál
    (2007)" → " - Polární speciál"), None when it does not start with the show's name."""
    words = stem.split()
    plain = [tv_inventory.normalized(w) for w in words]
    got = [i for i, w in enumerate(plain) if w]
    for show in sorted(show_names or [], key=len, reverse=True):
        want = [w for w in (tv_inventory.normalized(x) for x in show.split()) if w]
        if want and len(got) > len(want) and [plain[i] for i in got[:len(want)]] == want:
            return " - " + " ".join(words[got[len(want)]:])
    return None


def release_titles(name: str, show_names=()) -> list[str]:
    """Episode names a release's file name may carry after its number, cleanest first: "Městečko South Park
    S01E02-13 1997 CZ dab 1080p - Posilovač 4000.mkv" → ["Posilovač 4000"], "South.Park.S01E02.Posilovac.4000
    .DVDRip.XviD.CZ.ENG.mkv" → ["Posilovac 4000"]; none for "South Park S01E02 CZ Dabing FullHD+ by lfiq"."""
    stem = re.sub(r"(?i)\.(mkv|mp4|avi|m4v|ts|wmv)$", "", name or "")
    stem = _TAGS.sub(" ", stem).replace("_", " ").replace("+", " + ")
    stem = re.sub(r"(?<=\w)\.(?=\w)", " ", stem)
    m = _SE_AT.search(stem)
    lead = _LEAD_NUMBER.match(stem)
    if m:
        after = re.sub(r"^\s*-\s*\d{1,3}\b", "", stem[m.end():])      # "S01E02-13": the 2nd of 13
    elif lead:
        after = stem[lead.end():]                                      # a pack's "06 - Prázdný rám" (its folder: the show)
    else:
        # no number: a special named after the show's name ("Top Gear - Polární speciál")
        after = _after_show(stem, show_names)
        if after is None:
            return []
    after = re.sub(r"(?<=\S)-[A-Za-z0-9]+\s*$", "", after)             # "x264-AMB3R": the release group
    out = []
    for seg in re.split(r"\s+-\s+|\s*\.\s+|^\s*[-.]\s*", after):
        words, skip = [], False
        first = (seg.split() or [""])[0].lower().strip(".,;:!?")
        if first in _JUNK or _TOKEN_JUNK.match(first) or re.match(r"(?i)^[xh]\.?26[45]-", first):
            continue                                                   # a segment of tags ("WEB H264-GHOSTS")
        for w in seg.split():
            if skip:
                skip = False
                continue
            low = w.lower().strip(".,;:!?")
            if low == "by":
                skip = True                                            # "by lfiq": the uploader
                continue
            if low in _JUNK or _TOKEN_JUNK.match(low) or w == "+":
                if words:
                    break                                              # the name ends where the tags begin
                continue
            words.append(w)
        title = " ".join(words).strip(" -.")
        if len(re.sub(r"[^A-Za-zÀ-ž]", "", title)) >= 3 and title not in out:
            out.append(title)
    return out


def release_episode(name: str, cat: dict[tuple[int, int], dict], season: int | None, show_names=()
                    ) -> tuple[tuple[int, int], bool, str] | None:
    """(the TMDB episode a release's own episode name is, sure, the name) — None without a name TMDB knows.
    ``show_names``: a name without an episode number is read after the show's name (specials)."""
    unsure = None
    for title in release_titles(name, show_names):
        key, sure = best(title, cat, season)
        if key and sure and len(tv_inventory.normalized(title).split()) == 1 \
                and tv_inventory.normalized(title) not in {tv_inventory.normalized(t) for t in _names(cat[key])}:
            sure = False                 # one word ("speciál") inside a longer name ("Vietnamský speciál"): any
        if key and sure:
            return key, True, title
        if key and not unsure:
            unsure = (key, False, title)                    # "speciál" fits many — a surer name may follow
    return unsure


def czech(text: str) -> bool:
    return bool(re.search(r"[ěščřžýáíéůúťďňĚŠČŘŽÝÁÍÉŮÚŤĎŇ]", text or ""))


async def release_checker(tmdb_key: str, tmdb_id: int, season: int | None, show_names=()):
    """(file name → ``release_episode`` of the show, the catalog) for a search; (None, {}) when TMDB has
    nothing (the numbers decide alone)."""
    from app.db import get_db

    client, db = _client(tmdb_key), await get_db()
    try:
        cat = await catalog(client, db, tmdb_id)
    except Exception:  # noqa: BLE001 — TMDB down, no catalog yet
        cat = {}
    finally:
        await client.close()
        await db.close()
    if not cat:
        return None, {}
    return (lambda name: release_episode(name, cat, season, show_names)), cat


def _client(tmdb_key: str):
    from app.clients.tmdb import TMDBClient
    return TMDBClient(tmdb_key)


SPLIT_GAP_DAYS = 60       # a break this long within a TMDB season: the rest is the next season elsewhere


def other_numbers(cat: dict[tuple[int, int], dict], season: int, episode: int, anime: bool = False) -> dict:
    """How the rest of the world may number TMDB's episode: {"alt": [[season, episode]], "absolute": n}.

    - a season TMDB keeps whole, aired in parts with a long break ("cours"): TVDB, the uploaders and Plex
      make the part after the break the next season — Solo Leveling S01E13 (Jan 2025, after Mar 2024) is
      "S02E01"; only when TMDB has no such season itself
    - anime: the episode's number counted through the seasons ("Naruto 120"); TMDB counting through already
      (Naruto S03E120) keeps its number"""
    out: dict = {"alt": [], "absolute": None}
    numbered = sorted(k for k in cat if k[0] > 0)
    if (season, episode) not in cat:
        return out
    here = [k for k in numbered if k[0] == season]
    blocks, last = [[]], None
    for k in here:
        air = cat[k].get("air") or ""
        if last and air and (_days(last, air) or 0) > SPLIT_GAP_DAYS and blocks[-1]:
            blocks.append([])
        blocks[-1].append(k)
        last = air or last
    seasons = {k[0] for k in numbered}
    for i, block in enumerate(blocks):
        if (season, episode) in block and i > 0 and season + i not in seasons:
            out["alt"].append([season + i, block.index((season, episode)) + 1])
    if anime:
        position = numbered.index((season, episode)) + 1
        out["absolute"] = episode if episode > len(here) else position
        if out["absolute"] == episode and season == 1:
            out["absolute"] = None                     # the first season: its numbers are absolute already
    return out


def _days(a: str, b: str) -> int | None:
    from datetime import date
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except ValueError:
        return None
