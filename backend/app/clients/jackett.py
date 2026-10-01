import logging

import httpx

from app.models.schemas import TorrentResult

logger = logging.getLogger(__name__)


class JackettClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=30)

    async def search(self, query: str, limit: int = 30) -> list[TorrentResult]:
        try:
            resp = await self._http.get(
                f"{self._base_url}/api/v2.0/indexers/all/results",
                params={
                    "apikey": self._api_key,
                    "Query": query,
                },
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Jackett search failed: %s", e)
            return []

        return self._parse_json(resp.json(), limit)

    def _parse_json(self, data: dict, limit: int) -> list[TorrentResult]:
        results: list[TorrentResult] = []

        for item in data.get("Results", [])[:limit]:
            title = item.get("Title", "")
            if not title:
                continue

            magnet_url = item.get("MagnetUri") or ""
            link = item.get("Link") or ""
            size = int(item.get("Size", 0))
            seeders = int(item.get("Seeders", 0))
            peers = int(item.get("Peers", 0))
            leechers = max(0, peers - seeders)
            category = ", ".join(str(c) for c in item.get("Category", []))

            genres = item.get("Genres") or []
            description = item.get("Description") or ""
            grabs = item.get("Grabs")
            published = (item.get("PublishDate") or "")[:10]  # "2026-02-05"

            results.append(
                TorrentResult(
                    title=title,
                    size=size,
                    seeders=seeders,
                    leechers=leechers,
                    magnet_url=magnet_url or link,
                    link=link,
                    category=category,
                    genres=genres,
                    description=description,
                    grabs=grabs,
                    published_date=published,
                    guid=item.get("Guid") or "",
                )
            )

        return results

    async def close(self) -> None:
        await self._http.aclose()
