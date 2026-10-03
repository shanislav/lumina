"""Episodes found and downloaded by another uploader's order (South Park: CZ "S01E02 - Sopka" is TMDB's S01E03
"Volcano"): the episode's own name in a release is checked against TMDB's names — in the search (a file of
the wanted episode's name is right whatever its number, one of another episode's name is wrong) and in the
import (a file goes to the episode its name says, else to the one the user picked it for)."""

import sqlite3
import time

from app.core import events, registry
from app.core.offers.evaluate import MovieContext, evaluate, recommended_key
from app.core.quality import prefs_from_settings
from app.db import DB_PATH, init_db
from app.modules.library import episode_names, imports

CAT = {(1, 1): {"cs": "Kartman a anální sonda", "en": "Cartman Gets an Anal Probe", "runtime": 22},
       (1, 2): {"cs": "Posilovač 4000", "en": "Weight Gain 4000", "runtime": 22},
       (1, 3): {"cs": "Sopka", "en": "Volcano", "runtime": 22},
       (1, 4): {"cs": "Velká gay výprava", "en": "Big Gay Al's Big Gay Boat Ride", "runtime": 22}}
SHOW = ["Městečko South Park", "South Park"]


def test_episode_names_in_release_names():
    t = episode_names.release_titles
    assert t("Městečko South Park S01E02-13 1997 CZ dab 1080p - Posilovač 4000.mkv") == ["Posilovač 4000"]
    assert t("South.Park.S01E02.Posilovac.4000.DVDRip.XviD.CZ.ENG.mkv") == ["Posilovac 4000"]
    assert t("Městečko South Park - S01E02 - Sopka.avi") == ["Sopka"]
    assert t("South Park 01x02 - Posilovač 4000.mkv") == ["Posilovač 4000"]
    assert t("South Park S01E02 CZ Dabing FullHD+ by lfiq.mkv") == []
    assert t("Mestecko south park S01e02 720p  CZ.mkv") == []
    assert t("Solo.Leveling.S02E01.1080p.CR.WEB-DL.AVC.8bit.AAC.2.0.MultiAudios.Arabic.Dub.MultiSubs-Nomal1406.mkv") == []
    assert episode_names.release_episode("Městečko South Park - S01E02 - Sopka.avi", CAT, 1) == ((1, 3), True, "Sopka")
    assert episode_names.release_episode("South Park S01E02 - CZ Dabing.mp4", CAT, 1) is None


def _judge(name: str, want=(1, 2)):
    hit = episode_names.release_episode(name, CAT, want[0])
    by_name = {name: [list(hit[0]), hit[1], hit[2]]} if hit else {}
    ctx = MovieContext(titles=SHOW, runtime=22, episode={"season": want[0], "episode": want[1], "by_name": by_name})
    return evaluate(name, 300 * 2**20, ctx, prefs_from_settings({}))


def test_a_release_of_another_episodes_name_is_not_the_episode():
    sopka = _judge("Městečko South Park - S01E02 - Sopka.avi")
    assert sopka["film"] == "no" and "Sopka" in sopka["film_reasons"][0] and "S01E03" in sopka["film_reasons"][0]
    right = _judge("Městečko South Park - S01E03 - Posilovač 4000.avi")
    assert right["film"] == "yes" and right["name_ok"] and "číslo v souboru je jiné" in right["film_reasons"][0]
    named = _judge("South Park S01E02. Posilovač 4000.mkv")
    plain = _judge("South Park S01E02.mkv")
    assert named["film"] == plain["film"] == "yes" and named["name_ok"] and "name_ok" not in plain
    prefs = prefs_from_settings({})
    rows = sorted([{**plain, "size": 1}, {**named, "size": 2}], key=lambda r: recommended_key(r, prefs))
    assert rows[0].get("name_ok")        # the named one first, all else equal (a dub still goes before a name)
    # another show's file stays another show's
    assert _judge("Seven.Worlds.One.Planet.S01E03.Volcano.mkv")["film"] == "no"


async def _setup(tmp_path, monkeypatch):
    await init_db(registry.discover())
    registry.register_subscriptions(registry.discover())

    async def no_show(db, tmdb_id, title, year):
        await db.execute("INSERT OR IGNORE INTO library_shows (tmdb_id, title) VALUES (?, ?)", (tmdb_id, title))

    async def no_probe(path):
        return {}
    monkeypatch.setattr(imports, "_ensure_show", no_show)
    monkeypatch.setattr(imports, "probe_async", no_probe)
    shows = tmp_path / "Serials"
    season = shows / "South Park" / "Season 01"
    season.mkdir(parents=True)
    owned = season / "South Park - S01E03 - Sopka.avi"
    owned.write_bytes(b"sopka")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tv_library_dir', ?)", (str(shows),))
        conn.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, has_file) "
                     "VALUES (2190, 1, 3, ?, 1)", (str(owned),))
        conn.executemany("INSERT INTO tmdb_episodes (show_tmdb_id, season, episode, title_cs, title_en, runtime, "
                         "air_date, fetched_at) VALUES (2190, ?, ?, ?, ?, 22, '', ?)",
                         [(s, e, v["cs"], v["en"], time.time()) for (s, e), v in CAT.items()])
    dl = tmp_path / "Downloads"
    dl.mkdir()
    return season, owned, dl


def _download(path, action):
    return {"download_id": "t", "tmdb_id": 2190, "title": "South Park", "year": "1997", "content_type": "tv",
            "path": str(path), "library_action": action}


def _episodes():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT season, episode, filename FROM library_episodes ORDER BY season, episode").fetchall()


async def test_a_downloaded_file_goes_to_the_episode_its_name_says(tmp_path, monkeypatch):
    """Picked for E02 with "replace": the file says it is Sopka (E03) — it becomes E03 next to the owned one
    (a version), the owned Sopka is not deleted, E02 stays missing; the scan keeps it on E03."""
    season, owned, dl = await _setup(tmp_path, monkeypatch)
    f = dl / "Městečko South Park - S01E02 - Sopka.avi"
    f.write_bytes(b"other sopka")
    await events.emit("download.completed", _download(f, {"mode": "episode", "season": 1, "episode": 2, "replace": True}))
    assert owned.exists()
    assert (season / "Městečko South Park - S01E02 - Sopka.avi").exists()
    assert [r[:2] for r in _episodes()] == [(1, 3)]
    with sqlite3.connect(DB_PATH) as conn:
        assert conn.execute("SELECT season, episode FROM tv_episode_overrides").fetchall() == [(1, 3)]


async def test_a_file_without_a_name_is_the_episode_the_user_picked(tmp_path, monkeypatch):
    season, owned, dl = await _setup(tmp_path, monkeypatch)
    f = dl / "South Park S01E03 CZ Dabing.mkv"     # another order's number, no name: the user's pick decides
    f.write_bytes(b"weight gain")
    await events.emit("download.completed", _download(f, {"mode": "episode", "season": 1, "episode": 2}))
    assert owned.exists()
    assert [r[:2] for r in _episodes()] == [(1, 2), (1, 3)]
    with sqlite3.connect(DB_PATH) as conn:
        note = conn.execute("SELECT season, episode, note FROM tv_episode_overrides").fetchone()
    assert note[:2] == (1, 2) and "pro S01E02" in note[2]
