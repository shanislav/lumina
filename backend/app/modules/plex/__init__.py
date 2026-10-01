"""Tell Plex about library changes (optional, off by default).

Plex's own "scan automatically" watches the disk; this module is for setups where it
does not (network shares, Docker volumes) or as a sure thing. After an import, a rename
or an undo it asks Plex to scan just the changed movie folders (partial scan). Paths as
Lumina sees them are mapped to paths as Plex sees them — as they are, or by a manual rule
(the settings suggest one, never guessed silently).

With Plex set up (even without the scans) it also gives the library import a hint which
movie a file is, and runs a big rename so that Plex keeps its movies (``migration``).
"""

from app.core.migrations import add_column
from app.core.module import Module, Subscription, TaskSource
from app.core.schema import seed_automation
from app.modules.plex import hints, migration, tasks
from app.modules.plex.handler import on_movie_updated
from app.modules.plex.router import router

module = Module(
    name="plex",
    title="Plex",
    order=80,
    routers=[router],
    migrations=[seed_automation("plex", "Plex (obnovení knihovny)"), migration.TABLES,
                add_column("plex_snapshot", "edits", "TEXT")],
    subscriptions=[Subscription("library.movie_updated", on_movie_updated, priority=90),
                   Subscription("library.collect_hints", hints.on_collect_hints)],
    tasks=[TaskSource(tasks.read, "library.edit")],
)
