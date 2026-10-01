"""Running downloads, for the task list."""

from app.modules.downloads.router import list_downloads


def _speed(bps: float) -> str:
    return f"{bps / 1024**2:.1f} MB/s" if bps else ""


async def read() -> list[dict]:
    """Running and waiting downloads (aria2 and qBittorrent, as list_downloads gives them)."""
    out = []
    for d in (await list_downloads())["downloads"]:
        total, done = float(d.get("total_length") or 0), float(d.get("completed_length") or 0)
        if d.get("backend") == "qbittorrent":
            if (d.get("progress") or 0) >= 1:
                continue
        elif d.get("status") not in ("active", "waiting"):
            continue
        waiting = d.get("status") == "waiting"
        out.append({"id": f"dl-{d.get('gid') or d.get('hash')}", "title": d.get("filename") or "Stahování",
                    "detail": " · ".join(x for x in (d.get("source_label"),
                                                     "čeká" if waiting else _speed(float(d.get("download_speed") or 0))) if x),
                    "done": done, "total": total, "running": True, "unit": "bytes"})
    return out
