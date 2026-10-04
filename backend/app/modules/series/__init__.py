"""TV shows (docs SERIALY): a show's page with every season and the state of each episode,
the user's settings per show (profile, language mode, torrents) and its automation (auto.py: new
episodes, the Czech/Slovak dub of English ones — off / show / download, checked by the scheduler).

Finding and downloading an episode uses the common offers (app/core/offers with season + episode)
and the downloads module; the library module imports it (library.imports) and keeps the list of
owned episodes (library_episodes), which this module reads.
"""

from app.core.migrations import add_column
from app.core.module import Module, Subscription, TaskSource
from app.modules.series import auto, overview
from app.modules.series.router import router
from app.modules.series.store import SERIES_NO_DUB, SERIES_SETTINGS

module = Module(
    name="series",
    title="Seriály",
    order=27,
    routers=[router],
    migrations=[SERIES_SETTINGS, SERIES_NO_DUB,
                add_column("series_settings", "auto_new", "TEXT"),
                add_column("series_settings", "auto_from", "TEXT"),
                add_column("series_settings", "auto_dub", "TEXT"),
                auto.SERIES_AUTO,
                add_column("series_settings", "auto_upgrade", "TEXT"),
                overview.SERIES_OVERVIEW],
    subscriptions=[Subscription("scheduler.run", auto.on_scheduler_run)],
    tasks=[TaskSource(auto.tasks, "library.edit")],
)
