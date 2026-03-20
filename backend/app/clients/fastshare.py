import logging
import re
import unicodedata
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://fastshare.cz/api/api_kodi.php"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"


class FastShareFile:
    """File from FastShare KODI API search."""

    def __init__(
        self,
        file_id: str,
        name: str,
        size: int,
        download_url: str = "",
        resolution: str = "",
        duration: int = 0,
    ) -> None:
        self.file_id = file_id
        self.name = name
        self.size = size
        self.download_url = download_url
        self.resolution = resolution
        self.duration = duration


class FastShareClient:
    """FastShare client using the official KODI JSON API (api_kodi.php).

    Authentication uses the "Login pro PC, Android a KODI aplikace"
    credentials (not the web login). Login returns a hash token used
    as a FASTSHARE=<hash> cookie for premium downloads.
    """

    def __init__(self, login: str, password: str) -> None:
        self._login = login
        self._password = password
        self._http = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": UA},
        )
        self._hash: str = ""
        self._unlimited: bool = False
        self._credit_mb: int = 0
        # Cache: file_id → download_url (populated during search)
        self._download_cache: dict[str, str] = {}

    async def ensure_login(self) -> None:
        if self._hash:
            return

        resp = await self._http.get(
            API_URL,
            params={
                "process": "login",
                "login": self._login,
                "password": self._password,
            },
        )
        data = resp.json()
        logger.info("FastShare login response keys: %s", list(data.keys()))

        user = data.get("user")
        if not user or not user.get("hash"):
            error = data.get("error", data)
            raise RuntimeError(f"FastShare KODI login failed: {error}")

        self._hash = user["hash"]
        self._unlimited = str(user.get("unlimited", "")).lower() == "true"
        credit_data = user.get("data", {})
        self._credit_mb = int(credit_data.get("value") or 0) if credit_data else 0

        logger.info(
            "FastShare login OK: hash=%s***, unlimited=%s, credit=%d MB",
            self._hash[:8],
            self._unlimited,
            self._credit_mb,
        )

    @property
    def auth_cookie(self) -> str:
        """Cookie string for download authentication."""
        return f"FASTSHARE={self._hash}"

    @staticmethod
    def _strip_diacritics(text: str) -> str:
        """Strip diacritics from text — FastShare search doesn't handle them."""
        nfkd = unicodedata.normalize("NFKD", text)
        ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
        # Clean up punctuation but keep spaces
        clean = re.sub(r"[^\w\s]", " ", ascii_text).strip()
        clean = re.sub(r"\s+", " ", clean)
        return clean

    async def search(self, query: str, limit: int = 30) -> list[FastShareFile]:
        await self.ensure_login()

        # FastShare doesn't handle diacritics — strip them
        clean_query = self._strip_diacritics(query)
        logger.info("FastShare search: '%s' → '%s'", query, clean_query)

        resp = await self._http.get(
            API_URL,
            params={
                "process": "search",
                "term": clean_query,
                "pagination": min(limit, 200),
            },
        )

        data = resp.json()
        search_data = data.get("search", {})
        file_list: list[dict[str, Any]] = search_data.get("file", [])

        if not file_list:
            logger.info("FastShare: no results for '%s'", query)
            return []

        files: list[FastShareFile] = []
        for item in file_list[:limit]:
            filename = item.get("filename", "")
            download_url = item.get("download_url", "")
            file_id = str(item.get("id", ""))
            if not file_id:
                file_id = self._extract_file_id(download_url, item)

            size_data = item.get("data", {})
            size = int(size_data.get("value") or 0) if size_data else 0

            duration_data = item.get("duration", {})
            duration = int(duration_data.get("value") or 0) if duration_data else 0

            resolution = item.get("resolution", "")

            f = FastShareFile(
                file_id=file_id,
                name=filename,
                size=size,
                download_url=download_url,
                resolution=resolution,
                duration=duration,
            )
            files.append(f)

            # Cache the download URL for later use
            if download_url and file_id:
                self._download_cache[file_id] = download_url

        logger.info(
            "FastShare search '%s': %d results, %d with download URLs",
            query,
            len(files),
            sum(1 for f in files if f.download_url),
        )
        return files

    @staticmethod
    def _extract_file_id(download_url: str, item: dict) -> str:
        """Extract numeric file ID from download URL or item data."""
        # download_url looks like: https://data42.fastshare.cz/download.php?id=12345&...
        if download_url:
            import re
            match = re.search(r'[?&]id=(\d+)', download_url)
            if match:
                return match.group(1)
            # Or from URL path like /12345/filename
            match = re.search(r'/(\d{5,})', download_url)
            if match:
                return match.group(1)

        # Fallback: use hash of filename as ID (not ideal but works for cache)
        filename = item.get("filename", "unknown")
        return str(abs(hash(filename)) % 10**10)

    async def get_download_url(self, file_id: str) -> str:
        """Get download URL for a file.

        For premium: uses cached URL from search results.
        Returns the download_url that needs FASTSHARE cookie header.
        """
        await self.ensure_login()

        # Check cache first (populated during search)
        cached = self._download_cache.get(file_id)
        if cached:
            logger.info("FastShare: download URL from cache for %s", file_id)
            return cached

        # If not cached, we can't easily get it from the KODI API
        # (no file_info endpoint). Fall back to constructing a URL
        # that the user can access with their premium cookie.
        # This is a last resort — normally URLs come from search.
        logger.warning(
            "FastShare: no cached download URL for file %s, "
            "trying direct download endpoint",
            file_id,
        )

        # Try the fastshare.cz file page — with the hash cookie set,
        # it might redirect to the download
        resp = await self._http.get(
            f"https://fastshare.cz/{file_id}",
            headers={
                "Cookie": self.auth_cookie,
                "Referer": "https://fastshare.cz/",
            },
            follow_redirects=False,
        )

        if resp.status_code in (301, 302, 303, 307):
            location = resp.headers.get("location", "")
            if "download" in location:
                logger.info("FastShare: redirect download for %s → %s", file_id, location)
                self._download_cache[file_id] = location
                return location

        raise RuntimeError(
            f"FastShare: no download URL for file {file_id}. "
            f"Try searching for the file first."
        )

    async def close(self) -> None:
        await self._http.aclose()
