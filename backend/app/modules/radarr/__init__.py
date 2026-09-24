"""Hands finished movie downloads over to Radarr (blackhole or scan).

Temporary bridge — Lumina is going to replace Radarr, after which this module
is removed.
"""

from app.core.module import Module, Subscription
from app.core.schema import seed_automation
from app.modules.radarr.handler import on_download_completed
from app.modules.radarr.hints import on_collect_hints

module = Module(
    name="radarr",
    title="Radarr",
    order=60,
    migrations=[seed_automation("radarr", "Radarr")],
    subscriptions=[
        Subscription("download.completed", on_download_completed, priority=50),
        Subscription("library.collect_hints", on_collect_hints),
    ],
)
