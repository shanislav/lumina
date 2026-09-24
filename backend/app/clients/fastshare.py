import asyncio
import html
import logging
import re
import unicodedata
from typing import Any

import httpx

from app.core.mediainfo import normalize_language

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
        duration_f: str = "",
        thumbnail: str = "",
        uploaded_date: str = "",
    ) -> None:
        self.file_id = file_id
        self.name = name
        self.size = size
        self.download_url = download_url
        self.resolution = resolution
        self.duration = duration
        self.duration_f = duration_f
        self.thumbnail = thumbnail
        self.uploaded_date = uploaded_date


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
            # the KODI API returns HTML-escaped names ("Don&#39;t") — breaks titles and the page slug
            filename = html.unescape(item.get("filename", ""))
            download_url = item.get("download_url", "")
            file_id = str(item.get("id", ""))
            if not file_id:
                file_id = self._extract_file_id(download_url, item)

            size_data = item.get("data", {})
            size = int(size_data.get("value") or 0) if size_data else 0

            duration_data = item.get("duration", {})
            duration = int(duration_data.get("value") or 0) if duration_data else 0

            resolution = item.get("resolution", "")

            duration_f = item.get("duration_f", "")
            thumbnail = item.get("thumbnail", "")
            uploaded_date = item.get("uploaded_date", "")

            f = FastShareFile(
                file_id=file_id,
                name=filename,
                size=size,
                download_url=download_url,
                resolution=resolution,
                duration=duration,
                duration_f=duration_f,
                thumbnail=thumbnail,
                uploaded_date=uploaded_date,
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

    async def file_details(self, file_id: str, name: str) -> dict | None:
        """Technical info from the public file page (the KODI API has no detail call).

        Polite on purpose: at most PAGE_CONCURRENCY pages at once with a pause between
        requests, no login cookie, and callers cache the result — a file is read once.
        """
        url = f"https://fastshare.cloud/{file_id}/{page_slug(name)}"
        async with _page_limit:
            global _last_page_at
            # Starts are scheduled one at a time, so two waiting requests cannot both
            # read the same "last start" and fire together.
            async with _page_schedule:
                wait = _last_page_at + PAGE_INTERVAL_S - asyncio.get_running_loop().time()
                if wait > 0:
                    await asyncio.sleep(wait)
                _last_page_at = asyncio.get_running_loop().time()
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as client:
                resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return parse_file_page(resp.text)

    async def close(self) -> None:
        await self._http.aclose()


PAGE_CONCURRENCY = 2
PAGE_INTERVAL_S = 0.4
_page_limit = asyncio.Semaphore(PAGE_CONCURRENCY)
_page_schedule = asyncio.Lock()
_last_page_at = 0.0


def page_slug(name: str) -> str:
    """URL slug of a file page — must match FastShare exactly, otherwise the page has no details:
    NFKD → ascii → lower → [^a-z0-9.]+ → "-", only a leading "-" is trimmed ("Film (2009).mkv" → "film-2009-.mkv")."""
    m = re.search(r"\.[^.]+$", name)
    ext = m.group(0).lower() if m else ""
    base = name[: -len(ext)] if ext else name
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode().lower()
    return re.sub(r"^-", "", re.sub(r"[^a-z0-9.]+", "-", base)) + ext


def _field(block: str, label: str) -> str:
    m = re.search(label + r"\s*:?\s*(?:</b>)?\s*([^<]+)", block)
    return m.group(1).strip() if m else ""


def parse_file_page(page: str) -> dict | None:
    """Parse the .vf-resdur / .vf-tech blocks of a FastShare file page."""
    m = re.search(r'<div class="vf-meta">(.*?)<div class="vf-side">', page, re.S)
    if not m:
        return None
    block = re.sub(r"\s+", " ", m.group(1))
    width = height = 0
    if res := re.search(r"Rozlišení:\s*(\d+)x(\d+)", block):
        width, height = int(res.group(1)), int(res.group(2))
    duration = 0
    if dur := re.search(r"Délka:\s*(\d+):(\d+):(\d+)", block):
        h, mi, se = (int(x) for x in dur.groups())
        duration = h * 3600 + mi * 60 + se
    langs = [normalize_language(x) for x in _field(block, "Audio stopy").split(",") if x.strip()]
    subs = [normalize_language(x) for x in _field(block, "Titulky").split(",") if x.strip()]

    video_col = re.search(r"<b>Video:</b>(.*?)</div>", block)
    audio_col = re.search(r"<b>Audio:</b>(.*?)</div>", block)
    video_codec = _field(video_col.group(1), "Kodek") if video_col else ""
    bitrate = 0
    if video_col and (br := re.search(r"Bitrate:\s*(\d+)\s*kbps", video_col.group(1))):
        bitrate = int(br.group(1)) * 1000
    audio_codec = _field(audio_col.group(1), "Kodek") if audio_col else ""
    channels = 0
    if audio_col and (ch := re.search(r"Počet kanálů:\s*(\d+)", audio_col.group(1))):
        channels = int(ch.group(1))

    # Codec/channels are shown for the first audio track only.
    audio = [{"lang": lang, "codec": audio_codec if i == 0 else "", "channels": channels if i == 0 else 0}
             for i, lang in enumerate(langs) if lang]
    if not audio and audio_codec:
        audio = [{"lang": "", "codec": audio_codec, "channels": channels}]
    return {
        "duration_s": duration, "width": width, "height": height,
        "video_codec": video_codec.split("/")[0].strip(), "bitrate": bitrate,
        "audio": audio, "subtitles": [s for s in subs if s],
    }
