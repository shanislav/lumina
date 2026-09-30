"""Scheduler: when it runs, and what the nightly run starts (wanted checks, automatic downloads)."""

import importlib
import json
import sqlite3
from datetime import datetime

import pytest

from app.core import registry
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.offers.search import Offers
from app.core.quality import Prefs
from app.db import DB_PATH, init_db
from app.modules.scheduler.runner import is_due, next_run
from app.modules.wanted import store

CFG = {"time": "03:00"}


def test_due_once_a_day_from_the_time_on():
    at = datetime(2026, 10, 1, 3, 5)
    assert is_due(CFG, "2026-09-30 03:00:10", at)
    assert not is_due(CFG, "2026-10-01 03:00:10", at)                      # already ran today
    assert not is_due(CFG, "", datetime(2026, 10, 1, 2, 59))               # too early
    assert is_due(CFG, "", datetime(2026, 10, 1, 9, 0))                    # server was down at 3 → catches up
    assert not is_due(CFG, "", datetime(2026, 10, 1, 16, 0))               # but not in the afternoon
    assert next_run(CFG, "2026-10-01 03:00:10", at) == datetime(2026, 10, 2, 3, 0)


CTX = MovieContext(titles=["Matrix"], year=1999, runtime=136)


@pytest.fixture
async def setup(monkeypatch):
    modules = registry.discover()
    await init_db(modules)
    registry.register_subscriptions(modules)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tmdb_api_key', 'x')")
        conn.execute("UPDATE automations SET enabled = 1, config = ? WHERE type = 'scheduler'",
                     (json.dumps({"auto_download_wanted": "true"}),))

    async def fake_find(cfg, query, **kw):
        name = "The.Matrix.1999.1080p.x265.CZ.mkv"
        return Offers(CTX, Prefs(), [{"ident": "fhd", "name": name, "size": 4_000_000_000, "source": "webshare",
                                      "source_id": 1, "magnet_url": None, **evaluate(name, 4_000_000_000, CTX, Prefs())}])

    async def no_verify(offers, limit=10):
        return None
    monkeypatch.setattr(store, "find_offers", fake_find)
    monkeypatch.setattr(store, "verify_offers", no_verify)
    monkeypatch.setattr(store, "PAUSE_BETWEEN_FILMS_S", 0)

    started = []
    downloads = importlib.import_module("app.modules.downloads.router")

    async def fake_start(req):
        started.append(req)
        return {"gid": "g1", "status": "active"}
    monkeypatch.setattr(downloads, "start_download", fake_start)
    return started


async def test_nightly_run_checks_wanted_and_downloads_when_allowed(setup):
    started = setup
    wanted = importlib.import_module("app.modules.wanted.router")
    item = await wanted.add_wanted(wanted.WantedAdd(tmdb_id=603, title="Matrix", year="1999", check_now=False))
    scheduler = importlib.import_module("app.modules.scheduler.runner")
    await scheduler.run("test")
    while store.job_status()["running"]:
        import asyncio
        await asyncio.sleep(0.05)
    assert [r.file_ident for r in started] == ["fhd"] and started[0].tmdb_id == 603
    assert (await store.get(item["id"]))["status"] == "downloading"
    # the next night does not download it again
    await scheduler.run("test")
    assert store.job_status()["queued"] == 0 and len(started) == 1
