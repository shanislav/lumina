import hashlib
import logging
import xml.etree.ElementTree as ET

import httpx
from passlib.hash import md5_crypt

from app.core.mediainfo import normalize_language
from app.models.schemas import WebShareFile

logger = logging.getLogger(__name__)

API_BASE = "https://webshare.cz/api"


class WebShareClient:
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._token: str | None = None
        self._http = httpx.AsyncClient(timeout=30)

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        self._token = await self._login()
        return self._token

    async def _login(self) -> str:
        salt_resp = await self._http.post(
            f"{API_BASE}/salt/",
            data={"username_or_email": self._username},
        )
        salt_resp.raise_for_status()
        root = ET.fromstring(salt_resp.text)
        salt_status = root.findtext("status", default="")
        if salt_status != "OK":
            raise RuntimeError(
                f"WebShare salt failed: {salt_status} - "
                f"{root.findtext('message', default='unknown error')}"
            )
        salt = root.findtext("salt", default="")
        if not salt:
            raise RuntimeError("WebShare returned empty salt")

        encrypted = md5_crypt.using(salt=salt).hash(self._password)
        password_hash = hashlib.sha1(encrypted.encode("utf-8")).hexdigest()

        digest = hashlib.md5(
            f"{self._username}:Webshare:{password_hash}".encode("utf-8")
        ).hexdigest()

        login_resp = await self._http.post(
            f"{API_BASE}/login/",
            data={
                "username_or_email": self._username,
                "password": password_hash,
                "digest": digest,
                "keep_logged_in": 1,
            },
        )
        login_resp.raise_for_status()
        root = ET.fromstring(login_resp.text)
        status = root.findtext("status", default="")
        if status != "OK":
            message = root.findtext("message", default="unknown error")
            logger.error("WebShare login failed: status=%s message=%s", status, message)
            raise RuntimeError(f"WebShare login failed: {status} - {message}")

        token = root.findtext("token", default="")
        if not token:
            raise RuntimeError("WebShare login returned no token")
        logger.info("WebShare login successful")
        return token

    async def search(self, query: str, limit: int = 30) -> list[WebShareFile]:
        token = await self._ensure_token()
        resp = await self._http.post(
            f"{API_BASE}/search/",
            data={
                "what": query,
                "sort": "largest",
                "limit": limit,
                "offset": 0,
                "wst": token,
            },
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        files: list[WebShareFile] = []
        for f in root.iter("file"):
            ident = f.findtext("ident", default="")
            name = f.findtext("name", default="")
            size = int(f.findtext("size", default="0"))
            pos = int(f.findtext("positive_votes", default="0"))
            neg = int(f.findtext("negative_votes", default="0"))
            if ident and name:
                files.append(
                    WebShareFile(
                        ident=ident,
                        name=name,
                        size=size,
                        positive_votes=pos,
                        negative_votes=neg,
                    )
                )
        return files

    async def get_download_link(self, ident: str) -> str:
        token = await self._ensure_token()
        resp = await self._http.post(
            f"{API_BASE}/file_link/",
            data={"ident": ident, "wst": token},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        link = root.findtext("link", default="")
        if not link:
            raise RuntimeError(f"No download link for ident={ident}")
        return link

    async def file_info(self, ident: str) -> dict | None:
        """Technical info WebShare keeps about a file (official API, no page scraping)."""
        token = await self._ensure_token()
        resp = await self._http.post(f"{API_BASE}/file_info/", data={"ident": ident, "wst": token})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.findtext("status") != "OK":
            return None

        def num(el, tag) -> int:
            try:
                return int(float(el.findtext(tag) or 0))
            except ValueError:
                return 0

        video = root.find("video/stream")
        audio = []
        for stream in root.findall("audio/stream"):
            audio.append({
                "lang": normalize_language(stream.findtext("language")),
                "codec": stream.findtext("format") or "",
                "channels": num(stream, "channels"),
            })
        return {
            "duration_s": num(root, "length"),
            "width": num(video, "width") if video is not None else num(root, "width"),
            "height": num(video, "height") if video is not None else num(root, "height"),
            "video_codec": (video.findtext("format") if video is not None else root.findtext("format")) or "",
            "bitrate": num(root, "bitrate"),
            "audio": audio,
            "subtitles": [],
        }

    async def close(self) -> None:
        await self._http.aclose()
