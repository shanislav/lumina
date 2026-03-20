from app.clients.fastshare import FastShareClient
from app.sources.base import BaseSource, DownloadBackend, SearchResult, SourceType


class FastShareSource(BaseSource):
    source_type = SourceType.FASTSHARE
    download_backend = DownloadBackend.ARIA2

    def __init__(self, source_id: int, config: dict) -> None:
        super().__init__(source_id, config)
        # Support both old "heslo" and new "password" config key
        password = config.get("password") or config.get("heslo", "")
        self._client = FastShareClient(config["login"], password)

    async def search(self, query: str, limit: int = 30) -> list[SearchResult]:
        files = await self._client.search(query, limit)
        return [
            SearchResult(
                source_id=self.source_id,
                source_type=self.source_type,
                ident=f.file_id,
                name=f.name,
                size=f.size,
            )
            for f in files
        ]

    async def get_download_info(self, ident: str) -> dict:
        url = await self._client.get_download_url(ident)
        return {
            "url": url,
            "headers": {"Cookie": self._client.auth_cookie},
        }

    async def test_connection(self) -> bool:
        try:
            await self._client.ensure_login()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()
