"""The library's background work for the task list: a scan, the check for better versions."""

from datetime import datetime

from app.modules.library import importer, upgrades

RECENT_S = 600     # a failed scan stays in the list this long


async def read() -> list[dict]:
    out = []
    j = importer.job_status()
    if j.get("running"):
        phase = {"movies": "filmy", "tv": "seriály"}.get(j.get("phase"), j.get("phase") or "")
        out.append({"id": "library-scan", "title": "Sken knihovny",
                    "detail": f"{phase}{' — ' + j['current'] if j.get('current') else ''}",
                    "done": j.get("done"), "total": j.get("total"), "running": True, "link": "/library"})
    elif j.get("error") and j.get("finished_at"):
        finished = datetime.fromisoformat(j["finished_at"]).timestamp()
        if datetime.now().timestamp() - finished < RECENT_S:
            out.append({"id": f"library-scan-{int(finished)}", "title": "Sken knihovny", "running": False,
                        "error": j["error"], "finished_at": finished, "link": "/library"})
    u = upgrades.status()
    if u.get("running"):
        out.append({"id": "library-upgrades", "title": "Hledání lepších verzí",
                    "detail": f"{u.get('current') or ''} · nalezeno {u.get('found', 0)}".strip(" ·"),
                    "done": u.get("done"), "total": u.get("total"), "running": True, "link": "/library"})
    out += _audio_language()
    return out


def _audio_language() -> list[dict]:
    """Writing the sound language of many episodes (the router's background job)."""
    import importlib
    j = importlib.import_module("app.modules.library.router").audio_language_job()
    if not j.get("total"):
        return []
    title = f"Zápis jazyka zvuku ({(j.get('lang') or '').upper()}, {j['total']} dílů)"
    if j.get("running"):
        return [{"id": "library-audio-lang", "title": title, "detail": j.get("current") or "",
                 "done": j.get("done"), "total": j.get("total"), "running": True}]
    finished = datetime.fromisoformat(j["finished_at"]).timestamp() if j.get("finished_at") else 0
    if datetime.now().timestamp() - finished > RECENT_S:
        return []
    errors = j.get("errors") or []
    return [{"id": f"library-audio-lang-{int(finished)}", "title": title, "running": False, "finished_at": finished,
             "detail": f"hotovo, chyby: {len(errors)} — {errors[0]}" if errors else "hotovo",
             "error": errors[0] if errors and len(errors) >= j["total"] else None}]
