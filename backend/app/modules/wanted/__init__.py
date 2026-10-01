"""Wanted films — what the user wants to get, each with a quality profile (decisions/0005).

"Hledat teď" (and later the scheduler) looks for offers of the film (app/core/offers), keeps
the ones the profile allows and remembers the best. When the film shows up in the library
(event library.movie_updated), it is done. Found offers are announced as event offers.found,
so a notification module can pick them up later.
"""

from app.core.module import Module, Permission, Subscription, TaskSource
from app.modules.wanted import tasks
from app.modules.wanted.router import router
from app.modules.wanted.store import WANTED, on_download_cancelled, on_movie_updated, on_scheduler_run

module = Module(
    name="wanted",
    title="Chci",
    order=25,
    routers=[router],
    permissions=[Permission("wanted", "Přidávat a spravovat filmy v Chci", default=True)],
    migrations=[WANTED],
    subscriptions=[Subscription("library.movie_updated", on_movie_updated, priority=80),
                   Subscription("scheduler.run", on_scheduler_run),
                   Subscription("download.cancelled", on_download_cancelled)],
    tasks=[TaskSource(tasks.read, "wanted")],
)
