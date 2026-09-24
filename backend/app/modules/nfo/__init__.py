"""NFO files (Kodi/Jellyfin format) written by Lumina — optional, off by default.

Also a backup of Lumina's DB: a library scan trusts NFO files carrying the
<lumina> block (docs/decisions/0003). When enabled, every movie update keeps the
folder tidy: old NFO files are removed and one current movie.nfo is written.
"""

from app.core.module import Module, Subscription
from app.core.schema import seed_automation
from app.modules.nfo.writer import on_movie_updated

module = Module(
    name="nfo",
    title="NFO soubory",
    order=70,
    migrations=[seed_automation("nfo", "NFO soubory (záloha knihovny)")],
    subscriptions=[Subscription("library.movie_updated", on_movie_updated, priority=50)],
)
