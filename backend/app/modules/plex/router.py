from fastapi import APIRouter
from pydantic import BaseModel

from app.clients.plex import PlexClient
from app.config import get_effective_settings, movies_library_dir
from app.modules.plex.paths import section_for, to_plex

router = APIRouter(prefix="/api/plex", tags=["plex"])


class PlexTest(BaseModel):
    url: str
    token: str
    path_map: str = ""


@router.post("/test")
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
    }
