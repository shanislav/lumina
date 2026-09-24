"""Minimal in-process event bus.

Handlers of one event run sequentially by priority and share the payload dict,
so e.g. a renamer can change ``payload["path"]`` before an importer moves the file.
A failing handler is logged and skipped — it must never break other modules.

Known events:
    download.completed     {download_id, tmdb_id, title, year, content_type, path, library_action}
                           handlers may change "path" (e.g. after renaming/moving the file);
                           the library sets "imported": True once it took the file over
                           (later handlers then leave it alone)
    library.collect_hints  {path, hints: [(tmdb_id, source), ...]}
                           emitted per movie file during a library scan; handlers append hints
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
