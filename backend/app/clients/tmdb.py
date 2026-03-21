from datetime import date, timedelta

import httpx

from app.models.schemas import TMDBMovie

API_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=15)

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

    async def close(self) -> None:
        await self._http.aclose()
