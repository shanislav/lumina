"""Audio track transfer between versions of a film (decisions/0007).

Step 1 (this): compare two versions — find how the other version's audio lines up with the
reference video (offset, speed, cuts) and how sure that is. Later steps mux the track in
(mkvmerge), stretch for a different speed, split at cuts, preview clips, and the upgrade flow
"replace, but keep my SK/CZ audio".
Needs ffmpeg in the backend image.
"""

from app.core.module import Module, Permission, Subscription
from app.modules.audiosync.keep_audio import PENDING, on_download_completed, resume_pending
from app.modules.audiosync.router import RESULTS, router

module = Module(
    name="audiosync",
    title="Přenos zvuku",
    order=40,
    routers=[router],
    migrations=[RESULTS, PENDING],
    on_startup=[resume_pending],
    permissions=[Permission("audiosync", "Porovnávat a přenášet zvukové stopy mezi verzemi")],
    # after the renamer (p10), before the library import (p30): holds back "keep my CZ/SK audio" downloads
    subscriptions=[Subscription("download.completed", on_download_completed, priority=20)],
)
