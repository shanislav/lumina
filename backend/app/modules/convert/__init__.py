"""Úpravy souborů (decisions/0009): changes of library files in a background queue, each checked before it takes
the file's place. Now: Dolby Vision MKV → MP4 (lossless — Android TV players do not play DV in MKV), on its own
after a download and by hand. Later: smaller files (re-encoding), AVI → MKV."""

from app.core.module import Module, Permission, Subscription, TaskSource
from app.modules.convert import handlers, jobs, tasks
from app.modules.convert.router import router

module = Module(
    name="convert",
    title="Úpravy souborů",
    order=45,
    routers=[router],
    migrations=[jobs.JOBS, jobs.OPTIONS, jobs.RESULT],
    subscriptions=[Subscription("download.completed", handlers.on_download_completed, priority=90)],
    on_startup=[jobs.start],
    on_shutdown=[jobs.stop],
    permissions=[Permission("convert", "Úpravy souborů: přebalit Dolby Vision do MP4, zmenšit soubory")],
    tasks=[TaskSource(tasks.read, "convert")],
)
