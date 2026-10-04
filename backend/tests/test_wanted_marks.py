"""Search results show what is already in the library or on the wanted list — and who put it there."""

import pytest

from app.core import registry
from app.core.auth import User
from app.db import get_db, init_db
from app.models.schemas import TMDBMovie
from app.modules.search.router import mark_known
from app.modules.wanted.router import WantedAdd, add_wanted, wanted_of


@pytest.fixture
async def db():
    await init_db(registry.discover())


async def test_a_wanted_film_is_marked_with_who_added_it(db):
    await add_wanted(WantedAdd(tmdb_id=1001, title="Odyssea", year="2026", poster_url="x", check_now=False),
                     User(2, "vanco", "user", frozenset({"wanted"})))
    conn = await get_db()
    await conn.execute("INSERT OR REPLACE INTO series_settings (tmdb_id, auto_new) VALUES (2002, 'download')")
    await conn.commit()
    await conn.close()
    films = [TMDBMovie(tmdb_id=1001, title="Odyssea", original_title="", year="2026", overview="", poster_url=None),
             TMDBMovie(tmdb_id=1002, title="Jiný", original_title="", year="2026", overview="", poster_url=None),
             TMDBMovie(tmdb_id=2002, title="Seriál", original_title="", year="2025", overview="", poster_url=None, media_type="tv")]
    await mark_known(films)
    assert films[0].wanted["added_by"] == "vanco" and films[0].wanted["status"] == "wanted"
    assert films[1].wanted is None and not films[1].in_library
    assert films[2].wanted["status"] == "auto"                        # a show the automation looks for
    rows = await mark_known([{"tmdb_id": 1001, "media_type": "movie"}])
    assert rows[0]["wanted"]["added_by"] == "vanco"
    assert (await wanted_of(tmdb_id=1001))["added_by"] == "vanco" and await wanted_of(tmdb_id=1002) is None


async def test_pick_tells_every_file_the_profile_allows(monkeypatch):
    import sys
    from app.core.profiles import Profile
    router = sys.modules["app.modules.search.router"]
    asked = []

    async def profile(pid, kind="movie"):
        asked.append(kind)
        return Profile(name="Full HD", kind=kind, min_resolution="1080p")
    monkeypatch.setattr(router, "get_profile", profile)
    files = [{"source_id": 1, "ident": "a", "film": "yes", "resolution": "720p", "quality_score": 40},
             {"source_id": 1, "ident": "b", "film": "yes", "resolution": "1080p", "quality_score": 50},
             {"source_id": 2, "ident": "c", "film": "yes", "resolution": "2160p", "quality_score": 80},
             {"source_id": 2, "ident": "d", "film": "no", "resolution": "1080p", "quality_score": 90}]
    got = await router.search_pick(router.PickRequest(files=files, kind="tv"))
    assert got["key"] == "2:c" and got["ok_keys"] == ["2:c", "1:b"] and asked == ["tv"]
