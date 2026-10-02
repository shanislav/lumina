"""TV shows (docs SERIALY): a show's page with every season and the state of each episode,
the user's settings per show (profile, language mode, torrents, watching for new episodes).

Finding and downloading an episode uses the common offers (app/core/offers with season + episode)
and the downloads module; the library module imports it (library.imports) and keeps the list of
owned episodes (library_episodes), which this module reads.
"""

from app.core.module import Module
from app.modules.series.router import router
from app.modules.series.store import SERIES_SETTINGS

module = Module(
    name="series",
    title="Seriály",
    order=27,
    routers=[router],
    migrations=[SERIES_SETTINGS],
)
