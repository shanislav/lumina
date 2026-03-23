import logging
import uuid

import httpx

logger = logging.getLogger(__name__)


class Aria2Client:
    def __init__(self, rpc_url: str, secret: str = "") -> None:
        self._rpc_url = rpc_url
        self._secret = secret
        self._http = httpx.AsyncClient(timeout=15)

    async def _rpc(self, method: str, params: list) -> dict:
        """Execute an Aria2 JSON-RPC call."""
        full_params: list = []
        if self._secret:
            full_params.append(f"token:{self._secret}")
        full_params.extend(params)

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex[:8],
            "method": method,
            "params": full_params,
        }
        resp = await self._http.post(self._rpc_url, json=payload)
        data = resp.json()
        if "error" in data:
            err = data["error"]
            logger.error("Aria2 RPC %s failed: %s (secret=%s***)",
                         method, err, self._secret[:4] if self._secret else "NONE")
            raise RuntimeError(f"Aria2 error: {err}")
        return data

    async def add_uri(
        self, uri: str, directory: str, filename: str = "",
        single_connection: bool = False,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Add a download to Aria2 and return the GID.

        Args:
            single_connection: If True, disable segmented downloading.
                Use for servers that don't support Range requests (e.g. FastShare).
            headers: Optional HTTP headers to send with the download request.
                Used for cookie-based auth (e.g. FastShare FASTSHARE=<hash>).
        """
        options: dict[str, str | list[str]] = {"dir": directory}
        if filename:
            options["out"] = filename
        if single_connection:
            options["split"] = "1"
            options["max-connection-per-server"] = "1"
        if headers:
            # aria2 accepts headers as a list of "Key: Value" strings
            options["header"] = [f"{k}: {v}" for k, v in headers.items()]
        data = await self._rpc("aria2.addUri", [[uri], options])
        return data["result"]

    async def get_status(self, gid: str) -> dict:
        data = await self._rpc("aria2.tellStatus", [gid])
        return self._parse_status(data["result"])

    async def tell_active(self) -> list[dict]:
        """Return all active downloads."""
        try:
            data = await self._rpc("aria2.tellActive", [])
            return [self._parse_status(r) for r in data.get("result", [])]
        except RuntimeError:
            return []

    async def tell_stopped(self, offset: int = 0, limit: int = 10) -> list[dict]:
        """Return recently stopped/completed downloads."""
        try:
            data = await self._rpc("aria2.tellStopped", [offset, limit])
            return [self._parse_status(r) for r in data.get("result", [])]
        except RuntimeError:
            return []

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
            # Keep full files data to allow retrieving absolute path in post-processing
            "files": result.get("files", []),
        }

    async def force_remove(self, gid: str) -> bool:
        """Cancel an active/waiting download."""
        try:
            await self._rpc("aria2.forceRemove", [gid])
            return True
        except RuntimeError:
            return False

    async def remove_result(self, gid: str) -> bool:
        """Remove a completed/error/removed download from the list."""
        try:
            await self._rpc("aria2.removeDownloadResult", [gid])
            return True
        except RuntimeError:
            return False

    async def close(self) -> None:
        await self._http.aclose()
