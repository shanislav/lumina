"""Checking the wanted films, for the task list."""

from app.modules.wanted import store


async def read() -> list[dict]:
    s = store.job_status()
    if not s.get("running"):
        return []
    return [{"id": "wanted-check", "title": "Kontrola filmů v Chci",
             "detail": f"{s.get('current') or ''} · nalezeno {s.get('found', 0)}".strip(" ·"),
             "done": s.get("done"), "total": s.get("total"), "running": True, "link": "/wanted"}]
