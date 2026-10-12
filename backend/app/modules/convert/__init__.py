"""Úpravy souborů (decisions/0009): changes of library files in a background queue, each checked before it takes
the file's place: Dolby Vision MKV → MP4 (lossless — Android TV players do not play DV in MKV), on its own after a
download and by hand; smaller files at a chosen quality or bitrate (re-encoding, with a preview). Encoding can
run on the server or on a worker — the user's PC with the share mounted (workers.py)."""

from app.core.module import Module, Permission, Subscription, TaskSource
from app.modules.convert import handlers, jobs, tasks, workers
from app.modules.convert.router import router

module = Module(
    name="convert",
    title="Úpravy souborů",
    order=45,
    routers=[router, workers.router],
    migrations=[jobs.JOBS, jobs.OPTIONS, jobs.RESULT, workers.WORKERS, jobs.WORKER, jobs.LEASE, jobs.PROGRESS],
    subscriptions=[Subscription("download.completed", handlers.on_download_completed, priority=90)],
    on_startup=[jobs.start],
    on_shutdown=[jobs.stop],
    permissions=[Permission("convert", "Úpravy souborů: přebalit Dolby Vision do MP4, zmenšit soubory")],
    tasks=[TaskSource(tasks.read, "convert")],
    # the worker's endpoints take its token, not a signed-in user — every other route guards itself (require)
    requires_login=False,
)
