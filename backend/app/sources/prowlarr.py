import hashlib

from app.clients.prowlarr import ProwlarrClient
from app.sources.base import BaseSource, DownloadBackend, SearchResult, SourceType


class ProwlarrSource(BaseSource):
    source_type = SourceType.PROWLARR
    download_backend = DownloadBackend.QBITTORRENT

    def __init__(self, source_id: int, config: dict) -> None:
        super().__init__(source_id, config)
        self._client = ProwlarrClient(config["url"], config["api_key"])
        self._info_urls: dict[str, str] = {}   # ident → the tracker's detail page (for get_details)

    async def search(self, query: str, limit: int = 100) -> list[SearchResult]:
        # all of them: the most seeded ones are often other parts of a series (the film check sorts them out)
        results = []
        for t in await self._client.search(query, limit):
            # one torrent = one ident, whichever query found it (the download link differs per search)
            ident = hashlib.sha1((t.guid or t.magnet_url).encode()).hexdigest()[:16]
            if t.link:
                self._info_urls[ident] = t.link
            results.append(SearchResult(source_id=self.source_id, source_type=self.source_type, ident=ident,
                                        name=t.title, size=t.size, magnet_url=t.magnet_url, seeders=t.seeders))
        return results

    async def get_details(self, ident: str, name: str) -> dict | None:
        return await self._client.details(self._info_urls.get(ident, ""))

    async def get_download_info(self, ident: str, name: str = "") -> dict:
        raise NotImplementedError("Prowlarr downloads use the link from the search result")

    async def test_connection(self) -> bool:
        try:
            await self._client.test()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()
