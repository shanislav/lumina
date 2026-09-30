"""Scheduler — the nightly run (decisions/0005). Off by default.

At the configured time it emits ``scheduler.run``; the wanted list and the library (films with
"watch for a better version") enqueue their checks. It knows nothing about them (modularity).
Automatic downloads are options of this run, off by default — the modules then emit
``download.request``.

Config (automations row "scheduler"): time "HH:MM" (server local time), wanted, upgrades,
auto_download_wanted, auto_download_upgrades ("off" | "version" | "replace").
"""

from app.core.module import Module
from app.core.schema import seed_automation
from app.modules.scheduler.runner import router, start_loop, stop_loop

module = Module(
    name="scheduler",
    title="Plánovač",
    order=90,
    routers=[router],
    migrations=[seed_automation("scheduler", "Plánovač (noční hledání)")],
    on_startup=[start_loop],
    on_shutdown=[stop_loop],
)
