from datetime import date, timedelta

import httpx

from app.models.schemas import TMDBMovie

API_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=30)

    async def search_movie(self, title: str, language: str = "cs-CZ") -> list[TMDBMovie]:
        resp = await self._http.get(
            f"{API_BASE}/search/movie",
            params={
                "api_key": self._api_key,
                "query": title,
                "language": language,
                "include_adult": False,
            },
        )
        resp.raise_for_status()
        return await self._parse_movies(resp.json().get("results", []), limit=12)

    async def search_tv(self, title: str, language: str = "cs-CZ") -> list[TMDBMovie]:
        resp = await self._http.get(
            f"{API_BASE}/search/tv",
            params={
                "api_key": self._api_key,
                "query": title,
                "language": language,
            },
        )
        resp.raise_for_status()
        return await self._parse_tv(resp.json().get("results", []), limit=12)

    async def _parse_movies(self, items: list, limit: int = 20) -> list[TMDBMovie]:
        movies: list[TMDBMovie] = []
        for item in items[:limit]:
            release = item.get("release_date", "") or ""
            poster_path = item.get("poster_path")
            movies.append(
                TMDBMovie(
                    tmdb_id=item["id"],
                    title=item.get("title", ""),
                    original_title=item.get("original_title", ""),
                    year=release[:4] if len(release) >= 4 else "",
                    overview=item.get("overview", ""),
                    poster_url=f"{IMG_BASE}{poster_path}" if poster_path else None,
                    media_type="movie",
                )
            )
        return movies

    async def trending(self, language: str = "cs-CZ", time_window: str = "week") -> list[TMDBMovie]:
        resp = await self._http.get(
            f"{API_BASE}/trending/movie/{time_window}",
            params={"api_key": self._api_key, "language": language},
        )
        resp.raise_for_status()
        return await self._parse_movies(resp.json().get("results", []))

    async def now_playing(self, language: str = "cs-CZ") -> list[TMDBMovie]:
        resp = await self._http.get(
            f"{API_BASE}/movie/now_playing",
            params={"api_key": self._api_key, "language": language, "region": "CZ"},
        )
        resp.raise_for_status()
        return await self._parse_movies(resp.json().get("results", []))

    async def popular(self, language: str = "cs-CZ") -> list[TMDBMovie]:
        resp = await self._http.get(
            f"{API_BASE}/movie/popular",
            params={"api_key": self._api_key, "language": language, "region": "CZ"},
        )
        resp.raise_for_status()
        return await self._parse_movies(resp.json().get("results", []))

    async def recently_digital(self, language: str = "cs-CZ", days: int = 90) -> list[TMDBMovie]:
        """Movies with digital/streaming release in the last N days."""
        today = date.today()
        date_from = (today - timedelta(days=days)).isoformat()
        date_to = today.isoformat()
        resp = await self._http.get(
            f"{API_BASE}/discover/movie",
            params={
                "api_key": self._api_key,
                "language": language,
                "region": "CZ",
                "with_release_type": "4",  # 4 = digital
                "release_date.gte": date_from,
                "release_date.lte": date_to,
                "sort_by": "popularity.desc",
            },
        )
        resp.raise_for_status()
        return await self._parse_movies(resp.json().get("results", []))

    async def recently_digital_tv(self, language: str = "cs-CZ", days: int = 90) -> list[TMDBMovie]:
        """TV shows with recent episodes in the last N days."""
        today = date.today()
        date_from = (today - timedelta(days=days)).isoformat()
        date_to = today.isoformat()
        resp = await self._http.get(
            f"{API_BASE}/discover/tv",
            params={
                "api_key": self._api_key,
                "language": language,
                "air_date.gte": date_from,
                "air_date.lte": date_to,
                "sort_by": "popularity.desc",
                "with_original_language": "en|cs",
            },
        )
        resp.raise_for_status()
        return await self._parse_tv(resp.json().get("results", []))

    async def _parse_tv(self, items: list, limit: int = 20) -> list[TMDBMovie]:
        shows: list[TMDBMovie] = []
        for item in items[:limit]:
            air_date = item.get("first_air_date", "") or ""
            poster_path = item.get("poster_path")
            shows.append(
                TMDBMovie(
                    tmdb_id=item["id"],
                    title=item.get("name", ""),
                    original_title=item.get("original_name", ""),
                    year=air_date[:4] if len(air_date) >= 4 else "",
                    overview=item.get("overview", ""),
                    poster_url=f"{IMG_BASE}{poster_path}" if poster_path else None,
                    media_type="tv",
                )
            )
        return shows

    async def get_english_title(self, tmdb_id: int, media_type: str = "movie") -> str:
        """Fetch the English title for a movie or TV show."""
        endpoint = "movie" if media_type == "movie" else "tv"
        resp = await self._http.get(
            f"{API_BASE}/{endpoint}/{tmdb_id}",
            params={"api_key": self._api_key, "language": "en-US"},
        )
        resp.raise_for_status()
        data = resp.json()
        if media_type == "tv":
            return data.get("name", "")
        return data.get("title", "")

    async def get_tv_details(self, tmdb_id: int, language: str = "cs-CZ") -> dict:
        """Fetch TV show details including season count."""
        resp = await self._http.get(
            f"{API_BASE}/tv/{tmdb_id}",
            params={"api_key": self._api_key, "language": language},
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "tmdb_id": tmdb_id,
            "title": data.get("name", ""),
            "original_title": data.get("original_name", ""),
            "overview": data.get("overview", ""),
            "poster_url": f"{IMG_BASE}{data['poster_path']}" if data.get("poster_path") else None,
            "total_seasons": data.get("number_of_seasons", 0),
            "total_episodes": data.get("number_of_episodes", 0),
            "first_air_date": data.get("first_air_date", ""),
            "seasons": [
                {
                    "season_number": s.get("season_number", 0),
                    "episode_count": s.get("episode_count", 0),
                    "name": s.get("name", ""),
                    "air_date": s.get("air_date", ""),
                }
                for s in data.get("seasons", [])
                if s.get("season_number", 0) > 0  # skip specials (S00)
            ],
        }

    async def get_season(self, tmdb_id: int, season_number: int, language: str = "cs-CZ") -> list[dict]:
        """Fetch episodes for a specific season."""
        resp = await self._http.get(
            f"{API_BASE}/tv/{tmdb_id}/season/{season_number}",
            params={"api_key": self._api_key, "language": language},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "episode_number": ep.get("episode_number", 0),
                "name": ep.get("name", ""),
                "air_date": ep.get("air_date", ""),
                "overview": ep.get("overview", ""),
            }
            for ep in data.get("episodes", [])
        ]

    async def get_movie_details(self, tmdb_id: int, language: str = "cs-CZ") -> dict:
        """Fetch movie details."""
        resp = await self._http.get(
            f"{API_BASE}/movie/{tmdb_id}",
            params={"api_key": self._api_key, "language": language},
        )
        resp.raise_for_status()
        data = resp.json()
        release = data.get("release_date", "") or ""
        return {
            "tmdb_id": tmdb_id,
            "title": data.get("title", ""),
            "original_title": data.get("original_title", ""),
            "year": release[:4] if len(release) >= 4 else "",
            "overview": data.get("overview", ""),
            "poster_url": f"{IMG_BASE}{data['poster_path']}" if data.get("poster_path") else None,
        }

    async def search_movie_raw(self, title: str, year: int | None = None, language: str = "cs-CZ") -> list[dict]:
        """Search movies; returns raw TMDB result dicts (id, title, original_title, release_date, ...)."""
        params = {"api_key": self._api_key, "query": title, "language": language, "include_adult": False}
        if year:
            params["primary_release_year"] = year
        resp = await self._http.get(f"{API_BASE}/search/movie", params=params)
        resp.raise_for_status()
        return resp.json().get("results", [])

    async def find_by_imdb(self, imdb_id: str) -> int | None:
        resp = await self._http.get(
            f"{API_BASE}/find/{imdb_id}",
            params={"api_key": self._api_key, "external_source": "imdb_id"},
        )
        resp.raise_for_status()
        results = resp.json().get("movie_results", [])
        return results[0]["id"] if results else None

    async def get_movie_full(self, tmdb_id: int, language: str = "cs-CZ") -> dict:
        """Movie details needed for matching: runtime, original language and titles in all languages."""
        resp = await self._http.get(
            f"{API_BASE}/movie/{tmdb_id}",
            params={"api_key": self._api_key, "language": language, "append_to_response": "translations"},
        )
        resp.raise_for_status()
        data = resp.json()
        release = data.get("release_date", "") or ""
        original_language = data.get("original_language", "")
        titles_by_lang: dict[str, str] = {}
        if original_language and data.get("original_title"):
            titles_by_lang[original_language] = data["original_title"]
        for tr in data.get("translations", {}).get("translations", []):
            lang = tr.get("iso_639_1")
            title = ((tr.get("data") or {}).get("title") or "").strip()
            # several regional variants (cs-CZ, en-US/en-GB): the first one wins
            if lang and title and lang not in titles_by_lang:
                titles_by_lang[lang] = title
        titles = {data.get("title", ""), data.get("original_title", "")}
        titles.update(t for lang, t in titles_by_lang.items() if lang in ("cs", "sk", "en"))
        return {
            "tmdb_id": tmdb_id,
            "title": data.get("title", ""),
            "original_title": data.get("original_title", ""),
            "year": int(release[:4]) if len(release) >= 4 else None,
            "runtime": data.get("runtime") or 0,
            "original_language": data.get("original_language", ""),
            "imdb_id": data.get("imdb_id") or "",
            "overview": data.get("overview", ""),
            "poster_url": f"{IMG_BASE}{data['poster_path']}" if data.get("poster_path") else None,
            "titles": sorted(t for t in titles if t),
            "titles_by_lang": titles_by_lang,
        }

    async def close(self) -> None:
        await self._http.aclose()
