import sqlite3

from app.core import registry
from app.db import DB_PATH, init_db, set_settings
from app.models.schemas import DownloadRequest


async def _setup(limit: str):
    await init_db(registry.discover())
    await set_settings({"max_concurrent_downloads": limit})


def _running(n: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("INSERT INTO download_tracker (id, title, backend, status, processed) VALUES (?, ?, 'aria2', 'active', 0)",
                         [(f"g{i}", f"Film {i}") for i in range(n)])


async def test_over_the_limit_a_download_waits_and_starts_when_there_is_room(monkeypatch):
    import importlib
    from app.modules.downloads import queue
    router = importlib.import_module("app.modules.downloads.router")

    await _setup("2")
    _running(2)
    monkeypatch.setattr("app.modules.downloads.monitor.ensure_monitor_running", lambda: None)
    req = DownloadRequest(file_ident="x", source="webshare", source_id=1, tmdb_id=603, title="Matrix", year=1999,
                          library_action={"mode": "replace", "file_id": 7})
    out = await router.start_download(req, requested_by="shano")
    assert out["status"] == "queued"

    listed = (await router.list_downloads())["downloads"]
    assert listed[0]["status"] == "queued" and listed[0]["filename"] == "Matrix (1999)"
    assert listed[0]["requested_by"] == "shano" and listed[0]["mode"] == "replace"

    # still full: nothing starts
    started = []

    async def fake_start(r, requested_by="", queued=True):
        started.append((r.title, requested_by, queued, r.library_action))
        return {"gid": "new"}
    monkeypatch.setattr(router, "start_download", fake_start)
    assert await queue.drain() == 0

    # one finished → the waiting one starts, with its request as it was
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE download_tracker SET processed = 1 WHERE id = 'g0'")
    assert await queue.drain() == 1
    assert started == [("Matrix", "shano", False, {"mode": "replace", "file_id": 7})]
    assert await queue.pending() == 0


async def test_no_limit_and_first_come_first_served(monkeypatch):
    from app.modules.downloads import queue

    await _setup("0")
    _running(10)
    assert not await queue.must_wait()
    await set_settings({"max_concurrent_downloads": "20"})
    assert not await queue.must_wait()
    await queue.add({"title": "A"}, "")
    assert await queue.must_wait()      # somebody waits already — a new one goes behind


async def test_a_one_off_upgrade_stops_watching_after_the_import(monkeypatch):
    import importlib
    from app.modules.library import upgrades
    lib = importlib.import_module("app.modules.library.router")

    await init_db(registry.discover())
    await lib.set_film_settings_bulk(lib.BulkFilmSettings(tmdb_ids=[1, 2], profile_id=None, on_better="replace",
                                                          upgrade_once=True))
    await lib.set_film_settings(3, lib.FilmSettings(watch_upgrades=True, on_better="replace"))
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("INSERT INTO upgrade_checks (tmdb_id, status) VALUES (?, 'downloading')", [(1,), (3,)])

    queued = []
    monkeypatch.setattr(upgrades, "enqueue", lambda ids: queued.extend(ids))
    await upgrades.after_import(1)
    await upgrades.after_import(3)

    films = await lib.film_settings_all()
    assert films["1"]["watch_upgrades"] is False and films["2"] == {
        "profile_id": None, "watch_upgrades": True, "on_better": "replace", "upgrade_once": True}
    assert films["3"]["watch_upgrades"] is True
    checks = await upgrades.results()
    assert checks["1"]["status"] == "done" and checks["3"]["status"] == "none"
    assert queued == [3]                # the one-off film is not checked again


async def test_bulk_keeps_own_profiles_when_asked():
    import importlib
    lib = importlib.import_module("app.modules.library.router")

    await init_db(registry.discover())
    await lib.set_film_settings(5, lib.FilmSettings(profile_id=9))
    await lib.set_film_settings_bulk(lib.BulkFilmSettings(tmdb_ids=[5, 6], keep_profile=True, on_better="version"))
    films = await lib.film_settings_all()
    assert films["5"]["profile_id"] == 9 and films["6"]["profile_id"] is None
    assert films["5"]["on_better"] == films["6"]["on_better"] == "version"


async def test_the_preferred_version_is_never_replaced(monkeypatch):
    from app.core import events
    from app.modules.library import upgrades

    await init_db(registry.discover())
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("INSERT INTO library_movies (id, tmdb_id, title, year, status, preferred) VALUES (?, ?, 'F', '2000', 'matched', ?)",
                         [(1, 10, 1), (2, 20, 0)])
        conn.executemany("INSERT INTO upgrade_checks (tmdb_id, owned_id, status, best) VALUES (?, ?, 'better', '{\"ident\": \"x\"}')",
                         [(10, 1), (20, 2)])
    asked = []

    async def fake_emit(name, payload):
        asked.append(payload["library_action"])
        payload["started"] = {"gid": "g"}
        return payload
    monkeypatch.setattr(events, "emit", fake_emit)
    assert await upgrades.request_download(10, "replace")
    assert await upgrades.request_download(20, "replace")
    assert asked == [{"mode": "version"}, {"mode": "replace", "file_id": 2}]


async def test_stop_all_empties_the_queue_and_stops_the_upgrade_job(monkeypatch):
    import importlib
    from app.core import events
    from app.modules.downloads import queue
    from app.modules.library import upgrades
    router = importlib.import_module("app.modules.downloads.router")

    await _setup("1")
    for module in registry.discover():
        for sub in module.subscriptions:
            events.subscribe(sub.event, sub.handler, sub.priority, module.name)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO upgrade_checks (tmdb_id, status) VALUES (7, 'downloading')")
    await queue.add({"title": "A", "tmdb_id": 7}, "")
    await queue.add({"title": "B", "tmdb_id": 8}, "")
    monkeypatch.setattr(upgrades, "_queue", [1, 2, 3])
    monkeypatch.setattr(upgrades, "_auto_download", {1: "replace"})

    out = await router.stop_all(router.StopAll())
    assert out == {"dropped": 2, "cancelled": 0}
    assert await queue.pending() == 0
    assert upgrades._queue == [] and upgrades._auto_download == {}
    assert (await upgrades.results())["7"]["status"] == "better"
