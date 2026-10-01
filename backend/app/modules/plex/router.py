from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.clients.plex import PlexClient
from app.config import get_effective_settings, movies_library_dir
from app.modules.plex import friends, migration
from app.modules.plex.library import PlexError
from app.modules.plex.paths import section_for, suggest_rule, to_plex
from app.core.auth import require
from app.db import get_automation

router = APIRouter(prefix="/api/plex", tags=["plex"])


class PlexTest(BaseModel):
    url: str
    token: str
    path_map: str = ""


@router.post("/test", dependencies=[Depends(require("settings"))])
async def test_connection(body: PlexTest):
    """Connect with the settings being edited (not saved yet) and show where the library maps."""
    client = PlexClient(body.url, body.token)
    try:
        sections = await client.sections()
    except Exception as e:
        return {"ok": False, "error": str(e) or type(e).__name__}
    finally:
        await client.close()
    movies = [s for s in sections if s["type"] == "movie"]
    root = movies_library_dir(await get_effective_settings())
    locations = [loc for s in movies for loc in s["locations"]]
    mapped = to_plex(root, root, locations, body.path_map) if root else None
    section = section_for(mapped, movies) if mapped else None
    return {
        "ok": True,
        "sections": [{"title": s["title"], "type": s["type"], "locations": s["locations"]} for s in sections],
        "library_root": root,
        "plex_path": mapped,
        "section": section["title"] if section else None,
        "suggested_map": None if section or not root else suggest_rule(root, locations),
    }


# ─── A big rename (decisions/0008) ───

def _fail(e: PlexError):
    raise HTTPException(400, str(e))


@router.get("/migration", dependencies=[Depends(require("library.edit"))])
async def migration_overview() -> dict:
    return await migration.overview()


@router.post("/migration/start", dependencies=[Depends(require("library.edit"))])
async def migration_start() -> dict:
    try:
        return await migration.start()
    except PlexError as e:
        _fail(e)


@router.post("/migration/check", dependencies=[Depends(require("library.edit"))])
async def migration_check() -> dict:
    """Scan the section once and compare with the snapshot (in the background)."""
    if not await migration.active():
        raise HTTPException(400, "Migrace neběží")
    return {"started": migration.start_check()}


class FinishBody(BaseModel):
    empty_trash: bool = False
    repair: bool = False


@router.post("/migration/finish", dependencies=[Depends(require("library.edit"))])
async def migration_finish(body: FinishBody) -> dict:
    try:
        return await migration.finish(body.empty_trash, body.repair)
    except PlexError as e:
        _fail(e)


# ─── Friends' shared libraries ───

@router.get("/friends/movie", dependencies=[Depends(require("search"))])
async def friends_movie(tmdb_id: int = 0, imdb_id: str = "") -> list[dict]:
    """Who of your friends has this film in a library shared with you (last known state)."""
    return await friends.who_has(tmdb_id or None, imdb_id or None)


@router.get("/friends", dependencies=[Depends(require("settings"))])
async def friends_servers() -> list[dict]:
    return await friends.servers()


@router.post("/friends/refresh", dependencies=[Depends(require("settings"))])
async def friends_refresh() -> dict:
    return await friends.refresh()
