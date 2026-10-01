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
    (["/mnt/share/Video/Movies"], "", None),                                         # never guessed
    (["/movies"], "", None),                                                         # no way to tell
    (["/movies"], "/data/Video/Movies=/movies", "/movies/2014/Interstellar (2014)"),  # manual rule
    (["/movies"], "/other=/movies", None),
])
def test_to_plex(locations, rule, expected):
    assert to_plex(FOLDER, ROOT, locations, rule) == expected


def test_suggest_rule():
    from app.modules.plex.paths import suggest_rule
    assert suggest_rule(ROOT, ["/data/Share/Video/Movies"]) == "/data=/data/Share"
    assert suggest_rule(ROOT, ["/mnt/Movies", "/mnt/share/Video/Movies"]) == "/data=/mnt/share"
    # a test copy with the same layout next to the real library: no suggestion
    assert suggest_rule(ROOT, ["/data/Share/Video/Movies", "/data/Share/Test/Video/Movies"]) is None
    assert suggest_rule(ROOT, ["/movies"]) is None


def test_section_for():
    sections = [{"key": "1", "locations": ["/mnt/Movies"]}, {"key": "2", "locations": ["/mnt/MoviesKids"]}]
    assert section_for("/mnt/MoviesKids/2014/X", sections)["key"] == "2"
    assert section_for("/mnt/Movies/2014/X", sections)["key"] == "1"


class FakePlex:
    scans: list = []

    def __init__(self, url, token):
        pass

    async def sections(self):
        return [{"key": "3", "title": "Filmy", "type": "movie", "locations": ["/share/Video/Movies"], "refreshing": False},
                {"key": "4", "title": "Seriály", "type": "show", "locations": ["/share/Video/Serials"], "refreshing": False}]

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
                     (json.dumps({"url": "http://plex:32400", "token": "t", "path_map": "/data=/share"}),))
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


async def test_a_long_rename_is_one_scan_after_it_calms_down(plex, monkeypatch):
    """Changes keep coming (a big batch) → nothing is scanned until they stop, then once."""
    import asyncio
    monkeypatch.setattr(handler, "QUIET_S", 1.5)
    for i in range(handler.MAX_FOLDERS + 5):
        await handler.on_movie_updated({"folder": f"{ROOT}/2000/Movie {i} (2000)"})
        await asyncio.sleep(0.1)
    assert FakePlex.scans == []                     # still busy
    await asyncio.wait_for(handler._task, 10)
    assert FakePlex.scans == [("3", None)]          # one section scan


# ─── hints and the migration ───

def meta(key, tmdb, files, viewed=0, added=1000, deleted=False, imdb=None):
    return {"ratingKey": key, "title": f"Film {key}", "year": 2000, "guid": "plex://movie/x",
            "Guid": [{"id": f"imdb://{imdb or f'tt{tmdb}'}"}] + ([{"id": f"tmdb://{tmdb}"}] if tmdb else []), "viewCount": viewed or None,
            "addedAt": added, **({"deletedAt": 5} if deleted else {}),
            "Media": [{"Part": [{"file": f} for f in files]}]}


class FakeServer(FakePlex):
    """A Plex with movies and settings, which renames along with the disk on a scan."""
    items_now: list = []
    after_scan: list | None = None
    prefs_now: dict = {}
    calls: list = []

    async def prefs(self):
        return dict(FakeServer.prefs_now)

    async def set_prefs(self, values):
        FakeServer.prefs_now.update(values)
        FakeServer.calls.append(("prefs", dict(values)))

    async def items(self, key):
        return FakeServer.items_now

    async def scan(self, key, path=None):
        FakePlex.scans.append((key, path))
        if FakeServer.after_scan is not None:
            FakeServer.items_now = FakeServer.after_scan

    async def empty_trash(self, key):
        FakeServer.calls.append(("empty", key))

    async def mark_watched(self, key):
        FakeServer.calls.append(("watched", key))

    async def set_added_at(self, section, key, at):
        FakeServer.calls.append(("added", key, at))

    async def item(self, key):
        fields = [{"locked": True, "name": "thumb"}, {"locked": True, "name": "title"}] if key == "2" else []
        return {"ratingKey": key, "title": "Můj název" if key == "2" else "Film", "Field": fields}

    async def poster(self, key):
        return b"poster-" + key.encode()

    async def upload_poster(self, key, image):
        FakeServer.calls.append(("poster", key, image))

    async def edit_fields(self, section, key, values, locked):
        FakeServer.calls.append(("fields", key, values, sorted(locked)))


@pytest.fixture
async def server(plex, monkeypatch):
    from app.modules.plex import library, migration
    FakeServer.items_now = [meta("1", 11, ["/share/Video/Movies/2000/A/a.mkv"]),
                            meta("2", 22, ["/share/Video/Movies/2000/B/b.mkv"], viewed=2, added=777)]
    FakeServer.after_scan = None
    FakeServer.prefs_now = {"FSEventLibraryUpdatesEnabled": True, "FSEventLibraryPartialScanEnabled": True,
                            "ScheduledLibraryUpdatesEnabled": False, "autoEmptyTrash": True, "FriendlyName": "x"}
    FakeServer.calls = []
    monkeypatch.setattr(library, "PlexClient", FakeServer)
    monkeypatch.setattr(migration, "SCAN_START_S", 0)
    monkeypatch.setattr(migration.asyncio, "sleep", _no_sleep)
    yield migration


async def _no_sleep(_s):
    return None


async def test_plex_hint_for_a_file(server):
    from app.modules.plex import hints
    hints.forget()
    payload = {"path": "/data/Video/Movies/2000/B/b.mkv", "hints": []}
    await hints.on_collect_hints(payload)
    assert payload["hints"] == [(22, "plex")]
    other = {"path": "/data/Video/Movies/2000/C/c.mkv", "hints": []}
    await hints.on_collect_hints(other)
    assert other["hints"] == []


async def test_migration_keeps_movies_and_restores_settings(server):
    migration = server
    view = await migration.start()
    assert view["active"] and view["movies"] == 2
    assert FakeServer.prefs_now["autoEmptyTrash"] is False and FakeServer.prefs_now["FSEventLibraryUpdatesEnabled"] is False
    assert FakeServer.prefs_now["FriendlyName"] == "x"                       # other settings untouched
    # the rename: A got a new folder and Plex kept the item
    FakeServer.after_scan = [meta("1", 11, ["/share/Video/Movies/2000/A2/a.mkv"]), FakeServer.items_now[1]]
    report = await migration.check()
    assert FakePlex.scans == [("3", None)]                                  # one scan of the whole section
    assert report["moved"] == 1 and report["unchanged"] == 1 and not report["missing"] and not report["readded"]
    # the scan of a single folder is skipped while the migration runs
    await handler.on_movie_updated({"folder": FOLDER})
    assert not handler._pending
    await migration.finish(empty_trash=True, repair=False)
    assert ("empty", "3") in FakeServer.calls
    assert FakeServer.prefs_now["autoEmptyTrash"] is True and FakeServer.prefs_now["ScheduledLibraryUpdatesEnabled"] is False
    assert await migration.state() is None


async def test_a_movie_plex_added_anew_is_reported_and_repaired(server):
    migration = server
    await migration.start()
    # B lost: the old item in the trash, a new item for the same film without watched state
    FakeServer.after_scan = [FakeServer.items_now[0],
                             meta("2", 22, ["/share/Video/Movies/2000/B/b.mkv"], viewed=2, added=777, deleted=True),
                             meta("9", 22, ["/share/Video/Movies/2000/B2/b.mkv"], added=999)]
    report = await migration.check()
    assert report["readded"] == [{"title": "Film 2", "year": 2000, "old_key": "2", "new_key": "9",
                                  "watched": True, "added_at": 777}]
    assert report["missing"] == []
    again = await migration.check()                                         # still reported by the next batch
    assert len(again["readded"]) == 1
    await migration.finish(empty_trash=True, repair=True)
    assert ("watched", "9") in FakeServer.calls and ("added", "9", 777) in FakeServer.calls


async def test_repair_leaves_the_watched_state_plex_brought_back(server):
    migration = server
    await migration.start()
    FakeServer.after_scan = [FakeServer.items_now[0], meta("9", 22, ["/share/Video/Movies/2000/B2/b.mkv"], viewed=1)]
    await migration.check()
    await migration.finish(empty_trash=True, repair=True)
    assert not any(c[0] == "watched" for c in FakeServer.calls) and ("added", "9", 777) in FakeServer.calls


async def test_cancel_deletes_nothing(server):
    migration = server
    await migration.start()
    FakeServer.after_scan = [FakeServer.items_now[0]]                        # B vanished for good
    report = await migration.check()
    assert [m["title"] for m in report["missing"]] == ["Film 2"]
    await migration.finish(empty_trash=False, repair=False)
    assert not any(c[0] == "empty" for c in FakeServer.calls)
    assert FakeServer.prefs_now["FSEventLibraryUpdatesEnabled"] is True


async def test_a_new_item_with_the_old_state_needs_no_repair(server):
    migration = server
    await migration.start()
    FakeServer.after_scan = [FakeServer.items_now[0],
                             meta("9", 22, ["/share/Video/Movies/2000/B2/b.mkv"], viewed=2, added=777)]
    report = await migration.check()
    assert report["readded"] == [] and report["missing"] == []
    assert [m["rating_key"] for m in report["renewed"]] == ["9"]
    again = await migration.check()                                         # the new item is the snapshot's now
    assert again["renewed"] == [] and again["unchanged"] == 2


async def test_edits_by_hand_come_back_on_a_new_item(server):
    """A poster picked by hand and a locked title: Plex loses them with a new id — Lumina puts them back."""
    migration = server
    await migration.start()
    FakeServer.after_scan = [FakeServer.items_now[0],
                             meta("9", 22, ["/share/Video/Movies/2000/B2/b.mkv"], viewed=2, added=777)]
    report = await migration.check()
    assert report["edits_restored"] == ["Film 2"]
    assert ("poster", "9", b"poster-2") in FakeServer.calls
    assert ("fields", "9", {"title": "Můj název"}, ["thumb", "title"]) in FakeServer.calls
    await migration.finish(empty_trash=True, repair=False)
    import os
    assert not os.path.exists(migration.POSTERS)


async def test_an_old_item_known_by_imdb_only_is_paired(server):
    migration = server
    FakeServer.items_now = [FakeServer.items_now[0], meta("2", None, ["/share/Video/Movies/2000/B/b.mkv"], imdb="tt77", added=777)]
    await migration.start()
    FakeServer.after_scan = [FakeServer.items_now[0], meta("9", 22, ["/share/Video/Movies/2000/B2/b.mkv"], imdb="tt77", added=777)]
    report = await migration.check()
    assert [m["rating_key"] for m in report["renewed"]] == ["9"] and not report["missing"] and not report["new"]


async def test_a_film_plex_had_wrong_is_paired_by_its_file(server):
    """Plex knew the file as another film; after the folder move it matches the right one."""
    migration = server
    await migration.start()
    FakeServer.after_scan = [FakeServer.items_now[0], meta("9", 99, ["/share/Video/Movies/2016/Right (2016)/b.mkv"], viewed=2, added=777)]
    report = await migration.check()
    assert [m["rating_key"] for m in report["renewed"]] == ["9"] and not report["missing"] and not report["new"]


async def test_a_removed_movie_folder_scans_the_year_folder(plex, tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(handler, "QUIET_S", 0.5)
    """The last version deleted: its folder is gone — Plex rescans the year folder above it."""
    import os
    root = tmp_path / "Movies"
    (root / "2014").mkdir(parents=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'movies_library_dir'", (str(root),))
        conn.execute("UPDATE automations SET config = ? WHERE type = 'plex'",
                     (json.dumps({"url": "http://plex:32400", "token": "t", "path_map": f"{root}=/share/Video/Movies"}),))
    await handler.on_files_removed({"folders": [str(root / "2014" / "Interstellar (2014)")]})
    assert handler._pending == {os.path.normpath(str(root / "2014"))}
    await asyncio.wait_for(handler._task, 30)
    assert FakePlex.scans == [("3", "/share/Video/Movies/2014")]


async def test_which_friend_has_a_film(plex):
    from app.modules.plex import friends
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO plex_friend_servers (id, name, owner, owner_title, movies, last_ok, error) "
                     "VALUES ('abc', 'chuwi', 'milanle7', 'Milan', 1, '2026-09-18 17:05:00', 'nedostupný')")
        conn.execute("INSERT INTO plex_friend_movies (server_id, rating_key, tmdb_id, imdb_id, title, year, resolution) "
                     "VALUES ('abc', '77', 603, 'tt0133093', 'Matrix', 1999, '1080')")
    has = await friends.who_has(603, None)
    assert has == [{"owner": "Milan", "server": "chuwi", "resolution": "1080", "online": False,
                    "seen_at": "2026-09-18 17:05:00",
                    "url": "https://app.plex.tv/desktop/#!/server/abc/details?key=%2Flibrary%2Fmetadata%2F77"}]
    assert await friends.who_has(None, "tt0133093") == has
    assert await friends.who_has(604, None) == []
