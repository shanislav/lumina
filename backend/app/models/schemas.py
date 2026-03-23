from pydantic import BaseModel


class WebShareFile(BaseModel):
    ident: str
    name: str
    size: int
    positive_votes: int
    negative_votes: int


class TorrentResult(BaseModel):
    title: str
    size: int
    seeders: int
    leechers: int
    magnet_url: str
    link: str
    category: str
    genres: list[str] = []
    description: str = ""
    grabs: int | None = None
    published_date: str = ""


class TMDBMovie(BaseModel):
    tmdb_id: int
    title: str
    original_title: str
    year: str
    overview: str
    poster_url: str | None


class ScorableFile(BaseModel):
    """Unified intermediate type for scoring — merges all source results."""

    index: int
    name: str
    size: int
    source: str
    source_id: int = 0
    ident: str
    magnet_url: str | None = None
    seeders: int | None = None


class ScoredFile(BaseModel):
    ident: str
    name: str
    size: int
    quality: str
    is_dubbed: bool
    relevance_score: int
    source: str = "webshare"
    source_id: int = 0
    magnet_url: str | None = None
    seeders: int | None = None


class SearchRequest(BaseModel):
    query: str


class DownloadRequest(BaseModel):
    file_ident: str
    source: str = "webshare"
    source_id: int = 0
    magnet_url: str | None = None
    target_folder: str | None = None
    content_type: str = "movie"  # "movie" or "tv"
    # Fields for automation tracking
    tmdb_id: int | None = None
    title: str | None = ""
    year: int | None = 0


# --- Source CRUD models ---


class SourceCreate(BaseModel):
    type: str  # webshare | fastshare | jackett
    name: str
    enabled: bool = True
    config: dict


class SourceUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict | None = None


class SourceResponse(BaseModel):
    id: int
    type: str
    name: str
    enabled: bool
    config: dict
    created_at: str
    updated_at: str

# --- Automation & Integration models ---

class Automation(BaseModel):
    id: int
    type: str
    name: str
    enabled: bool
    config: dict


class AutomationUpdate(BaseModel):
    enabled: bool | None = None
    config: dict | None = None
