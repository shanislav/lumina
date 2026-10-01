"""Audio of a film across its versions (decisions/0007): the editor in the library.

The user picks the reference track (the one that fits the picture, checked by watching); every
other track — of the same file or of another version — is measured against it directly. Tracks
that fit are moved into the chosen version, shifted, stretched or assembled across cuts, and the
result is checked before the library takes it over. Downloads only replace or add a version —
nothing happens to the audio on its own.
Needs ffmpeg and mkvtoolnix in the backend image.
"""

from app.core.module import Module, Permission
from app.modules.audiosync.router import MAPS, REFS, RESULTS, router

# migration 2 of a removed feature (keep-audio on download) — migrations are numbered by position
PENDING = """
CREATE TABLE IF NOT EXISTS audiosync_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

module = Module(
    name="audiosync",
    title="Přenos zvuku",
    order=40,
    routers=[router],
    migrations=[RESULTS, PENDING, MAPS, REFS],
    permissions=[Permission("audiosync", "Editor zvuku: porovnávat, opravovat a přenášet zvukové stopy")],
)
