"""Wanted films: check with a profile, offers.found, done when the film reaches the library."""

import importlib
import sqlite3

import pytest

from app.core import events, registry
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.offers.search import Offers
from app.core.quality import Prefs
from app.db import DB_PATH, init_db
from app.modules.wanted import store

CTX = MovieContext(titles=["Matrix", "The Matrix"], year=1999, runtime=136)


def row(name, size, ident):
    return {"ident": ident, "name": name, "size": size, "source": "webshare", "source_id": 1, "magnet_url": None,
            **evaluate(name, size, CTX, Prefs())}


@pytest.fixture
async def wanted(monkeypatch):
    modules = registry.discover()
    await init_db(modules)
    registry.register_subscriptions(modules)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tmdb_api_key', 'x')")

    async def fake_find(cfg, query, **kw):
        return Offers(CTX, Prefs(), [
            row("The.Matrix.1999.2160p.HDR.CZ.mkv", 30_000_000_000, "4k"),
            row("The.Matrix.1999.1080p.x265.CZ.mkv", 4_000_000_000, "fhd"),
            row("The.Matrix.1999.1080p.EN.mkv", 8_000_000_000, "en"),
        ])

    async def no_verify(offers, limit=10):
        return None
    monkeypatch.setattr(store, "find_offers", fake_find)
    monkeypatch.setattr(store, "verify_offers", no_verify)
    return importlib.import_module("app.modules.wanted.router")


async def test_full_hd_profile_finds_only_full_hd_with_czech(wanted):
    found = []

    async def listener(payload):
        found.append(payload)
    events.subscribe("offers.found", listener)
    item = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", profile_id=2,
                                                    check_now=False))
    result = await store.check(item["id"])
    assert result == {"status": "found", "matches": 1}
    stored = await store.get(item["id"])
    assert stored["status"] == "found" and '"ident": "fhd"' in stored["best"]
    assert found and found[0]["best"]["ident"] == "fhd" and found[0]["profile"] == "Full HD"


async def test_nothing_suitable_stays_wanted(wanted, monkeypatch):
    item = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", profile_id=3,
                                                    check_now=False))
    # 4K profile: only the 4K file with Czech audio suits
    result = await store.check(item["id"])
    assert result["status"] == "found" and result["matches"] == 1
    await wanted.update_wanted(item["id"], wanted.WantedUpdate(profile_id=None))
    monkeypatch.setattr(store, "find_offers", lambda *a, **k: _empty())
    assert (await store.check(item["id"]))["status"] == "wanted"


async def _empty():
    return Offers(CTX, Prefs(), [])


async def test_done_when_the_film_reaches_the_library(wanted):
    item = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", check_now=False))
    await events.emit("library.movie_updated", {"tmdb_id": 603, "status": "manual"})
    assert (await store.get(item["id"]))["status"] == "done"
    # adding it again (e.g. wants it in 4K) makes it wanted again
    again = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", profile_id=3,
                                                     check_now=False))
    assert again["id"] == item["id"] and again["status"] == "wanted" and again["profile_id"] == 3
