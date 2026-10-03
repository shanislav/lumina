"""Notifications (store.py): a bell in the navigation with what happened — downloads landed or failed, the wanted
list and the nightly check found something, the TV automation found or started episodes. Built only from the
other modules' events."""

from app.core.module import Module, Subscription
from app.modules.notify import handlers
from app.modules.notify.router import router
from app.modules.notify.store import NOTIFICATIONS

module = Module(
    name="notify",
    title="Upozornění",
    order=95,
    routers=[router],
    migrations=[NOTIFICATIONS],
    subscriptions=[Subscription("download.completed", handlers.on_download_completed, priority=95),
                   Subscription("download.failed", handlers.on_download_failed),
                   Subscription("offers.found", handlers.on_offers_found),
                   Subscription("series.found", handlers.on_series_found)],
)
