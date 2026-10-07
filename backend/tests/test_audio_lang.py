"""The user's word on an episode's sound language (library/audio_lang): into MKV in place, MP4 by a copy,
AVI only in Lumina; only tracks without a language unless one is named."""

import pytest

from app.modules.library import audio_lang


def test_which_tracks():
    audio = [{"lang": "en"}, {"lang": ""}, {"lang": "und"}]
    assert audio_lang._targets(audio, None) == [1, 2]
    assert audio_lang._targets(audio, 0) == [0]
    assert audio_lang._targets(audio, 5) == []


async def test_avi_keeps_it_in_lumina(tmp_path):
    f = tmp_path / "South Park - S28E03.avi"
    f.write_bytes(b"x")
    out = await audio_lang.set_language(str(f), "cs", media={"audio": [{"lang": ""}]})
    assert out["written"] is False and out["media"]["audio"][0]["lang"] == "cs" and out["tracks"] == 1


async def test_mkv_is_written_in_place(tmp_path, monkeypatch):
    f = tmp_path / "South Park - S28E03.mkv"
    f.write_bytes(b"x")
    calls = []

    async def fake_run(*args, timeout=0):
        calls.append(args)
        return 0, ""

    async def probe(path):
        return {"audio": [{"lang": "cs"}]}
    monkeypatch.setattr(audio_lang, "_run", fake_run)
    monkeypatch.setattr(audio_lang, "probe_async", probe)
    out = await audio_lang.set_language(str(f), "cs", media={"audio": [{"lang": ""}]})
    assert calls == [("mkvpropedit", str(f), "--edit", "track:a1", "--set", "language=cze")]
    assert out["written"] and out["media"]["audio"][0]["lang"] == "cs"


async def test_an_unknown_language_is_refused(tmp_path):
    with pytest.raises(ValueError):
        await audio_lang.set_language(str(tmp_path / "x.mkv"), "xx", media={"audio": [{"lang": ""}]})


async def test_only_files_of_the_tv_library(tmp_path, monkeypatch):
    import sqlite3
    from app.core import registry
    from app.db import DB_PATH, init_db
    import importlib
    router = importlib.import_module("app.modules.library.router")
    await init_db(registry.discover())
    shows = tmp_path / "Serials" / "Show" / "Season 01"
    shows.mkdir(parents=True)
    inside, outside = shows / "Show - S01E01.avi", tmp_path / "secret.avi"
    inside.write_bytes(b"x")
    outside.write_bytes(b"x")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tv_library_dir', ?)", (str(tmp_path / "Serials"),))

    async def probe(path):
        return {"audio": [{"lang": "en"}]}
    monkeypatch.setattr(audio_lang, "probe_async", probe)
    out = await router.tv_audio_language(router.AudioLanguage(paths=[str(inside), str(outside)], lang="cs", track=0))
    assert len(out["done"]) == 1 and out["done"][0]["languages"] == "CS" and len(out["errors"]) == 1


async def test_a_whole_season_is_written_in_the_background(tmp_path, monkeypatch):
    """An MP4 is copied whole — a season takes minutes, longer than the page's request may wait (504): more than a
    few files go to the background, the task list shows them."""
    import asyncio
    import sqlite3
    from app.core import registry
    from app.db import DB_PATH, init_db
    import importlib
    router = importlib.import_module("app.modules.library.router")
    tasks = importlib.import_module("app.modules.library.tasks")
    await init_db(registry.discover())
    season = tmp_path / "Serials" / "Show" / "Season 02"
    season.mkdir(parents=True)
    files = [season / f"Show - S02E{n:02d}.avi" for n in range(1, 6)]
    for f in files:
        f.write_bytes(b"x")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tv_library_dir', ?)", (str(tmp_path / "Serials"),))
    gate = asyncio.Event()

    async def probe(path):
        await gate.wait()
        return {"audio": [{"lang": "en"}]}
    monkeypatch.setattr(audio_lang, "probe_async", probe)
    out = await router.tv_audio_language(router.AudioLanguage(paths=[str(f) for f in files], lang="cs", track=0))
    assert out == {"started": True, "total": 5}
    await asyncio.sleep(0)
    assert router.audio_language_job()["running"]
    assert (await tasks.read())[-1]["total"] == 5
    # the next season meanwhile: queued after the running one
    more = [season / f"Show - S03E{n:02d}.avi" for n in range(1, 5)]
    for f in more:
        f.write_bytes(b"x")
    out = await router.tv_audio_language(router.AudioLanguage(paths=[str(f) for f in more], lang="sk", track=0))
    assert out["queued"] and router.audio_language_job()["total"] == 9
    gate.set()
    for _ in range(100):
        if not router.audio_language_job()["running"]:
            break
        await asyncio.sleep(0.01)
    job = router.audio_language_job()
    assert not job["running"] and job["done"] == 9 and job["errors"] == [] and job["langs"] == ["cs", "sk"]
    assert (await tasks.read())[-1]["detail"] == "hotovo"
