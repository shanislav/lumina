"""Library: scans the movie/TV library folders, matches files with TMDB (NFO first)."""

from app.core.migrations import add_column
from app.core.module import Module, Subscription
from app.modules.library.imports import on_download_completed
from app.modules.library.organize import FILE_OPERATIONS
from app.modules.library.router import router

LIBRARY_V1 = """
CREATE TABLE IF NOT EXISTS library_movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER,
    title TEXT,
    original_title TEXT,
    year TEXT,
    poster_url TEXT,
    overview TEXT,
    filename TEXT,
    file_path TEXT UNIQUE,
    file_size INTEGER DEFAULT 0,
    quality TEXT DEFAULT '',
    language TEXT DEFAULT '',
    added_at TEXT,
    scanned_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS library_shows (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT,
    original_title TEXT,
    year TEXT,
    poster_url TEXT,
    overview TEXT,
    total_seasons INTEGER DEFAULT 0,
    total_episodes INTEGER DEFAULT 0,
    scanned_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS library_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_tmdb_id INTEGER REFERENCES library_shows(tmdb_id) ON DELETE CASCADE,
    season INTEGER,
    episode INTEGER,
    episode_title TEXT DEFAULT '',
    air_date TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    quality TEXT DEFAULT '',
    language TEXT DEFAULT '',
    has_file INTEGER DEFAULT 0,
    UNIQUE(show_tmdb_id, season, episode)
);
"""

TMDB_CACHE = """
CREATE TABLE IF NOT EXISTS tmdb_movies (
    tmdb_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""

module = Module(
    name="library",
    title="Knihovna",
    order=20,
    routers=[router],
    # before radarr/sonarr (p50): a download meant as a new version/replacement goes straight to the library
    subscriptions=[Subscription("download.completed", on_download_completed, priority=30)],
    migrations=[
        LIBRARY_V1,
        add_column("library_movies", "matched_by", "TEXT DEFAULT 'filename'"),
        # v3+: identification with confidence + review queue (phase 1)
        add_column("library_movies", "status", "TEXT DEFAULT 'matched'"),
        add_column("library_movies", "confidence", "INTEGER DEFAULT 0"),
        add_column("library_movies", "candidates", "TEXT DEFAULT '[]'"),
        add_column("library_movies", "media", "TEXT DEFAULT '{}'"),
        add_column("library_movies", "duration_s", "INTEGER DEFAULT 0"),
        add_column("library_movies", "file_mtime", "REAL DEFAULT 0"),
        add_column("library_movies", "imdb_id", "TEXT DEFAULT ''"),
        TMDB_CACHE,
        FILE_OPERATIONS,
    ],
)
