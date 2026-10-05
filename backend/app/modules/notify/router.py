from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import ADMIN_ONLY, require
from app.modules.notify import store

router = APIRouter(prefix="/api/notifications", tags=["notify"])


@router.get("")
async def notifications(limit: int = 30, user=Depends(require())) -> dict:
    return await store.listing(user, max(1, min(limit, 100)))


@router.delete("", dependencies=[Depends(require(ADMIN_ONLY))])
async def clear() -> dict:
    """The admin clears the notification history (for everyone)."""
    return {"hidden": await store.clear()}


class Seen(BaseModel):
    last_id: int


@router.post("/seen")
async def seen(body: Seen, user=Depends(require("search"))) -> dict:
    await store.mark_seen(user.id, body.last_id)
    return {"ok": True}
