"""Tell Plex about library changes (optional, off by default).

Plex's own "scan automatically" watches the disk; this module is for setups where it
does not (network shares, Docker volumes) or as a sure thing. After an import, a rename
or an undo it asks Plex to scan just the changed movie folders (partial scan). Paths as
Lumina sees them are mapped to paths as Plex sees them — automatically (the shared tail
of the folders) or by a manual rule.
"""

from app.core.module import Module, Subscription
from app.core.schema import seed_automation
from app.modules.plex.handler import on_movie_updated
from app.modules.plex.router import router

module = Module(
    name="plex",
    title="Plex",
    order=80,
    routers=[router],
    migrations=[seed_automation("plex", "Plex (obnovení knihovny)")],
    subscriptions=[Subscription("library.movie_updated", on_movie_updated, priority=90)],
)
