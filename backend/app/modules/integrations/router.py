import logging
from typing import Any, Optional
from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from app.db import get_automations, update_automation
from app.core.auth import require

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

class AutomationUpdate(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None

@router.get("", dependencies=[Depends(require("settings"))])
async def list_integrations():
    return await get_automations()

@router.put("/{type_name}", dependencies=[Depends(require("settings"))])
async def edit_integration(type_name: str, body: AutomationUpdate):
    await update_automation(type_name, enabled=body.enabled, config=body.config)
    return {"status": "ok"}
