"""What plays in Plex right now — the phone's "Sleduji": a tap and the film / episode being watched is there,
with its subtitles. Asked only when the user opens it. Everyone who watches (friends the server is shared with
too — "Vančo watches a film, send him subtitles"), the owner (Plex account id 1) first."""

from app.clients.plex import PlexClient
from app.db import get_automation, get_db
from app.modules.plex.paths import from_plex

OWNER = "1"


async def now_playing() -> dict:
    cfg = ((await get_automation("plex")) or {}).get("config") or {}
    if not (cfg.get("url") and cfg.get("token")):
        return {"configured": False, "items": []}
    client = PlexClient(cfg["url"], cfg["token"])
    try:
        sessions = await client.sessions()
    finally:
        await client.close()
    items = []
    db = await get_db()
    try:
        for s in sessions:
            if s.get("type") not in ("movie", "episode"):
                continue
            user = s.get("User") or {}
            part = ((s.get("Media") or [{}])[0].get("Part") or [{}])[0]
            path = from_plex(part.get("file") or "", cfg.get("path_map", "")) if part.get("file") else ""
            item = {"kind": s["type"], "title": s.get("title") or "", "year": s.get("year"),
                    "show": s.get("grandparentTitle") or "", "season": s.get("parentIndex"), "episode": s.get("index"),
                    "state": (s.get("Player") or {}).get("state") or "", "player": (s.get("Player") or {}).get("title") or "",
                    "progress": round(100 * (s.get("viewOffset") or 0) / s["duration"]) if s.get("duration") else None,
                    "id": None, "tmdb_id": None,
                    "user": user.get("title") or "", "mine": str(user.get("id") or "") == OWNER}
            if path and s["type"] == "movie":
                row = await (await db.execute("SELECT id, tmdb_id FROM library_movies WHERE file_path = ?", (path,))).fetchone()
            elif path:
                row = await (await db.execute("SELECT id, show_tmdb_id FROM library_episodes WHERE file_path = ? AND has_file = 1",
                                              (path,))).fetchone()
            else:
                row = None
            if row:
                item["id"], item["tmdb_id"] = row[0], row[1]
            items.append(item)
    finally:
        await db.close()
    items.sort(key=lambda i: (not i["mine"], i["state"] != "playing", i["user"].lower()))
    return {"configured": True, "items": items}
