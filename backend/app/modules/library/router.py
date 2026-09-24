"""Library router — movie/TV library listing, scan job control, manual match fixes."""

import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.db import get_db
from fastapi import HTTPException

from app.config import movies_library_dir
from app.core import naming
from app.modules.library import importer, organize
from app.modules.library.notify import emit_movie_updated

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])

# ─── SCAN ───

@router.post("/scan")
async def scan_library(force: bool = False):
    """Start a background scan of the movie + TV library. Poll /scan/status for progress.

    force=true re-identifies also movies that are already matched (never the ones the user chose).
    """
    started = importer.start_scan(force=force)
    return {"started": started, **importer.job_status()}


@router.get("/scan/status")
async def scan_status():
    return importer.job_status()


# ─── MOVIES ───

_MOVIE_COLUMNS = (
    "id, tmdb_id, title, original_title, year, poster_url, filename, file_size, quality, "
    "language, added_at, matched_by, status, confidence, candidates, media, duration_s, file_path"
)


def _movie_row(r) -> dict:
    return {
        "id": r["id"], "tmdb_id": r["tmdb_id"], "title": r["title"],
        "original_title": r["original_title"], "year": r["year"], "poster_url": r["poster_url"],
        "filename": r["filename"], "file_size": r["file_size"], "quality": r["quality"],
        "language": r["language"], "added_at": r["added_at"],
        "matched_by": r["matched_by"] or "filename",
        "status": r["status"] or "matched",
        "confidence": r["confidence"] or 0,
        "candidates": json.loads(r["candidates"] or "[]"),
        "media": json.loads(r["media"] or "{}"),
        "duration_s": r["duration_s"] or 0,
        "file_path": r["file_path"],
    }


@router.get("/movies")
async def get_library_movies(status: str | None = None):
    """List movies in library. status=review|unmatched|matched|manual filters the list."""
    db = await get_db()
    try:
        query = f"SELECT {_MOVIE_COLUMNS} FROM library_movies"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        cursor = await db.execute(query + " ORDER BY added_at DESC", params)
        return [_movie_row(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


@router.get("/owned")
async def owned_versions(tmdb_ids: str):
    """Versions in the library for the given TMDB ids (comma separated) — used by search/downloads
    to show "you already have it" with quality info. Only identified movies count."""
    ids = [int(i) for i in tmdb_ids.split(",") if i.strip().isdigit()][:200]
    if not ids:
        return {}
    db = await get_db()
    try:
        cursor = await db.execute(
            f"SELECT id, tmdb_id, filename, file_size, quality, language, media, duration_s, status "
            f"FROM library_movies WHERE status IN ('matched', 'manual') AND tmdb_id IN ({','.join('?' for _ in ids)})",
            ids,
        )
        result: dict[str, list] = {}
        for r in await cursor.fetchall():
            media = json.loads(r["media"] or "{}")
            result.setdefault(str(r["tmdb_id"]), []).append({
                "id": r["id"], "filename": r["filename"], "file_size": r["file_size"], "quality": r["quality"],
                "language": r["language"], "duration_s": r["duration_s"] or 0,
                "hdr": naming.hdr_label(media, r["filename"]), "codec": naming.codec_label(media),
            })
        return result
    finally:
        await db.close()


@router.get("/movies/summary")
async def get_library_summary():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status, COUNT(*) FROM library_movies GROUP BY status")
        return {(row[0] or "matched"): row[1] for row in await cursor.fetchall()}
    finally:
        await db.close()


@router.delete("/movies/{movie_id}")
async def delete_library_movie(movie_id: int):
    """Remove movie from library DB (not from disk)."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM library_movies WHERE id = ?", (movie_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


class FixMatchRequest(BaseModel):
    tmdb_id: int


@router.put("/movies/{movie_id}/fix")
async def fix_movie_match(movie_id: int, body: FixMatchRequest):
    """Set the movie of a file by hand (from review candidates or a TMDB search). Scans never override it."""
    cfg = await get_effective_settings()
    client = TMDBClient(cfg["tmdb_api_key"])
    db = await get_db()
    try:
        details = await client.get_movie_full(body.tmdb_id)
        await db.execute(
            """UPDATE library_movies
            SET tmdb_id = ?, title = ?, original_title = ?, year = ?,
                poster_url = ?, overview = ?, imdb_id = ?, matched_by = 'manual', status = 'manual'
            WHERE id = ?""",
            (details["tmdb_id"], details["title"], details["original_title"],
             str(details["year"] or ""), details["poster_url"], details["overview"],
             details["imdb_id"], movie_id),
        )
        await db.execute(
            "INSERT OR REPLACE INTO tmdb_movies (tmdb_id, data, fetched_at) VALUES (?, ?, strftime('%s','now'))",
            (details["tmdb_id"], json.dumps(details)),
        )
        await db.commit()
        await emit_movie_updated(db, movie_id)
        return {"ok": True, "title": details["title"]}
    finally:
        await client.close()
        await db.close()


@router.get("/movies/{movie_id}/search-tmdb")
async def search_tmdb_for_fix(movie_id: int, query: str):
    """Search TMDB for a movie to fix a wrong match."""
    cfg = await get_effective_settings()
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        results = await client.search_movie(query)
        return [
            {"tmdb_id": m.tmdb_id, "title": m.title, "year": m.year, "poster_url": m.poster_url}
            for m in results[:8]
        ]
    finally:
        await client.close()


# ─── SHOWS ───

@router.get("/shows")
async def get_library_shows():
    """List all TV shows with episode count progress."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT s.tmdb_id, s.title, s.original_title, s.year, s.poster_url,
                      s.total_seasons, s.total_episodes,
                      COUNT(CASE WHEN e.has_file = 1 THEN 1 END) as owned_episodes
            FROM library_shows s
            LEFT JOIN library_episodes e ON e.show_tmdb_id = s.tmdb_id
            GROUP BY s.tmdb_id
            ORDER BY s.title"""
        )
        rows = await cursor.fetchall()
        return [
            {
                "tmdb_id": r[0], "title": r[1], "original_title": r[2],
                "year": r[3], "poster_url": r[4],
                "total_seasons": r[5], "total_episodes": r[6],
                "owned_episodes": r[7],
            }
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/shows/{tmdb_id}")
async def get_show_detail(tmdb_id: int):
    """Get show detail with all seasons and episodes (owned/missing)."""
    db = await get_db()
    try:
        # Show info
        cursor = await db.execute(
            "SELECT tmdb_id, title, original_title, year, poster_url, overview, total_seasons, total_episodes FROM library_shows WHERE tmdb_id = ?",
            (tmdb_id,),
        )
        show = await cursor.fetchone()
        if not show:
            return {"error": "Show not found"}

        # Episodes
        cursor = await db.execute(
            """SELECT season, episode, episode_title, air_date,
                      has_file, filename, file_size, quality, language
            FROM library_episodes
            WHERE show_tmdb_id = ?
            ORDER BY season, episode""",
            (tmdb_id,),
        )
        rows = await cursor.fetchall()

        # Group by season
        seasons: dict[int, list] = {}
        for r in rows:
            sn = r[0]
            if sn not in seasons:
                seasons[sn] = []
            seasons[sn].append({
                "episode": r[1], "title": r[2], "air_date": r[3],
                "has_file": bool(r[4]), "filename": r[5] or "",
                "file_size": r[6] or 0, "quality": r[7] or "",
                "language": r[8] or "",
            })

        return {
            "tmdb_id": show[0], "title": show[1], "original_title": show[2],
            "year": show[3], "poster_url": show[4], "overview": show[5],
            "total_seasons": show[6], "total_episodes": show[7],
            "seasons": [
                {"season_number": sn, "episodes": eps}
                for sn, eps in sorted(seasons.items())
            ],
        }
    finally:
        await db.close()


@router.delete("/shows/{tmdb_id}")
async def delete_library_show(tmdb_id: int):
    """Remove show and its episodes from library DB."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM library_episodes WHERE show_tmdb_id = ?", (tmdb_id,))
        await db.execute("DELETE FROM library_shows WHERE tmdb_id = ?", (tmdb_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# ─── ORGANIZE (fix names on disk) ───

class OrganizeRequest(BaseModel):
    movie_ids: list[int]


async def _organize_context():
    cfg = await get_effective_settings()
    root = movies_library_dir(cfg)
    if not root:
        raise HTTPException(400, "Knihovna filmů není nastavená")
    return TMDBClient(cfg["tmdb_api_key"]), root


def _plan_view(plan: dict, root: str) -> dict:
    rel = lambda p: p[len(root):].lstrip("/") if p and p.startswith(root) else p  # noqa: E731
    return {
        **{k: plan[k] for k in ("movie_ids", "tmdb_id", "title", "year", "conflicts", "remove_folder")},
        "folder": rel(plan["folder"]),
        "target_folder": rel(plan["target_folder"]),
        "ops": [{"kind": op["kind"], "src": rel(op["src"]), "dst": rel(op["dst"])} for op in plan["ops"]],
    }


@router.get("/movies/{movie_id}/organize")
async def organize_plan(movie_id: int):
    """What fixing this movie on disk would do (nothing is changed)."""
    client, root = await _organize_context()
    db = await get_db()
    try:
        return _plan_view(await organize.plan_movie(db, client, movie_id, root), root)
    except organize.OrganizeError as e:
        raise HTTPException(400, str(e))
    finally:
        await client.close()
        await db.close()


@router.get("/organize")
async def organize_plan_all():
    """All matched/manual movies whose folder or file name differs from the naming rules."""
    client, root = await _organize_context()
    db = await get_db()
    try:
        return [_plan_view(p, root) for p in await organize.plan_all(db, client, root)]
    finally:
        await client.close()
        await db.close()


@router.post("/organize")
async def organize_apply(body: OrganizeRequest):
    """Fix the given movies on disk (one undoable batch). Plans are recomputed right before applying."""
    client, root = await _organize_context()
    db = await get_db()
    batch_id = None
    done, failed = [], []
    try:
        handled: set[int] = set()
        for movie_id in body.movie_ids:
            if movie_id in handled:
                continue
            try:
                plan = await organize.plan_movie(db, client, movie_id, root)
                handled.update(plan["movie_ids"])
                if not plan["ops"]:
                    continue
                batch_id = await organize.apply_plan(db, plan, root, batch_id)
                done.append({"title": plan["title"], "ops": len(plan["ops"])})
                for mid in plan["movie_ids"]:
                    await emit_movie_updated(db, mid)
            except organize.OrganizeError as e:
                failed.append({"movie_id": movie_id, "error": str(e)})
        return {"batch_id": batch_id, "done": done, "failed": failed}
    finally:
        await client.close()
        await db.close()


@router.get("/operations")
async def list_operations(limit: int = 20):
    """Recent organize batches (newest first)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT batch_id, MIN(created_at) AS created_at, COUNT(*) AS ops,
                      SUM(status = 'undone') AS undone
               FROM file_operations GROUP BY batch_id ORDER BY MIN(id) DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


@router.post("/operations/{batch_id}/undo")
async def undo_operations(batch_id: str):
    cfg = await get_effective_settings()
    root = movies_library_dir(cfg)
    db = await get_db()
    try:
        undone = await organize.undo_batch(db, batch_id, root)
        cursor = await db.execute("SELECT DISTINCT movie_id FROM file_operations WHERE batch_id = ?", (batch_id,))
        for row in await cursor.fetchall():
            if row[0]:
                await emit_movie_updated(db, row[0])
        return {"undone": undone}
    finally:
        await db.close()
