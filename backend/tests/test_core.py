import sqlite3

from app.config import movies_library_dir, tv_library_dir
from app.core import events, registry
from app.core.module import Module
from app.db import DB_PATH


async def test_events_run_by_priority_and_share_payload():
    calls = []

    async def rename(payload):
        calls.append("rename")
        payload["path"] = "/renamed.mkv"

    async def importer(payload):
        calls.append(f"import {payload['path']}")

    events.subscribe("download.completed", importer, priority=50, owner="importer")
    events.subscribe("download.completed", rename, priority=10, owner="renamer")

    payload = await events.emit("download.completed", {"path": "/orig.mkv"})

    assert calls == ["rename", "import /renamed.mkv"]
    assert payload["path"] == "/renamed.mkv"


async def test_failing_handler_does_not_stop_other_modules():
    calls = []

    async def broken(payload):
        raise RuntimeError("boom")

    async def healthy(payload):
        calls.append("ok")

    events.subscribe("x", broken, priority=1, owner="broken")
    events.subscribe("x", healthy, priority=2, owner="healthy")

    await events.emit("x", {})

    assert calls == ["ok"]


def test_discover_finds_modules_with_unique_names():
    modules = registry.discover()
    names = [m.name for m in modules]
    assert len(names) == len(set(names))
    assert {"settings", "sources", "search", "downloads", "library",
            "integrations", "renamer"} <= set(names)
    assert not {"radarr", "sonarr"} & set(names)
    assert [m.order for m in modules] == sorted(m.order for m in modules)


def test_disabled_modules_are_skipped_but_required_stay():
    modules = [
        Module(name="core_thing", title="", required=True),
        Module(name="optional", title=""),
        Module(name="other", title=""),
    ]
    active = registry.active(modules, {"core_thing", "optional"})
    assert [m.name for m in active] == ["core_thing", "other"]


def test_read_disabled_from_settings():
    assert registry.read_disabled() == set()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO settings VALUES ('disabled_modules', 'radarr, sonarr,')")
    assert registry.read_disabled() == {"radarr", "sonarr"}


def test_library_dirs_fall_back_to_download_dirs():
    cfg = {"plex_media_dir": "/dl/movies", "tv_media_dir": "/dl/tv"}
    assert movies_library_dir(cfg) == "/dl/movies"
    assert tv_library_dir(cfg) == "/dl/tv"
    cfg |= {"movies_library_dir": "/lib/movies", "tv_library_dir": "/lib/tv"}
    assert movies_library_dir(cfg) == "/lib/movies"
    assert tv_library_dir(cfg) == "/lib/tv"


async def test_groq_model_is_part_of_effective_settings():
    from app.config import get_effective_settings
    from app.db import init_db, set_settings

    from app.clients.groq_scorer import DEFAULT_GROQ_MODEL

    await init_db(registry.discover())
    assert (await get_effective_settings())["groq_model"] == DEFAULT_GROQ_MODEL
    await set_settings({"groq_model": "qwen/qwen3.8-27b"})
    assert (await get_effective_settings())["groq_model"] == "qwen/qwen3.8-27b"


async def test_retired_groq_model_is_migrated_to_default():
    from app.clients.groq_scorer import DEFAULT_GROQ_MODEL
    from app.db import init_db

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO settings VALUES ('groq_model', 'llama-3.3-70b-versatile')")
    await init_db(registry.discover())
    with sqlite3.connect(DB_PATH) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='groq_model'").fetchone() == (DEFAULT_GROQ_MODEL,)
