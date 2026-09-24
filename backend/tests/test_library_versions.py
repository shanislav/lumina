"""Download of an owned movie: as another version, or replacing one (library.imports)."""

import json
import sqlite3

import pytest

from app.core import events, registry
from app.db import DB_PATH, init_db
from app.modules.library import imports

DETAILS = {
    "tmdb_id": 603, "title": "Matrix", "original_title": "The Matrix", "year": 1999, "runtime": 136,
    "original_language": "en", "imdb_id": "tt0133093", "overview": "", "poster_url": None,
    "titles": ["The Matrix"], "titles_by_lang": {"en": "The Matrix", "cs": "Matrix"},
}
OLD_MEDIA = {"duration_s": 8160, "width": 1280, "height": 544, "video_codec": "AVC", "audio": [{"lang": "cs"}]}
NEW_MEDIA = {"duration_s": 8170, "width": 3840, "height": 1600, "video_codec": "HEVC", "hdr": "HDR10", "audio": [{"lang": "cs"}, {"lang": "en"}]}


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    await init_db(registry.discover())
    registry.register_subscriptions(registry.discover())
    root = tmp_path / "Movies"
    folder = root / "1999" / "The Matrix (1999)"
    folder.mkdir(parents=True)
    old = folder / "The Matrix (1999) [720p x264] [CS] {tmdb-603}.mkv"
    old.write_bytes(b"old")
    (folder / "The Matrix (1999) [720p x264] [CS] {tmdb-603}.cs.srt").write_text("sub")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    new = downloads / "The.Matrix.1999.2160p.UHD.HDR.CZ.EN.mkv"
    new.write_bytes(b"new")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('movies_library_dir', ?)", (str(root),))
        conn.execute("INSERT INTO settings (key, value) VALUES ('tmdb_api_key', 'x')")
        conn.execute("INSERT INTO tmdb_movies (tmdb_id, data, fetched_at) VALUES (603, ?, 9e12)", (json.dumps(DETAILS),))
        conn.execute("INSERT INTO library_movies (tmdb_id, title, filename, file_path, status, media, duration_s) "
                     "VALUES (603, 'Matrix', ?, ?, 'matched', ?, 8160)", (old.name, str(old), json.dumps(OLD_MEDIA)))

    async def fake_probe(path):
        return NEW_MEDIA
    monkeypatch.setattr(imports, "probe_async", fake_probe)
    return folder, old, new


def _payload(new, action):
    return {"download_id": "g", "tmdb_id": 603, "title": "Matrix", "year": 1999, "content_type": "movie",
            "path": str(new), "library_action": action}


def _rows():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT filename, status FROM library_movies ORDER BY id").fetchall()


NEW_NAME = "The Matrix (1999) [2160p x265 HDR10] [CS+EN] {tmdb-603}.mkv"


async def test_download_as_additional_version(setup):
    folder, old, new = setup
    payload = await events.emit("download.completed", _payload(new, {"mode": "version"}))
    assert payload["imported"] is True
    assert (folder / NEW_NAME).exists() and old.exists()
    assert _rows() == [(old.name, "matched"), (NEW_NAME, "manual")]


async def test_download_replaces_version(setup):
    folder, old, new = setup
    await events.emit("download.completed", _payload(new, {"mode": "replace", "file_id": 1}))
    assert (folder / NEW_NAME).exists()
    assert not old.exists()
    assert not (folder / (old.stem + ".cs.srt")).exists()   # its subtitles went with it
    assert _rows() == [(NEW_NAME, "manual")]


async def test_replace_is_refused_when_durations_differ(setup, monkeypatch):
    folder, old, new = setup

    async def other_movie(path):
        return dict(NEW_MEDIA, duration_s=5400)  # 90 min — not the same film
    monkeypatch.setattr(imports, "probe_async", other_movie)
    await events.emit("download.completed", _payload(new, {"mode": "replace", "file_id": 1}))
    assert old.exists() and (folder / NEW_NAME).exists()    # nothing deleted, kept as a version
    assert len(_rows()) == 2


async def test_plain_download_is_left_to_other_modules(setup):
    folder, old, new = setup
    payload = await events.emit("download.completed", _payload(new, None))
    assert not payload.get("imported") and new.exists()
