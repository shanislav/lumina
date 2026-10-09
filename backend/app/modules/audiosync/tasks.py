"""The audio editor's job (measuring tracks, building a file), for the task list."""

import importlib
import time

KIND = {"map": "Měření zvukových stop", "apply": "Úprava zvuku filmu", "dub": "Přenos dabingu dílů"}
PHASE = {
    "start": "začínám", "target": "kontroluji stopy cíle", "align": "srovnávám verze", "tracks": "měřím jednotlivé stopy",
    "track": "měřím stopu zvlášť", "compare": "porovnávám dabingy", "speed": "zjišťuji rychlost",
    "windows": "porovnávám úseky filmu", "cuts": "hledám místa střihu", "prepare": "připravuji stopy",
    "mux": "skládám soubor", "verify": "kontroluji výsledek", "import": "předávám knihovně",
}
RECENT_S = 3600    # a finished job (its result or error) stays in the list this long


async def read() -> list[dict]:
    job = importlib.import_module("app.modules.audiosync.router")._job
    if not job.get("kind"):
        return []
    title = f"{KIND.get(job['kind'], 'Práce se zvukem')}{' — ' + job['title'] if job.get('title') else ''}"
    link = (f"/series?tmdb={job['tmdb_id']}" if job.get("kind") == "dub" else f"/library/audio?tmdb={job['tmdb_id']}")         if job.get("tmdb_id") else None
    if job.get("running"):
        phase = PHASE.get(job.get("phase") or "", job.get("phase") or "")
        return [{"id": "audiosync", "title": title, "detail": f"{phase}{' — ' + job['current'] if job.get('current') else ''}",
                 "done": job.get("done"), "total": job.get("total"), "running": True, "link": link}]
    finished = job.get("finished_at")
    if not finished or time.time() - finished > RECENT_S:
        return []
    if job.get("kind") == "dub":
        rep = job.get("report") or []
        ok = sum(1 for r in rep if r.get("status") == "ok")
        detail = job.get("error") or f"hotovo — přeneseno {ok} z {job.get('total') or len(rep)}" + (
            f", nejisté / chyba {len(rep) - ok}" if len(rep) > ok else "")
    else:
        added = (job.get("report") or {}).get("added") or []
        detail = job.get("error") or ("hotovo" + (f" — přidáno {', '.join(added)}" if added else ""))
    return [{"id": f"audiosync-{int(finished)}", "title": title, "detail": detail, "running": False,
             "error": job.get("error"), "finished_at": finished, "link": link}]
