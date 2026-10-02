"""Starting downloads (Aria2 / qBittorrent), download list and completion monitor.

When a tracked download finishes, the monitor emits ``download.completed``;
post-processing (renaming, library import, ...) is done by the modules
subscribed to that event.
"""

from app.core.migrations import add_column
from app.core.module import Module, Permission, Subscription, TaskSource
from app.modules.downloads import tasks
from app.modules.downloads.monitor import ensure_monitor_running
from app.modules.downloads.router import on_download_request, router
from app.modules.downloads.queue import DOWNLOAD_QUEUE
from app.modules.downloads.store import DOWNLOAD_TRACKER_V1, prune_history

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
        # source label (WebShare / FastShare / Torrent) survives a restart
        add_column("download_tracker", "source_label", "TEXT DEFAULT ''"),
        # who asked for it (a user, "Chci (plánovač)" …) and when — the download list shows it
        add_column("download_tracker", "requested_by", "TEXT DEFAULT ''"),
        add_column("download_tracker", "created_at", "TEXT DEFAULT ''"),
        # downloads over the limit of concurrent ones wait here
        DOWNLOAD_QUEUE,
        # the download list is Lumina's own history (aria2 / qBittorrent forget finished ones)
        add_column("download_tracker", "finished_at", "TEXT DEFAULT ''"),
        add_column("download_tracker", "file_name", "TEXT DEFAULT ''"),
        add_column("download_tracker", "size", "INTEGER DEFAULT 0"),
    ],
    subscriptions=[Subscription("download.request", on_download_request)],
    on_startup=[ensure_monitor_running, prune_history],
    permissions=[Permission("download", "Stahovat (a rušit stahování)", default=True)],
    tasks=[TaskSource(tasks.read, "download")],
)
