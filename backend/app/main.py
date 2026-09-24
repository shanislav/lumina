import inspect
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import registry
from app.db import init_db
from app.sources.registry import SourceRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
logger = logging.getLogger("app")

ALL_MODULES = registry.discover()
ACTIVE_MODULES = registry.active(ALL_MODULES, registry.read_disabled())


async def _run_hooks(hooks) -> None:
    for hook in hooks:
        result = hook()
        if inspect.isawaitable(result):
            await result


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrations run for all modules (also disabled ones) so the schema stays
    # complete and a module can be enabled later without surprises.
    await init_db(ALL_MODULES)
    registry.register_subscriptions(ACTIVE_MODULES)
    for module in ACTIVE_MODULES:
        await _run_hooks(module.on_startup)
    logger.info("Active modules: %s", ", ".join(m.name for m in ACTIVE_MODULES))
    yield
    for module in reversed(ACTIVE_MODULES):
        try:
            await _run_hooks(module.on_shutdown)
        except Exception:
            logger.exception("Shutdown of module '%s' failed", module.name)


app = FastAPI(title="Lumina", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for _module in ACTIVE_MODULES:
    for _router in _module.routers:
        app.include_router(_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "sources": len(SourceRegistry.get().sources)}


@app.get("/api/modules")
async def list_modules() -> list[dict]:
    active = {m.name for m in ACTIVE_MODULES}
    return [
        {"name": m.name, "title": m.title, "required": m.required, "active": m.name in active}
        for m in ALL_MODULES
    ]
