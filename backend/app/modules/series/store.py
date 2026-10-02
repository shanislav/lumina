"""TV shows: the user's settings per show (on top of the defaults) and the state of each episode.

Settings of a show: quality profile (kind tv), language mode, torrents yes/no, watch for new episodes.
NULL in a column = the default (setting "series_defaults"), so changing a default changes every show
that has no own value.

Language modes (docs SERIALY):
    local_or_temp  the best language available now; an episode without CZ/SK waits for a dub ("temp")
    local_only     only CZ/SK — nothing else is downloaded
    original       the dub is not looked for
"""

import json
from datetime import date

from app.db import get_db

SERIES_SETTINGS = """
CREATE TABLE IF NOT EXISTS series_settings (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    poster_url TEXT,
    profile_id INTEGER,
    lang_mode TEXT,
    torrent INTEGER,
    monitor INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

LANG_MODES = ("local_or_temp", "local_only", "original")
DEFAULTS = {"profile_id": None, "lang_mode": "local_or_temp", "torrent": True, "monitor": False}
FIELDS = ("profile_id", "lang_mode", "torrent", "monitor")


def _lang(code: str) -> str:
    code = (code or "").strip().lower()
    return {"cz": "cs", "cze": "cs", "ces": "cs", "slo": "sk", "slk": "sk", "eng": "en"}.get(code, code)


def languages_of(text: str) -> list[str]:
    """The library's "CZ,EN" (scan, by name) or "CS,EN" (import, by the file) → ["cs", "en"]."""
    return [l for l in (_lang(x) for x in (text or "").split(",")) if l]


async def get_defaults() -> dict:
    from app.db import get_all_settings
    raw = (await get_all_settings()).get("series_defaults") or ""
    try:
        saved = json.loads(raw) if raw else {}
    except ValueError:
        saved = {}
    return {k: saved.get(k, v) for k, v in DEFAULTS.items()}


async def save_defaults(values: dict) -> dict:
    from app.db import set_settings
    current = await get_defaults()
    for key in FIELDS:
        if key in values:
            current[key] = values[key]
    if current["lang_mode"] not in LANG_MODES:
        current["lang_mode"] = DEFAULTS["lang_mode"]
    await set_settings({"series_defaults": json.dumps(current)})
    return current


async def get_settings(tmdb_id: int) -> dict:
    """{"own": the show's own values (None = default), "effective": what applies, "defaults": …}."""
    defaults = await get_defaults()
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM series_settings WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
    finally:
        await db.close()
    own = {k: (row[k] if row else None) for k in FIELDS}
    for key in ("torrent", "monitor"):
        if own[key] is not None:
            own[key] = bool(own[key])
    effective = {k: own[k] if own[k] is not None else defaults[k] for k in FIELDS}
    return {"own": own, "effective": effective, "defaults": defaults}


async def save_settings(tmdb_id: int, values: dict, show: dict | None = None) -> dict:
    """values: only the keys to change; None = back to the default."""
    current = (await get_settings(tmdb_id))["own"]
    for key in FIELDS:
        if key in values:
            current[key] = values[key]
    if current["lang_mode"] is not None and current["lang_mode"] not in LANG_MODES:
        current["lang_mode"] = None
    show = show or {}
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO series_settings (tmdb_id, title, year, poster_url, profile_id, lang_mode, torrent, monitor, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(tmdb_id) DO UPDATE SET "
            "title = CASE WHEN excluded.title != '' THEN excluded.title ELSE series_settings.title END, "
            "year = CASE WHEN excluded.year != '' THEN excluded.year ELSE series_settings.year END, "
            "poster_url = COALESCE(excluded.poster_url, series_settings.poster_url), "
            "profile_id = excluded.profile_id, lang_mode = excluded.lang_mode, torrent = excluded.torrent, "
            "monitor = excluded.monitor, updated_at = excluded.updated_at",
            (tmdb_id, show.get("title") or "", str(show.get("year") or ""), show.get("poster_url"),
             current["profile_id"], current["lang_mode"],
             None if current["torrent"] is None else int(current["torrent"]),
             None if current["monitor"] is None else int(current["monitor"])))
        await db.commit()
    finally:
        await db.close()
    return await get_settings(tmdb_id)


async def owned_episodes(tmdb_id: int) -> dict[tuple[int, int], dict]:
    """What the library has of the show: (season, episode) → file. Read from the library module's
    table — it may be switched off, then nothing is owned."""
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT id, season, episode, filename, file_path, file_size, quality, language FROM library_episodes "
            "WHERE show_tmdb_id = ? AND has_file = 1", (tmdb_id,))).fetchall()
    except Exception:
        rows = []
    finally:
        await db.close()
    return {(r["season"], r["episode"]): {"id": r["id"], "filename": r["filename"], "file_path": r["file_path"],
                                          "size": r["file_size"] or 0, "quality": r["quality"] or "",
                                          "languages": languages_of(r["language"])} for r in rows}


def episode_state(ep: dict, owned: dict | None, lang_mode: str, local_langs: list[str], today: date) -> str:
    """owned | temp (owned, but not in CZ/SK — waits for the dub) | missing | upcoming."""
    if owned:
        langs = owned.get("languages") or []
        if lang_mode != "original" and langs and not any(l in local_langs for l in langs):
            return "temp"
        return "owned"
    air = ep.get("air_date") or ""
    if not air or air > today.isoformat():
        return "upcoming"
    return "missing"


def season_view(season: dict, episodes: list[dict], owned: dict, lang_mode: str, local_langs: list[str],
                today: date) -> dict:
    n = season["season_number"]
    out, counts = [], {"owned": 0, "temp": 0, "missing": 0, "upcoming": 0}
    for ep in episodes:
        have = owned.get((n, ep["episode_number"]))
        state = episode_state(ep, have, lang_mode, local_langs, today)
        counts[state] += 1
        out.append({"episode": ep["episode_number"], "name": ep.get("name") or "", "air_date": ep.get("air_date") or "",
                    "runtime": ep.get("runtime") or 0, "overview": ep.get("overview") or "",
                    "state": state, "file": have})
    # files of episodes TMDB does not list (specials numbered differently, a different order)
    known = {e["episode_number"] for e in episodes}
    extra = [{"episode": e, "name": "", "air_date": "", "runtime": 0, "overview": "", "state": "owned", "file": f}
             for (s, e), f in sorted(owned.items()) if s == n and e not in known]
    counts["owned"] += len(extra)
    return {**season, "episodes": out + extra, "counts": counts}
