"""Notifications: built from the other modules' events, repeats left out, a show's downloads merged."""

import pytest

from app.core import registry
from app.core.auth import User
from app.db import init_db
from app.modules.notify import handlers, store

ADMIN = User(1, "admin", "admin", frozenset())
VIEWER = User(2, "kid", "user", frozenset({"search"}))


@pytest.fixture
async def db():
    await init_db(registry.discover())


async def test_downloads_of_a_show_merge_and_permissions_filter(db):
    for ep in (6, 7):
        await handlers.on_download_completed({"tmdb_id": 81356, "title": "Sexuální výchova", "content_type": "tv",
                                              "imported": True, "library_action": {"season": 2, "episode": ep}})
    await handlers.on_download_completed({"tmdb_id": 603, "title": "Matrix", "year": 1999, "content_type": "movie",
                                          "imported": False})
    got = await store.listing(ADMIN)
    assert [i["title"] for i in got["items"]] == ["Staženo, ale není v knihovně: Matrix (1999)", "V knihovně: Sexuální výchova"]
    assert got["items"][1]["body"] == "S02E06, S02E07" and got["items"][1]["link"] == "/series?tmdb=81356"
    assert got["unread"] == 2
    assert (await store.listing(VIEWER))["items"] == []              # downloads need "download"
    await store.mark_seen(ADMIN.id, got["items"][0]["id"])
    assert (await store.listing(ADMIN))["unread"] == 0


async def test_the_same_find_is_not_repeated(db):
    p = {"kind": "upgrade", "tmdb_id": 1, "title": "Film", "year": "2000", "best": {"ident": "a", "resolution": "2160p"}}
    await handlers.on_offers_found(p)
    await handlers.on_offers_found(p)                                # the next night: the same file
    await handlers.on_offers_found({**p, "best": {"ident": "b"}})    # another file: news
    assert len((await store.listing(ADMIN))["items"]) == 2
    await handlers.on_series_found({"tmdb_id": 5, "title": "Seriál", "found": [[1, 3, "new"]], "downloading": []})
    items = (await store.listing(VIEWER))["items"]
    assert items[0]["title"] == "Automatika našla: Seriál" and items[0]["body"] == "S01E03 · čeká na tebe"


async def test_an_import_for_review_warns(db):
    await handlers.on_download_completed({"tmdb_id": 9, "title": "Film", "year": 2001, "content_type": "movie",
                                          "imported": True, "review": "délka 33 min, film má 112 min"})
    item = (await store.listing(ADMIN))["items"][0]
    assert item["title"] == "Na kontrolu: Film (2001)" and item["level"] == "warn"
