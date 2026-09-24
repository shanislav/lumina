import sqlite3

from app.core import registry
from app.db import DB_PATH, init_db

# Schema as created by init_db() before per-module migrations existed (upstream 5fa0aa1),
# but without the columns that were added later via ALTER TABLE, to cover the oldest DBs.
LEGACY_SCHEMA = """
CREATE TABLE sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
CREATE TABLE automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0, config TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE download_tracker (
    id TEXT PRIMARY KEY, tmdb_id INTEGER, title TEXT, year INTEGER, backend TEXT,
    status TEXT, target_dir TEXT, processed INTEGER DEFAULT 0
);
CREATE TABLE library_movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER, title TEXT, original_title TEXT,
    year TEXT, poster_url TEXT, overview TEXT, filename TEXT, file_path TEXT UNIQUE,
    file_size INTEGER DEFAULT 0, quality TEXT DEFAULT '', language TEXT DEFAULT '',
    added_at TEXT, scanned_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE scanned_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
    normalized_title TEXT NOT NULL, year TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0,
    quality TEXT NOT NULL DEFAULT 'unknown', language TEXT NOT NULL DEFAULT '-',
    modified_at TEXT NOT NULL DEFAULT '', scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO settings VALUES ('tmdb_api_key', 'secret-key');
INSERT INTO sources (type, name, config) VALUES ('webshare', 'WebShare', '{"username": "u"}');
INSERT INTO automations (type, name, enabled, config) VALUES ('renamer', 'Renamer (Media Info)', 1, '{"format": "x"}');
INSERT INTO download_tracker (id, title, processed) VALUES ('gid1', 'Matrix', 1);
INSERT INTO library_movies (title, file_path) VALUES ('Matrix', '/m/matrix.mkv');
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


async def test_fresh_db_gets_all_tables():
    await init_db(registry.discover())
    with sqlite3.connect(DB_PATH) as conn:
        assert {
            "settings", "sources", "automations", "download_tracker", "library_movies",
            "library_shows", "library_episodes", "schema_migrations",
            "tmdb_movies", "file_operations",
        } <= _tables(conn)
        assert {r[0] for r in conn.execute("SELECT type FROM automations")} == {"renamer", "radarr", "sonarr", "nfo"}
        assert "content_type" in _columns(conn, "download_tracker")
        assert "matched_by" in _columns(conn, "library_movies")


async def test_migrations_are_idempotent():
    modules = registry.discover()
    await init_db(modules)
    with sqlite3.connect(DB_PATH) as conn:
        first = conn.execute("SELECT module, version FROM schema_migrations ORDER BY 1, 2").fetchall()
    await init_db(modules)
    with sqlite3.connect(DB_PATH) as conn:
        second = conn.execute("SELECT module, version FROM schema_migrations ORDER BY 1, 2").fetchall()
    assert first == second


async def test_legacy_db_keeps_data_and_gets_new_columns():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(LEGACY_SCHEMA)

    await init_db(registry.discover())

    with sqlite3.connect(DB_PATH) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='tmdb_api_key'").fetchone() == ("secret-key",)
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)
        # existing automation row (enabled + config) must not be reset by the seed migration
        assert conn.execute("SELECT enabled, config FROM automations WHERE type='renamer'").fetchone() == (1, '{"format": "x"}')
        assert conn.execute("SELECT title FROM download_tracker").fetchone() == ("Matrix",)
        assert conn.execute("SELECT title FROM library_movies").fetchone() == ("Matrix",)
        assert "content_type" in _columns(conn, "download_tracker")
        assert "matched_by" in _columns(conn, "library_movies")
