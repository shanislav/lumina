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
