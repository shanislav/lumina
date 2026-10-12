"""A season's dub moved from the old versions of its episodes: which file gets it from which version, or why not."""

import json
import sqlite3

from app.core import registry
from app.db import DB_PATH, init_db
from app.modules.audiosync import episodes as dubs


async def test_each_episode_its_target_and_the_version_with_the_dub(tmp_path):
    await init_db(registry.discover())
    season = tmp_path / "Bones" / "Season 01"
    season.mkdir(parents=True)
    files = {}
    for name in ("E01 HEVC.mkv", "E01 old.avi", "E02 HEVC.mkv", "E03 HEVC.mkv", "E03 old.avi", "E04 old.avi"):
        files[name] = season / name
        files[name].write_bytes(b"x" * 10)
    media = {"E01 HEVC.mkv": ["cs"], "E01 old.avi": ["sk"], "E02 HEVC.mkv": ["cs"], "E03 HEVC.mkv": ["cs", "sk"],
             "E03 old.avi": ["sk"], "E04 old.avi": ["sk"]}
    with sqlite3.connect(DB_PATH) as conn:
        for ep, main in ((1, "E01 HEVC.mkv"), (2, "E02 HEVC.mkv"), (3, "E03 HEVC.mkv"), (4, "E04 old.avi")):
            conn.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, filename, has_file) "
                         "VALUES (1911, 1, ?, ?, ?, 1)", (ep, str(files[main]), main))
        for name, path in files.items():
            ep = int(name[1:3])
            conn.execute("INSERT INTO tv_files (file_path, show_tmdb_id, season, episodes) VALUES (?, 1911, 1, ?)",
                         (str(path), json.dumps([ep])))
            conn.execute("INSERT INTO tv_media (file_path, size, mtime, media) VALUES (?, 10, 0, ?)",
                         (str(path), json.dumps({"audio": [{"lang": l} for l in media[name]]})))
    got = {p["episode"]: p for p in await dubs.pairs(1911, 1, "sk")}
    assert got[1]["status"] == "ready" and got[1]["target"] == "E01 HEVC.mkv" and got[1]["source"] == "E01 old.avi"
    assert got[1]["source_track"] == 0 and got[1]["target_track"] == 0
    assert got[2]["status"] == "skip"                      # no old version
    assert got[3]["status"] == "done"                      # the new one has the dub already
    assert got[4]["status"] == "done"                      # its only file is the old one with the dub


def test_the_new_release_own_language_by_its_name():
    assert dubs.own_language("Bones - S01E04 - Muž v medvědovi [1080p x265] [CS].mkv", "sk") == "cs"
    assert dubs.own_language("Bones - S01E04 [1080p x265] [CS+SK].mkv", "sk") == "cs"     # moved again
    assert dubs.own_language("Bones.S01E04.1080p.WEB-DL.HEVC.mkv", "sk") is None
    assert dubs.own_language("Show S01E01 [CS+EN].mkv", "sk") is None                     # which one is it?


async def test_the_own_track_without_a_language_named_and_only_that(monkeypatch):
    from app.core import mediainfo
    from app.modules.library import audio_lang
    written = []

    async def fake_set(path, lang, track=None, media=None):
        written.append((lang, track))
        return {"written": True, "media": media, "tracks": 1}
    monkeypatch.setattr(audio_lang, "set_language", fake_set)
    tracks = {"a.mkv": [{"lang": ""}, {"lang": "sk"}], "b.mkv": [{"lang": "cs"}, {"lang": "sk"}],
              "c.mkv": [{"lang": "und"}, {"lang": "sk"}]}

    async def fake_probe(path):
        return {"audio": tracks[path]}
    monkeypatch.setattr(mediainfo, "probe_async", fake_probe)
    await dubs._name_own_track("a.mkv", "Bones S01E04 [CS].mkv", 0, "sk")
    await dubs._name_own_track("b.mkv", "Bones S01E05 [CS].mkv", 0, "sk")     # has its language already
    await dubs._name_own_track("c.mkv", "Bones.S01E06.HEVC.mkv", 0, "sk")     # the name tells none
    assert written == [("cs", 0)]
