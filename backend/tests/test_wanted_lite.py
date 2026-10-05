"""A child's account (may search and ask in Chci, may not download): what it asks for waits for the admin —
never downloaded automatically, a show only asked for; the top bar marks what is new in Chci."""

import importlib
import sqlite3

import pytest
from fastapi import HTTPException

from app.core import registry
from app.core.auth import User
from app.db import DB_PATH, init_db
from app.models.schemas import TMDBMovie
from app.modules.search.router import mark_known
from app.modules.wanted import store

KID = User(5, "ema", "user", frozenset({"search", "wanted", "library.view", "player"}))
ADMIN = User(1, "shano", "admin", frozenset())


@pytest.fixture
async def wanted(monkeypatch):
    modules = registry.discover()
    await init_db(modules)
    registry.register_subscriptions(modules)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tmdb_api_key', 'x')")

    async def no_poster(tmdb_id):
        return None
    router = importlib.import_module("app.modules.wanted.router")
    monkeypatch.setattr(router, "_tmdb_poster", no_poster)
    return router


async def test_what_a_child_asks_for_is_never_downloaded_automatically(wanted, monkeypatch):
    kid = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", check_now=False), KID)
    mine = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=604, title="Matrix Reloaded", year="2003",
                                                    check_now=False), ADMIN)
    assert kid["auto"] == 0 and kid["added_by"] == "ema" and mine["auto"] == 1

    async def found(wanted_id):
        return {"status": "found"}
    asked = []

    async def download(wanted_id):
        asked.append(wanted_id)
    monkeypatch.setattr(store, "check", found)
    monkeypatch.setattr(store, "request_download", download)
    monkeypatch.setattr(store, "PAUSE_BETWEEN_FILMS_S", 0)
    store._auto_download.update([kid["id"], mine["id"]])
    store._queue.extend([kid["id"], mine["id"]])
    await store._run()
    assert asked == [mine["id"]]

    # the admin lets it go
    await wanted.update_wanted(kid["id"], wanted.WantedUpdate(auto=True), ADMIN)
    assert (await store.get(kid["id"]))["auto"] == 1


async def test_a_child_only_asks(wanted):
    item = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", check_now=False), KID)
    other = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=604, title="Reloaded", year="2003", check_now=False),
                                    ADMIN)
    with pytest.raises(HTTPException) as e:
        await wanted.update_wanted(item["id"], wanted.WantedUpdate(auto=True), KID)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        await wanted.remove_wanted(other["id"], KID)          # not theirs
    assert (await wanted.remove_wanted(item["id"], KID))["ok"]
    # asking for what the admin has already wanted keeps its automation
    again = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=604, title="Reloaded", year="2003", check_now=False), KID)
    assert again["id"] == other["id"] and again["auto"] == 1 and again["added_by"] == "shano"


async def test_a_show_is_asked_for_and_the_admin_decides(wanted):
    show = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Bluey", year="2018", media_type="tv"), KID)
    film = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", check_now=False), KID)
    assert show["id"] != film["id"] and show["media_type"] == "tv"     # TMDB ids of films and shows differ
    assert store._queue == [] or show["id"] not in store._queue        # nothing searched
    assert await store.check(show["id"]) is None
    items = await mark_known([TMDBMovie(tmdb_id=603, title="Bluey", original_title="", year="2018", overview="",
                                        poster_url=None, media_type="tv"),
                              TMDBMovie(tmdb_id=603, title="Matrix", original_title="", year="1999", overview="",
                                        poster_url=None)])
    assert items[0].wanted["status"] == "request" and items[0].wanted["added_by"] == "ema"
    assert items[1].wanted["status"] == "wanted"
    assert (await wanted.wanted_of(tmdb_id=603, media_type="tv"))["id"] == show["id"]
    assert (await wanted.wanted_of(tmdb_id=603))["id"] == film["id"]
    # the film reaching the library does not finish the show
    from app.core import events
    await events.emit("library.movie_updated", {"tmdb_id": 603, "status": "manual"})
    assert (await store.get(show["id"]))["status"] == "wanted"
    await store.show_wanted(603)                    # the admin sets the show to be got
    assert (await store.get(show["id"]))["status"] == "done"


async def test_the_top_bar_marks_what_others_added(wanted):
    assert (await store.unseen(ADMIN.id, ADMIN.username))[0] == 0        # the first look: nothing old is new
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE wanted_seen SET seen_at = '2000-01-01 00:00:00'")
    await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", check_now=False), KID)
    await wanted.add_wanted(wanted.WantedAdd(tmdb_id=10, title="Bluey", media_type="tv"), KID)
    await wanted.add_wanted(wanted.WantedAdd(tmdb_id=604, title="Reloaded", check_now=False), ADMIN)
    assert (await store.unseen(ADMIN.id, ADMIN.username)) == (2, "2000-01-01 00:00:00")   # not their own
    await store.seen(ADMIN.id)
    assert (await store.unseen(ADMIN.id, ADMIN.username))[0] == 0
