from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.tasks import ensure_monitor_running
from app.routers import search, download, sources, settings, duplicates, integrations
from app.sources.registry import SourceRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    ensure_monitor_running()
    await SourceRegistry.get().reload()
    yield
    await SourceRegistry.get().close_all()


app = FastAPI(title="Lumina", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(download.router)
app.include_router(sources.router)
app.include_router(settings.router)
app.include_router(duplicates.router)
app.include_router(integrations.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "sources": len(SourceRegistry.get().sources)}
