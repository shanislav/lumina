import json
from datetime import datetime

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.modules.wanted import store
from app.core.auth import User, require

router = APIRouter(prefix="/api/wanted", tags=["wanted"])


class WantedAdd(BaseModel):
    tmdb_id: int | None = None
    wikidata_id: str | None = None
    title: str
    original_title: str = ""
    year: str = ""
    poster_url: str | None = None
    profile_id: int | None = None
    check_now: bool = True


class WantedUpdate(BaseModel):
    profile_id: int | None = None
    note: str | None = None
    status: str | None = None        # back to "wanted" (e.g. after a failed download)


def _out(row) -> dict:
    d = dict(row)
    d["best"] = json.loads(d.get("best") or "{}")
    return d


async def _tmdb_poster(tmdb_id: int) -> str | None:
    from app.clients.tmdb import TMDBClient
    from app.config import get_effective_settings
    cfg = await get_effective_settings()
    client = TMDBClient(cfg.get("tmdb_api_key", ""))
    try:
        return (await client.get_movie_full(tmdb_id)).get("poster_url")
    except Exception:
        return None
    finally:
        await client.close()


@router.get("/of", dependencies=[Depends(require("search"))])
async def wanted_of(tmdb_id: int = 0, wikidata_id: str = "") -> dict | None:
    """Is this film on the list (not done yet)? — the film's page shows it."""
    if not tmdb_id and not wikidata_id:
        return None
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, status, added_by, added_at, waiting FROM wanted WHERE status != 'done' AND "
            + ("tmdb_id = ?" if tmdb_id else "wikidata_id = ?"), (tmdb_id or wikidata_id,))).fetchone()
    finally:
        await db.close()
    return dict(row) if row else None


@router.get("")
async def list_wanted():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM wanted ORDER BY status = 'done', added_at DESC")
        rows = [_out(r) for r in await cursor.fetchall()]
        # films added without a poster (e.g. through the API) get it from TMDB once
        for row in [r for r in rows if not r["poster_url"] and r["tmdb_id"]][:5]:
            row["poster_url"] = await _tmdb_poster(row["tmdb_id"])
            if row["poster_url"]:
                await db.execute("UPDATE wanted SET poster_url = ? WHERE id = ?", (row["poster_url"], row["id"]))
        await db.commit()
        return rows
    finally:
        await db.close()


@router.post("")
async def add_wanted(body: WantedAdd, user: User = Depends(require("wanted"))):
    who = getattr(user, "username", "")          # called from code (no request): nobody
    if not body.tmdb_id and not body.wikidata_id:
        raise HTTPException(400, "Film potřebuje TMDB nebo Wikidata id")
    db = await get_db()
    try:
        if body.tmdb_id:
            cursor = await db.execute("SELECT id FROM wanted WHERE tmdb_id = ?", (body.tmdb_id,))
        else:
            cursor = await db.execute("SELECT id FROM wanted WHERE wikidata_id = ?", (body.wikidata_id,))
        existing = await cursor.fetchone()
        if existing:
            wanted_id = existing[0]
            await db.execute("UPDATE wanted SET profile_id = ?, status = CASE WHEN status = 'done' THEN 'wanted' ELSE status END, "
                             "added_by = CASE WHEN added_by = '' OR added_by IS NULL THEN ? ELSE added_by END "
                             "WHERE id = ?", (body.profile_id, who, wanted_id))
        else:
            cursor = await db.execute(
                "INSERT INTO wanted (tmdb_id, wikidata_id, title, original_title, year, poster_url, profile_id, added_at, added_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (body.tmdb_id or None, body.wikidata_id or "", body.title, body.original_title, body.year[:4],
                 body.poster_url or (await _tmdb_poster(body.tmdb_id) if body.tmdb_id else None),
                 body.profile_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), who),
            )
            wanted_id = cursor.lastrowid
        await db.commit()
    finally:
        await db.close()
    if body.check_now:
        store.enqueue([wanted_id])
    return await store.get(wanted_id)


@router.patch("/{wanted_id}", dependencies=[Depends(require("wanted"))])
async def update_wanted(wanted_id: int, body: WantedUpdate):
    # only what the request sent (profile_id: null = back to the default profile)
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if not fields:
        return await store.get(wanted_id)
    if body.status is not None and body.status not in ("wanted", "found", "downloading", "done"):
        raise HTTPException(400, "Neznámý stav")
    db = await get_db()
    try:
        sets = ", ".join(f"{k} = ?" for k in fields)
        cursor = await db.execute(f"UPDATE wanted SET {sets} WHERE id = ?", (*fields.values(), wanted_id))
        if not cursor.rowcount:
            raise HTTPException(404, "Není v seznamu")
        await db.commit()
    finally:
        await db.close()
    return await store.get(wanted_id)


@router.delete("/{wanted_id}", dependencies=[Depends(require("wanted"))])
async def remove_wanted(wanted_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM wanted WHERE id = ?", (wanted_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


class CheckRequest(BaseModel):
    ids: list[int] = []          # empty = every film not done yet


@router.post("/check", dependencies=[Depends(require("wanted"))])
async def check_wanted(body: CheckRequest):
    ids = body.ids
    if not ids:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT id FROM wanted WHERE status != 'done' ORDER BY checked_at IS NOT NULL, checked_at")
            ids = [r[0] for r in await cursor.fetchall()]
        finally:
            await db.close()
    return store.enqueue(ids)


@router.get("/check/status")
async def check_status():
    return store.job_status()
