"""App settings API, setup status, folder browser, language list."""

from app.core.module import Module
from app.modules.settings.router import router

module = Module(name="settings", title="Nastavení", order=1, required=True, routers=[router])
