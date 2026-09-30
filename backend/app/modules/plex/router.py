from fastapi import Depends, APIRouter
from pydantic import BaseModel

from app.clients.plex import PlexClient
from app.config import get_effective_settings, movies_library_dir
from app.modules.plex.paths import section_for, to_plex
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
    }


# Plex server settings that matter when many files are renamed at once
_WATCH = {
    "FSEventLibraryUpdatesEnabled": "Automaticky prohledávat knihovnu (sledování změn na disku)",
    "FSEventLibraryPartialScanEnabled": "Částečné prohledání při zjištění změn",
    "autoEmptyTrash": "Automaticky vysypat koš po každém prohledání",
}


@router.get("/migration-check", dependencies=[Depends(require("library.edit"))])
async def migration_check() -> dict:
    """Before a big rename: what Plex does on its own. Watching the disk splits the rename into many
    small scans; with the trash emptied after each, a renamed film can come back as a new one."""
    automation = await get_automation("plex")
    cfg = (automation or {}).get("config") or {}
    if not (cfg.get("url") and cfg.get("token")):
        return {"configured": False, "lumina_scans": False, "settings": []}
    client = PlexClient(cfg["url"], cfg["token"])
    try:
        prefs = await client.prefs()
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "reachable": False, "error": str(e) or type(e).__name__,
                "lumina_scans": bool(automation["enabled"]), "settings": []}
    finally:
        await client.close()
    settings = [{"id": k, "title": t, "on": str(prefs.get(k)).lower() in ("true", "1")}
                for k, t in _WATCH.items() if k in prefs]
    return {"configured": True, "reachable": True, "lumina_scans": bool(automation["enabled"]), "settings": settings}
