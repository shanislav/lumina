"""TV shows: settings on top of the defaults, the state of each episode."""

from datetime import date

import pytest

from app.core import registry
from app.db import init_db
from app.modules.series import store

TODAY = date(2026, 10, 2)


@pytest.fixture
async def db():
    await init_db(registry.discover())


async def test_settings_fall_back_to_the_defaults(db):
    s = await store.get_settings(1399)
    assert s["effective"] == {"profile_id": None, "lang_mode": "local_or_temp", "torrent": True,
                              "auto_new": "off", "auto_from": "next", "auto_dub": "off"}
    await store.save_settings(1399, {"torrent": False, "lang_mode": "local_only"}, {"title": "Hra o trůny", "year": 2011})
    await store.save_defaults({"auto_new": "notify", "torrent": True, "auto_dub": "nonsense"})
    s = await store.get_settings(1399)
    assert s["own"]["torrent"] is False and s["effective"]["auto_new"] == "notify"  # own value / new default
    assert s["effective"]["auto_dub"] == "off"                                       # not a choice → default
    assert s["effective"]["lang_mode"] == "local_only"
    s = await store.save_settings(1399, {"lang_mode": None, "torrent": None})        # back to the defaults
    assert s["effective"]["lang_mode"] == "local_or_temp" and s["effective"]["torrent"] is True


def test_episode_states():
    aired, future = {"air_date": "2026-09-01"}, {"air_date": "2026-12-24"}
    cz, en, unknown = {"languages": ["cs", "en"]}, {"languages": ["en"]}, {"languages": []}
    local = ["cs", "sk"]
    assert store.episode_state(aired, cz, "local_or_temp", local, TODAY) == "owned"
    assert store.episode_state(aired, en, "local_or_temp", local, TODAY) == "temp"      # waits for the dub
    assert store.episode_state(aired, en, "original", local, TODAY) == "owned"
    assert store.episode_state(aired, unknown, "local_or_temp", local, TODAY) == "unknown"  # the tracks say no language: not shown as Czech
    assert store.episode_state(aired, None, "local_or_temp", local, TODAY) == "missing"
    assert store.episode_state(future, None, "local_or_temp", local, TODAY) == "upcoming"
    assert store.episode_state({"air_date": ""}, None, "local_or_temp", local, TODAY) == "upcoming"
    assert store.languages_of("CZ,EN") == ["cs", "en"] and store.languages_of("") == []


def test_season_view_counts_and_files_tmdb_does_not_list():
    season = {"season_number": 1, "episode_count": 3}
    eps = [{"episode_number": n, "air_date": "2026-01-0%d" % n} for n in (1, 2, 3)]
    owned = {(1, 1): {"languages": ["cs"]}, (1, 2): {"languages": ["en"]}, (1, 9): {"languages": ["cs"]}}
    view = store.season_view(season, eps, owned, "local_or_temp", ["cs", "sk"], TODAY)
    assert view["counts"] == {"owned": 2, "temp": 1, "unknown": 0, "missing": 1, "upcoming": 0}
    assert [e["episode"] for e in view["episodes"]] == [1, 2, 3, 9]


def test_scan_reads_episodes_the_old_parser_misses(tmp_path):
    from app.modules.library.importer import _episode_by_folder
    show = tmp_path / "CHALUPÁŘI"
    path = show / "chalupari-01-chudak-dedecek-hd-1975-cs-78pt.mkv"
    got = _episode_by_folder({"filename": path.name, "file_path": str(path)}, str(tmp_path))
    assert got == {"show_name": "CHALUPÁŘI", "season": 1, "episode": 1, "year": None}
    loose = tmp_path / "x.mkv"
    assert _episode_by_folder({"filename": "x.mkv", "file_path": str(loose)}, str(tmp_path)) is None


def test_show_is_its_folder_and_tmdb_must_really_match(tmp_path):
    from types import SimpleNamespace as S
    from app.modules.library.importer import _best_show, _show_folder
    tv = str(tmp_path)
    assert _show_folder(f"{tv}/StarGate Atlantis/2. HD/SGA - S02E02.mkv".replace("/", __import__("os").sep), tv) == ("StarGate Atlantis", None)
    assert _show_folder(str(tmp_path / "The.Book.of.Boba.Fett.S01.1080p.WEB-DL-DeDo" / "x.mkv"), tv) == ("The Book of Boba Fett", None)
    assert _show_folder(str(tmp_path / "Rodina Addamsovcov 1964" / "S01" / "x.avi"), tv) == ("Rodina Addamsovcov", "1964")
    assert _show_folder(str(tmp_path / "x.mkv"), tv) is None
    blue = S(tmdb_id=1, title="Blue", original_title="Blue", year="2018")
    bluey = S(tmdb_id=2, title="Bluey", original_title="Bluey", year="2018")
    sgauth = S(tmdb_id=3, title="Sgauth", original_title="Sgauth", year="2023")
    assert _best_show([blue, bluey], "Bluey", None).tmdb_id == 2
    assert _best_show([sgauth], "SGA", None) is None
    atlantis = S(tmdb_id=2290, title="Hvězdná brána: Atlantida", original_title="Stargate Atlantis", year="2004")
    assert _best_show([atlantis], "StarGate Atlantis", None).tmdb_id == 2290


def test_an_episode_that_never_got_a_dub_waits_for_none():
    """South Park S14E05–06: the user marks them — owned, not "waiting for the dub", not in a season plan."""
    season = {"season_number": 14, "episode_count": 2}
    eps = [{"episode_number": n, "air_date": "2010-04-14"} for n in (5, 6)]
    owned = {(14, 5): {"languages": ["en"]}, (14, 6): {"languages": ["en"]}}
    view = store.season_view(season, eps, owned, "local_or_temp", ["cs", "sk"], TODAY, {(14, 5)})
    assert [(e["state"], e["no_dub"]) for e in view["episodes"]] == [("owned", True), ("temp", False)]
