"""Plex Media Server API — only what Lumina needs: library sections and a partial scan."""

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
             "locations": [loc["path"] for loc in d.get("Location", []) if loc.get("path")]}
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

    async def close(self) -> None:
        await self._http.aclose()
