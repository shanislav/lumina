import asyncio
import logging

from fastapi import APIRouter

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.clients.groq_scorer import score_results, _fallback_scoring
from app.models.schemas import TMDBMovie, ScoredFile, ScorableFile
from app.sources.base import SearchResult
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search/movies", response_model=list[TMDBMovie])
async def search_movies(query: str, language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    # Use explicit language param, or first configured language, or cs
    lang_code = language or cfg.get("languages", "cs").split(",")[0].strip()
    tmdb_locale = f"{lang_code}-{lang_code.upper()}"  # cs -> cs-CZ, en -> en-EN, etc.
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.search_movie(query, language=tmdb_locale)
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


def _to_scorable(results: list[SearchResult]) -> list[ScorableFile]:
    """Convert unified SearchResults into ScorableFiles for the scorer."""
    return [
        ScorableFile(
            index=i,
            name=r.name,
            size=r.size,
            source=r.source_type.value,
            source_id=r.source_id,
            ident=r.ident,
            magnet_url=r.magnet_url,
            seeders=r.seeders,
        )
        for i, r in enumerate(results)
    ]


@router.get("/search/files", response_model=list[ScoredFile])
async def search_files(query: str, language: str | None = None) -> list[ScoredFile]:
    cfg = await get_effective_settings()
    sources = SourceRegistry.get().sources
    if not sources:
        return []

    # Parse language list
    languages = [l.strip() for l in cfg.get("languages", "cs").split(",") if l.strip()]
    # If explicit language filter, only use that one for scoring
    if language:
        languages = [language]

    async def _safe_search(source, q: str) -> list[SearchResult]:
        try:
            return await source.search(q)
        except Exception as e:
            logger.warning(
                "Source %s (id=%d) search failed: %s",
                source.source_type.value,
                source.source_id,
                e,
            )
            return []

    tasks = [_safe_search(s, query) for s in sources]
    results_per_source = await asyncio.gather(*tasks)

    all_results: list[SearchResult] = []
    for batch in results_per_source:
        all_results.extend(batch)

    logger.info("Search '%s': %d total results from %d sources", query, len(all_results), len(sources))

    if not all_results:
        return []

    scorable = _to_scorable(all_results)

    try:
        scored = await score_results(query, scorable, cfg["groq_api_key"], languages=languages)
    except Exception as e:
        logger.warning("AI scoring failed, using fallback: %s", e)
        scored = _fallback_scoring(scorable, languages=languages)

    min_score = int(cfg.get("min_relevance_score", "70"))
    return [s for s in scored if s.relevance_score >= min_score]
