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
    assert s["effective"] == {"profile_id": None, "lang_mode": "local_or_temp", "torrent": True, "monitor": False}
    await store.save_settings(1399, {"torrent": False, "lang_mode": "local_only"}, {"title": "Hra o trůny", "year": 2011})
    await store.save_defaults({"monitor": True, "torrent": True})
    s = await store.get_settings(1399)
    assert s["own"]["torrent"] is False and s["effective"]["monitor"] is True        # own value / new default
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
    assert store.episode_state(aired, unknown, "local_or_temp", local, TODAY) == "owned"  # language not known
    assert store.episode_state(aired, None, "local_or_temp", local, TODAY) == "missing"
    assert store.episode_state(future, None, "local_or_temp", local, TODAY) == "upcoming"
    assert store.episode_state({"air_date": ""}, None, "local_or_temp", local, TODAY) == "upcoming"
    assert store.languages_of("CZ,EN") == ["cs", "en"] and store.languages_of("") == []


def test_season_view_counts_and_files_tmdb_does_not_list():
    season = {"season_number": 1, "episode_count": 3}
    eps = [{"episode_number": n, "air_date": "2026-01-0%d" % n} for n in (1, 2, 3)]
    owned = {(1, 1): {"languages": ["cs"]}, (1, 2): {"languages": ["en"]}, (1, 9): {"languages": ["cs"]}}
    view = store.season_view(season, eps, owned, "local_or_temp", ["cs", "sk"], TODAY)
    assert view["counts"] == {"owned": 2, "temp": 1, "missing": 1, "upcoming": 0}
    assert [e["episode"] for e in view["episodes"]] == [1, 2, 3, 9]
