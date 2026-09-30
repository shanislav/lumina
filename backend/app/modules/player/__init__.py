"""Playing a library film in the browser (outside Plex) — to check a file, a dub, lip sync.

ffmpeg turns the file into HLS on the fly (see sessions.py); the page uses hls.js.
"""

from app.core.module import Module, Permission
from app.modules.player.router import router
from app.modules.player.sessions import on_shutdown, on_startup

module = Module(
    name="player",
    title="Přehrávač",
    order=45,
    routers=[router],
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
    permissions=[Permission("player", "Přehrávat filmy v prohlížeči", default=True)],
)
