import inspect
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import registry
from app.core.auth import User, register_permissions, require
from app.db import init_db
from app.sources.registry import SourceRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per HTTP call is too noisy
logger = logging.getLogger("app")

ALL_MODULES = registry.discover()
ACTIVE_MODULES = registry.active(ALL_MODULES, registry.read_disabled())
register_permissions(ACTIVE_MODULES)


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

# The UI calls the API on its own origin (nginx /api/); cross-origin callers get no cookies
# (no allow_credentials), so they cannot act as a signed-in user.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for _module in ACTIVE_MODULES:
    for _router in _module.routers:
        # every module endpoint needs a signed-in user; permissions are checked per endpoint
        app.include_router(_router, dependencies=[Depends(require())] if _module.requires_login else [])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "sources": len(SourceRegistry.get().sources)}


@app.get("/api/tasks")
async def tasks(user: User = Depends(require())) -> list[dict]:
    """Background work of all active modules the user may see (the task button in the navigation)."""
    out: list[dict] = []
    for module in ACTIVE_MODULES:
        for source in module.tasks:
            if source.permission and not user.can(source.permission):
                continue
            try:
                out += [{"module": module.name, **t} for t in await source.read()]
            except Exception:  # noqa: BLE001 — one module's trouble must not hide the others
                logger.debug("tasks of module '%s' failed", module.name, exc_info=True)
    return out


@app.get("/api/modules", dependencies=[Depends(require())])
async def list_modules() -> list[dict]:
    """active = running now; enabled = what the setting says (differs until a backend restart)."""
    from app.core.registry import read_disabled

    active = {m.name for m in ACTIVE_MODULES}
    disabled = read_disabled()
    return [
        {"name": m.name, "title": m.title, "required": m.required, "active": m.name in active,
         "enabled": m.required or m.name not in disabled}
        for m in ALL_MODULES
    ]
