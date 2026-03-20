import logging
import re

import httpx

logger = logging.getLogger(__name__)

BTIH_PATTERN = re.compile(r"urn:btih:([a-fA-F0-9]{40})", re.IGNORECASE)


def extract_hash_from_magnet(magnet_url: str) -> str:
    """Extract the info hash from a magnet URI."""
    match = BTIH_PATTERN.search(magnet_url)
    if match:
        return match.group(1).lower()
    b32_match = re.search(r"urn:btih:([A-Z2-7]{32})", magnet_url, re.IGNORECASE)
    if b32_match:
        import base64

        raw = base64.b32decode(b32_match.group(1).upper())
        return raw.hex()
    return ""


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._http = httpx.AsyncClient(timeout=15)
        self._logged_in = False

    async def login(self) -> None:
        if self._logged_in:
            return
        resp = await self._http.post(
            f"{self._base_url}/api/v2/auth/login",
            data={"username": self._username, "password": self._password},
        )
        resp.raise_for_status()
        if resp.text.strip() != "Ok.":
            raise RuntimeError(f"qBittorrent login failed: {resp.text}")
        self._logged_in = True
        logger.info("qBittorrent login successful")

    async def add_torrent(self, magnet_url: str, save_path: str) -> str:
        """Add a torrent via magnet link. Returns the info hash."""
        await self.login()
        resp = await self._http.post(
            f"{self._base_url}/api/v2/torrents/add",
            data={"urls": magnet_url, "savepath": save_path},
        )
        resp.raise_for_status()
        return extract_hash_from_magnet(magnet_url)

    async def get_status(self, torrent_hash: str) -> dict:
        await self.login()
        resp = await self._http.get(
            f"{self._base_url}/api/v2/torrents/info",
            params={"hashes": torrent_hash},
        )
        resp.raise_for_status()
        torrents = resp.json()
        if not torrents:
            return {"hash": torrent_hash, "status": "not_found"}

        t = torrents[0]
        return {
            "hash": t.get("hash", ""),
            "status": t.get("state", "unknown"),
            "progress": t.get("progress", 0),
            "download_speed": t.get("dlspeed", 0),
            "total_size": t.get("total_size", 0),
            "downloaded": t.get("downloaded", 0),
        }

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        """Remove a torrent from qBittorrent. Optionally delete downloaded files."""
        await self.login()
        resp = await self._http.post(
            f"{self._base_url}/api/v2/torrents/delete",
            data={
                "hashes": torrent_hash,
                "deleteFiles": str(delete_files).lower(),
            },
        )
        return resp.status_code == 200

    async def close(self) -> None:
        await self._http.aclose()
