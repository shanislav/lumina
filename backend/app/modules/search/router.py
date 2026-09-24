import asyncio
import logging
import re
import unicodedata

from fastapi import APIRouter

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.clients.groq_scorer import score_results
from app.models.schemas import TMDBMovie, ScoredFile, ScorableFile
from app.sources.base import SearchResult, SourceType
from app.sources.registry import SourceRegistry
from app.modules.search.details import cached_details, get_details
from app.modules.search.evaluate import (
    RELEVANCE, MovieContext, evaluate, prefs_from_settings, recommended_key, year_of,
)
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


def _clean_query(query: str) -> str:
    """Strip year, apostrophes, special chars for better torrent search."""
    clean = re.sub(r"[''ʼ]s?\b", "", query)   # apostrophe + optional s
    clean = re.sub(r"\b\d{4}\b", "", clean)    # year
    clean = re.sub(r"[^\w\s]", " ", clean)     # special chars
    clean = re.sub(r"\s+", " ", clean).strip()  # normalize spaces
    return clean


def _build_alt_queries(query: str, original_title: str = "") -> list[str]:
    """Build alternative queries for torrent search fallback."""
    alts: list[str] = []
    cleaned = _clean_query(query)
    if cleaned and cleaned.lower() != query.lower():
        alts.append(cleaned)
    if original_title and original_title.lower() != query.lower():
        alts.append(original_title)
        cleaned_orig = _clean_query(original_title)
        if cleaned_orig and cleaned_orig.lower() != original_title.lower():
            alts.append(cleaned_orig)
    return alts


# Direct-download sources also return archives, torrents, subtitles, disc images … — only
# playable video files make sense for the library.
DDL_VIDEO_EXTS = {"mkv", "mp4", "avi", "m4v", "ts", "m2ts", "wmv", "mov", "mpg", "mpeg", "webm", "divx", "ogm"}
MAX_DDL_QUERIES = 3


def _is_video_name(name: str) -> bool:
    if "." not in name:
        return True  # no extension → cannot tell, keep
    return name.rsplit(".", 1)[-1].lower() in DDL_VIDEO_EXTS


def _norm(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def _clean_title(text: str) -> str:
    text = re.sub(r"['’ʼ]", "", text)  # "Don't" → "Dont", as in file names
    text = re.sub(r"\b(19|20)\d{2}\b", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ddl_queries(query: str, original_title: str = "", en_title: str = "") -> list[str]:
    """Queries for WebShare/FastShare. Uploaders mostly use the short local title ("Podfukáři 3"),
    sometimes the full one or the English one — one query alone misses most files."""
    no_year = re.sub(r"\b(19|20)\d{2}\b", "", query).strip()
    main = re.split(r"\s*[:–—]\s*|\s+-\s+", no_year)[0]
    queries: list[str] = []
    seen: set[str] = set()
    for candidate in (main, no_year, en_title, original_title):
        cleaned = _clean_title(candidate or "")
        key = _norm(cleaned)
        if len(key) >= 2 and key not in seen:
            seen.add(key)
            queries.append(cleaned)
    return queries[:MAX_DDL_QUERIES] or [query]


MIN_SEEDERS = 10


class SearchFilesResponse(BaseModel):
    movie: dict
    prefer_local_audio: bool
    files: list[ScoredFile]


def _unique_names(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        if n and _norm(_clean_title(n)) not in {_norm(_clean_title(x)) for x in out}:
            out.append(n)
    return out


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


@router.get("/search/files", response_model=SearchFilesResponse)
async def search_files(
    query: str,
    language: str | None = None,
    original_title: str | None = None,
    tmdb_id: int | None = None,
    media_type: str | None = None,
) -> "SearchFilesResponse":
    cfg = await get_effective_settings()
    sources = SourceRegistry.get().sources
    if not sources:
        return SearchFilesResponse(movie={}, prefer_local_audio=True, files=[])

    # Parse language list
    languages = [l.strip() for l in cfg.get("languages", "cs").split(",") if l.strip()]
    if language:
        languages = [language]

    # Build all query variants
    all_queries = [query]
    alt_queries = _build_alt_queries(query, original_title or "")
    all_queries.extend(alt_queries)

    # Everything TMDB knows about the film's names, its year and runtime (one call). Files are named
    # in any language, and the runtime lets verified durations expose wrong/incomplete files.
    en_title = ""
    ctx = MovieContext(year=year_of(query))
    if tmdb_id:
        client = TMDBClient(cfg["tmdb_api_key"])
        try:
            if (media_type or "movie") == "movie":
                full = await client.get_movie_full(tmdb_id)
                by_lang = full.get("titles_by_lang") or {}
                en_title = by_lang.get("en", "")
                ctx.runtime = full.get("runtime") or 0
                ctx.year = full.get("year") or ctx.year
                ctx.titles = [full.get("title", ""), full.get("original_title", ""),
                              *(by_lang.get(l, "") for l in ("cs", "sk", "en"))]
            else:
                en_title = await client.get_english_title(tmdb_id, media_type or "movie")
        except Exception as e:
            logger.warning("TMDB details for tmdb=%s failed: %s", tmdb_id, e)
        finally:
            await client.close()
        if en_title and en_title.lower() not in [q.lower() for q in all_queries]:
            all_queries.append(en_title)
    ctx.titles = _unique_names([re.sub(r"\b(19|20)\d{2}\b", "", query).strip(), original_title or "",
                                en_title, *ctx.titles])

    # Deduplicate queries (case-insensitive)
    seen_lower: set[str] = set()
    unique_queries: list[str] = []
    for q in all_queries:
        if q.lower() not in seen_lower:
            seen_lower.add(q.lower())
            unique_queries.append(q)

    logger.info("Search queries: %s", unique_queries)

    async def _safe_search(source, q: str) -> list[SearchResult]:
        try:
            results = await source.search(q)
            logger.info("Source %s '%s' → %d results", source.source_type.value, q[:40], len(results))
            return results
        except Exception as e:
            logger.warning(
                "Source %s (id=%d) search '%s' failed: %s",
                source.source_type.value, source.source_id, q, e,
            )
            return []

    # DDL sources get a few cleaned variants (short local title, full local title, English title);
    # Jackett gets ALL query variants (EN title, stripped diacritics, etc.)
    ddl_queries = _ddl_queries(query, original_title or "", en_title)
    logger.info("DDL queries: %s", ddl_queries)

    tasks = []
    for source in sources:
        queries = unique_queries if source.source_type == SourceType.JACKETT else ddl_queries
        for q in queries:
            tasks.append(_safe_search(source, q))
    logger.info(
        "Dispatching %d search tasks across %d sources: %s",
        len(tasks), len(sources), ", ".join(f"{s.source_type.value}:{s.source_id}" for s in sources),
    )
    results_per_task = await asyncio.gather(*tasks)

    # Merge and deduplicate by ident
    seen_idents: set[str] = set()
    all_results: list[SearchResult] = []
    skipped = 0
    for batch in results_per_task:
        for r in batch:
            if r.source_type != SourceType.JACKETT and not _is_video_name(r.name):
                skipped += 1
                continue
            if r.ident not in seen_idents:
                seen_idents.add(r.ident)
                all_results.append(r)
    if skipped:
        logger.info("Skipped %d non-video DDL results (archives, torrents, subtitles, ...)", skipped)

    logger.info(
        "Search '%s': %d unique results from %d sources × %d queries",
        query, len(all_results), len(sources), len(unique_queries),
    )

    prefs = prefs_from_settings(cfg)
    empty = SearchFilesResponse(movie=ctx.as_dict(), prefer_local_audio=prefs.prefer_local_audio, files=[])
    # torrents nobody seeds are useless
    all_results = [r for r in all_results
                   if not (r.source_type == SourceType.JACKETT and (r.seeders or 0) < MIN_SEEDERS)]
    if not all_results:
        return empty

    # Rules first (names, years, durations, quality, languages) — with details already cached
    # for these files, so a repeated search is verified straight away.
    known = await cached_details([(r.source_type.value, r.ident) for r in all_results])
    rows: list[dict] = []
    for r in all_results:
        details = known.get((r.source_type.value, r.ident))
        ev = evaluate(r.name, r.size, ctx, prefs, details, r.duration_s, r.width, r.height)
        rows.append({
            "ident": r.ident, "name": r.name, "size": r.size, "source": r.source_type.value,
            "source_id": r.source_id, "magnet_url": r.magnet_url, "seeders": r.seeders,
            "quality": ev["resolution"] or "unknown", "relevance_score": RELEVANCE[ev["film"]], **ev,
        })

    # AI only decides what the rules could not ("Dune Part Two" vs "Dune: Part One", odd names).
    unclear = [row for row in rows if row["film"] == "unsure"]
    if unclear and cfg.get("groq_api_key"):
        scorable = [ScorableFile(index=i, name=row["name"], size=row["size"], source=row["source"],
                                 source_id=row["source_id"], ident=row["ident"], seeders=row["seeders"])
                    for i, row in enumerate(unclear)]
        try:
            scored = await score_results(" / ".join(ctx.titles), scorable, cfg["groq_api_key"],
                                         languages=list(prefs.local_langs), model=cfg["groq_model"])
            by_ident = {x.ident: x.relevance_score for x in scored}
            min_score = int(cfg.get("min_relevance_score", "70"))
            for row in unclear:
                rel = by_ident.get(row["ident"])
                if rel is None:
                    continue
                row["relevance_score"] = rel
                if rel >= min_score:
                    row["film"], row["film_reasons"] = "yes", row["film_reasons"] + ["AI: je to tento film"]
                elif rel < 30:
                    row["film"], row["film_reasons"] = "no", row["film_reasons"] + ["AI: jiný obsah"]
        except Exception as e:
            logger.warning("AI check of %d unclear files failed: %s", len(unclear), e)

    rows.sort(key=lambda row: recommended_key(row, prefs))
    counts = {k: sum(1 for r in rows if r["film"] == k) for k in ("yes", "unsure", "length", "no")}
    logger.info("Search '%s': %s (AI asked about %d)", query, counts, len(unclear))
    return SearchFilesResponse(movie=ctx.as_dict(), prefer_local_audio=prefs.prefer_local_audio,
                               files=[ScoredFile(**row) for row in rows])


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
    ctx = MovieContext(titles=movie.get("titles") or [], year=movie.get("year"), runtime=movie.get("runtime") or 0)
    prefs = prefs_from_settings(await get_effective_settings())
    out: dict[str, dict | None] = {}
    for f in body.files:
        key = f"{f.source_id}:{f.ident}"
        d = details.get(key)
        out[key] = {"details": d, **evaluate(f.name, f.size, ctx, prefs, d)} if d else None
    return out
