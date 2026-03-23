import logging
import httpx
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

class RadarrClient:
    """Client for interacting with Radarr API."""

    def __init__(self, api_url: str, api_key: str) -> None:
        self._url = api_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            timeout=30,
            headers={"X-Api-Key": self._api_key},
        )

    async def get_movie_by_tmdb_id(self, tmdb_id: int) -> Optional[dict]:
        """Check if a movie exists in Radarr by its TMDB ID."""
        try:
            resp = await self._http.get(f"{self._url}/api/v3/movie")
            if resp.status_code != 200:
                logger.error("Radarr API error: %s", resp.status_code)
                return None
            
            movies = resp.json()
            for movie in movies:
                if movie.get("tmdbId") == tmdb_id:
                    return movie
            return None
        except Exception as e:
            logger.error("Failed to check movie in Radarr: %s", e)
            return None

    async def add_movie(self, tmdb_id: int, title: str, year: int, root_folder: str, profile_id: int = 1) -> bool:
        """Add a new movie to Radarr."""
        try:
            # First lookup in TMDB to get full movie data
            lookup_resp = await self._http.get(f"{self._url}/api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
            if lookup_resp.status_code != 200:
                logger.error("Radarr lookup error for TMDB %s: %s", tmdb_id, lookup_resp.status_code)
                return False
            
            movie_data = lookup_resp.json()
            
            # Prepare payload
            payload = {
                **movie_data,
                "rootFolderPath": root_folder,
                "qualityProfileId": profile_id,
                "monitored": True,
                "addOptions": {"searchForMovie": False}
            }
            
            resp = await self._http.post(f"{self._url}/api/v3/movie", json=payload)
            if resp.status_code in (200, 201):
                logger.info("Movie '%s (%d)' added to Radarr (TMDB %d)", title, year, tmdb_id)
                return True
            else:
                logger.error("Failed to add movie to Radarr: %s", resp.text)
                return False
        except Exception as e:
            logger.error("Radarr client error adding movie: %s", e)
            return False

    async def trigger_blackhole_scan(self, path: str) -> bool:
        """Trigger Radarr to scan a specific folder for new movies (DownloadedMoviesScan)."""
        try:
            payload = {
                "name": "DownloadedMoviesScan",
                "path": path
            }
            resp = await self._http.post(f"{self._url}/api/v3/command", json=payload)
            if resp.status_code in (200, 201, 202):
                logger.info("Radarr scan triggered for path: %s", path)
                return True
            return False
        except Exception as e:
            logger.error("Failed to trigger Radarr scan: %s", e)
            return False

    async def close(self) -> None:
        await self._http.aclose()

    async def get_root_folders(self) -> List[dict]:
        """Get configured root folders from Radarr."""
        try:
            resp = await self._http.get(f"{self._url}/api/v3/rootfolder")
            return resp.json() if resp.status_code == 200 else []
        except Exception:
            return []

    async def get_quality_profiles(self) -> List[dict]:
        """Get quality profiles from Radarr."""
        try:
            resp = await self._http.get(f"{self._url}/api/v3/qualityprofile")
            return resp.json() if resp.status_code == 200 else []
        except Exception:
            return []
