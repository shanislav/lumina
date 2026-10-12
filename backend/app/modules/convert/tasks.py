"""The queue in the task list: the running job, and how many wait."""

import os

from app.modules.convert import jobs


async def read() -> list[dict]:
    if not jobs.current:
        return []
    waiting = sum(1 for j in await jobs.recent(500) if j["status"] == "queued")
    phase = jobs.PHASES.get(jobs.current.get("phase") or "", "")
    value = jobs.current.get("value")
    if phase and value is not None:
        phase += f" {value:.0f} %" if jobs.current.get("phase") == "encode" else f" {value:g}"
    detail = os.path.basename(jobs.current["path"]) + (f" — {phase}" if phase else "") + (
        f" · ve frontě {waiting}" if waiting else "")
    return [{"id": f"convert-{jobs.current['id']}", "title": jobs.KINDS.get(jobs.current["kind"], "Úprava souboru"),
             "detail": detail, "running": True, "link": "/library?convert=1"}]
