import base64
import logging
import re
import unicodedata

import httpx

logger = logging.getLogger(__name__)

BASE_CZ = "https://fastshare.cz"
BASE_CLOUD = "https://fastshare.cloud"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"


class FastShareFile:
    """Raw file parsed from FastShare search HTML."""

    def __init__(self, file_id: str, name: str, size_text: str) -> None:
        self.file_id = file_id
        self.name = name
        self.size = self._parse_size(size_text)

    @staticmethod
    def _parse_size(text: str) -> int:
        text = text.strip().upper()
        match = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", text)
        if not match:
            return 0
        val = float(match.group(1))
        unit = match.group(2)
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(val * multipliers.get(unit, 1))


class FastShareClient:
    def __init__(self, login: str, password: str) -> None:
        self._login = login
        self._password = password
        self._http = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": UA},
        )
        self._logged_in = False
        self._user_id: str = ""

    async def ensure_login(self) -> None:
        if self._logged_in:
            return
        resp = await self._http.post(
            f"{BASE_CZ}/sql.php",
            data={"login": self._login, "heslo": self._password},
        )
        final_url = str(resp.url)
        if "error" in final_url or "login" in final_url:
            raise RuntimeError(f"FastShare login failed (redirected to {final_url})")
        self._logged_in = True
        logger.info("FastShare login successful")

    async def search(self, query: str, limit: int = 20) -> list[FastShareFile]:
        await self.ensure_login()

        nfkd = unicodedata.normalize("NFKD", query)
        ascii_query = nfkd.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^\w\s-]", "", ascii_query).strip().replace(" ", "-").lower()
        slug = slug or "search"
        page_resp = await self._http.get(f"{BASE_CLOUD}/{slug}/s")

        token_match = re.search(
            r'id="search_token"\s+value="([^"]+)"', page_resp.text
        )
        uid_match = re.search(r"u=(\d+)&", page_resp.text)

        if not token_match:
            logger.warning("FastShare: could not find search_token")
            return []

        token = token_match.group(1)
        self._user_id = uid_match.group(1) if uid_match else ""
        logger.info(
            "FastShare search: slug=%s, token=%s, user_id=%s",
            slug, token[:8], self._user_id,
        )

        # Step 2: AJAX search (step=0 is first page, limit=1 means page 1)
        # FastShare search doesn't handle diacritics — use the ASCII-stripped
        # version as the search term (same text used for slug, but with spaces)
        clean_query = re.sub(r"[^\w\s]", "", ascii_query).strip().lower()
        clean_query = clean_query or query
        term_b64 = base64.b64encode(clean_query.encode("utf-8")).decode("ascii")
        ajax_resp = await self._http.get(
            f"{BASE_CLOUD}/test2.php",
            params={
                "token": token,
                "u": self._user_id,
                "term": term_b64,
                "type": "video",
                "limit": 1,
                "step": 0,
                "search_purpose": "",
                "search_resolution": "",
                "plain_search": "0",
                "order": "",
            },
            headers={"Referer": f"{BASE_CLOUD}/{slug}/s"},
        )

        logger.info(
            "FastShare AJAX response: %d bytes, status %d",
            len(ajax_resp.text), ajax_resp.status_code,
        )

        # "nebylo nic nalezeno" = nothing found
        if "nebylo nic nalezeno" in ajax_resp.text:
            logger.info("FastShare: no results for query '%s'", query)
            return []

        files = self._parse_search_html(ajax_resp.text, limit)
        logger.info("FastShare parsed %d files", len(files))
        return files

    def _parse_search_html(self, html: str, limit: int) -> list[FastShareFile]:
        """Extract files from the AJAX HTML fragment."""
        files: list[FastShareFile] = []

        # FastShare uses unquoted href attributes:
        #   <a href=https://fastshare.cloud/26237359/hele-kamo-kdo-tu-vari-2005-.mkv
        file_links = re.findall(
            r'href=https://fastshare\.cloud/(\d+)/([^\s<>"]+)', html
        )
        # Sizes from video_time spans — filter only actual size values (e.g. "1.9GB"),
        # not resolution strings (e.g. "&nbsp;&nbsp;1920x1080")
        all_video_time = re.findall(
            r'class="video_time[^"]*">([^<]+)</span>', html
        )
        sizes = [s for s in all_video_time if re.match(r'[\d.]+\s*[GMKT]B', s.strip(), re.IGNORECASE)]

        # Deduplicate by file_id (same file appears multiple times in HTML)
        seen: set[str] = set()
        size_idx = 0
        for file_id, raw_name in file_links:
            if file_id in seen:
                continue
            seen.add(file_id)

            # Clean up name: replace hyphens, decode URL encoding
            name = raw_name.replace("-", " ").strip()
            # Get corresponding size
            size_text = sizes[size_idx] if size_idx < len(sizes) else "0MB"
            size_idx += 1

            files.append(FastShareFile(file_id, name, size_text))
            if len(files) >= limit:
                break

        return files

    async def get_download_url(self, file_id: str) -> str:
        """Get a direct download URL — uses premium speed if logged in."""
        await self.ensure_login()

        # Load file page to get CSRF token
        page_resp = await self._http.get(
            f"{BASE_CLOUD}/{file_id}/file",
            headers={"Referer": BASE_CLOUD},
        )

        csrf_match = re.search(r'name="csrf"\s+value="([^"]+)"', page_resp.text)
        if not csrf_match:
            raise RuntimeError(f"FastShare: CSRF token not found for file {file_id}")
        csrf = csrf_match.group(1)

        # Try premium download first (full speed for paid accounts)
        premium_resp = await self._http.post(
            f"{BASE_CLOUD}/download/",
            params={"lang": "cs", "u": file_id},
            data={"csrf": csrf},
            headers={"Referer": f"{BASE_CLOUD}/{file_id}/file"},
            follow_redirects=False,
        )

        if premium_resp.status_code in (301, 302, 303, 307):
            download_url = premium_resp.headers.get("location", "")
            if "download.php" in download_url:
                logger.info("FastShare: using premium download for file %s", file_id)
                return download_url

        # Fallback to free download (slower, no premium)
        logger.info("FastShare: premium not available, falling back to free for file %s", file_id)
        free_resp = await self._http.post(
            f"{BASE_CLOUD}/free/",
            params={"lang": "cs", "u": file_id},
            data={"csrf": csrf},
            headers={"Referer": f"{BASE_CLOUD}/{file_id}/file"},
            follow_redirects=False,
        )

        if free_resp.status_code in (301, 302, 303, 307):
            download_url = free_resp.headers.get("location", "")
            if "download_free.php" in download_url or "download.php" in download_url:
                return download_url
            raise RuntimeError(
                f"FastShare: unexpected redirect to {download_url}"
            )

        raise RuntimeError(
            f"FastShare: expected redirect, got {free_resp.status_code}"
        )

    async def close(self) -> None:
        await self._http.aclose()
