"""Episodes found and downloaded by another uploader's order (South Park: TMDB's S01E02 is "Sopka" / Volcano,
S01E03 "Posilovač 4000" — many Czech files number them the other way round): the episode's own name in a release is checked against TMDB's names — in the search (a file of
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
       (1, 2): {"cs": "Sopka", "en": "Volcano", "runtime": 22},
       (1, 3): {"cs": "Posilovač 4000", "en": "Weight Gain 4000", "runtime": 22},
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
    assert episode_names.release_episode("Městečko South Park - S01E02 - Posilovač 4000.avi", CAT, 1) ==         ((1, 3), True, "Posilovač 4000")
    assert episode_names.release_episode("South Park S01E02 - CZ Dabing.mp4", CAT, 1) is None


def _judge(name: str, want=(1, 2), mixed=False):
    hit = episode_names.release_episode(name, CAT, want[0])
    by_name = {name: [list(hit[0]), hit[1], hit[2]]} if hit else {}
    ctx = MovieContext(titles=SHOW, runtime=22, episode={"season": want[0], "episode": want[1], "by_name": by_name,
                                                         "mixed": mixed})
    return evaluate(name, 300 * 2**20, ctx, prefs_from_settings({}))


def test_a_release_of_another_episodes_name_is_not_the_episode():
    other = _judge("Městečko South Park - S01E02 - Posilovač 4000.avi")
    assert other["film"] == "no" and "Posilovač" in other["film_reasons"][0] and "S01E03" in other["film_reasons"][0]
    right = _judge("Městečko South Park - S01E03 - Sopka.avi")
    assert right["film"] == "yes" and right["name_ok"] and "číslo v souboru je jiné" in right["film_reasons"][0]
    named = _judge("South Park S01E02. Sopka.mkv")
    plain = _judge("South Park S01E02.mkv")
    assert named["film"] == plain["film"] == "yes" and named["name_ok"] and "name_ok" not in plain
    prefs = prefs_from_settings({})
    rows = sorted([{**plain, "size": 1}, {**named, "size": 2}], key=lambda r: recommended_key(r, prefs))
    assert rows[0].get("name_ok")        # the named one first, all else equal (a dub still goes before a name)
    # another show's file stays another show's
    assert _judge("Seven.Worlds.One.Planet.S01E02.Volcano.mkv")["film"] == "no"
    # other files of this number are named as other episodes: one without a name may be either
    mixed = _judge("South Park S01E02 CZ Dabing.mkv", mixed=True)
    assert mixed["film"] == "unsure" and "číslují různě" in mixed["film_reasons"][-1]


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
    owned = season / "South Park - S01E02 - Sopka.avi"
    owned.write_bytes(b"sopka")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tv_library_dir', ?)", (str(shows),))
        conn.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, has_file) "
                     "VALUES (2190, 1, 2, ?, 1)", (str(owned),))
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
    """Picked for E03 with "replace": the file says it is Sopka (E02) — it becomes E02 next to the owned one
    (a version), the owned Sopka is not deleted, E03 stays missing; the scan keeps it on E02."""
    season, owned, dl = await _setup(tmp_path, monkeypatch)
    f = dl / "Městečko South Park - S01E03 - Sopka.avi"
    f.write_bytes(b"other sopka")
    await events.emit("download.completed", _download(f, {"mode": "episode", "season": 1, "episode": 3, "replace": True}))
    assert owned.exists()
    assert (season / "Městečko South Park - S01E03 - Sopka.avi").exists()
    assert [r[:2] for r in _episodes()] == [(1, 2)]
    with sqlite3.connect(DB_PATH) as conn:
        assert conn.execute("SELECT season, episode FROM tv_episode_overrides").fetchall() == [(1, 2)]


async def test_a_file_without_a_name_is_the_episode_the_user_picked(tmp_path, monkeypatch):
    season, owned, dl = await _setup(tmp_path, monkeypatch)
    f = dl / "South Park S01E02 CZ Dabing.mkv"     # another order's number, no name: the user's pick decides
    f.write_bytes(b"weight gain")
    await events.emit("download.completed", _download(f, {"mode": "episode", "season": 1, "episode": 3}))
    assert owned.exists()
    assert [r[:2] for r in _episodes()] == [(1, 2), (1, 3)]
    with sqlite3.connect(DB_PATH) as conn:
        note = conn.execute("SELECT season, episode, note FROM tv_episode_overrides").fetchone()
    assert note[:2] == (1, 3) and "pro S01E03" in note[2]


def test_release_groups_and_longer_show_names():
    """"Dark.S03E08.1080p.WEB.H264-GHOSTS" is no episode named "Ghosts"; a one-word show ("Dark") inside a
    longer name is another show — unsure, not yes."""
    from app.core.episode_match import judge_episode
    assert episode_names.release_titles("Dark.S03E08.1080p.WEB.H264-GHOSTS CZ Titulky.mkv") == []
    for other in ("Dark Matter S02E03 (2026) 1080p_cz.tit.mkv", "Into.The.Dark.02x03.DVB-C.CZ.avi",
                  "Dark Winds S02E03 CzTit.mp4", "S02E03 His Dark Materials CZ titulky.mkv"):
        assert judge_episode(other, ["Dark"], 2, 3).status == "unsure", other
    assert judge_episode("Dark (2017) S02E03 CZ dabing 1080p.mkv", ["Dark"], 2, 3).status == "yes"
    assert judge_episode("Městečko South Park 720p CZ S01E02.mkv", ["Městečko South Park", "South Park"], 1, 2).status == "yes"

    # the wanted episode's name in another show's file ("Dark Winds S03E03 Chiidii Ghosts" for Dark's "Ghosts")
    hit = [[2, 3], True, "Chiidii Ghosts"]
    ctx = MovieContext(titles=["Dark"], runtime=50, episode={"season": 2, "episode": 3,
                                                           "by_name": {"Dark Winds S03E03 Chiidii Ghosts.mkv": hit}})
    assert evaluate("Dark Winds S03E03 Chiidii Ghosts.mkv", 1, ctx, prefs_from_settings({}))["film"] == "unsure"


def test_packs_of_more_seasons_are_read():
    from app.core.episode_match import parse_episode
    assert parse_episode("South Park 1-26. série + speciály + film (1997-2024)(CZ/EN)[2160p]").seasons == list(range(1, 27))
    assert parse_episode("Dr. House 1.-8. serie CZ").seasons == list(range(1, 9))
    assert parse_episode("Breaking Bad Season 1-5 1080p").seasons == [1, 2, 3, 4, 5]
    assert parse_episode("Pratele 3. serie CZ").season == 3
    assert parse_episode("Městečko South Park S01E02-13 1997 CZ dab 1080p - Sopka.mkv").episodes == [2]


async def test_a_whole_show_pack_brings_what_is_missing(tmp_path, monkeypatch):
    """"South Park komplet": episodes by their folders ("Season 02/05 - …") and names, the owned one stays
    (no replacing asked), the film in the pack stays in the downloads."""
    season, owned, dl = await _setup(tmp_path, monkeypatch)
    pack = dl / "South Park komplet"
    files = [pack / "Season 01" / "South Park S01E02 - Sopka.avi",          # owned already
             pack / "Season 01" / "South Park S01E03 - Posilovač 4000.avi",
             pack / "Season 02" / "05 - Ikeova obřízka.avi",
             pack / "South Park - Peklo na zemi (1999).mkv"]
    for i, f in enumerate(files):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * (10 + i))
    await events.emit("download.completed", {
        "download_id": "p", "tmdb_id": 2190, "title": "South Park", "year": "1997", "content_type": "tv",
        "path": str(files[0]), "extra_paths": [str(f) for f in files[1:]],
        "library_action": {"mode": "pack", "replace_owned": False}})
    assert owned.exists() and files[0].exists()                    # not replaced, left in the downloads
    assert files[3].exists()                                       # the film is no episode
    assert [r[:2] for r in _episodes()] == [(1, 2), (1, 3), (2, 5)]
    assert (season.parent / "Season 02" / "05 - Ikeova obřízka.avi").exists()


async def test_an_episodes_languages_are_its_audio_tracks(tmp_path, monkeypatch):
    """Rick a Morty "[CS+EN].mkv" was "EN" (the name's CZ was looked for, the renamer writes CS): the scan
    takes the audio tracks MediaInfo read."""
    import json as _json
    from app.db import get_db
    from app.modules.library import importer
    from app.modules.library.files import _detect_language
    assert _detect_language("Rick a Morty - S04E01 [1080p x265] [CS].mkv") == "CZ"
    await _setup(tmp_path, monkeypatch)
    path = str(tmp_path / "Serials" / "South Park" / "Season 01" / "South Park - S01E02 - Sopka.avi")
    db = await get_db()
    try:
        await db.execute("UPDATE library_episodes SET language = 'EN' WHERE file_path = ?", (path,))
        await db.execute("INSERT INTO tv_media (file_path, size, mtime, media) VALUES (?, 5, 1, ?)",
                         (path, _json.dumps({"audio": [{"lang": "cs"}, {"lang": "en"}]})))
        await db.commit()
        await importer._tv_media(db, [{"file_path": path, "file_size": 5, "added_at": 1, "filename": "x"}])
        row = await (await db.execute("SELECT language FROM library_episodes WHERE file_path = ?", (path,))).fetchone()
    finally:
        await db.close()
    assert row[0] == "CS,EN"


def test_a_season_split_elsewhere_and_anime_numbers():
    """Solo Leveling: TMDB S01E01–25, the second part after a break of nine months is "S02E01–13" for the
    rest of the world; Naruto: "Naruto 120"."""
    cat = {(1, e): {"air": "2024-01-07" if e <= 12 else "2025-01-05"} for e in range(1, 26)}
    assert episode_names.other_numbers(cat, 1, 13) == {"alt": [[2, 1]], "absolute": None}
    assert episode_names.other_numbers(cat, 1, 12) == {"alt": [], "absolute": None}
    cat[(2, 1)] = {"air": "2026-01-01"}                                   # TMDB has S02 itself: no guessing
    assert episode_names.other_numbers(cat, 1, 13)["alt"] == []
    naruto = {(1, e): {"air": "2002-10-03"} for e in range(1, 53)} | {(2, e): {"air": "2003-10-01"} for e in range(53, 105)}
    assert episode_names.other_numbers(naruto, 2, 60, anime=True)["absolute"] == 60

    ctx = MovieContext(titles=["Solo Leveling"], runtime=24,
                       episode={"season": 1, "episode": 13, "other": {"alt": [[2, 1]], "absolute": None}})
    ev = evaluate("Solo.Leveling.S02E01.1080p.WEB.x264.mkv", 1, ctx, prefs_from_settings({}))
    assert ev["film"] == "yes" and ev["film_reasons"][0] == "jiné číslování: S02E01"
    assert evaluate("Solo.Leveling.S02E02.1080p.mkv", 1, ctx, prefs_from_settings({}))["film"] == "no"
    ctx = MovieContext(titles=["Naruto"], runtime=23, episode={"season": 3, "episode": 120,
                                                               "other": {"alt": [], "absolute": 120}})
    assert evaluate("Naruto 120 CZ dabing.avi", 1, ctx, prefs_from_settings({}))["film"] in ("yes", "unsure")


async def test_whole_show_packs_by_seasons_or_by_years(monkeypatch):
    """"Hra o trůny / Game of Thrones (2011–2019)[WebRip][1080p](HEVC)(CZ)" holds the whole show (its years,
    no seasons); "House of the Dragon" is no pack of "House"; a single episode is no pack."""
    from types import SimpleNamespace
    from app.core.offers import season as season_mod
    from app.sources.base import SearchResult, SourceType

    class FakeTMDB:
        def __init__(self, key):
            pass

        async def get_tv_full(self, tmdb_id):
            return {"title": "Hra o trůny", "original_title": "Game of Thrones", "first_air_date": "2011-04-17",
                    "titles_by_lang": {"cs": "Hra o trůny", "en": "Game of Thrones"}, "alternative_titles": [],
                    "episode_runtime": 57, "seasons": [{"season_number": n} for n in range(1, 9)]}

        async def close(self):
            pass

    names = ["Hra o trůny / Game of Thrones (2011–2019)[WebRip][1080p](HEVC)(CZ)",
             "Game of Thrones S01-S08 1080p CZ", "House of the Dragon S01-S02 CZ", "Game of Thrones S03E05 CZ",
             "Hra o trůny 3. série CZ"]

    async def fake_search(sources, ddl, torrent):
        return [SearchResult(source_id=1, source_type=SourceType("prowlarr"), ident=str(i), name=n, size=10**10,
                             seeders=5) for i, n in enumerate(names)]
    monkeypatch.setattr(season_mod, "TMDBClient", FakeTMDB)
    monkeypatch.setattr(season_mod, "search_sources", fake_search)
    monkeypatch.setattr(season_mod.SourceRegistry, "get",
                        classmethod(lambda cls: SimpleNamespace(sources=[SimpleNamespace(source_type=SourceType("prowlarr"))])))
    out = await season_mod.find_show_packs({"tmdb_api_key": "x"}, 1399)
    got = {p["name"]: (p["covers"], p["complete"]) for p in out["packs"]}
    assert got == {names[0]: (8, True), names[1]: (8, False), names[4]: (1, False)}


def test_the_season_plan_places_files_by_their_episode():
    """A file placed on another episode (its episode name, another numbering) is that episode in the plan."""
    from app.core.offers.season import group_sets
    rows = [{"name": "Solo.Leveling.S02E01.1080p.WEB.mkv", "size": 1, "source": "webshare", "film": "yes",
             "episodes": [13], "quality_score": 50},
            {"name": "Solo.Leveling.S02E02.1080p.WEB.mkv", "size": 1, "source": "webshare", "film": "yes",
             "episodes": [14], "quality_score": 50}]
    sets = group_sets(rows, 1, [13, 14], 24)
    assert sets[0]["covered"] == [13, 14]


def test_a_whole_show_pack_skips_the_episodes_the_user_has():
    files = [{"index": 0, "name": "South Park S01-S28/Season 01/South Park S01E02 - Sopka.mkv"},
             {"index": 1, "name": "South Park S01-S28/Season 01/South Park S01E02 - Sopka.cs.srt"},
             {"index": 2, "name": "South Park S01-S28/Season 01/South Park S01E03 - Posilovač 4000.mkv"},
             {"index": 3, "name": "South Park S01-S28/Série 2/05 - Ikeova obřízka.avi"},
             {"index": 4, "name": "South Park S01-S28/Film/South Park - Peklo na zemi.mkv"},
             {"index": 5, "name": "South Park S01-S28/Season 02/South Park S02E06.mkv"}]
    assert imports.pack_skip(files, {(1, 2), (2, 5)}) == [0, 1, 3]


def test_a_special_without_a_number_is_found_by_its_name():
    """"Top Gear - Polární speciál (2007) CZ.avi": no SxxEyy — the name after the show's name is TMDB's S00E12."""
    cat = {(0, 12): {"cs": "Polární speciál", "en": "Polar Special", "runtime": 62},
           (0, 33): {"cs": "Speciál USA", "en": "USA Road Trip", "runtime": 67},
           (1, 1): {"cs": "1. epizoda", "en": "Episode 1", "runtime": 60}}
    shows = ["Top Gear"]
    assert episode_names.release_episode("Top Gear - Polární speciál (2007) CZ.avi", cat, 0, shows) == \
        ((0, 12), True, "Polární speciál")
    assert episode_names.release_episode("Top.Gear.Polar.Special.2007.720p.mkv", cat, 0, shows)[0] == (0, 12)
    assert episode_names.release_episode("Top Gear 1080p CZ.mkv", cat, 0, shows) is None
    assert episode_names.release_episode("Fifth Gear - Polar Special.mkv", cat, 0, shows) is None


def test_a_name_that_fits_many_episodes_decides_nothing():
    """"Top Gear speciál. Když se nedaří III": "speciál" fits every "… speciál" — not the Polar Special."""
    cat = {(0, 12): {"cs": "Polární speciál", "en": "Polar Special"}, (0, 34): {"cs": "Indický speciál", "en": "India Special"},
           (0, 33): {"cs": "Speciál USA", "en": "USA Road Trip"}}
    name = "Top Gear speciál. Když se nedaří III(CZ)[TvRip][1080pLQ].mkv"
    hit = episode_names.release_episode(name, cat, 0, ["Top Gear"])
    assert hit is None or hit[1] is False
    by_name = {name: [list(hit[0]), hit[1], hit[2]]} if hit else {}
    ctx = MovieContext(titles=["Top Gear"], runtime=60, episode={"season": 0, "episode": 12, "by_name": by_name})
    assert evaluate(name, 1, ctx, prefs_from_settings({}))["film"] == "no"
