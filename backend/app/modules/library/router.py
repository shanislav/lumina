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
    from app.config import tv_library_dir
    roots = [movies_library_dir(cfg), tv_library_dir(cfg)]          # a batch of movies or of TV shows
    db = await get_db()
    try:
        undone = await organize.undo_batch(db, batch_id, roots)
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


# ─── TV library inventory (docs SERIALY) ───

@router.get("/tv/inventory", dependencies=[Depends(require("library.view"))])
async def tv_inventory_summary() -> dict:
    """What the TV library holds as the last scan saw it: each show folder (which show, who says so,
    where Lumina and Plex disagree) with its problem files."""
    from app.modules.library import tv_inventory
    db = await get_db()
    try:
        return await tv_inventory.summary(db)
    finally:
        await db.close()


class TvOverride(BaseModel):
    folder: str
    tmdb_id: int | None = None        # None = no fix, the scan decides again


@router.put("/tv/override", dependencies=[Depends(require("library.edit"))])
async def tv_override(body: TvOverride) -> dict:
    """The user says which show a folder is; the next scan uses it (and the renamer after it)."""
    from app.modules.library import tv_inventory
    db = await get_db()
    try:
        await tv_inventory.set_override(db, body.folder, body.tmdb_id)
        if body.tmdb_id:
            await db.execute("UPDATE tv_folders SET tmdb_id = ?, source = 'user' WHERE folder = ?", (body.tmdb_id, body.folder))
            await db.commit()
    finally:
        await db.close()
    return {"ok": True}


# ─── TV renamer (docs SERIALY) ───

class TvOrganizeRequest(BaseModel):
    folders: list[str]
    # "names": new file names only, files stay in their folders (Plex keeps its items — decisions/0008)
    stage: Literal["all", "names"] = "all"


class TvEpisodeOverride(BaseModel):
    file: str                         # the file, relative to the TV library
    season: int | None = None         # None = no fix, the scan decides again
    episode: int | None = None
    note: str = ""


@router.put("/tv/episode-override", dependencies=[Depends(require("library.edit"))])
async def tv_episode_override(body: TvEpisodeOverride) -> dict:
    """The user says which episode a file is (its name can not tell it); the next scan and the renamer use it."""
    from app.config import tv_library_dir
    from app.modules.library import tv_inventory
    root = tv_library_dir(await get_effective_settings())
    path = os.path.normpath(os.path.join(root, body.file))
    if not root or not path.startswith(os.path.normpath(root) + os.sep) or not os.path.isfile(path):
        raise HTTPException(400, "Soubor v knihovně seriálů není")
    db = await get_db()
    try:
        await tv_inventory.set_episode_override(db, path, body.season, body.episode, body.note)
    finally:
        await db.close()
    return {"ok": True}


class TvNaming(BaseModel):
    tmdb_id: int
    numbering: Literal["files", "tmdb"]


async def _tv_organize_context():
    from app.config import tv_library_dir
    cfg = await get_effective_settings()
    root = tv_library_dir(cfg)
    if not root:
        raise HTTPException(400, "Knihovna seriálů není nastavená")
    return TMDBClient(cfg["tmdb_api_key"]), root


def _tv_plan_view(plan: dict, root: str) -> dict:
    rel = lambda p: os.path.relpath(p, root).replace(os.sep, "/") if p else p  # noqa: E731
    kinds: dict[str, int] = {}
    for op in plan["ops"]:
        kinds[op["kind"]] = kinds.get(op["kind"], 0) + 1
    return {
        **{k: plan.get(k) for k in ("folder", "tmdb_id", "title", "year", "numbering", "conflicts", "blocked",
                                    "tips", "media_missing", "renumbered", "suggested", "sure_names")},
        "renumber": [{**r, "file": rel(r["file"])} for r in plan.get("renumber", [])],
        "unsure": [{**u, "file": rel(u["file"])} for u in plan.get("unsure", [])],
        "source_folder": rel(plan["source_folder"]),
        "target_folder": rel(plan["target_folder"]),
        "kinds": kinds,
        "ops": [{"kind": op["kind"], "src": rel(op["src"]), "dst": rel(op["dst"])} for op in plan["ops"]],
        "skipped": [{"file": rel(s["file"]), "why": s["why"]} for s in plan["skipped"]],
    }


@router.get("/tv/organize", dependencies=[Depends(require("library.edit"))])
async def tv_organize_plans(folder: str | None = None):
    """What fixing the TV shows on disk would do (nothing is changed): one show folder, or all that need it."""
    from app.modules.library import organize_tv
    client, root = await _tv_organize_context()
    db = await get_db()
    try:
        if folder is not None:
            return _tv_plan_view(await organize_tv.plan_show(db, client, folder, root), root)
        return [_tv_plan_view(p, root) for p in await organize_tv.plan_all(db, client, root)]
    except organize.OrganizeError as e:
        raise HTTPException(400, str(e))
    finally:
        await client.close()
        await db.close()


@router.post("/tv/organize", dependencies=[Depends(require("library.edit"))])
async def tv_organize_apply(body: TvOrganizeRequest):
    """Fix the given show folders on disk (one undoable batch). Plans are recomputed right before applying."""
    from app.modules.library import organize_tv
    client, root = await _tv_organize_context()
    db = await get_db()
    batch_id = None
    done, failed = [], []
    try:
        for folder in body.folders:
            try:
                plan = await organize_tv.plan_show(db, client, folder, root)
                if body.stage == "names":
                    plan = organize_tv.names_only(plan)
                if not plan["ops"]:
                    continue
                batch_id = await organize_tv.apply_plan(db, plan, root, batch_id)
                done.append({"title": plan["title"], "folder": folder, "ops": len(plan["ops"]),
                             "new_folder": os.path.relpath(plan["target_folder"], root).split(os.sep)[0]})
            except organize.OrganizeError as e:
                failed.append({"folder": folder, "error": str(e)})
        if done:
            from app.core import events
            # Plex: the new folders and the old ones (gone, or left with what was not an episode)
            await events.emit("library.files_added", {"folders": sorted({os.path.join(root, d[k]) for d in done
                                                                         for k in ("folder", "new_folder")})})
        return {"batch_id": batch_id, "done": done, "failed": failed}
    finally:
        await client.close()
        await db.close()


@router.put("/tv/naming", dependencies=[Depends(require("library.edit"))])
async def tv_naming(body: TvNaming) -> dict:
    """How a show's files are numbered: the user's (files/Plex) or TMDB's (its parts and specials)."""
    from app.modules.library import organize_tv
    db = await get_db()
    try:
        await organize_tv.set_numbering(db, body.tmdb_id, body.numbering)
    finally:
        await db.close()
    return {"ok": True}


# ─── One episode (its window on the show page) ───

class EpisodeFile(BaseModel):
    file_path: str


@router.get("/episodes/{episode_id}", dependencies=[Depends(require("library.view"))])
async def episode_detail(episode_id: int) -> dict:
    """The episode with its files (versions) and their MediaInfo."""
    from app.modules.library import episodes
    db = await get_db()
    try:
        ep = await episodes.episode(db, episode_id)
        if not ep or not ep["has_file"]:
            raise HTTPException(404, "Díl v knihovně není")
        no_dub = False
        try:                                       # the series module's word: a dub was never made
            from app.modules.series import store as series_store
            no_dub = (ep["season"], ep["episode"]) in await series_store.no_dub(ep["show_tmdb_id"])
        except Exception:  # noqa: BLE001 — the series module switched off
            pass
        return {"id": ep["id"], "show_tmdb_id": ep["show_tmdb_id"], "show_title": ep["show_title"], "season": ep["season"],
                "episode": ep["episode"], "episode_title": ep["episode_title"], "air_date": ep["air_date"],
                "file_path": ep["file_path"], "no_dub": no_dub, "versions": await episodes.versions(db, ep)}
    finally:
        await db.close()


@router.post("/episodes/{episode_id}/delete-file", dependencies=[Depends(require("library.delete"))])
async def episode_delete_file(episode_id: int, body: EpisodeFile) -> dict:
    """Delete one file of the episode from DISK — definitive (the UI asks first)."""
    from app.config import tv_library_dir
    from app.modules.library import episodes
    root = tv_library_dir(await get_effective_settings())
    if not root:
        raise HTTPException(400, "Knihovna seriálů není nastavená")
    db = await get_db()
    try:
        ep = await episodes.episode(db, episode_id)
        if not ep:
            raise HTTPException(404, "Díl v knihovně není")
        try:
            return {"deleted": await episodes.delete_file(db, ep, body.file_path, root)}
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        await db.close()


# ─── Which episode each file is: the user's word, an AI suggestion (docs SERIALY) ───

async def _folder_files(db, root: str, folder: str) -> tuple[int | None, list[dict]]:
    f = await (await db.execute("SELECT tmdb_id FROM tv_folders WHERE folder = ?", (folder,))).fetchone()
    rows = await (await db.execute(
        "SELECT f.file_path, f.season, f.episodes, f.status, f.note, f.facts, m.media FROM tv_files f "
        "LEFT JOIN tv_media m USING (file_path) WHERE f.folder = ? ORDER BY f.file_path", (folder,))).fetchall()
    files = []
    for r in rows:
        facts = json.loads(r["facts"] or "{}")
        eps = json.loads(r["episodes"] or "[]")
        files.append({"path": r["file_path"], "file": os.path.relpath(r["file_path"], root).replace(os.sep, "/"),
                      "name": os.path.basename(r["file_path"]), "own": facts.get("title") or "",
                      "season": r["season"], "episode": eps[0] if eps else None, "status": r["status"], "note": r["note"] or "",
                      "manual": bool(facts.get("manual")), "plex": facts.get("plex"),
                      "duration": (json.loads(r["media"] or "{}").get("duration_s") or 0) if r["media"] else 0})
    return (f[0] if f else None), files


@router.get("/tv/folder", dependencies=[Depends(require("library.view"))])
async def tv_folder_detail(folder: str) -> dict:
    """A show folder's files (number, own name, length, state, the user's word) and TMDB's episodes of the show."""
    from app.config import tv_library_dir
    from app.modules.library import episode_names
    cfg = await get_effective_settings()
    root = tv_library_dir(cfg)
    client = TMDBClient(cfg["tmdb_api_key"])
    db = await get_db()
    try:
        tmdb_id, files = await _folder_files(db, root, folder)
        if not tmdb_id:
            raise HTTPException(400, "Seriál složky není určený — vyber ho nejdřív")
        cat = await episode_names.catalog(client, db, tmdb_id)
        episodes = [{"season": s, "episode": e, "cs": naming.episode_title(v["cs"]) and v["cs"] or "", "en": v["en"],
                     "runtime": v["runtime"], "air": v["air"]} for (s, e), v in sorted(cat.items())]
        from app.clients import ai
        return {"folder": folder, "tmdb_id": tmdb_id, "groq": bool(ai.available(cfg)),
                "files": [{k: v for k, v in f.items() if k != "path"} for f in files if f["status"] != "extra"],
                "episodes": episodes}
    finally:
        await client.close()
        await db.close()


class TvAiMap(BaseModel):
    folder: str
    season: int | None = None          # the files of this season (by their number now); None = all
    files: list[str] | None = None     # or these files (relative to the TV library)


async def _dialogue_and_plots(client, tmdb_id: int, files: list[dict], cat: dict) -> None:
    """Files whose name says nothing get a few lines of their subtitles, TMDB's episodes of their seasons a
    short plot — the AI matches what is said to what happens (Solo Leveling "S02E01.mp4" ↔ S01E13)."""
    import asyncio
    from app.modules.library import ai_episodes

    need = [f for f in files[:ai_episodes.MAX_FILES] if not f.get("own")]
    if not need:
        return
    gate = asyncio.Semaphore(4)

    async def one(f):
        async with gate:
            f["dialogue"] = await ai_episodes.dialogue(f["path"])
    await asyncio.gather(*(one(f) for f in need))
    if not any(f.get("dialogue") for f in need):
        return
    seasons = {f["season"] + d for f in need if f.get("season") is not None for d in (-1, 0, 1)} & {k[0] for k in cat}
    for sn in sorted(seasons):
        try:
            cs = await client.get_season(tmdb_id, sn)
            en = {e["episode_number"]: e.get("overview") or "" for e in await client.get_season(tmdb_id, sn, language="en-US")}
        except Exception:  # noqa: BLE001 — no plots then
            continue
        for e in cs:
            if (sn, e["episode_number"]) in cat:
                cat[(sn, e["episode_number"])]["plot"] = e.get("overview") or en.get(e["episode_number"], "")


@router.post("/tv/ai-map", dependencies=[Depends(require("library.edit"))])
async def tv_ai_map(body: TvAiMap) -> dict:
    """AI (Gemini / Groq) suggests which TMDB episode each file is — a suggestion the user accepts or not."""
    from app.config import tv_library_dir
    from app.modules.library import ai_episodes, episode_names
    cfg = await get_effective_settings()
    root = tv_library_dir(cfg)
    client = TMDBClient(cfg["tmdb_api_key"])
    db = await get_db()
    try:
        tmdb_id, files = await _folder_files(db, root, body.folder)
        if not tmdb_id:
            raise HTTPException(400, "Seriál složky není určený")
        files = [f for f in files if f["status"] != "extra"
                 and (body.season is None or f["season"] == body.season)
                 and (not body.files or f["file"] in body.files)]
        if not files:
            raise HTTPException(400, "Žádné soubory k návrhu")
        cat = await episode_names.catalog(client, db, tmdb_id)
        await _dialogue_and_plots(client, tmdb_id, files, cat)
        try:
            found = await ai_episodes.suggest(cfg, files, cat, body.season)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"AI: {e or type(e).__name__}")
        for s in found:
            s["file"] = os.path.relpath(s.pop("path"), root).replace(os.sep, "/")
        return {"suggestions": found, "asked": len(files)}
    finally:
        await client.close()
        await db.close()


class AudioLanguage(BaseModel):
    ids: list[int] = []            # library_episodes ids
    paths: list[str] = []          # or files of the TV library (another version of an episode)
    lang: str                      # cs | sk | en …
    track: int | None = None       # an audio track (0-based); None = every track without a language


@router.post("/tv/audio-language", dependencies=[Depends(require("library.edit"))])
async def tv_audio_language(body: AudioLanguage) -> dict:
    """The user's word on episodes' sound language — into the files (MKV, MP4) and Lumina."""
    from app.config import tv_library_dir
    from app.modules.library import audio_lang
    root = os.path.realpath(tv_library_dir(await get_effective_settings()) or "/nonexistent")
    db = await get_db()
    done, errors = [], []
    try:
        targets: list[tuple[int | None, str]] = []
        for ep_id in body.ids[:200]:
            row = await (await db.execute("SELECT file_path FROM library_episodes WHERE id = ? AND has_file = 1",
                                          (ep_id,))).fetchone()
            targets.append((ep_id, row[0] if row else ""))
        for p in body.paths[:50]:
            real = os.path.realpath(p)
            targets.append((None, real if real.startswith(root + os.sep) else ""))   # the TV library only
        for ep_id, path in targets:
            if not path or not os.path.exists(path):
                errors.append(f"{ep_id or 'soubor'}: soubor nenalezen")
                continue
            cached = await (await db.execute("SELECT media FROM tv_media WHERE file_path = ?", (path,))).fetchone()
            try:
                out = await audio_lang.set_language(path, body.lang, body.track,
                                                    json.loads(cached[0]) if cached and cached[0] else None)
                langs = await audio_lang.save(db, path, out["media"])
                await db.commit()
                done.append({"id": ep_id, "written": out["written"], "languages": langs, "tracks": out["tracks"]})
            except (ValueError, RuntimeError, OSError) as e:
                errors.append(f"{os.path.basename(path)}: {e}")
    finally:
        await db.close()
    return {"done": done, "errors": errors}
