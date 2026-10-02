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
    # running ones first (the two tracked), then the queue
    assert [d["status"] for d in listed][-1] == "queued" and listed[-1]["filename"] == "Matrix (1999)"
    assert listed[-1]["requested_by"] == "shano" and listed[-1]["mode"] == "replace"
    assert {d.get("gid") for d in listed[:2]} == {"g0", "g1"}

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


def test_profile_score_range_and_overall_bitrate():
    from app.core.profiles import Profile, block, profile_from_row, score_range
    from app.core.quality import Prefs

    fhd = Profile(min_resolution="1080p", max_resolution="1080p", max_size_gb=7)
    r = score_range(fhd, Prefs())
    assert r["possible"] and r["size_2h_gb"] == [0.0, 7.0]
    assert 0 < r["min"] < r["max"] <= 100 and "1080p" in r["max_example"]
    # an impossible profile says so
    assert not score_range(Profile(min_mbps=20, max_size_gb=5), Prefs())["possible"]
    # the bitrate limit is the overall one (size / length), not the estimated picture part
    p = Profile(min_mbps=5)
    assert block({"bitrate": 5.2e6, "video_bitrate": 4.5e6}, p) is None
    assert block({"bitrate": 4.8e6}, p) == "bitrate 4.8 Mb/s < 5"
    # profiles saved with the old video bitrate keep their limit
    old = profile_from_row({"id": 1, "name": "x", "is_default": 0, "config": '{"min_video_mbps": 3}'})
    assert old.min_mbps == 3


async def test_the_download_list_is_lumina_history_newest_first():
    import importlib
    from app.modules.downloads import store
    router = importlib.import_module("app.modules.downloads.router")

    await _setup("3")
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("INSERT INTO download_tracker (id, title, year, backend, status, processed, created_at, finished_at, "
                         "file_name, size) VALUES (?, ?, 2000, 'aria2', ?, 1, ?, ?, ?, 5)",
                         [(f"h{i}", f"Film {i}", "complete", f"2026-10-01 10:{i:02d}:00", f"2026-10-01 12:{i:02d}:00",
                           f"Film {i}.mkv") for i in range(25)])
        conn.execute("INSERT INTO download_tracker (id, title, backend, status, processed, created_at) "
                     "VALUES ('old', 'Old', 'aria2', 'complete', 1, '2020-01-01 00:00:00')")
    await store.prune_history()
    out = await router.list_downloads(offset=0, limit=10)
    assert out["downloads"] == [] and out["history_total"] == 25
    assert [d["filename"] for d in out["history"][:2]] == ["Film 24.mkv", "Film 23.mkv"]
    page3 = (await router.list_downloads(offset=20, limit=10))["history"]
    assert len(page3) == 5 and page3[-1]["filename"] == "Film 0.mkv"
    assert (await router.remove_download("h3", backend="history"))["ok"]
    assert (await router.list_downloads())["history_total"] == 24
