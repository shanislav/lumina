"""Finished downloads into the library (library.imports): versions, replacing, new movies, episodes."""

import json
import os
import shutil
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


async def test_owned_movie_without_choice_becomes_a_version(setup):
    folder, old, new = setup
    payload = await events.emit("download.completed", _payload(new, None))
    assert payload["imported"] is True
    assert old.exists() and (folder / NEW_NAME).exists()   # never deletes without a choice
    assert len(_rows()) == 2


async def test_new_movie_gets_its_own_folder_with_subtitles(setup):
    folder, old, new = setup
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM library_movies")
    (new.parent / (new.stem + ".cs.srt")).write_text("sub")
    (new.parent / "Other.Movie.srt").write_text("other")          # not ours — stays
    shutil.rmtree(folder)
    payload = await events.emit("download.completed", _payload(new, None))
    assert payload["imported"] is True
    assert payload["path"] == str(folder / NEW_NAME)
    assert (folder / NEW_NAME).exists() and not new.exists()
    assert (folder / (NEW_NAME[:-4] + ".cs.srt")).exists()
    assert (new.parent / "Other.Movie.srt").exists()
    assert _rows() == [(NEW_NAME, "manual")]


async def test_without_library_folder_the_download_stays(setup):
    folder, old, new = setup
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM library_movies")
        conn.execute("DELETE FROM settings WHERE key = 'movies_library_dir'")
    payload = await events.emit("download.completed", _payload(new, None))
    assert not payload.get("imported") and new.exists()


async def test_episode_goes_to_show_and_season(setup, tmp_path):
    shows = tmp_path / "Serials"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tv_library_dir', ?)", (str(shows),))
    ep = tmp_path / "Downloads" / "Dark.S02E03.1080p.mkv"
    ep.write_bytes(b"e")
    payload = await events.emit("download.completed", {
        "download_id": "t", "tmdb_id": 70523, "title": "Dark", "year": "2017", "content_type": "tv", "path": str(ep)})
    target = shows / "Dark (2017)" / "Season 02" / "Dark.S02E03.1080p.mkv"
    assert payload["imported"] is True and payload["path"] == str(target)
    assert target.exists() and not ep.exists()


async def test_episode_without_tv_library_stays(setup, tmp_path):
    ep = tmp_path / "Downloads" / "Dark.S02E03.1080p.mkv"
    ep.write_bytes(b"e")
    payload = await events.emit("download.completed", {
        "download_id": "t", "tmdb_id": 70523, "title": "Dark", "year": "2017", "content_type": "tv", "path": str(ep)})
    assert not payload.get("imported") and ep.exists()


async def test_length_mismatch_goes_to_review_and_deletes_nothing(setup, monkeypatch):
    folder, old, new = setup
    async def sample(path):
        return dict(NEW_MEDIA, duration_s=60)   # a 1-minute sample, not the film
    monkeypatch.setattr(imports, "probe_async", sample)
    await events.emit("download.completed", _payload(new, {"mode": "replace", "file_id": 1}))
    assert old.exists()
    rows = _rows()
    assert rows[0] == (old.name, "matched") and rows[1][1] == "review"


async def test_note_and_one_preferred_version(setup):
    import importlib
    lib = importlib.import_module("app.modules.library.router")  # the package exports the APIRouter as "router"
    folder, old, new = setup
    await events.emit("download.completed", _payload(new, {"mode": "version"}))
    await lib.update_version(1, lib.VersionUpdate(note="  pre deti — CZ dabing ", preferred=True))
    await lib.update_version(2, lib.VersionUpdate(preferred=True))
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, note, preferred FROM library_movies ORDER BY id").fetchall()
    assert rows == [(1, "pre deti — CZ dabing", 0), (2, "", 1)]


async def test_replace_takes_the_old_versions_foreign_named_subtitles(setup):
    folder, old, new = setup
    (folder / "Matrix (1999) [Webrip] [eng].ass").write_text("old release subs")
    (new.parent / (new.stem + ".cs.srt")).write_text("new release subs")
    await events.emit("download.completed", _payload(new, {"mode": "replace", "file_id": 1}))
    assert not (folder / "Matrix (1999) [Webrip] [eng].ass").exists()
    assert (folder / (NEW_NAME[:-4] + ".cs.srt")).exists()       # the new version's own subtitles stay


async def test_film_not_in_tmdb_is_imported_by_its_searched_title(setup, tmp_path):
    folder, old, new = setup
    parody = tmp_path / "Downloads" / "Par-parmenu-CZ-2004.avi"
    parody.write_bytes(b"p")
    payload = await events.emit("download.completed", {
        "download_id": "x", "tmdb_id": 0, "title": "Pár Pařmenů", "year": 0, "content_type": "movie",
        "path": str(parody)})
    target = folder.parent.parent / "2004" / "Pár Pařmenů (2004)" / "Pár Pařmenů (2004) [720p x264] [CS] .avi"
    assert payload["imported"] is True
    assert os.path.dirname(payload["path"]) == str(target.parent)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT tmdb_id, title, year, status FROM library_movies WHERE title = 'Pár Pařmenů'").fetchone()
    assert row == (None, "Pár Pařmenů", "2004", "manual")

