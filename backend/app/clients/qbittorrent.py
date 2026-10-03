import hashlib
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


def _bencode_end(data: bytes, i: int) -> int:
    """Index just past the bencoded value starting at ``i``."""
    c = data[i:i + 1]
    if c == b"i":
        return data.index(b"e", i) + 1
    if c in (b"l", b"d"):
        i += 1
        while data[i:i + 1] != b"e":
            i = _bencode_end(data, i)
        return i + 1
    colon = data.index(b":", i)
    return colon + 1 + int(data[i:colon])


def info_hash(torrent: bytes) -> str:
    """The info hash of a .torrent file (SHA-1 of its bencoded "info" dictionary)."""
    if torrent[:1] != b"d":
        raise ValueError("not a torrent file")
    i = 1
    while torrent[i:i + 1] != b"e":
        key_end = _bencode_end(torrent, i)
        key = torrent[torrent.index(b":", i) + 1:key_end]
        value_end = _bencode_end(torrent, key_end)
        if key == b"info":
            return hashlib.sha1(torrent[key_end:value_end]).hexdigest()
        i = value_end
    raise ValueError("torrent file without info")


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
        # 204 without a body: no login needed (address in qBittorrent's "bypass authentication" list)
        if resp.status_code != 204 and resp.text.strip() != "Ok.":
            raise RuntimeError(f"qBittorrent login failed: {resp.text}")
        self._logged_in = True
        logger.info("qBittorrent login successful")

    async def add_torrent(self, magnet_url: str, save_path: str) -> str:
        """Add a torrent by a magnet link or a link to a .torrent file (private trackers through
        Jackett / Prowlarr give those). Returns the info hash."""
        await self.login()
        if not magnet_url.startswith("magnet:"):
            # Lumina fetches the file itself (the indexer is reachable from here, not always from
            # qBittorrent) — a link may also redirect to a magnet
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
                resp = await http.get(magnet_url)
                while resp.is_redirect:
                    target = resp.headers.get("location", "")
                    if target.startswith("magnet:"):
                        return await self.add_torrent(target, save_path)
                    resp = await http.get(httpx.URL(magnet_url).join(target))
                resp.raise_for_status()
            torrent = resp.content
            torrent_hash = info_hash(torrent)
            resp = await self._http.post(
                f"{self._base_url}/api/v2/torrents/add",
                data={"savepath": save_path} if save_path else {},
                files={"torrents": ("lumina.torrent", torrent, "application/x-bittorrent")},
            )
            resp.raise_for_status()
            return torrent_hash
        resp = await self._http.post(
            f"{self._base_url}/api/v2/torrents/add",
            data={"urls": magnet_url, **({"savepath": save_path} if save_path else {})},
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
            "state": t.get("state", "unknown"),
            "name": t.get("name", ""),
            "save_path": t.get("save_path", ""),
            "content_path": t.get("content_path", ""),
            "progress": t.get("progress", 0),
            "download_speed": t.get("dlspeed", 0),
            "total_size": t.get("total_size", 0),
            "downloaded": t.get("downloaded", 0),
        }

    async def files(self, torrent_hash: str) -> list[dict]:
        """The torrent's files ([] until a magnet's metadata is in): {index, name (path in the torrent), size,
        priority, progress}."""
        await self.login()
        resp = await self._http.get(f"{self._base_url}/api/v2/torrents/files", params={"hash": torrent_hash})
        if resp.status_code != 200:
            return []
        return [{**f, "index": f.get("index", i)} for i, f in enumerate(resp.json())]

    async def set_file_priority(self, torrent_hash: str, indexes: list[int], priority: int) -> None:
        """0 = do not download; 1 = normal."""
        if not indexes:
            return
        await self.login()
        resp = await self._http.post(f"{self._base_url}/api/v2/torrents/filePrio",
                                     data={"hash": torrent_hash, "id": "|".join(str(i) for i in indexes),
                                           "priority": str(priority)})
        resp.raise_for_status()

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
