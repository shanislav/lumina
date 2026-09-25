import asyncio
import logging

from fastapi import APIRouter

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.core.offers.details import get_details
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.offers.search import find_offers
from app.core.quality import prefs_from_settings
from app.models.schemas import TMDBMovie, ScoredFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search/movies", response_model=list[TMDBMovie])
async def search_movies(query: str, language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        results = await asyncio.gather(
            client.search_movie(query, language=tmdb_locale),
            client.search_tv(query, language=tmdb_locale),
            return_exceptions=True,
        )
        movies = results[0] if isinstance(results[0], list) else []
        shows = results[1] if isinstance(results[1], list) else []
        # Interleave: movie, tv, movie, tv... then append remaining
        merged: list[TMDBMovie] = []
        mi, ti = 0, 0
        while mi < len(movies) or ti < len(shows):
            if mi < len(movies):
                merged.append(movies[mi])
                mi += 1
            if ti < len(shows):
                merged.append(shows[ti])
                ti += 1
        return merged[:20]
    finally:
        await client.close()


@router.get("/discover/trending", response_model=list[TMDBMovie])
async def discover_trending(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.trending(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/now-playing", response_model=list[TMDBMovie])
async def discover_now_playing(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.now_playing(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/recently-digital", response_model=list[TMDBMovie])
async def discover_recently_digital(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.recently_digital(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/recently-digital-tv", response_model=list[TMDBMovie])
async def discover_recently_digital_tv(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.recently_digital_tv(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/popular", response_model=list[TMDBMovie])
async def discover_popular(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.popular(language=tmdb_locale)
    finally:
        await client.close()


class SearchFilesResponse(BaseModel):
    movie: dict
    prefer_local_audio: bool
    files: list[ScoredFile]


@router.get("/search/files", response_model=SearchFilesResponse)
async def search_files(
    query: str,
    language: str | None = None,
    original_title: str | None = None,
    tmdb_id: int | None = None,
    media_type: str | None = None,
) -> "SearchFilesResponse":
    """All files of a film on all sources, judged (app/core/offers)."""
    cfg = await get_effective_settings()
    offers = await find_offers(cfg, query, original_title=original_title or "", tmdb_id=tmdb_id,
                               media_type=media_type or "movie")
    return SearchFilesResponse(movie=offers.movie.as_dict(), prefer_local_audio=offers.prefs.prefer_local_audio,
                               files=[ScoredFile(**row) for row in offers.rows])


class DetailsFile(BaseModel):
    source_id: int
    ident: str
    name: str
    size: int = 0


class DetailsRequest(BaseModel):
    files: list[DetailsFile]
    movie: dict | None = None     # {"titles", "year", "runtime"} from the search response


@router.post("/search/details")
async def search_details(body: DetailsRequest) -> dict:
    """Verified details from the sources + the file re-evaluated with them (quality, languages,
    length check). {"<source_id>:<ident>": {"details": …, **evaluation} | null}"""
    details = await get_details([f.model_dump() for f in body.files])
    movie = body.movie or {}
    ctx = MovieContext.from_dict(movie)
    prefs = prefs_from_settings(await get_effective_settings())
    out: dict[str, dict | None] = {}
    for f in body.files:
        key = f"{f.source_id}:{f.ident}"
        d = details.get(key)
        out[key] = {"details": d, **evaluate(f.name, f.size, ctx, prefs, d)} if d else None
    return out
