from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel


class SourceType(str, Enum):
    WEBSHARE = "webshare"
    FASTSHARE = "fastshare"
    JACKETT = "jackett"


class DownloadBackend(str, Enum):
    ARIA2 = "aria2"
    QBITTORRENT = "qbittorrent"


class SearchResult(BaseModel):
    """Unified search result returned by every source plugin."""

    source_id: int
    source_type: SourceType
    ident: str
    name: str
    size: int
    magnet_url: str | None = None
    seeders: int | None = None
    # What some sources tell right in the search result (FastShare: duration + resolution)
    duration_s: int = 0
    width: int = 0
    height: int = 0


class BaseSource(ABC):
    """Every source plugin implements this interface."""

    source_type: SourceType
    download_backend: DownloadBackend

    def __init__(self, source_id: int, config: dict) -> None:
        self.source_id = source_id
        self._config = config

    @abstractmethod
    async def search(self, query: str, limit: int = 30) -> list[SearchResult]: ...

    @abstractmethod
    async def get_download_info(self, ident: str) -> dict:
        """Return backend-specific download info.

        For Aria2 sources: {"url": "https://..."}
        For qBittorrent sources: {"magnet_url": "magnet:?..."}
        """
        ...

    async def get_details(self, ident: str, name: str) -> dict | None:
        """Technical details of a file as the source knows them (optional).

        Same shape as app.core.mediainfo.probe(): duration_s, width, height, video_codec,
        bitrate, audio: [{lang, codec, channels}], subtitles: [lang]. None = not supported.
        """
        return None

    @abstractmethod
    async def test_connection(self) -> bool:
        """Validate credentials / reachability. Return True if OK."""
        ...

    @abstractmethod
    async def close(self) -> None: ...
