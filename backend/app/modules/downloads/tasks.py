"""Running downloads, for the task list."""

import os

from app.modules.downloads.router import list_downloads


def _speed(bps: float) -> str:
    return f"{bps / 1024**2:.1f} MB/s" if bps else ""


async def read() -> list[dict]:
    out = []
    for d in (await list_downloads())["downloads"]:
        if d.get("backend") == "qbittorrent":
            if (d.get("progress") or 0) >= 1:
                continue
            out.append({"id": f"dl-{d['hash']}", "title": d.get("filename") or "Torrent",
                        "detail": " · ".join(x for x in (d.get("source_label"), _speed(d.get("download_speed", 0))) if x),
                        "done": d.get("completed_length"), "total": d.get("total_length"), "running": True,
                        "unit": "bytes"})
            continue
        if d.get("status") not in ("active", "waiting"):
            continue
        files = d.get("files") or [{}]
        name = os.path.basename(files[0].get("path") or "") or (d.get("bittorrent") or {}).get("info", {}).get("name") \
            or d.get("gid", "")
        out.append({"id": f"dl-{d.get('gid')}", "title": name,
                    "detail": " · ".join(x for x in (d.get("source_label"),
                                                     "čeká" if d["status"] == "waiting" else _speed(float(d.get("downloadSpeed") or 0)))
                                         if x),
                    "done": float(d.get("completedLength") or 0), "total": float(d.get("totalLength") or 0),
                    "running": True, "unit": "bytes"})
    return out
