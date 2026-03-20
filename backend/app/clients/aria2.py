import uuid

import httpx


class Aria2Client:
    def __init__(self, rpc_url: str, secret: str = "") -> None:
        self._rpc_url = rpc_url
        self._secret = secret
        self._http = httpx.AsyncClient(timeout=15)

    async def add_uri(self, uri: str, directory: str, filename: str = "") -> str:
        """Add a download to Aria2 and return the GID."""
        params: list = []
        if self._secret:
            params.append(f"token:{self._secret}")
        params.append([uri])

        options: dict[str, str] = {"dir": directory}
        if filename:
            options["out"] = filename
        params.append(options)

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "aria2.addUri",
            "params": params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Aria2 error: {data['error']}")
        return data["result"]

    async def get_status(self, gid: str) -> dict:
        params: list = []
        if self._secret:
            params.append(f"token:{self._secret}")
        params.append(gid)

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "aria2.tellStatus",
            "params": params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Aria2 error: {data['error']}")
        return self._parse_status(data["result"])

    async def tell_active(self) -> list[dict]:
        """Return all active downloads."""
        params: list = []
        if self._secret:
            params.append(f"token:{self._secret}")

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "aria2.tellActive",
            "params": params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return []
        return [self._parse_status(r) for r in data.get("result", [])]

    async def tell_stopped(self, offset: int = 0, limit: int = 10) -> list[dict]:
        """Return recently stopped/completed downloads."""
        params: list = []
        if self._secret:
            params.append(f"token:{self._secret}")
        params.extend([offset, limit])

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "aria2.tellStopped",
            "params": params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return []
        return [self._parse_status(r) for r in data.get("result", [])]

    @staticmethod
    def _parse_status(result: dict) -> dict:
        files = result.get("files", [])
        filename = ""
        if files:
            path = files[0].get("path", "")
            filename = path.rsplit("/", 1)[-1] if "/" in path else path
        return {
            "gid": result.get("gid", ""),
            "status": result.get("status", ""),
            "total_length": int(result.get("totalLength", 0)),
            "completed_length": int(result.get("completedLength", 0)),
            "download_speed": int(result.get("downloadSpeed", 0)),
            "filename": filename,
        }

    async def force_remove(self, gid: str) -> bool:
        """Cancel an active/waiting download."""
        params: list = []
        if self._secret:
            params.append(f"token:{self._secret}")
        params.append(gid)

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "aria2.forceRemove",
            "params": params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return "error" not in data

    async def remove_result(self, gid: str) -> bool:
        """Remove a completed/error/removed download from the list."""
        params: list = []
        if self._secret:
            params.append(f"token:{self._secret}")
        params.append(gid)

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": "aria2.removeDownloadResult",
            "params": params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return "error" not in data

    async def close(self) -> None:
        await self._http.aclose()
