"""OpenSubtitles.com REST API (v1): search subtitles of a film, download one.

Search needs only the API key; a download needs a login (username + password of the account) and
counts against the account's daily limit. The "movie hash" of a file finds subtitles made for exactly
that release (attributes.moviehash_match).
"""

import os
import struct
import time

import httpx

API = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "Lumina v1.0"


def movie_hash(path: str) -> str:
    """OpenSubtitles hash: file size + 64-bit sums of the first and the last 64 KiB."""
    size = os.path.getsize(path)
    chunk = 65536
    if size < chunk * 2:
        return ""
    h = size
    with open(path, "rb") as f:
        for offset in (0, size - chunk):
            f.seek(offset)
            data = f.read(chunk)
            for (v,) in struct.iter_unpack("<Q", data):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


class OpenSubtitlesError(Exception):
    pass


_tokens: dict[str, tuple[float, str, str]] = {}    # username → (time, token, base url)
TOKEN_TTL_S = 12 * 3600


class OpenSubtitlesClient:
    def __init__(self, api_key: str, username: str = "", password: str = "") -> None:
        if not api_key:
            raise OpenSubtitlesError("OpenSubtitles není nastavené (API klíč v Nastavení)")
        self._key, self._user, self._password = api_key, username, password
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True, headers={
            "Api-Key": api_key, "User-Agent": USER_AGENT, "Accept": "application/json"})

    async def close(self) -> None:
        await self._http.aclose()

    async def search(self, *, tmdb_id: int = 0, imdb_id: str = "", query: str = "", languages: list[str],
                     foreign_parts: str = "include", moviehash: str = "",
                     episode: tuple[int, int] | None = None) -> list[dict]:
        """foreign_parts: include | only (forced subtitles) | exclude. episode: (season, episode) of the
        show tmdb_id is."""
        params: dict = {"languages": ",".join(sorted({l.lower() for l in languages})),
                        "order_by": "download_count", "type": "episode" if episode else "movie"}
        if foreign_parts != "include":            # the default — leaving it out avoids a redirect
            params["foreign_parts_only"] = foreign_parts
        if episode:
            params["season_number"], params["episode_number"] = episode
            if tmdb_id:
                params["parent_tmdb_id"] = tmdb_id
            else:
                params["query"] = query
        elif tmdb_id:
            params["tmdb_id"] = tmdb_id
        elif imdb_id:
            params["imdb_id"] = int(imdb_id.lstrip("t") or 0)
        else:
            params["query"] = query
        if moviehash:
            params["moviehash"] = moviehash
        # the API redirects to its canonical URL: parameters sorted by name, defaults left out
        resp = await self._http.get(f"{API}/subtitles", params=dict(sorted(params.items())))
        if resp.status_code in (401, 403):
            raise OpenSubtitlesError("OpenSubtitles odmítlo API klíč")
        resp.raise_for_status()
        out = []
        for item in resp.json().get("data", []):
            a = item.get("attributes") or {}
            files = a.get("files") or []
            if not files:
                continue
            out.append({
                "id": item.get("id"), "file_id": files[0].get("file_id"), "file_name": files[0].get("file_name") or "",
                "language": (a.get("language") or "").lower(), "release": a.get("release") or "",
                "downloads": a.get("download_count") or 0, "forced": bool(a.get("foreign_parts_only")),
                "hearing_impaired": bool(a.get("hearing_impaired")), "fps": a.get("fps") or 0,
                "hash_match": bool(a.get("moviehash_match")),
                "machine": bool(a.get("machine_translated") or a.get("ai_translated")),
                "uploader": ((a.get("uploader") or {}).get("name")) or "", "uploaded": (a.get("upload_date") or "")[:10],
            })
        return out

    async def _login(self) -> tuple[str, str]:
        if not (self._user and self._password):
            raise OpenSubtitlesError("Stažení potřebuje účet OpenSubtitles (jméno a heslo v Nastavení)")
        hit = _tokens.get(self._user)
        if hit and time.time() - hit[0] < TOKEN_TTL_S:
            return hit[1], hit[2]
        resp = await self._http.post(f"{API}/login", json={"username": self._user, "password": self._password})
        if resp.status_code in (400, 401):
            hint = " (zadej uživatelské jméno, ne e-mail)" if "@" in self._user else ""
            raise OpenSubtitlesError(f"OpenSubtitles: špatné jméno nebo heslo{hint}")
        resp.raise_for_status()
        data = resp.json()
        base = data.get("base_url") or "api.opensubtitles.com"
        base = base if base.startswith("http") else f"https://{base}/api/v1"
        _tokens[self._user] = (time.time(), data["token"], base)
        return data["token"], base

    async def download(self, file_id: int) -> tuple[bytes, dict]:
        """The subtitle file and {remaining, reset_time} of the daily limit."""
        token, base = await self._login()
        resp = await self._http.post(f"{base}/download", json={"file_id": file_id, "sub_format": "srt"},
                                     headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 406:
            raise OpenSubtitlesError("Denní limit stažení OpenSubtitles vyčerpán: "
                                     + (resp.json().get("message") or ""))
        if resp.status_code == 401:
            _tokens.pop(self._user, None)
            raise OpenSubtitlesError("OpenSubtitles: přihlášení vypršelo, zkus to znovu")
        resp.raise_for_status()
        info = resp.json()
        file = await self._http.get(info["link"])
        file.raise_for_status()
        return file.content, {"remaining": info.get("remaining"), "reset_time": info.get("reset_time") or ""}

    async def test(self) -> dict:
        """API key (a tiny search) and, when given, the account (login)."""
        await self.search(tmdb_id=603, languages=["cs"])
        out = {"api_key": True, "account": None}
        if self._user and self._password:
            await self._login()
            out["account"] = True
        return out
