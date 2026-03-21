import hashlib

from app.clients.jackett import JackettClient
from app.sources.base import BaseSource, DownloadBackend, SearchResult, SourceType


class JackettSource(BaseSource):
    source_type = SourceType.JACKETT
    download_backend = DownloadBackend.QBITTORRENT

    def __init__(self, source_id: int, config: dict) -> None:
        super().__init__(source_id, config)
        self._client = JackettClient(config["url"], config["api_key"])

    async def search(self, query: str, limit: int = 30) -> list[SearchResult]:
        torrents = await self._client.search(query, limit)
        results: list[SearchResult] = []
        for t in torrents:
            ident = hashlib.sha1(t.magnet_url.encode()).hexdigest()[:16]
            results.append(
                SearchResult(
                    source_id=self.source_id,
                    source_type=self.source_type,
                    ident=ident,
                    name=t.title,
                    size=t.size,
                    magnet_url=t.magnet_url,
                    seeders=t.seeders,
                )
            )
        return results

    async def get_download_info(self, ident: str) -> dict:
        raise NotImplementedError(
            "Jackett downloads use magnet_url from the search result"
        )

    async def test_connection(self) -> bool:
        try:
            await self._client.search("test", limit=1)
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()
