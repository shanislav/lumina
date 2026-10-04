"""Find and judge a film's files on all sources (moved here from the search module so that
background jobs — upgrade checks, later a scheduler — use exactly what the search UI uses)."""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from app.clients import ai
from app.clients.groq_scorer import score_results
from app.clients.tmdb import TMDBClient
from app.core.offers.details import cached_details, get_details
from app.core.episode_match import parse_episode
from app.core.cinema import before_digital, before_local_digital, mark_before_release, remember
from app.core.cinema import remembered as cinema_memory
from app.core.offers.evaluate import RELEVANCE, MovieContext, evaluate, recommended_key, year_of
from app.core.quality import Prefs, prefs_from_settings
from app.core.text import clean_text
from app.models.schemas import ScorableFile
from app.sources.base import TORRENT_SOURCES, SearchResult
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

# Direct-download sources also return archives, torrents, subtitles, disc images … — only
# playable video files make sense for the library.
DDL_VIDEO_EXTS = {"mkv", "mp4", "avi", "m4v", "ts", "m2ts", "wmv", "mov", "mpg", "mpeg", "webm", "divx", "ogm"}
MAX_DDL_QUERIES = 6
MIN_SEEDERS = 1   # private trackers (Sk-CzTorrent) have few seeders even for good torrents; the count is shown
DETAIL_SOURCES = ("webshare", "fastshare", "prowlarr")   # verification order: WebShare = one API call


def _clean_query(query: str) -> str:
    """Strip year, apostrophes, special chars for better torrent search."""
    clean = re.sub(r"[''ʼ]s?\b", "", query)   # apostrophe + optional s
    clean = re.sub(r"\b\d{4}\b", "", clean)    # year
    clean = re.sub(r"[^\w\s]", " ", clean)     # special chars
    clean = re.sub(r"\s+", " ", clean).strip()  # normalize spaces
    return clean


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


def torrent_query_list(query: str, en_title: str = "", original_title: str = "") -> list[str]:
    """The typed title without a year, and the English (or original, when in Latin letters) title."""
    out: list[str] = []
    for q in (re.sub(r"\s*\b(19|20)\d{2}\b", "", query).strip(), en_title, original_title):
        q = (q or "").strip()
        if q and re.search(r"[A-Za-z]", q) and _norm(q) not in {_norm(x) for x in out}:
            out.append(q)
    return out[:2]


def episode_queries(titles: list[str], season: int, episode: int, episode_names: list[str] = (),
                    other: dict | None = None) -> tuple[list[str], list[str]]:
    """(DDL queries, torrent queries) for one episode. Uploaders name episodes "Show S01E03" (sometimes
    "Show 1x03") after the local or the English name; torrent trackers also have the whole season
    ("Show S01") — a pack holds the episode too."""
    names = [_clean_title(t) for t in _unique_names(titles)]
    names = [n for n in names if len(_norm(n)) >= 2][:3]
    if not names:
        return [], []
    se = f"S{season:02d}E{episode:02d}"
    # the world's other numbers of the episode ("Solo Leveling S02E01" = TMDB S01E13, "Naruto 120")
    others = [f"S{s:02d}E{e:02d}" for s, e in (other or {}).get("alt", [])]
    if (other or {}).get("absolute"):
        others.append(str(other["absolute"]))
    ddl = [f"{names[0]} {se}", *(f"{names[0]} {x}" for x in others), *(f"{n} {se}" for n in names[1:]),
           f"{names[0]} {season}x{episode:02d}"]
    # by the episode's name too: an uploader of another order numbers it otherwise ("S01E02 - Sopka" is S01E03)
    ddl[1 + len(others) + 1:1 + len(others) + 1] = [f"{names[0]} {t}" for t in list(dict.fromkeys(episode_names))[:2]]
    latin = next((n for n in names if n.isascii() and re.search(r"[A-Za-z]", n)), names[0])
    # Czech trackers name packs "Show (komplet,720p,CZ)", "Show 1. - S03" — "Show S02" finds nothing there
    return ddl[:MAX_DDL_QUERIES + len(others)], [f"{latin} {se}", *(f"{latin} {x}" for x in others),
                                                 f"{latin} S{season:02d}", latin]


def _unique_names(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        if n and _norm(_clean_title(n)) not in {_norm(_clean_title(x)) for x in out}:
            out.append(n)
    return out


MAX_NAMESAKES = 3


async def _namesakes(client: TMDBClient, film: dict, titles: list[str]) -> list[dict]:
    """Other films with the same name from about the same year — uploaders name them alike, so
    their files must not pass as ours ("Runner" 2026 vs "Běžkyně / The Runner" 2026)."""
    from app.core.film_match import STOPWORDS, tokens

    ours = {frozenset(tokens(t) - STOPWORDS) for t in titles if t} | {frozenset(tokens(film.get("original_title", "")) - STOPWORDS)}
    found: dict[int, dict] = {}
    for query in {film.get("original_title") or "", film.get("title") or ""} - {""}:
        try:
            results = await client.search_movie_raw(query)
        except Exception as e:
            logger.info("TMDB namesakes of %s failed: %s", query, e)
            continue
        for r in results:
            year = int((r.get("release_date") or "0000")[:4] or 0)
            names = {frozenset(tokens(r.get(k) or "") - STOPWORDS) for k in ("title", "original_title")}
            if (r.get("id") != film["tmdb_id"] and film.get("year") and year and abs(year - film["year"]) <= 1
                    and names & ours):
                found.setdefault(r["id"], r)
    out = []
    for other_id in list(found)[:MAX_NAMESAKES]:
        try:
            other = await client.get_movie_full(other_id)
        except Exception:
            continue
        out.append({"title": other["title"], "titles": other["titles"], "year": other["year"],
                    "runtime": other["runtime"]})
    return out


@dataclass
class Offers:
    movie: MovieContext
    prefs: Prefs
    rows: list[dict] = field(default_factory=list)   # ScoredFile-shaped dicts, recommended order


async def search_sources(sources, ddl: list[str], torrent_queries: list[str]) -> list[SearchResult]:
    """Every query on every source at once; videos only (DDL), seeded torrents only, each file once."""
    async def _safe_search(source, q: str) -> list[SearchResult]:
        try:
            results = await source.search(q)
            logger.info("Source %s '%s' → %d results", source.source_type.value, q[:40], len(results))
            return results
        except Exception as e:
            logger.warning("Source %s (id=%d) search '%s' failed: %s",
                           source.source_type.value, source.source_id, q, e)
            return []

    tasks = [
        _safe_search(source, q)
        for source in sources
        for q in (torrent_queries if source.source_type.value in TORRENT_SOURCES else ddl)
    ]
    seen_idents: set[str] = set()
    all_results: list[SearchResult] = []
    for batch in await asyncio.gather(*tasks):
        for r in batch:
            r.name = clean_text(r.name)   # any source: one broken name must not break the search
            if r.source_type.value not in TORRENT_SOURCES and not is_video_name(r.name):
                continue
            if r.source_type.value in TORRENT_SOURCES and (r.seeders or 0) < MIN_SEEDERS:
                continue   # torrents nobody seeds are useless
            if r.ident not in seen_idents:
                seen_idents.add(r.ident)
                all_results.append(r)
    return all_results


async def find_offers(cfg: dict, query: str, *, original_title: str = "", tmdb_id: int | None = None,
                      media_type: str = "movie", use_ai: bool = True, wikidata_id: str | None = None,
                      season: int | None = None, episode: int | None = None, torrent: bool = True,
                      by_name=None, episode_names: list[str] | None = None,
                      other_numbers: dict | None = None) -> Offers:
    """All files of a film on all sources, judged by rules (+ AI for unclear ones).
    A TV show with season + episode: the files of that episode (and packs that hold it); ``by_name`` checks
    a file's own episode name against TMDB's (``evaluate.judge_episode_name``), ``episode_names`` (the
    episode's names) are searched for too — a file of another number with the episode's name."""
    sources = SourceRegistry.get().sources
    if media_type == "movie" and cfg.get("movies_torrent") == "false":
        torrent = False      # switched off for films in Settings
    if not torrent:          # the user does not want torrents (for this show / for films)
        sources = [src for src in sources if src.source_type.value not in TORRENT_SOURCES]
    prefs = prefs_from_settings(cfg)
    ctx = MovieContext(year=year_of(query))
    if not sources:
        return Offers(ctx, prefs)


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
                ctx.releases = full.get("releases") or {}
                ctx.pre_digital = before_digital(ctx.releases)
                ctx.pre_local = before_local_digital(ctx.releases)
                if not ctx.pre_digital and ctx.releases.get("digital"):
                    ctx.recorded, ctx.recorded_sizes = await cinema_memory(tmdb_id)
                ctx.titles = [full.get("title", ""), full.get("original_title", ""),
                              *(by_lang.get(l, "") for l in ("cs", "sk", "en")),
                              *full.get("alternative_titles", [])]
                ctx.people = full.get("people", [])
                ctx.other_parts = full.get("other_parts", [])
                ctx.namesakes = await _namesakes(client, full, ctx.titles)
            elif season is not None and episode:
                show = await client.get_tv_full(tmdb_id)
                by_lang = show.get("titles_by_lang") or {}
                en_title = by_lang.get("en", "")
                local_titles = [by_lang.get(l, "") for l in prefs.local_langs]
                ctx.titles = [*local_titles, show.get("title", ""), en_title, show.get("original_title", ""),
                              *show.get("alternative_titles", [])]
                ctx.runtime = show.get("episode_runtime") or 0
                try:
                    ep = next((e for e in await client.get_season(tmdb_id, season)
                               if e["episode_number"] == episode), None)
                    ctx.runtime = (ep or {}).get("runtime") or ctx.runtime
                except Exception as e:
                    logger.info("TMDB season %s of tmdb=%s failed: %s", season, tmdb_id, e)
            else:
                en_title = await client.get_english_title(tmdb_id, media_type)
        except Exception as e:
            logger.warning("TMDB details for tmdb=%s failed: %s", tmdb_id, e)
        finally:
            await client.close()
    elif wikidata_id:
        # not in TMDB: names, year and runtime from Wikidata
        from app.clients.wikidata import WikidataClient
        wd = WikidataClient()
        try:
            film = await wd.get_film(wikidata_id)
        except Exception as e:
            logger.warning("Wikidata %s failed: %s", wikidata_id, e)
            film = None
        finally:
            await wd.close()
        if film:
            ctx.titles = film["titles"]
            ctx.year = film["year"] or ctx.year
            ctx.runtime = film["runtime"]
            local_titles = film["titles"][:2]
    ctx.titles = _unique_names([re.sub(r"\b(19|20)\d{2}\b", "", query).strip(), original_title,
                                en_title, *ctx.titles])
    if media_type == "tv" and season is not None and episode:
        ctx.episode = {"season": season, "episode": episode}
        if other_numbers and (other_numbers.get("alt") or other_numbers.get("absolute")):
            ctx.episode["other"] = other_numbers
        ctx.year = None           # years in episode names are the show's, not a mismatch
        use_ai = False            # the episode rules know packs and other episodes; the AI knows films


    # DDL sources get a few cleaned variants (short local title, full local title, English title);
    # torrent indexers at most two: Prowlarr asks its trackers one query after another (~1.5 s each),
    # a year in the query only narrows what the plain title finds, the film check sorts the rest out
    if ctx.episode:
        ddl, torrent_queries = episode_queries(ctx.titles, season, episode, episode_names or [], other_numbers)
    else:
        ddl = ddl_queries(query, original_title, en_title, local_titles, ctx.year)
        torrent_queries = torrent_query_list(query, en_title, original_title)
    all_results = await search_sources(sources, ddl, torrent_queries)
    if ctx.episode and not all_results and ddl:
        # nothing by the number: the show's name alone ("Fotr na tripu 7. série 5. díl" — the rules judge them)
        plain = _unique_names([q.rsplit(" ", 1)[0] for q in ddl[:1]])
        all_results = await search_sources(sources, plain, [])
        ddl = [*ddl, *plain]
    logger.info("Search '%s': %d unique results (DDL queries %s)", query, len(all_results), ddl)
    if not all_results:
        return Offers(ctx, prefs)

    # Rules first (names, years, durations, quality, languages) — with details already cached
    # for these files, so a repeated search is verified straight away.
    if ctx.episode and by_name:
        hits = {r.name: by_name(r.name) for r in all_results}
        ctx.episode["by_name"] = {n: [list(h[0]), h[1], h[2]] for n, h in hits.items() if h}
        # files numbered as the wanted episode whose own name is surely another one: uploaders number it
        # differently, a file of the number without a name is not sure
        ctx.episode["mixed"] = any(
            h and h[1] and tuple(h[0]) != (season, episode)
            and (p := parse_episode(n)).season == season and p.episodes == [episode]
            for n, h in hits.items())
    known = await cached_details([(r.source_type.value, r.ident) for r in all_results])

    def judged() -> list[dict]:
        rows = []
        for r in all_results:
            details = known.get((r.source_type.value, r.ident))
            ev = evaluate(r.name, r.size, ctx, prefs, details, r.duration_s, r.width, r.height)
            row = {"ident": r.ident, "name": r.name, "size": r.size, "source": r.source_type.value,
                   "source_id": r.source_id, "magnet_url": r.magnet_url, "seeders": r.seeders, "published": r.published}
            if ctx.recorded or ctx.recorded_sizes or r.published:
                ev = mark_before_release(ev, row, set(ctx.recorded), set(ctx.recorded_sizes),
                                         (ctx.releases or {}).get("digital") or "")
            rows.append({**row, "quality": ev["resolution"] or "unknown", "relevance_score": RELEVANCE[ev["film"]], **ev})
        return rows

    rows = judged()
    if ctx.pre_digital and tmdb_id and media_type == "movie":
        await remember(tmdb_id, rows)             # after the release day they are still recordings

    # AI only decides what the rules could not ("Dune Part Two" vs "Dune: Part One", odd names).
    unclear = [row for row in rows if row["film"] == "unsure"]
    if use_ai and unclear and ai.available(cfg):
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
        scored = await score_results(ai_names, scorable, cfg.get("groq_api_key", ""),
                                     languages=list(prefs.local_langs), model=cfg.get("groq_model", ""), cfg=cfg)
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
    ev = evaluate(row["name"], row["size"], ctx, prefs, details)
    ev = mark_before_release(ev, row, set(ctx.recorded), set(ctx.recorded_sizes), (ctx.releases or {}).get("digital") or "")
    return {**row, "details": details, **ev}


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
    if row.get("cinema") in ("video", "likely", "suspect"):
        return "cinema"
    if row.get("size") == owned.get("file_size"):
        return "same_file"
    if row.get("quality_score", 0) <= (owned.get("quality_score") or 0):
        return "quality"
    if has_local_audio(owned.get("language", ""), prefs) and row.get("lang_tier", 0) < LOCAL_AUDIO_TIER:
        return "language"
    return None
