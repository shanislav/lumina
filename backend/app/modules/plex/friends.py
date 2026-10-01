"""Films on the Plex servers friends share with you — "Milan has it too", so there is no need to
download it. Lumina keeps the last known list of every shared server (a friend's server is not always
on) and refreshes it every few hours; nothing goes into Lumina's own library."""

import asyncio
import logging
from datetime import datetime
from urllib.parse import quote

import httpx

from app.db import get_automation, get_db
from app.modules.plex.library import movie

logger = logging.getLogger(__name__)

TABLES = """
CREATE TABLE IF NOT EXISTS plex_friend_servers (
    id TEXT PRIMARY KEY,            -- the server's clientIdentifier
    name TEXT,
    owner TEXT,                     -- plex.tv user name of the friend
    owner_title TEXT,               -- the name the friend shows under
    movies INTEGER DEFAULT 0,
    last_ok TEXT,                   -- the list below is from this time
    last_try TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS plex_friend_movies (
    server_id TEXT NOT NULL,
    rating_key TEXT NOT NULL,
    tmdb_id INTEGER,
    imdb_id TEXT,
    title TEXT,
    year INTEGER,
    resolution TEXT,
    PRIMARY KEY (server_id, rating_key)
);
CREATE INDEX IF NOT EXISTS plex_friend_movies_tmdb ON plex_friend_movies (tmdb_id);
CREATE INDEX IF NOT EXISTS plex_friend_movies_imdb ON plex_friend_movies (imdb_id);
"""

REFRESH_S = 6 * 3600
HEADERS = {"Accept": "application/json", "X-Plex-Product": "Lumina", "X-Plex-Client-Identifier": "lumina"}

_state: dict = {"running": False}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _token() -> str | None:
    cfg = ((await get_automation("plex")) or {}).get("config") or {}
    return cfg.get("token") or None


async def _shared_servers(http: httpx.AsyncClient, token: str) -> list[dict]:
    """Servers of other people this account can use (plex.tv), with the friends' display names."""
    resp = await http.get("https://plex.tv/api/v2/resources", params={"includeHttps": 1, "includeRelay": 1},
                          headers={**HEADERS, "X-Plex-Token": token})
    resp.raise_for_status()
    titles: dict[str, str] = {}
    try:
        friends = await http.get("https://plex.tv/api/v2/friends", headers={**HEADERS, "X-Plex-Token": token})
        titles = {f.get("username") or "": f.get("title") or f.get("username") or "" for f in friends.json()}
    except Exception:  # noqa: BLE001 — names are a nicety
        pass
    return [{**s, "owner_title": titles.get(s.get("sourceTitle") or "") or s.get("sourceTitle") or ""}
            for s in resp.json() if "server" in (s.get("provides") or "") and not s.get("owned")]


async def _movies_of(http: httpx.AsyncClient, server: dict) -> list[dict]:
    """All films of a friend's server — over its public address, else Plex's relay (local addresses are
    the friend's home network)."""
    headers = {**HEADERS, "X-Plex-Token": server["accessToken"]}
    errors = []
    for conn in sorted((c for c in server.get("connections", []) if not c.get("local")), key=lambda c: bool(c.get("relay"))):
        try:
            resp = await http.get(conn["uri"] + "/library/sections", headers=headers, timeout=10)
            resp.raise_for_status()
            sections = resp.json()["MediaContainer"].get("Directory", [])
            films = []
            for s in sections:
                if s.get("type") != "movie":
                    continue
                resp = await http.get(f"{conn['uri']}/library/sections/{s['key']}/all", headers=headers, timeout=120,
                                      params={"type": 1, "includeGuids": 1})
                resp.raise_for_status()
                for m in resp.json()["MediaContainer"].get("Metadata", []):
                    films.append({**movie(m), "resolution": next(
                        (md.get("videoResolution") for md in m.get("Media", []) if md.get("videoResolution")), "")})
            return films
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}")
    raise RuntimeError("nedostupný (" + ", ".join(errors or ["žádná veřejná adresa"]) + ")")


async def refresh() -> dict:
    """Read the film lists of all shared servers; an unreachable one keeps its last known list."""
    token = await _token()
    if not token or _state["running"]:
        return {"servers": 0}
    _state["running"] = True
    ok = 0
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            servers = await _shared_servers(http, token)
            db = await get_db()
            try:
                ids = [s["clientIdentifier"] for s in servers]
                # a server no longer shared goes, with its films
                cursor = await db.execute("SELECT id FROM plex_friend_servers")
                for (old,) in await cursor.fetchall():
                    if old not in ids:
                        await db.execute("DELETE FROM plex_friend_servers WHERE id = ?", (old,))
                        await db.execute("DELETE FROM plex_friend_movies WHERE server_id = ?", (old,))
                for s in servers:
                    sid = s["clientIdentifier"]
                    await db.execute(
                        "INSERT INTO plex_friend_servers (id, name, owner, owner_title, last_try) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, owner = excluded.owner, "
                        "owner_title = excluded.owner_title, last_try = excluded.last_try",
                        (sid, s.get("name"), s.get("sourceTitle"), s["owner_title"], _now()))
                    try:
                        films = await _movies_of(http, s)
                    except Exception as e:  # noqa: BLE001
                        await db.execute("UPDATE plex_friend_servers SET error = ? WHERE id = ?", (str(e), sid))
                        logger.info("Plex friend %s (%s): %s", s.get("name"), s.get("sourceTitle"), e)
                        continue
                    await db.execute("DELETE FROM plex_friend_movies WHERE server_id = ?", (sid,))
                    await db.executemany(
                        "INSERT OR REPLACE INTO plex_friend_movies (server_id, rating_key, tmdb_id, imdb_id, title, year, resolution) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(sid, f["rating_key"], f["tmdb_id"], f["imdb_id"], f["title"], f["year"], f["resolution"]) for f in films])
                    await db.execute("UPDATE plex_friend_servers SET movies = ?, last_ok = ?, error = NULL WHERE id = ?",
                                     (len(films), _now(), sid))
                    ok += 1
                    logger.info("Plex friend %s (%s): %d films", s.get("name"), s.get("sourceTitle"), len(films))
                await db.commit()
            finally:
                await db.close()
    finally:
        _state["running"] = False
    return {"servers": len(servers), "reachable": ok}


async def who_has(tmdb_id: int | None, imdb_id: str | None) -> list[dict]:
    """Friends whose shared library has this film, with a link that opens it in Plex."""
    if not tmdb_id and not imdb_id:
        return []
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT m.*, s.name AS server, s.owner, s.owner_title, s.last_ok, s.error FROM plex_friend_movies m "
            "JOIN plex_friend_servers s ON s.id = m.server_id WHERE m.tmdb_id = ? OR (? != '' AND m.imdb_id = ?)",
            (tmdb_id or -1, imdb_id or "", imdb_id or ""))
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    return [{
        "owner": r["owner_title"] or r["owner"], "server": r["server"], "resolution": r["resolution"],
        "online": not r["error"], "seen_at": r["last_ok"],
        "url": f"https://app.plex.tv/desktop/#!/server/{r['server_id']}/details?key="
               + quote(f"/library/metadata/{r['rating_key']}", safe=""),
    } for r in rows]


async def servers() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM plex_friend_servers ORDER BY owner_title")
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def _loop() -> None:
    await asyncio.sleep(60)          # let the app start first
    while True:
        try:
            await refresh()
        except Exception as e:  # noqa: BLE001 — try again next time
            logger.warning("Plex friends refresh failed: %s", e)
        await asyncio.sleep(REFRESH_S)


_task: asyncio.Task | None = None


async def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    if _task:
        _task.cancel()
