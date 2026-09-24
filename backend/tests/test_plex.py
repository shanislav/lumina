"""Plex module: path mapping and which folders get scanned (fake Plex, no network)."""

import json
import sqlite3

import pytest

from app.core import registry
from app.db import DB_PATH, init_db
from app.modules.plex import handler
from app.modules.plex.paths import section_for, to_plex

ROOT = "/data/Video/Movies"
FOLDER = "/data/Video/Movies/2014/Interstellar (2014)"


@pytest.mark.parametrize("locations, rule, expected", [
    (["/data/Video/Movies"], "", FOLDER),                                            # same paths
    (["/mnt/share/Video/Movies"], "", "/mnt/share/Video/Movies/2014/Interstellar (2014)"),  # shared tail
    (["/movies"], "", None),                                                         # no way to tell
    (["/movies"], "/data/Video/Movies=/movies", "/movies/2014/Interstellar (2014)"),  # manual rule
    (["/movies"], "/other=/movies", None),
])
def test_to_plex(locations, rule, expected):
    assert to_plex(FOLDER, ROOT, locations, rule) == expected


def test_section_for():
    sections = [{"key": "1", "locations": ["/mnt/Movies"]}, {"key": "2", "locations": ["/mnt/MoviesKids"]}]
    assert section_for("/mnt/MoviesKids/2014/X", sections)["key"] == "2"
    assert section_for("/mnt/Movies/2014/X", sections)["key"] == "1"


class FakePlex:
    scans: list = []

    def __init__(self, url, token):
        pass

    async def sections(self):
        return [{"key": "3", "title": "Filmy", "type": "movie", "locations": ["/share/Video/Movies"]},
                {"key": "4", "title": "Seriály", "type": "show", "locations": ["/share/Video/Serials"]}]

    async def scan(self, key, path=None):
        FakePlex.scans.append((key, path))

    async def close(self):
        pass


@pytest.fixture
async def plex(monkeypatch):
    await init_db(registry.discover())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('movies_library_dir', ?)", (ROOT,))
        conn.execute("UPDATE automations SET enabled = 1, config = ? WHERE type = 'plex'",
                     (json.dumps({"url": "http://plex:32400", "token": "t"}),))
    FakePlex.scans = []
    monkeypatch.setattr(handler, "PlexClient", FakePlex)


async def test_scans_changed_folders(plex):
    done = await handler.scan_folders([FOLDER, "/elsewhere/X"])
    assert FakePlex.scans == [("3", "/share/Video/Movies/2014/Interstellar (2014)")]
    assert done == ["3: /share/Video/Movies/2014/Interstellar (2014)"]


async def test_many_folders_scan_the_section_once(plex):
    folders = [f"{ROOT}/2000/Movie {i} (2000)" for i in range(handler.MAX_FOLDERS + 1)]
    await handler.scan_folders(folders)
    assert FakePlex.scans == [("3", None)]


async def test_disabled_does_nothing(plex):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE automations SET enabled = 0 WHERE type = 'plex'")
    await handler.on_movie_updated({"folder": FOLDER})
    assert not handler._pending
