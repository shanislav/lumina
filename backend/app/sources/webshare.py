from app.clients.webshare import WebShareClient
from app.sources.base import BaseSource, DownloadBackend, SearchResult, SourceType


class WebShareSource(BaseSource):
    source_type = SourceType.WEBSHARE
    download_backend = DownloadBackend.ARIA2

    def __init__(self, source_id: int, config: dict) -> None:
        super().__init__(source_id, config)
        self._client = WebShareClient(config["username"], config["password"])

    async def search(self, query: str, limit: int = 30) -> list[SearchResult]:
        files = await self._client.search(query, limit)
        return [
            SearchResult(
                source_id=self.source_id,
                source_type=self.source_type,
                ident=f.ident,
                name=f.name,
                size=f.size,
            )
            for f in files
        ]

    async def get_download_info(self, ident: str) -> dict:
        url = await self._client.get_download_link(ident)
        return {"url": url}

    async def test_connection(self) -> bool:
        try:
            await self._client._ensure_token()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()
