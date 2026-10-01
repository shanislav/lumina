"""Prowlarr — torrent indexers (private trackers too) behind one search API."""

import logging

import httpx

from app.core import mediainfo_text
from app.core.throttle import Throttle
from app.models.schemas import TorrentResult

logger = logging.getLogger(__name__)

CATEGORIES = [2000, 5000]   # movies, TV
# detail pages on the trackers themselves: a few at a time, politely
PAGE_THROTTLE = Throttle("tracker pages", concurrency=2, interval=0.5, cooldown=600)


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=60, headers={"X-Api-Key": api_key})

    async def search(self, query: str, limit: int = 30) -> list[TorrentResult]:
        try:
            resp = await self._http.get(f"{self._base_url}/api/v1/search",
                                        params={"query": query, "type": "search", "categories": CATEGORIES, "limit": 100})
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Prowlarr search failed: %s", e)
            return []
        results = []
        for item in sorted(resp.json(), key=lambda x: -(x.get("seeders") or 0))[:limit]:
            link = item.get("magnetUrl") or item.get("downloadUrl") or ""
            if not item.get("title") or not link:
                continue
            results.append(TorrentResult(
                title=item["title"], size=int(item.get("size") or 0), seeders=int(item.get("seeders") or 0),
                leechers=int(item.get("leechers") or 0), magnet_url=link, link=item.get("infoUrl") or "",
                category=", ".join(c.get("name", "") for c in item.get("categories", [])),
                grabs=item.get("grabs"), published_date=(item.get("publishDate") or "")[:10],
                description=item.get("indexer") or "", guid=item.get("guid") or "",
            ))
        return results

    async def details(self, info_url: str) -> dict | None:
        """MediaInfo the uploader put on the tracker's detail page (audio tracks, length, HDR …)."""
        if not info_url or PAGE_THROTTLE.cooling_down:
            return None
        async with PAGE_THROTTLE.slot():
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(info_url)
        if resp.status_code in (403, 429):
            PAGE_THROTTLE.trip()
            return None
        if resp.status_code != 200:
            return None
        return mediainfo_text.parse(mediainfo_text.page_text(resp.text))

    async def test(self) -> None:
        resp = await self._http.get(f"{self._base_url}/api/v1/indexer")
        resp.raise_for_status()

    async def close(self) -> None:
        await self._http.aclose()
