"""Subtitles of library films: what a film has (tracks in the file, files next to it), whether it may
need forced subtitles (TMDB lists more spoken languages), and downloading them from OpenSubtitles.com
next to the video ("<video>.cs.forced.srt") — Plex reads them from there.
Settings: opensubtitles_api_key (search), opensubtitles_username / _password (downloads).
"""

from app.core.module import Module, Permission
from app.modules.subtitles.router import router

module = Module(
    name="subtitles",
    title="Titulky",
    order=42,
    routers=[router],
    permissions=[Permission("subtitles", "Hledat a stahovat titulky (zapisuje do knihovny)")],
)
