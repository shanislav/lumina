import json
import logging

from fastapi import APIRouter, HTTPException

from app.db import get_db
from app.models.schemas import SourceCreate, SourceResponse, SourceUpdate
from app.sources.base import SourceType
from app.sources.registry import SOURCE_CLASSES, SourceRegistry, _ensure_classes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])
VALID_TYPES = {t.value for t in SourceType}
SENSITIVE_KEYS = {"password", "heslo", "api_key", "secret"}


def _mask_config(config: dict) -> dict:
    """Replace sensitive values with asterisks."""
    masked = {}
    for k, v in config.items():
        if k in SENSITIVE_KEYS and isinstance(v, str) and v:
            masked[k] = "********"
        else:
            masked[k] = v
    return masked


def _row_to_response(row) -> SourceResponse:
    config = json.loads(row["config"])
    return SourceResponse(
        id=row["id"],
        type=row["type"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        config=_mask_config(config),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[SourceResponse])
async def list_sources() -> list[SourceResponse]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sources ORDER BY id")
        rows = await cursor.fetchall()
        return [_row_to_response(r) for r in rows]
    finally:
        await db.close()


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(body: SourceCreate) -> SourceResponse:
    if body.type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid type. Must be one of: {VALID_TYPES}")

    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO sources (type, name, enabled, config)
               VALUES (?, ?, ?, ?)""",
            (body.type, body.name, int(body.enabled), json.dumps(body.config)),
        )
        await db.commit()
        source_id = cursor.lastrowid

        cursor = await db.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()

    await SourceRegistry.get().reload()
    return _row_to_response(row)


@router.put("/{source_id}", response_model=SourceResponse)
async def update_source(source_id: int, body: SourceUpdate) -> SourceResponse:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Source not found")

        updates: list[str] = []
        params: list = []

        if body.name is not None:
            updates.append("name = ?")
            params.append(body.name)
        if body.enabled is not None:
            updates.append("enabled = ?")
            params.append(int(body.enabled))
        if body.config is not None:
            # Merge: if value is "********", keep the old value
            old_config = json.loads(row["config"])
            new_config = {}
            for k, v in body.config.items():
                if v == "********" and k in old_config:
                    new_config[k] = old_config[k]
                else:
                    new_config[k] = v
            updates.append("config = ?")
            params.append(json.dumps(new_config))

        if updates:
            updates.append("updated_at = datetime('now')")
            params.append(source_id)
            await db.execute(
                f"UPDATE sources SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()

        cursor = await db.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()

    await SourceRegistry.get().reload()
    return _row_to_response(row)


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: int) -> None:
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Source not found")
    finally:
        await db.close()

    await SourceRegistry.get().reload()


@router.post("/{source_id}/test")
async def test_source(source_id: int) -> dict:
    """Test an existing source's connection."""
    source = SourceRegistry.get().get_source_by_id(source_id)
    if not source:
        raise HTTPException(404, "Source not found or disabled")
    try:
        ok = await source.test_connection()
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/test")
async def test_source_config(body: SourceCreate) -> dict:
    """Test a source config without saving it (for the 'Add' form)."""
    _ensure_classes()
    if body.type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid type. Must be one of: {VALID_TYPES}")

    source_type = SourceType(body.type)
    cls = SOURCE_CLASSES[source_type]
    instance = cls(source_id=0, config=body.config)
    try:
        ok = await instance.test_connection()
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await instance.close()
