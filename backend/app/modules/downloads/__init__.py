"""Starting downloads (Aria2 / qBittorrent), download list and completion monitor.

When a tracked download finishes, the monitor emits ``download.completed``;
post-processing (renaming, library import, ...) is done by the modules
subscribed to that event.
"""

from app.core.migrations import add_column
from app.core.module import Module
from app.modules.downloads.monitor import ensure_monitor_running
from app.modules.downloads.router import router
from app.modules.downloads.store import DOWNLOAD_TRACKER_V1

module = Module(
    name="downloads",
    title="Stahování",
    order=10,
    required=True,
    routers=[router],
    migrations=[
        DOWNLOAD_TRACKER_V1,
        add_column("download_tracker", "content_type", "TEXT DEFAULT 'movie'"),
        add_column("download_tracker", "intent", "TEXT DEFAULT ''"),
    ],
    on_startup=[ensure_monitor_running],
)
