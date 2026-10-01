"""Minimal in-process event bus.

Handlers of one event run sequentially by priority and share the payload dict,
so e.g. a renamer can change ``payload["path"]`` before an importer moves the file.
A failing handler is logged and skipped — it must never break other modules.

Known events:
    download.completed     {download_id, tmdb_id, title, year, content_type, path, library_action}
                           handlers may change "path" (e.g. after renaming/moving the file);
                           the library sets "imported": True once it took the file over
                           (later handlers then leave it alone); "held_by": <module> = that module
                           took the file for more work and emits the event again later
    library.collect_hints  {path, hints: [(tmdb_id, source), ...]}
                           emitted per movie file during a library scan; handlers append hints
    library.movie_updated  {movie_id, status, tmdb_id, file_path, folder, media, tmdb, versions, ...}
                           a library file changed (import, scan, rename, undo) — NFO, Plex, wanted react
    library.files_removed  {folders: [path, ...]}
                           files of the library were deleted (a version, a whole movie folder) — Plex rescans
    offers.found           {kind: "wanted", wanted_id, tmdb_id, title, year, profile, matches, best}
                           a check found offers the profile allows (for a future notification module)
    download.request       {file_ident, source, source_id, magnet_url, tmdb_id, title, year, content_type,
                            library_action, requested_by} — start a download (the downloads module does it,
                           sets "started" or "error")
    download.cancelled     {tmdb_ids: [...], stop_all: bool} — downloads Lumina started were cancelled or taken
                           out of the queue; stop_all = "Zastavit vše": background checks must not start new
                           ones (library upgrades, wanted stop their jobs)
    scheduler.run          {wanted, upgrades, auto_download_wanted, auto_download_upgrades} — the nightly
                           run; wanted / library enqueue their checks
"""

import logging

from app.core.module import EventHandler

logger = logging.getLogger(__name__)

_handlers: dict[str, list[tuple[int, str, EventHandler]]] = {}


def subscribe(event: str, handler: EventHandler, priority: int = 100, owner: str = "") -> None:
    _handlers.setdefault(event, []).append((priority, owner, handler))
    _handlers[event].sort(key=lambda h: h[0])


def clear() -> None:
    _handlers.clear()


async def emit(event: str, payload: dict) -> dict:
    for _priority, owner, handler in list(_handlers.get(event, [])):
        try:
            await handler(payload)
        except Exception:
            logger.exception("Handler of '%s' in module '%s' failed", event, owner)
    return payload
