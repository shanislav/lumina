"""Wikidata + Wikipedia — film metadata for what TMDB does not know (fan parodies, rare Czech
titles such as "Pár Pařmenů"). Official APIs, no key. Wikimedia asks for an identifying
User-Agent and no bursts, hence the throttle.

search_films(query) → [{wikidata_id, title, original_title, titles, year, runtime, overview,
                        poster_url, imdb_id, tmdb_id, csfd_id}]
"""

import logging

import httpx

from app.core.throttle import Throttle

logger = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "Lumina/1.0 (self-hosted media library manager; https://github.com/shanislav/lumina)"
THROTTLE = Throttle("Wikidata", concurrency=2, interval=0.2, cooldown=120)

# instance of (P31): film and its common kinds
FILM_CLASSES = {
    "Q11424",     # film
    "Q24862",     # short film
    "Q202866",    # animated film
    "Q506240",    # television film
    "Q226730",    # silent film
    "Q93204",     # documentary film
    "Q20667187",  # 3D film
    "Q17123180",  # sequel film
    "Q1054574",   # romance film
    "Q18011172",  # film project
}
MINUTE_UNITS = {"http://www.wikidata.org/entity/Q7727"}   # minute


def _claim_values(claims: dict, prop: str) -> list:
    out = []
    for c in claims.get(prop, []):
        value = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if value is not None:
            out.append(value)
    return out


def _year(claims: dict) -> int | None:
    years = []
    for v in _claim_values(claims, "P577"):
        t = v.get("time", "") if isinstance(v, dict) else ""
        if len(t) >= 5 and t[1:5].isdigit():
            years.append(int(t[1:5]))
    return min(years) if years else None


def _runtime(claims: dict) -> int:
    for v in _claim_values(claims, "P2047"):
        if isinstance(v, dict) and v.get("unit") in MINUTE_UNITS:
            try:
                return round(float(v["amount"]))
            except (KeyError, ValueError):
                pass
    return 0


def _first_str(claims: dict, prop: str) -> str:
    vals = [v for v in _claim_values(claims, prop) if isinstance(v, str)]
    return vals[0] if vals else ""


def parse_entity(entity: dict, languages: tuple[str, ...] = ("cs", "sk", "en")) -> dict | None:
    """One Wikidata entity → film metadata, or None when it is not a film."""
    claims = entity.get("claims") or {}
    kinds = {v.get("id") for v in _claim_values(claims, "P31") if isinstance(v, dict)}
    description = " ".join((d.get("value") or "") for d in (entity.get("descriptions") or {}).values()).lower()
    if not (kinds & FILM_CLASSES or "film" in description):
        return None
    labels = {k: v.get("value", "") for k, v in (entity.get("labels") or {}).items()}
    aliases = [a.get("value", "") for lang in languages for a in (entity.get("aliases") or {}).get(lang, [])]
    title = next((labels[l] for l in languages if labels.get(l)), next(iter(labels.values()), ""))
    original = next((v.get("text") for v in _claim_values(claims, "P1476") if isinstance(v, dict)), "") or title
    titles = []
    for t in [title, original, *(labels.get(l, "") for l in languages), *aliases]:
        if t and t not in titles:
            titles.append(t)
    image = _first_str(claims, "P3383") or _first_str(claims, "P18")   # poster, else any image
    tmdb = _first_str(claims, "P4947")
    return {
        "wikidata_id": entity.get("id", ""),
        "title": title,
        "original_title": original,
        "titles": titles,
        "year": _year(claims),
        "runtime": _runtime(claims),
        "overview": next((entity.get("descriptions", {}).get(l, {}).get("value", "") for l in languages
                          if entity.get("descriptions", {}).get(l)), ""),
        "poster_url": (f"https://commons.wikimedia.org/wiki/Special:FilePath/{image.replace(' ', '_')}?width=500"
                       if image else None),
        "imdb_id": _first_str(claims, "P345"),
        "tmdb_id": int(tmdb) if tmdb.isdigit() else None,
        "csfd_id": _first_str(claims, "P2529"),
        "wiki_title": ((entity.get("sitelinks") or {}).get("cswiki") or {}).get("title", ""),
    }


class WikidataClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT})

    async def _get(self, url: str, params: dict) -> dict:
        async with THROTTLE.slot():
            resp = await self._http.get(url, params=params)
        if resp.status_code in (403, 429):
            THROTTLE.trip()
        resp.raise_for_status()
        return resp.json()

    async def search_films(self, query: str, language: str = "cs", limit: int = 8) -> list[dict]:
        if THROTTLE.cooling_down:
            return []
        found = await self._get(WIKIDATA_API, {"action": "wbsearchentities", "search": query, "language": language,
                                               "uselang": language, "type": "item", "limit": 20, "format": "json"})
        ids = [x["id"] for x in found.get("search", [])]
        if not ids:
            return []
        data = await self._get(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(ids),
                                              "props": "labels|aliases|descriptions|claims|sitelinks",
                                              "languages": "cs|sk|en", "format": "json"})
        films = [f for f in (parse_entity(data["entities"][i]) for i in ids if i in data.get("entities", {})) if f]
        for film in films[:limit]:
            if film["wiki_title"] and (not film["overview"] or not film["poster_url"]):
                await self._add_wikipedia(film)
        return films[:limit]

    async def get_film(self, wikidata_id: str) -> dict | None:
        data = await self._get(WIKIDATA_API, {"action": "wbgetentities", "ids": wikidata_id,
                                              "props": "labels|aliases|descriptions|claims|sitelinks",
                                              "languages": "cs|sk|en", "format": "json"})
        entity = data.get("entities", {}).get(wikidata_id)
        return parse_entity(entity) if entity else None

    async def _add_wikipedia(self, film: dict) -> None:
        """Czech Wikipedia summary: a readable description and a picture."""
        title = film["wiki_title"].replace(" ", "_")
        try:
            summary = await self._get(f"https://cs.wikipedia.org/api/rest_v1/page/summary/{title}", {})
        except httpx.HTTPError as e:
            logger.info("Wikipedia summary of %s failed: %s", title, e)
            return
        if summary.get("extract"):
            film["overview"] = summary["extract"]
        if not film["poster_url"] and (summary.get("thumbnail") or {}).get("source"):
            film["poster_url"] = summary["thumbnail"]["source"]

    async def close(self) -> None:
        await self._http.aclose()
