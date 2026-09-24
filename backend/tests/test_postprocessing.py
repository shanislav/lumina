"""download.completed → renamer / radarr / sonarr wiring (without external services)."""

import json
import sqlite3

from app.core import events, registry
from app.db import DB_PATH, init_db


async def _setup(automation_updates: dict[str, tuple[int, dict]]):
    modules = registry.discover()
    await init_db(modules)
    with sqlite3.connect(DB_PATH) as conn:
        for type_name, (enabled, config) in automation_updates.items():
            conn.execute(
                "UPDATE automations SET enabled = ?, config = ? WHERE type = ?",
                (enabled, json.dumps(config), type_name),
            )
    registry.register_subscriptions(modules)


def _payload(path, content_type="movie"):
    return {"download_id": "g1", "tmdb_id": 603, "title": "The Matrix", "year": 1999,
            "content_type": content_type, "path": str(path)}


async def test_everything_disabled_leaves_file_untouched(tmp_path):
    await _setup({})
    f = tmp_path / "Matrix.1999.1080p.mkv"
    f.write_bytes(b"x")

    payload = await events.emit("download.completed", _payload(f))

    assert payload["path"] == str(f)
    assert f.exists()


async def test_renamer_renames_and_updates_payload(tmp_path):
    await _setup({"renamer": (1, {"format": "{title} ({year}) {tmdb-{id}}", "use_mediainfo": "false"})})
    f = tmp_path / "Matrix.1999.1080p.mkv"
    f.write_bytes(b"x")

    payload = await events.emit("download.completed", _payload(f))

    expected = tmp_path / "The Matrix (1999) {tmdb-603}.mkv"
    assert payload["path"] == str(expected)
    assert expected.exists() and not f.exists()


async def test_radarr_and_sonarr_skip_without_config(tmp_path):
    # Enabled but without url/api_key → must not touch the file or call anything.
    await _setup({"radarr": (1, {}), "sonarr": (1, {})})
    f = tmp_path / "Show.S01E01.mkv"
    f.write_bytes(b"x")

    for content_type in ("movie", "tv"):
        payload = await events.emit("download.completed", _payload(f, content_type))
        assert payload["path"] == str(f)
    assert f.exists()
