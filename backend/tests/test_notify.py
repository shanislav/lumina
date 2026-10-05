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


async def test_the_admin_clears_the_history_repeats_stay_out(db):
    await store.add("upgrade", "Lepší verze: Matrix", dedup="upgrade:603:x")
    await store.add("wanted", "Nalezeno: Dune")
    assert await store.clear() == 2
    assert (await store.listing(ADMIN)) == {"items": [], "unread": 0}
    assert await store.add("upgrade", "Lepší verze: Matrix", dedup="upgrade:603:x") is None    # told already
    await store.add("wanted", "Nalezeno: Duna 2")
    assert [n["title"] for n in (await store.listing(ADMIN))["items"]] == ["Nalezeno: Duna 2"]


def test_only_an_admin_clears():
    from app.core.auth import ADMIN_ONLY
    assert ADMIN.can(ADMIN_ONLY) and not VIEWER.can(ADMIN_ONLY)


async def test_a_wanted_film_found_right_after_adding_rings_no_bell(db):
    """Chci shows it then (its top bar mark); the bell is for a film that turns up later — and not for who may not
    download (it leads to the offers)."""
    p = {"kind": "wanted", "tmdb_id": 7, "title": "Film", "year": "2000", "best": {"ident": "a"}}
    await handlers.on_offers_found({**p, "first": True})
    assert (await store.listing(ADMIN))["items"] == []
    await handlers.on_offers_found({**p, "first": False})
    assert [i["title"] for i in (await store.listing(ADMIN))["items"]] == ["Chci — nalezeno: Film (2000)"]
    kid = User(9, "ema", "user", frozenset({"search", "wanted"}))
    assert (await store.listing(kid))["items"] == []
