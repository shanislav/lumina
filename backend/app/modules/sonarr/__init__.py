"""Hands finished TV downloads over to Sonarr (blackhole or scan)."""

from app.core.module import Module, Subscription
from app.core.schema import seed_automation
from app.modules.sonarr.handler import on_download_completed

module = Module(
    name="sonarr",
    title="Sonarr",
    order=60,
    migrations=[seed_automation("sonarr", "Sonarr")],
    subscriptions=[Subscription("download.completed", on_download_completed, priority=50)],
)
