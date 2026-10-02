"""Library router — movie/TV library listing, scan job control, manual match fixes."""

import json
import os
import logging
from typing import Literal

from fastapi import Depends, APIRouter
from pydantic import BaseModel

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.db import get_db
from fastapi import HTTPException

from app.config import movies_library_dir
from app.core import naming, quality
from app.core.quality import prefs_from_settings
from app.modules.library import importer, organize, upgrades
from app.modules.library.notify import emit_movie_updated
from app.core.auth import User, require

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])

# ─── SCAN ───

@router.post("/scan", dependencies=[Depends(require("library.edit"))])
async def scan_library(force: bool = False):
    """Start a background scan of the movie + TV library. Poll /scan/status for progress.

    force=true re-identifies also movies that are already matched (never the ones the user chose).
    """
    started = importer.start_scan(force=force)
    return {"started": started, **importer.job_status()}


@router.get("/scan/status", dependencies=[Depends(require("library.view"))])
async def scan_status():
    return importer.job_status()


# ─── MOVIES ───

_MOVIE_COLUMNS = (
    "id, tmdb_id, title, original_title, year, poster_url, filename, file_size, quality, "
    "language, added_at, matched_by, status, confidence, candidates, media, duration_s, file_path, "
    "note, preferred"
)


def _quality(media: dict, filename: str, size: int, prefs: quality.Prefs) -> dict:
    """Same quality model as search offers — library files and offers are directly comparable."""
    q = quality.score(quality.facts_from_media(media, filename, size or 0), prefs)
    return {"quality_score": q.score, "quality_summary": q.summary,
            "quality_parts": [[label, pts] for label, pts in q.parts]}


def _movie_row(r, prefs: quality.Prefs | None = None) -> dict:
    media = json.loads(r["media"] or "{}")
    return {
        **_quality(media, r["filename"] or "", r["file_size"] or 0, prefs or quality.Prefs()),
        "id": r["id"], "tmdb_id": r["tmdb_id"], "title": r["title"],
        "original_title": r["original_title"], "year": r["year"], "poster_url": r["poster_url"],
        "filename": r["filename"], "file_size": r["file_size"], "quality": r["quality"],
        "language": r["language"], "added_at": r["added_at"],
        "matched_by": r["matched_by"] or "filename",
        "status": r["status"] or "matched",
        "confidence": r["confidence"] or 0,
        # only a file still to be decided needs its candidates (they were ~1/3 of the whole list)
        "candidates": json.loads(r["candidates"] or "[]") if r["status"] in ("review", "unmatched") else [],
        "media": media,
        "duration_s": r["duration_s"] or 0,
        "file_path": r["file_path"],
        "note": r["note"] or "",
        "preferred": bool(r["preferred"]),
    }


@router.get("/movies", dependencies=[Depends(require("library.view"))])
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
        prefs = prefs_from_settings(await get_effective_settings())
        return [_movie_row(r, prefs) for r in await cursor.fetchall()]
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
            f"SELECT id, tmdb_id, filename, file_size, quality, language, media, duration_s, status, note, preferred "
            f"FROM library_movies WHERE status IN ('matched', 'manual') AND tmdb_id IN ({','.join('?' for _ in ids)})",
            ids,
        )
        result: dict[str, list] = {}
        prefs = prefs_from_settings(await get_effective_settings())
        for r in await cursor.fetchall():
            media = json.loads(r["media"] or "{}")
            q = _quality(media, r["filename"], r["file_size"], prefs)
            result.setdefault(str(r["tmdb_id"]), []).append({
                "id": r["id"], "filename": r["filename"], "file_size": r["file_size"], "quality": r["quality"],
                "language": r["language"], "duration_s": r["duration_s"] or 0,
                "hdr": naming.hdr_label(media, r["filename"]), "codec": naming.codec_label(media),
                "quality_score": q["quality_score"], "quality_summary": q["quality_summary"],
                "note": r["note"] or "", "preferred": bool(r["preferred"]),
            })
        return result
    finally:
        await db.close()


@router.get("/movies/summary", dependencies=[Depends(require("library.view"))])
async def get_library_summary():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status, COUNT(*) FROM library_movies GROUP BY status")
        return {(row[0] or "matched"): row[1] for row in await cursor.fetchall()}
    finally:
        await db.close()


@router.delete("/movies/{movie_id}", dependencies=[Depends(require("library.delete"))])
async def delete_library_movie(movie_id: int):
    """Remove movie from library DB (not from disk)."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM library_movies WHERE id = ?", (movie_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/movies/{movie_id}/file", dependencies=[Depends(require("library.delete"))])
async def delete_version_file(movie_id: int):
    """Delete this version from DISK and the library — definitive (the UI asks first)."""
    from app.modules.library.imports import delete_version

    root = movies_library_dir(await get_effective_settings())
    if not root:
        raise HTTPException(400, "Knihovna filmů není nastavená")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT file_path FROM library_movies WHERE id = ?", (movie_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Movie not found")
        # only files inside the library — never anything else on the disk
        if not os.path.normpath(row["file_path"]).startswith(os.path.normpath(root) + os.sep):
            raise HTTPException(400, "Soubor není v knihovně filmů")
        deleted = await delete_version(db, movie_id, root)
        return {"deleted": deleted}
    finally:
        await db.close()


class VersionUpdate(BaseModel):
    note: str | None = None
    preferred: bool | None = None


@router.patch("/movies/{movie_id}", dependencies=[Depends(require("library.edit"))])
async def update_version(movie_id: int, body: VersionUpdate):
    """Note on a version / mark it preferred (one preferred version per movie)."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT tmdb_id FROM library_movies WHERE id = ?", (movie_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Movie not found")
        if body.note is not None:
            await db.execute("UPDATE library_movies SET note = ? WHERE id = ?", (body.note.strip()[:200], movie_id))
        if body.preferred is not None:
            if body.preferred and row["tmdb_id"]:
                await db.execute("UPDATE library_movies SET preferred = 0 WHERE tmdb_id = ?", (row["tmdb_id"],))
            await db.execute("UPDATE library_movies SET preferred = ? WHERE id = ?", (int(body.preferred), movie_id))
        await db.commit()
        await emit_movie_updated(db, movie_id)   # the NFO backup carries note / preferred
        return {"ok": True}
    finally:
        await db.close()


class FixMatchRequest(BaseModel):
    tmdb_id: int


@router.put("/movies/{movie_id}/fix", dependencies=[Depends(require("library.edit"))])
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


@router.get("/movies/{movie_id}/search-tmdb", dependencies=[Depends(require("library.edit"))])
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

@router.get("/shows", dependencies=[Depends(require("library.view"))])
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


@router.get("/shows/{tmdb_id}", dependencies=[Depends(require("library.view"))])
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


@router.delete("/shows/{tmdb_id}", dependencies=[Depends(require("library.delete"))])
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
    # "names": only new file names, files stay in their folders (Plex keeps movies — decisions/0008);
    # "all": names and folders
    stage: Literal["all", "names"] = "all"


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


@router.get("/movies/{movie_id}/organize", dependencies=[Depends(require("library.edit"))])
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


@router.get("/organize", dependencies=[Depends(require("library.edit"))])
async def organize_plan_all():
    """All matched/manual movies whose folder or file name differs from the naming rules."""
    client, root = await _organize_context()
    db = await get_db()
    try:
        return [_plan_view(p, root) for p in await organize.plan_all(db, client, root)]
    finally:
        await client.close()
        await db.close()


@router.post("/organize", dependencies=[Depends(require("library.edit"))])
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
                if body.stage == "names":
                    plan = organize.names_only(plan)
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


@router.get("/operations", dependencies=[Depends(require("library.view"))])
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


@router.post("/operations/{batch_id}/undo", dependencies=[Depends(require("library.edit"))])
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


# ─── BETTER VERSIONS (background) ───

class UpgradeCheckRequest(BaseModel):
    tmdb_ids: list[int]


@router.post("/upgrades/check", dependencies=[Depends(require("library.edit"))])
async def check_upgrades(body: UpgradeCheckRequest):
    """Look for better versions of these movies in the background (one by one, politely). A film whose
    own choice is to download a better version gets it right away, as in the nightly run."""
    await upgrades.apply_film_choices(body.tmdb_ids)
    return upgrades.enqueue(body.tmdb_ids)


class FilmSettings(BaseModel):
    profile_id: int | None = None
    watch_upgrades: bool = False
    on_better: str = ""          # '' = as the scheduler says | notify | version | replace
    upgrade_once: bool = False   # stop watching once a better version is in the library

ON_BETTER = ("", "notify", "version", "replace")


@router.get("/films", dependencies=[Depends(require("library.view"))])
async def film_settings_all():
    """{tmdb_id: {profile_id, watch_upgrades}} for films that have their own settings."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT tmdb_id, profile_id, watch_upgrades, on_better, upgrade_once FROM library_films")
        return {str(r[0]): {"profile_id": r[1], "watch_upgrades": bool(r[2]), "on_better": r[3] or "",
                            "upgrade_once": bool(r[4])}
                for r in await cursor.fetchall()}
    finally:
        await db.close()


@router.put("/films/{tmdb_id}", dependencies=[Depends(require("library.edit"))])
async def set_film_settings(tmdb_id: int, body: FilmSettings):
    db = await get_db()
    try:
        if body.on_better not in ON_BETTER:
            raise HTTPException(400, "Neznámá volba")
        await _save_film(db, tmdb_id, body)
        await db.commit()
        return {"tmdb_id": tmdb_id, **body.model_dump()}
    finally:
        await db.close()


async def _save_film(db, tmdb_id: int, s: FilmSettings) -> None:
    await db.execute("INSERT INTO library_films (tmdb_id, profile_id, watch_upgrades, on_better, upgrade_once) "
                     "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tmdb_id) DO UPDATE SET profile_id = excluded.profile_id, "
                     "watch_upgrades = excluded.watch_upgrades, on_better = excluded.on_better, "
                     "upgrade_once = excluded.upgrade_once",
                     (tmdb_id, s.profile_id, int(s.watch_upgrades), s.on_better, int(s.upgrade_once)))


class BulkFilmSettings(BaseModel):
    tmdb_ids: list[int]
    keep_profile: bool = False   # True = leave each film's own profile
    profile_id: int | None = None
    watch_upgrades: bool = True
    on_better: str = ""
    upgrade_once: bool = False
    check_now: bool = False      # look for the better versions right away (not only at night)


@router.post("/films/bulk", dependencies=[Depends(require("library.edit"))])
async def set_film_settings_bulk(body: BulkFilmSettings):
    """The same settings for many films at once — e.g. all SD films: the default profile, download and
    replace a better version, once. With check_now the check (and the downloads) start right away."""
    if body.on_better not in ON_BETTER:
        raise HTTPException(400, "Neznámá volba")
    ids = list(dict.fromkeys(t for t in body.tmdb_ids if t))
    db = await get_db()
    try:
        own = {}
        if body.keep_profile and ids:
            cursor = await db.execute(f"SELECT tmdb_id, profile_id FROM library_films WHERE tmdb_id IN ({','.join('?' * len(ids))})", ids)
            own = {r[0]: r[1] for r in await cursor.fetchall()}
        for t in ids:
            await _save_film(db, t, FilmSettings(
                profile_id=own.get(t) if body.keep_profile else body.profile_id, watch_upgrades=body.watch_upgrades,
                on_better=body.on_better, upgrade_once=body.upgrade_once))
        await db.commit()
    finally:
        await db.close()
    job = None
    if body.check_now and body.watch_upgrades and ids:
        await upgrades.apply_film_choices(ids)
        job = upgrades.enqueue(ids)
    return {"saved": len(ids), "job": job}


@router.get("/watched", dependencies=[Depends(require("library.view"))])
async def watched_films():
    """Films watched for a better version, with the last check."""
    return await upgrades.watched()


class UpgradeDownload(BaseModel):
    mode: str = "version"      # version | replace


@router.post("/upgrades/{tmdb_id}/download")
async def download_upgrade(tmdb_id: int, body: UpgradeDownload, user: User = Depends(require("download"))):
    """Download the better version the last check found — as another version or replacing the owned one."""
    if body.mode not in ("version", "replace"):
        raise HTTPException(400, "Neznámý režim")
    if body.mode == "replace" and not user.can("library.delete"):
        raise HTTPException(403, "Nahrazení maže soubor — na to nemáš oprávnění")
    started = await upgrades.request_download(tmdb_id, body.mode, requested_by=user.username)
    if not started:
        raise HTTPException(400, "Lepší verze už není k dispozici — zkontroluj znovu")
    return {"started": True}


@router.get("/upgrades/status", dependencies=[Depends(require("library.view"))])
async def upgrades_status():
    return upgrades.status()


@router.get("/upgrades", dependencies=[Depends(require("library.view"))])
async def upgrade_results():
    """Last check per movie: {tmdb_id: {status better|none|error, upgrades, best, checked_at, ...}}."""
    return await upgrades.results()
