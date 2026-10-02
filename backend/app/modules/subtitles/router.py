import asyncio
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.clients.opensubtitles import OpenSubtitlesClient, OpenSubtitlesError, movie_hash
from app.clients.tmdb import TMDBClient
from app.config import get_effective_settings
from app.core import events
from app.core.auth import require
from app.core.quality import prefs_from_settings
from app.db import get_all_settings, get_db
from app.modules.subtitles import files, jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])

_spoken: dict[int, list[str]] = {}


async def _movie(movie_id: int) -> dict:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id, tmdb_id, title, year, file_path, imdb_id FROM library_movies WHERE id = ?",
                                      (movie_id,))).fetchone()
    finally:
        await db.close()
    if not row or not row["file_path"]:
        raise HTTPException(404, "Film v knihovně není")
    return dict(row)


async def _spoken_languages(tmdb_id: int) -> list[str]:
    if not tmdb_id:
        return []
    if tmdb_id not in _spoken:
        cfg = await get_effective_settings()
        client = TMDBClient(cfg["tmdb_api_key"])
        try:
            _spoken[tmdb_id] = (await client.get_movie_full(tmdb_id)).get("spoken_languages") or []
        except Exception as e:
            logger.info("TMDB languages of %s failed: %s", tmdb_id, e)
            return []
        finally:
            await client.close()
    return _spoken[tmdb_id]


async def _client() -> OpenSubtitlesClient:
    s = await get_all_settings()
    try:
        return OpenSubtitlesClient(s.get("opensubtitles_api_key") or "", s.get("opensubtitles_username") or "",
                                   s.get("opensubtitles_password") or "")
    except OpenSubtitlesError as e:
        raise HTTPException(400, str(e))


@router.get("/movie/{movie_id}", dependencies=[Depends(require("library.view"))])
async def subtitle_status(movie_id: int) -> dict:
    """What subtitles the film has (in the file, next to it) and whether it may need forced ones:
    TMDB lists more spoken languages than one."""
    m = await _movie(movie_id)
    embedded, external = await asyncio.gather(asyncio.to_thread(files.embedded, m["file_path"]),
                                              asyncio.to_thread(files.external, m["file_path"]))
    spoken = await _spoken_languages(m["tmdb_id"])
    local = list(prefs_from_settings(await get_effective_settings()).local_langs)
    has_forced = any(t["forced"] for t in embedded) or any(f["forced"] for f in external)
    folder = os.path.dirname(m["file_path"])
    synced = await jobs.results([os.path.join(folder, f["file"]) for f in external])
    running = jobs.status()
    for f in external:
        path = os.path.join(folder, f["file"])
        f["sync"] = synced.get(path)
        f["syncing"] = path == running["current"] or path in running["queued"]
    return {"embedded": embedded, "external": external, "spoken_languages": spoken,
            "needs_forced": len(spoken) > 1, "has_forced": has_forced, "local_langs": local,
            "configured": bool((await get_all_settings()).get("opensubtitles_api_key"))}


@router.get("/movie/{movie_id}/search", dependencies=[Depends(require("subtitles"))])
async def subtitle_search(movie_id: int, forced: bool = False, languages: str = "") -> dict:
    """Subtitles of the film on OpenSubtitles in the wanted languages; forced = only the ones for the
    foreign parts. The ones made for exactly this file (movie hash) come first."""
    m = await _movie(movie_id)
    langs = [l for l in languages.split(",") if l] or list(prefs_from_settings(await get_effective_settings()).local_langs)
    try:
        mhash = await asyncio.to_thread(movie_hash, m["file_path"])
    except OSError:
        mhash = ""
    client = await _client()
    try:
        rows = await client.search(tmdb_id=m["tmdb_id"] or 0, imdb_id=m.get("imdb_id") or "",
                                   query=f"{m['title']} {m['year'] or ''}".strip(), languages=langs,
                                   foreign_parts="only" if forced else "include", moviehash=mhash)
    except OpenSubtitlesError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"OpenSubtitles: {e}")
    finally:
        await client.close()
    rows.sort(key=lambda r: (not r["hash_match"], r["machine"], -r["downloads"]))
    fps = await asyncio.to_thread(files.video_fps, m["file_path"])
    return {"results": rows[:40], "video_fps": round(fps, 3)}


class SubtitleDownload(BaseModel):
    file_id: int
    language: str
    forced: bool = False
    fps: float = 0            # the subtitles' fps from the search (0 = unknown)
    replace: bool = False


@router.post("/movie/{movie_id}/download", dependencies=[Depends(require("subtitles"))])
async def subtitle_download(movie_id: int, body: SubtitleDownload) -> dict:
    """Download the subtitles next to the video as "<video>.<lang>[.forced].srt"; timed for another
    frame rate (25 vs 23.976) they are rescaled. Plex then picks them up."""
    m = await _movie(movie_id)
    lang = (body.language or "").lower()[:3]
    if not lang.isalpha():
        raise HTTPException(400, "Neznámý jazyk")
    target = files.target_name(m["file_path"], lang, body.forced)
    if os.path.exists(target) and not body.replace:
        raise HTTPException(409, f"{os.path.basename(target)} už existuje")
    client = await _client()
    try:
        data, limit = await client.download(body.file_id)
    except OpenSubtitlesError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"OpenSubtitles: {e}")
    finally:
        await client.close()
    text = files.decode(data)
    note = ""
    fps = await asyncio.to_thread(files.video_fps, m["file_path"])
    if body.fps and fps and abs(body.fps / fps - 1) > 0.005:
        text = files.rescale(text, body.fps / fps)
        note = f"přečasováno z {body.fps:g} na {fps:.3f} fps"
    with open(target, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text.replace("\r\n", "\n"))
    logger.info("Subtitles %s saved (%s)", target, note or "as they were")
    jobs.enqueue(m["file_path"], target, m["title"])        # then fitted to the film's sound
    await events.emit("library.files_added", {"folders": [os.path.dirname(target)]})
    return {"file": os.path.basename(target), "note": note, "remaining": limit.get("remaining"),
            "reset_time": limit.get("reset_time")}


class SyncBody(BaseModel):
    file: str                 # a subtitle file next to the video (name only)


@router.post("/movie/{movie_id}/sync", dependencies=[Depends(require("subtitles"))])
async def subtitle_sync(movie_id: int, body: SyncBody) -> dict:
    """Fit a subtitle file next to the video to the film's sound (shift, frame rate) — in the background."""
    m = await _movie(movie_id)
    name = os.path.basename(body.file)
    if name not in {f["file"] for f in files.external(m["file_path"])} or not name.lower().endswith(".srt"):
        raise HTTPException(400, "Jen titulky .srt vedle filmu")
    jobs.enqueue(m["file_path"], os.path.join(os.path.dirname(m["file_path"]), name), m["title"])
    return {"queued": True}


@router.post("/test", dependencies=[Depends(require("settings"))])
async def subtitle_test() -> dict:
    client = await _client()
    try:
        return await client.test()
    except OpenSubtitlesError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"OpenSubtitles: {e}")
    finally:
        await client.close()
