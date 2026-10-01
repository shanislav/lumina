"""The check after a rename batch, for the task list."""

import time

from app.modules.plex import migration

RECENT_S = 1800


async def read() -> list[dict]:
    job = migration._job
    link = "/library?rename=1"
    if job.get("running"):
        detail = "Plex prohledává knihovnu" if job.get("phase") == "scan" else "porovnávám filmy"
        return [{"id": "plex-check", "title": "Kontrola Plexu po přejmenování", "detail": detail,
                 "running": True, "link": link}]
    finished = job.get("finished_at")
    if not finished or time.time() - finished > RECENT_S:
        return []
    state = await migration.state()
    report = (state or {}).get("report") or {}
    problems = len(report.get("missing", [])) + len(report.get("readded", []))
    detail = job.get("error") or (f"{problems} filmů k pozornosti" if problems else f"v pořádku, přesunuto {report.get('moved', 0)}")
    return [{"id": f"plex-check-{int(finished)}", "title": "Kontrola Plexu po přejmenování", "detail": detail,
             "running": False, "error": job.get("error") or (detail if problems else None), "finished_at": finished, "link": link}]
