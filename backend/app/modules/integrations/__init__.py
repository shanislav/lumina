"""API for listing/configuring automations (the rows seeded by renamer/nfo modules)."""

from app.core.module import Module
from app.modules.integrations.router import router

module = Module(name="integrations", title="Integrace", order=40, routers=[router])
