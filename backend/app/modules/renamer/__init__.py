"""Renames a finished download by a configurable pattern (MediaInfo tags).

Runs first on ``download.completed`` and updates ``payload["path"]`` so that
modules running later work with the renamed file.
"""

from app.core.module import Module, Subscription
from app.core.schema import seed_automation
from app.modules.renamer.handler import on_download_completed

module = Module(
    name="renamer",
    title="Renamer",
    order=50,
    migrations=[seed_automation("renamer", "Renamer (Media Info)")],
    subscriptions=[Subscription("download.completed", on_download_completed, priority=10)],
)
