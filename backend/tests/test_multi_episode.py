"""One file of more episodes: "S04E01-E02" owns both, a two-part premiere stored whole ("S01E01" of 86 min)
is suggested and the user's word on it holds; replacing or deleting such a file never loses the other episode."""

import json
import os
import sqlite3

import pytest

from app.core import events, registry
from app.db import DB_PATH, get_db, init_db
from app.modules.library import episodes, imports, organize_tv, tv_inventory

from tests.test_tv_inventory import FakeTMDB, scan, tv  # noqa: F401 — the scan's fixture


def owned(tmdb_id):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT season, episode, file_path FROM library_episodes WHERE show_tmdb_id = ? AND has_file = 1 "
                            "ORDER BY season, episode", (tmdb_id,)).fetchall()


def test_episodes_of_a_file():
    assert tv_inventory.file_episodes(1, [1, 2], None) == [1, 2]
    assert tv_inventory.file_episodes(5, [1, 2], None) == [5, 6]         # the scan numbered the file otherwise
    assert tv_inventory.file_episodes(1, [1], 2) == [1, 2]               # the user's word
    assert tv_inventory.file_episodes(1, [1, 2], 1) == [1]
    assert tv_inventory.file_episodes(3, [], None) == [3]


CAT = {(1, 1): {"en": "Rising (1)", "cs": "Vynoření (1)", "runtime": 45},
       (1, 2): {"en": "Rising (2)", "cs": "Vynoření (2)", "runtime": 41},
       (1, 3): {"en": "Hide and Seek", "cs": "Na schovávanou", "runtime": 43},
       (1, 4): {"en": "Episode 4", "cs": "Epizoda 4", "runtime": 12},
       (1, 5): {"en": "Episode 5", "cs": "Epizoda 5", "runtime": 12}}


def test_a_two_part_premiere_stored_whole_is_suggested():
    assert tv_inventory.two_parts(CAT, 1, 1, 84 * 60, set()) == 2
    assert tv_inventory.two_parts(CAT, 1, 1, 84 * 60, {(1, 2)}) is None       # the 2nd part is owned
    assert tv_inventory.two_parts(CAT, 1, 1, 45 * 60, set()) is None          # one episode long
    # twice as long but no parts in TMDB's names (TMDB's runtime is off: "Trabantem" 12 min, the files 25)
    assert tv_inventory.two_parts(CAT, 1, 4, 25 * 60, set()) is None


async def test_a_file_of_more_episodes_owns_them_all(tv):  # noqa: F811
    root = tv
    path = os.path.join(root, "StarGate Atlantis", "1", "Stargate.Atlantis.S01E05-E06.720p.mkv")
    with open(path, "wb") as f:
        f.write(b"x")
    await scan(root)
    got = [(s, e) for s, e, p in owned(2290) if p == path]
    assert got == [(1, 5), (1, 6)]
    db = await get_db()
    try:
        ep = await episodes.episode(db, (await (await db.execute(
            "SELECT id FROM library_episodes WHERE show_tmdb_id = 2290 AND season = 1 AND episode = 6")).fetchone())[0])
        assert [v["file_path"] for v in await episodes.versions(db, ep)] == [path]
        assert (await episodes.in_file(db, ep))["episodes"] == [5, 6]
    finally:
        await db.close()


async def test_the_users_word_holds_and_can_be_taken_back(tv):  # noqa: F811
    root = tv
    path = os.path.join(root, "StarGate Atlantis", "1", "Stargate.Atlantis.S01E03.720p.Cz.mkv")
    await scan(root)
    db = await get_db()
    try:
        await tv_inventory.set_file_span(db, path, 2)
        assert await episodes.apply_span(db, path, 2) == [3, 4]
    finally:
        await db.close()
    assert [(s, e) for s, e, p in owned(2290) if p == path] == [(1, 3), (1, 4)]
    await scan(root)                                                     # the next scan says the same
    assert [(s, e) for s, e, p in owned(2290) if p == path] == [(1, 3), (1, 4)]
    with sqlite3.connect(DB_PATH) as conn:
        facts = json.loads(conn.execute("SELECT facts FROM tv_files WHERE file_path = ?", (path,)).fetchone()[0])
    assert facts["span"] == 2
    db = await get_db()
    try:
        await tv_inventory.set_file_span(db, path, None)
        assert await episodes.apply_span(db, path, None) == [3]
    finally:
        await db.close()
    assert [(s, e) for s, e, p in owned(2290) if p == path] == [(1, 3)]


def test_the_renamer_names_a_file_by_all_its_episodes():
    row = {"file_path": "/s/Show/S01/Show.S01E01.mkv", "season": 1, "episodes": [1, 2], "status": "ok",
           "facts": {"file": [1, [1]], "title": "Rising", "span": 2}}
    season, eps, title, _ = organize_tv.episode_target(row, "files", {(1, 1): "Vynoření (1)"})
    assert (season, eps) == (1, [1, 2])


async def test_deleting_a_file_of_two_episodes_clears_both(tmp_path):
    await init_db(registry.discover())
    root = str(tmp_path / "Serials")
    path = os.path.join(root, "Bones", "S04", "Bones.S04E01-E02.avi")
    os.makedirs(os.path.dirname(path))
    with open(path, "wb") as f:
        f.write(b"x")
    db = await get_db()
    try:
        for e in (1, 2):
            await db.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, filename, has_file) "
                             "VALUES (1911, 4, ?, ?, 'Bones.S04E01-E02.avi', 1)", (e, path))
        await db.execute("INSERT INTO tv_files (file_path, folder, show_tmdb_id, season, episodes, status) "
                         "VALUES (?, 'Bones', 1911, 4, '[1, 2]', 'ok')", (path,))
        await db.commit()
        ep = await episodes.episode(db, 1)
        await episodes.delete_file(db, ep, path, root)
    finally:
        await db.close()
    assert owned(1911) == []


@pytest.fixture
async def tv_import(tmp_path, monkeypatch):
    await init_db(registry.discover())
    registry.register_subscriptions(registry.discover())

    async def no_tmdb(db, tmdb_id, title, year):
        await db.execute("INSERT OR IGNORE INTO library_shows (tmdb_id, title) VALUES (?, ?)", (tmdb_id, title))
    monkeypatch.setattr(imports, "_ensure_show", no_tmdb)
    shows = tmp_path / "Serials"
    season = shows / "Bones" / "Season 04"
    season.mkdir(parents=True)
    old = season / "Bones - S04E01-E02 - Yanks in the U.K..avi"
    old.write_bytes(b"old")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tv_library_dir', ?)", (str(shows),))
        for e in (1, 2):
            conn.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, has_file) VALUES (1911, 4, ?, ?, 1)",
                         (e, str(old)))
    yield tmp_path, old


async def test_replacing_one_episode_keeps_a_file_of_two(tv_import):
    tmp_path, old = tv_import
    dl = tmp_path / "Downloads"
    dl.mkdir()
    new = dl / "Bones.S04E01.1080p.CZ.mkv"
    new.write_bytes(b"new")
    await events.emit("download.completed", {
        "download_id": "t", "tmdb_id": 1911, "title": "Bones", "year": "2005", "content_type": "tv", "path": str(new),
        "library_action": {"mode": "episode", "season": 4, "episode": 1, "replace": True}})
    assert old.exists()                                       # E02 is only in it
    got = {e: os.path.basename(p) for s, e, p in owned(1911)}
    assert got == {1: "Bones.S04E01.1080p.CZ.mkv", 2: old.name}


async def test_a_file_of_both_episodes_replaces_the_old_one(tv_import):
    tmp_path, old = tv_import
    dl = tmp_path / "Downloads"
    dl.mkdir()
    new = dl / "Bones.S04E01-E02.1080p.CZ.mkv"
    new.write_bytes(b"new")
    await events.emit("download.completed", {
        "download_id": "t", "tmdb_id": 1911, "title": "Bones", "year": "2005", "content_type": "tv", "path": str(new),
        "library_action": {"mode": "episode", "season": 4, "episode": 1, "replace": True}})
    assert not old.exists()
    assert {e for s, e, p in owned(1911) if os.path.basename(p) == new.name} == {1, 2}
