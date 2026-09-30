"""API of the browser player: file info, start a stream at a second, serve its HLS files."""

import asyncio
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.auth import User, require
from app.db import get_db
from app.modules.player import sessions

router = APIRouter(prefix="/api/player", tags=["player"])


async def _path(movie_id: int) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, title, year, file_path FROM library_movies WHERE id = ?", (movie_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row or not row["file_path"] or not os.path.isfile(row["file_path"]):
        raise HTTPException(404, "Soubor nenalezen")
    return dict(row)


@router.get("/{movie_id}/info", dependencies=[Depends(require("player"))])
async def info(movie_id: int) -> dict:
    f = await _path(movie_id)
    data = await asyncio.to_thread(sessions.probe, f["file_path"])
    return {"id": movie_id, "title": f["title"], "year": f["year"], **data}


class StartBody(BaseModel):
    at: float = 0.0
    audio: int = 0


@router.post("/{movie_id}/start")
async def start(movie_id: int, body: StartBody, user: User = Depends(require("player"))) -> dict:
    f = await _path(movie_id)
    s, _ = await sessions.start(user.id, movie_id, f["file_path"], body.at, body.audio)
    return {"session": s.id, "start": s.start, "audio": s.audio}


_NAME = re.compile(r"^(index\.m3u8|s\d{5}\.ts)$")


@router.get("/s/{sid}/{name}")
async def stream_file(sid: str, name: str, user: User = Depends(require("player"))) -> FileResponse:
    s = sessions.get(sid)
    if not s or s.user_id != user.id or not _NAME.match(name):
        raise HTTPException(404)
    path = s.dir / name
    # the encoder may be a moment behind the player — wait for the file a little
    for _ in range(60):
        if path.is_file() and (name != "index.m3u8" or path.stat().st_size > 0):
            break
        if s.proc and s.proc.returncode not in (None, 0):
            raise HTTPException(500, "Převod pro přehrávání selhal")
        await asyncio.sleep(0.5)
    else:
        raise HTTPException(404, "Ještě není připraveno")
    media = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})


@router.delete("/s/{sid}")
async def stop(sid: str, user: User = Depends(require("player"))) -> dict:
    s = sessions.get(sid)
    if s and s.user_id == user.id:
        await sessions.stop(sid)
    return {"ok": True}
