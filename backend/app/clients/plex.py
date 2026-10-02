"""Plex Media Server API — only what Lumina needs: library sections, scans, the movies of a section,
server settings and the trash (a big rename, decisions/0008)."""

import httpx


class PlexClient:
    def __init__(self, url: str, token: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"X-Plex-Token": token, "Accept": "application/json"},
            timeout=15,
        )

    async def sections(self) -> list[dict]:
        """[{key, title, type, locations: [path, ...]}]"""
        resp = await self._http.get("/library/sections")
        resp.raise_for_status()
        dirs = resp.json().get("MediaContainer", {}).get("Directory", [])
        return [
            {"key": str(d["key"]), "title": d.get("title", ""), "type": d.get("type", ""),
             "locations": [loc["path"] for loc in d.get("Location", []) if loc.get("path")],
             "refreshing": bool(d.get("refreshing"))}
            for d in dirs
        ]

    async def scan(self, section_key: str, path: str | None = None) -> None:
        """Scan one folder of a section (or the whole section without a path)."""
        resp = await self._http.get(f"/library/sections/{section_key}/refresh",
                                    params={"path": path} if path else None)
        resp.raise_for_status()

    async def prefs(self) -> dict[str, object]:
        """Server settings by id (FSEventLibraryUpdatesEnabled, autoEmptyTrash, …)."""
        resp = await self._http.get("/:/prefs")
        resp.raise_for_status()
        return {s["id"]: s.get("value") for s in resp.json().get("MediaContainer", {}).get("Setting", []) if "id" in s}

    async def set_prefs(self, values: dict[str, object]) -> None:
        """Change server settings ({id: value}); booleans as 1/0."""
        params = {k: (int(v) if isinstance(v, bool) else v) for k, v in values.items()}
        resp = await self._http.put("/:/prefs", params=params)
        resp.raise_for_status()

    async def items(self, section_key: str) -> list[dict]:
        """The section's movies as Plex returns them (with the ids of all agents: Guid)."""
        resp = await self._http.get(f"/library/sections/{section_key}/all",
                                    params={"includeGuids": 1}, timeout=120)
        resp.raise_for_status()
        return resp.json().get("MediaContainer", {}).get("Metadata", [])

    async def episodes(self, section_key: str) -> list[dict]:
        """Every episode of a TV section (show, season and episode numbers, files)."""
        resp = await self._http.get(f"/library/sections/{section_key}/all", params={"type": 4}, timeout=300)
        resp.raise_for_status()
        return resp.json().get("MediaContainer", {}).get("Metadata", [])

    async def empty_trash(self, section_key: str) -> None:
        resp = await self._http.put(f"/library/sections/{section_key}/emptyTrash")
        resp.raise_for_status()

    async def mark_watched(self, rating_key: str) -> None:
        resp = await self._http.get("/:/scrobble", params={"key": rating_key,
                                                           "identifier": "com.plexapp.plugins.library"})
        resp.raise_for_status()

    async def set_added_at(self, section_key: str, rating_key: str, added_at: int, kind: int = 1) -> None:
        """kind: Plex's metadata type — 1 movie, 4 episode."""
        resp = await self._http.put(f"/library/sections/{section_key}/all",
                                    params={"type": kind, "id": rating_key, "addedAt.value": added_at})
        resp.raise_for_status()

    async def item(self, rating_key: str) -> dict:
        """One movie with its locked fields (``Field``)."""
        resp = await self._http.get(f"/library/metadata/{rating_key}")
        resp.raise_for_status()
        return resp.json()["MediaContainer"]["Metadata"][0]

    async def poster(self, rating_key: str) -> bytes:
        resp = await self._http.get(f"/library/metadata/{rating_key}/thumb", timeout=60)
        resp.raise_for_status()
        return resp.content

    async def upload_poster(self, rating_key: str, image: bytes) -> None:
        """Upload a poster; Plex selects it."""
        resp = await self._http.post(f"/library/metadata/{rating_key}/posters", content=image, timeout=60)
        resp.raise_for_status()

    async def edit_fields(self, section_key: str, rating_key: str, values: dict, locked: list[str]) -> None:
        """Set fields of a movie and lock them, as an edit in Plex does."""
        params: dict[str, object] = {"type": 1, "id": rating_key}
        for name, value in values.items():
            params[f"{name}.value"] = value
        for name in locked:
            params[f"{name}.locked"] = 1
        resp = await self._http.put(f"/library/sections/{section_key}/all", params=params)
        resp.raise_for_status()

    async def close(self) -> None:
        await self._http.aclose()
