"""CRUD + connection test for source plugins (plugins themselves live in app/sources)."""

from app.core.module import Module
from app.modules.sources.router import router
from app.sources.registry import SourceRegistry


async def _load_sources() -> None:
    await SourceRegistry.get().reload()


async def _close_sources() -> None:
    await SourceRegistry.get().close_all()


module = Module(
    name="sources",
    title="Zdroje",
    order=2,
    required=True,
    routers=[router],
    on_startup=[_load_sources],
    on_shutdown=[_close_sources],
)
