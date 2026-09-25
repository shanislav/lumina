"""Find and judge a film's files on all sources (moved here from the search module so that
background jobs — upgrade checks, later a scheduler — use exactly what the search UI uses)."""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from app.clients.groq_scorer import score_results
from app.clients.tmdb import TMDBClient
from app.core.offers.details import cached_details, get_details
from app.core.offers.evaluate import RELEVANCE, MovieContext, evaluate, recommended_key, year_of
from app.core.quality import Prefs, prefs_from_settings
from app.models.schemas import ScorableFile
from app.sources.base import SearchResult, SourceType
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

# Direct-download sources also return archives, torrents, subtitles, disc images … — only
# playable video files make sense for the library.
DDL_VIDEO_EXTS = {"mkv", "mp4", "avi", "m4v", "ts", "m2ts", "wmv", "mov", "mpg", "mpeg", "webm", "divx", "ogm"}
MAX_DDL_QUERIES = 6
MIN_SEEDERS = 10
DETAIL_SOURCES = ("webshare", "fastshare")   # verification order: WebShare = one API call


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


def is_video_name(name: str) -> bool:
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


def _collapse_acronyms(text: str) -> str:
    """ "S.W.A.T." → "SWAT" (WebShare matches the joined form, FastShare the spaced one)."""
    return re.sub(r"\b(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", lambda m: m.group(0).replace(".", ""), text)


def ddl_queries(query: str, original_title: str = "", en_title: str = "",
                local_titles: list[str] | None = None, year: int | None = None) -> list[str]:
    """Queries for WebShare/FastShare, most specific first. Each source returns a limited list, so a
    short title alone is drowned by namesakes ("S W A T": 6 film files of 30, the rest TV episodes);
    the full local title or "title year" bring the film's files (30 of 30). Measured 2026-09-25:
    WebShare finds "SWAT 2003", FastShare "S W A T 2003" — both forms are asked."""
    year = year or year_of(query)
    no_year = re.sub(r"\b(19|20)\d{2}\b", "", query).strip()
    main = re.split(r"\s*[:–—]\s*|\s+-\s+", no_year)[0]
    # (text, add the year after cleaning — cleaning drops years)
    candidates = [*((t, False) for t in local_titles or []), (no_year, False)]
    if year:
        candidates += [(main, True), (_collapse_acronyms(main), True)]
    candidates += [(en_title, False), (original_title, False), (main, False)]
    queries: list[str] = []
    seen: set[str] = set()
    for candidate, add_year in candidates:
        cleaned = _clean_title(candidate or "")
        if add_year and cleaned:
            cleaned = f"{cleaned} {year}"
        key = cleaned.lower()
        if len(_norm(cleaned)) >= 2 and key not in seen:
            seen.add(key)
            queries.append(cleaned)
    return queries[:MAX_DDL_QUERIES] or [query]


def _unique_names(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        if n and _norm(_clean_title(n)) not in {_norm(_clean_title(x)) for x in out}:
            out.append(n)
    return out


@dataclass
class Offers:
    movie: MovieContext
    prefs: Prefs
    rows: list[dict] = field(default_factory=list)   # ScoredFile-shaped dicts, recommended order


async def find_offers(cfg: dict, query: str, *, original_title: str = "", tmdb_id: int | None = None,
                      media_type: str = "movie", use_ai: bool = True) -> Offers:
    """All files of a film on all sources, judged by rules (+ AI for unclear ones)."""
    sources = SourceRegistry.get().sources
    prefs = prefs_from_settings(cfg)
    ctx = MovieContext(year=year_of(query))
    if not sources:
        return Offers(ctx, prefs)

    all_queries = [query, *_build_alt_queries(query, original_title)]

    # Everything TMDB knows about the film's names, its year and runtime (one call). Files are named
    # in any language, and the runtime lets verified durations expose wrong/incomplete files.
    en_title = ""
    local_titles: list[str] = []
    if tmdb_id:
        client = TMDBClient(cfg["tmdb_api_key"])
        try:
            if media_type == "movie":
                full = await client.get_movie_full(tmdb_id)
                by_lang = full.get("titles_by_lang") or {}
                en_title = by_lang.get("en", "")
                local_titles = [by_lang.get(l, "") for l in prefs.local_langs]
                ctx.runtime = full.get("runtime") or 0
                ctx.year = full.get("year") or ctx.year
                ctx.titles = [full.get("title", ""), full.get("original_title", ""),
                              *(by_lang.get(l, "") for l in ("cs", "sk", "en")),
                              *full.get("alternative_titles", [])]
                ctx.people = full.get("people", [])
                ctx.other_parts = full.get("other_parts", [])
            else:
                en_title = await client.get_english_title(tmdb_id, media_type)
        except Exception as e:
            logger.warning("TMDB details for tmdb=%s failed: %s", tmdb_id, e)
        finally:
            await client.close()
        if en_title and en_title.lower() not in [q.lower() for q in all_queries]:
            all_queries.append(en_title)
    ctx.titles = _unique_names([re.sub(r"\b(19|20)\d{2}\b", "", query).strip(), original_title,
                                en_title, *ctx.titles])

    unique_queries: list[str] = []
    for q in all_queries:
        if q.lower() not in {u.lower() for u in unique_queries}:
            unique_queries.append(q)

    async def _safe_search(source, q: str) -> list[SearchResult]:
        try:
            results = await source.search(q)
            logger.info("Source %s '%s' → %d results", source.source_type.value, q[:40], len(results))
            return results
        except Exception as e:
            logger.warning("Source %s (id=%d) search '%s' failed: %s",
                           source.source_type.value, source.source_id, q, e)
            return []

    # DDL sources get a few cleaned variants (short local title, full local title, English title);
    # Jackett gets ALL query variants (EN title, stripped diacritics, etc.)
    ddl = ddl_queries(query, original_title, en_title, local_titles, ctx.year)
    tasks = [
        _safe_search(source, q)
        for source in sources
        for q in (unique_queries if source.source_type == SourceType.JACKETT else ddl)
    ]
    results_per_task = await asyncio.gather(*tasks)

    seen_idents: set[str] = set()
    all_results: list[SearchResult] = []
    for batch in results_per_task:
        for r in batch:
            if r.source_type != SourceType.JACKETT and not is_video_name(r.name):
                continue
            if r.source_type == SourceType.JACKETT and (r.seeders or 0) < MIN_SEEDERS:
                continue   # torrents nobody seeds are useless
            if r.ident not in seen_idents:
                seen_idents.add(r.ident)
                all_results.append(r)
    logger.info("Search '%s': %d unique results (DDL queries %s)", query, len(all_results), ddl)
    if not all_results:
        return Offers(ctx, prefs)

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
    if use_ai and unclear and cfg.get("groq_api_key"):
        await _ask_ai(cfg, ctx, prefs, unclear)

    rows.sort(key=lambda row: recommended_key(row, prefs))
    counts = {k: sum(1 for r in rows if r["film"] == k) for k in ("yes", "unsure", "length", "no")}
    logger.info("Search '%s': %s (AI asked about %d)", query, counts, len(unclear) if use_ai else 0)
    return Offers(ctx, prefs, rows)


async def _ask_ai(cfg: dict, ctx: MovieContext, prefs: Prefs, unclear: list[dict]) -> None:
    scorable = [ScorableFile(index=i, name=row["name"], size=row["size"], source=row["source"],
                             source_id=row["source_id"], ident=row["ident"], seeders=row["seeders"])
                for i, row in enumerate(unclear)]
    try:
        ai_names = " / ".join(ctx.titles) + (f" ({ctx.year})" if ctx.year else "")
        scored = await score_results(ai_names, scorable, cfg["groq_api_key"],
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


def reevaluate(row: dict, details: dict, ctx: MovieContext, prefs: Prefs) -> dict:
    """A row judged again with verified details from its source."""
    return {**row, "details": details, **evaluate(row["name"], row["size"], ctx, prefs, details)}


async def verify_offers(offers: Offers, limit: int = 15) -> None:
    """Verify likely offers at their source (real bitrate, languages, length), in place.

    Same policy as the UI: junk ("no") is never verified, likely matches first, the same file
    (same size) on both sources once — WebShare first, FastShare only when WebShare gives nothing.
    Details are cached for 90 days and the source clients are throttled.
    """
    groups: dict[int, list[dict]] = {}
    for row in offers.rows:
        if row["source"] in DETAIL_SOURCES and row["film"] != "no":
            groups.setdefault(row["size"], []).append(row)
    queues = [sorted(copies, key=lambda r: DETAIL_SOURCES.index(r["source"]))
              for copies in groups.values() if not any(c.get("verified") for c in copies)]
    queues.sort(key=lambda q: (q[0]["film"] != "yes", -q[0]["quality_score"]))
    queues = queues[:limit]
    while queues:
        current = [q[0] for q in queues]
        got = await get_details([{"source_id": r["source_id"], "ident": r["ident"], "name": r["name"]}
                                 for r in current])
        retry = []
        for queue, row in zip(queues, current):
            details = got.get(f"{row['source_id']}:{row['ident']}")
            if details:
                row.update(reevaluate(row, details, offers.movie, offers.prefs))
            elif len(queue) > 1:
                retry.append(queue[1:])
        queues = retry
    offers.rows.sort(key=lambda row: recommended_key(row, offers.prefs))


LOCAL_AUDIO_TIER = 2   # evaluate.language_tier: 2 = local audio by name, 3 = verified


def has_local_audio(language: str, prefs: Prefs) -> bool:
    """language = the library's "CS,EN" list."""
    return any(l.strip().lower() in prefs.local_langs for l in (language or "").split(","))


def upgrade_block(row: dict, owned: dict, prefs: Prefs) -> str | None:
    """Why an offer is NOT an upgrade of an owned version, or None when it is one.

    owned: {"quality_score", "language", "file_size"}. Rule (agreed with the user): the right
    film, not the very file, a higher score (any), and it keeps Czech/Slovak audio if the owned
    version has it. The UI (FileTable upgrade mode) applies the same rule.
    """
    if row.get("film") not in ("yes", "unsure"):
        return "film"
    if row.get("size") == owned.get("file_size"):
        return "same_file"
    if row.get("quality_score", 0) <= (owned.get("quality_score") or 0):
        return "quality"
    if has_local_audio(owned.get("language", ""), prefs) and row.get("lang_tier", 0) < LOCAL_AUDIO_TIER:
        return "language"
    return None
