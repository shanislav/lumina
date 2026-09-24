import logging
from typing import Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import get_automations, update_automation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

class AutomationUpdate(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None

@router.get("")
async def list_integrations():
    return await get_automations()

@router.put("/{type_name}")
async def edit_integration(type_name: str, body: AutomationUpdate):
    await update_automation(type_name, enabled=body.enabled, config=body.config)
    return {"status": "ok"}

@router.get("/{type_name}/options")
async def get_integration_options(type_name: str):
    if type_name not in ("radarr", "sonarr"):
        return {}

    from app.db import get_automations

    automations = await get_automations()
    auto_cfg = next((a for a in automations if a["type"] == type_name), None)

    if not auto_cfg or not auto_cfg["config"].get("url"):
        return {"root_folders": [], "quality_profiles": []}

    cfg = auto_cfg["config"]

    if type_name == "radarr":
        from app.clients.radarr import RadarrClient
        client = RadarrClient(cfg["url"], cfg["api_key"])
    else:
        from app.clients.sonarr import SonarrClient
        client = SonarrClient(cfg["url"], cfg["api_key"])

    try:
        folders = await client.get_root_folders()
        profiles = await client.get_quality_profiles()
        return {
            "root_folders": [{"path": f["path"], "id": f["id"]} for f in folders],
            "quality_profiles": [{"name": p["name"], "id": p["id"]} for p in profiles],
        }
    finally:
        await client.close()
