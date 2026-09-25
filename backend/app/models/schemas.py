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
    media_type: str = "movie"  # "movie" or "tv"
    wikidata_id: str | None = None   # a film TMDB does not know, found on Wikidata (tmdb_id = 0)


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
    # Languages from the file name (deterministic parser); the UI replaces them with
    # the real tracks from /api/search/details when the source knows them.
    audio_langs: list[str] = []
    subtitle_langs: list[str] = []
    # evaluation (app/core/offers/evaluate.py)
    film: str = "unsure"                 # yes | unsure | length | no
    film_reasons: list[str] = []
    quality_score: int = 0
    quality_summary: str = ""
    quality_parts: list[list] = []
    resolution: str = ""
    codec: str = ""
    bitrate: int = 0
    hdr: str = ""
    duration_s: int = 0
    audio: list[dict] = []
    verified: bool = False
    lang_tier: int = 0


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
    # What to do with an already owned movie once the download finishes:
    # {"mode": "replace", "file_id": <library file id>} or {"mode": "version"}
    library_action: dict | None = None


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
