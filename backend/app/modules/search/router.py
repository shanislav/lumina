import asyncio
import logging
from urllib.parse import urlencode

from fastapi import Depends, APIRouter

from app.config import get_effective_settings
from app.clients.tmdb import TMDBClient
from app.clients.wikidata import WikidataClient
from app.core.offers.details import get_details
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.offers.search import find_offers
from app.core.profiles import block, get_profile, suitable
from app.core.quality import prefs_from_settings
from app.models.schemas import TMDBMovie, ScoredFile
from pydantic import BaseModel
from app.core.auth import require

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])


# TMDB wants a real locale — "cs-CS" does not exist and TMDB then answers in English.
TMDB_LOCALES = {"cs": "cs-CZ", "sk": "sk-SK", "en": "en-US", "de": "de-DE", "pl": "pl-PL", "hu": "hu-HU",
                "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "ru": "ru-RU", "uk": "uk-UA"}


def _locale(cfg: dict, language: str | None) -> tuple[str, str]:
    """(language code, TMDB locale) — an explicit request language, else the metadata language setting."""
    code = (language or cfg.get("metadata_language") or "cs").strip().lower()
    return code, TMDB_LOCALES.get(code, code)


@router.get("/search/movies", dependencies=[Depends(require("search"))], response_model=list[TMDBMovie])
async def search_movies(query: str, language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code, tmdb_locale = _locale(cfg, language)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        results = await asyncio.gather(
            client.search_movie(query, language=tmdb_locale),
            client.search_tv(query, language=tmdb_locale),
            return_exceptions=True,
        )
        movies = results[0] if isinstance(results[0], list) else []
        shows = results[1] if isinstance(results[1], list) else []
        if not movies:
            movies = await _wikidata_films(query, lang_code)
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


_suggest_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
SUGGEST_TTL_S = 600


def _suggestion(item: dict, person: str = "") -> dict | None:
    kind = item.get("media_type")
    if kind not in ("movie", "tv"):
        return None
    date = item.get("release_date") or item.get("first_air_date") or ""
    poster = item.get("poster_path")
    return {"tmdb_id": item["id"], "media_type": kind,
            "title": item.get("title") or item.get("name") or "",
            "original_title": item.get("original_title") or item.get("original_name") or "",
            "year": date[:4], "overview": item.get("overview") or "",
            "poster_url": f"https://image.tmdb.org/t/p/w500{poster}" if poster else None,
            "popularity": item.get("popularity") or 0, "person": person}


@router.get("/search/suggest", dependencies=[Depends(require("search"))])
async def search_suggest(q: str) -> list[dict]:
    """As-you-type suggestions: films and shows whose title fits, and the best-known films of a person
    ("keanu" → Matrix, John Wick) — for when the exact title is not remembered."""
    import time
    query = q.strip()
    if len(query) < 2:
        return []
    cfg = await get_effective_settings()
    _, locale = _locale(cfg, None)
    key = (query.lower(), locale)
    hit = _suggest_cache.get(key)
    if hit and time.time() - hit[0] < SUGGEST_TTL_S:
        return hit[1]
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        results = await client.search_multi(query, language=locale)
    except Exception as e:
        logger.info("Suggest '%s' failed: %s", query, e)
        return []
    finally:
        await client.close()
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def add(s: dict | None) -> None:
        if s and (s["media_type"], s["tmdb_id"]) not in seen:
            seen.add((s["media_type"], s["tmdb_id"]))
            out.append(s)

    for item in results[:10]:
        if item.get("media_type") == "person":
            for known in sorted(item.get("known_for") or [], key=lambda k: -(k.get("popularity") or 0))[:3]:
                add(_suggestion(known, person=item.get("name") or ""))
        else:
            add(_suggestion(item))
    out = out[:8]
    from app.db import get_db
    db = await get_db()
    try:
        ids = [x["tmdb_id"] for x in out if x["media_type"] == "movie"]
        owned = {r[0] for r in await (await db.execute(
            f"SELECT DISTINCT tmdb_id FROM library_movies WHERE tmdb_id IN ({','.join('?' * len(ids))})", ids)).fetchall()} if ids else set()
    except Exception:            # the library module may be switched off
        owned = set()
    finally:
        await db.close()
    for x in out:
        x["in_library"] = x["media_type"] == "movie" and x["tmdb_id"] in owned
    if len(_suggest_cache) > 500:
        _suggest_cache.clear()
    _suggest_cache[key] = (time.time(), out)
    return out


async def _wikidata_films(query: str, lang_code: str) -> list[TMDBMovie]:
    """Films TMDB does not know (fan parodies, rare Czech titles) — from Wikidata/Wikipedia."""
    client = WikidataClient()
    try:
        films = await client.search_films(query, language=lang_code)
    except Exception as e:
        logger.info("Wikidata search '%s' failed: %s", query, e)
        return []
    finally:
        await client.close()
    return [TMDBMovie(tmdb_id=0, title=f["title"], original_title=f["original_title"], year=str(f["year"] or ""),
                      overview=f["overview"], poster_url=f["poster_url"], media_type="movie",
                      wikidata_id=f["wikidata_id"]) for f in films]


@router.get("/discover/trending", dependencies=[Depends(require("search"))], response_model=list[TMDBMovie])
async def discover_trending(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code, tmdb_locale = _locale(cfg, language)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.trending(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/now-playing", dependencies=[Depends(require("search"))], response_model=list[TMDBMovie])
async def discover_now_playing(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code, tmdb_locale = _locale(cfg, language)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.now_playing(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/recently-digital", dependencies=[Depends(require("search"))], response_model=list[TMDBMovie])
async def discover_recently_digital(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code, tmdb_locale = _locale(cfg, language)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.recently_digital(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/recently-digital-tv", dependencies=[Depends(require("search"))], response_model=list[TMDBMovie])
async def discover_recently_digital_tv(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code, tmdb_locale = _locale(cfg, language)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.recently_digital_tv(language=tmdb_locale)
    finally:
        await client.close()


@router.get("/discover/popular", dependencies=[Depends(require("search"))], response_model=list[TMDBMovie])
async def discover_popular(language: str | None = None) -> list[TMDBMovie]:
    cfg = await get_effective_settings()
    lang_code, tmdb_locale = _locale(cfg, language)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        return await client.popular(language=tmdb_locale)
    finally:
        await client.close()


class SearchFilesResponse(BaseModel):
    movie: dict
    prefer_local_audio: bool
    files: list[ScoredFile]


@router.get("/search/files", dependencies=[Depends(require("search"))], response_model=SearchFilesResponse)
async def search_files(
    query: str,
    language: str | None = None,
    original_title: str | None = None,
    tmdb_id: int | None = None,
    media_type: str | None = None,
    wikidata_id: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    torrent: bool = True,
) -> "SearchFilesResponse":
    """All files of a film on all sources, judged (app/core/offers). A TV show with season + episode:
    the files of that episode."""
    cfg = await get_effective_settings()
    offers = await find_offers(cfg, query, original_title=original_title or "", tmdb_id=tmdb_id,
                               media_type=media_type or "movie", wikidata_id=wikidata_id,
                               season=season, episode=episode, torrent=torrent)
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


@router.post("/search/details", dependencies=[Depends(require("search"))])
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


class PickRequest(BaseModel):
    profile_id: int | None = None
    files: list[dict]             # the offers as the file table has them (verified ones re-evaluated)


@router.post("/search/pick", dependencies=[Depends(require("search"))])
async def search_pick(body: PickRequest) -> dict:
    """The offer a quality profile would take now (what "Chci" would download), and why the others not."""
    profile = await get_profile(body.profile_id)
    ok = suitable(body.files, profile)
    reasons: dict[str, int] = {}
    for r in body.files:
        if r.get("film") in ("yes", "unsure") and (why := block(r, profile)):
            reasons[why] = reasons.get(why, 0) + 1
    best = ok[0] if ok else None
    return {
        "profile": profile.name,
        "key": f"{best['source_id']}:{best['ident']}" if best else None,
        "suitable": len(ok),
        "reasons": sorted(reasons.items(), key=lambda x: -x[1])[:3],
    }


_info_cache: dict[tuple[int, str], dict] = {}


@router.get("/search/movie-info", dependencies=[Depends(require("search"))])
async def movie_info(tmdb_id: int = 0, wikidata_id: str = "", title: str = "", year: str = "") -> dict:
    """Genres, length, rating, director, cast and links (ČSFD, IMDb) for the film's page.
    ČSFD has no API: the exact page comes from Wikidata (property P2529), else a ČSFD search."""
    key = (tmdb_id, wikidata_id)
    if key in _info_cache:
        return _info_cache[key]
    out: dict = {"genres": [], "runtime": 0, "rating": 0, "votes": 0, "directors": [], "cast": [],
                 "imdb_url": None, "csfd_url": None, "csfd_exact": False}
    cfg = await get_effective_settings()
    if tmdb_id:
        client = TMDBClient(cfg["tmdb_api_key"])
        try:
            full = await client.get_movie_full(tmdb_id, language=_locale(cfg, None)[1])
            out.update({k: full.get(k) for k in ("genres", "runtime", "rating", "votes", "directors", "cast")})
            if full.get("imdb_id"):
                out["imdb_url"] = f"https://www.imdb.com/title/{full['imdb_id']}/"
            wikidata_id = wikidata_id or full.get("wikidata_id") or ""
            title = title or full.get("title") or ""
            year = year or str(full.get("year") or "")
        except Exception as e:  # noqa: BLE001 — the page works without it
            logger.info("Movie info of tmdb %s failed: %s", tmdb_id, e)
        finally:
            await client.close()
    if wikidata_id:
        wd = WikidataClient()
        try:
            film = await wd.get_film(wikidata_id)
            if film and film.get("csfd_id"):
                out.update(csfd_url=f"https://www.csfd.cz/film/{film['csfd_id']}/", csfd_exact=True)
        except Exception as e:  # noqa: BLE001
            logger.info("Wikidata %s failed: %s", wikidata_id, e)
        finally:
            await wd.close()
    if not out["csfd_url"] and title:
        out["csfd_url"] = "https://www.csfd.cz/hledat/?" + urlencode({"q": f"{title} {year}".strip()})
    _info_cache[key] = out
    return out
