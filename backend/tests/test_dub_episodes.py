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


def test_dense_windows_make_segments_noise_left_out():
    from app.modules.audiosync import analyze as engine

    def w(at, off, good=True):
        return engine.Window(at=at, offset=off, score=0.3 if good else 0.0, sharpness=3.0 if good else 0.0)
    # 0.25 for a while, a 45 s stretch at 0.02 (a small cut at an ad break), one noisy window, 0.4 after
    wins = [w(t, 0.25) for t in range(0, 300, 15)] + [w(t, 0.02) for t in range(300, 345, 15)] + \
        [w(345, 0.9)] + [w(t, 0.4) for t in range(360, 600, 15)]
    segs = engine._dense_segments(wins, 0.15, 3)
    assert [round(s.offset, 2) for s in segs] == [0.25, 0.02, 0.4]
    assert engine._stairs(segs) is None                            # up and down: cuts, not a speed


def test_segments_stepping_one_way_are_a_speed_a_hair_off():
    from app.modules.audiosync import analyze as engine
    segs = [engine.Segment(start=t, end=t + 300, offset=-0.3 * k) for k, t in enumerate(range(0, 2400, 300))]
    slope = engine._stairs(segs)
    assert slope is not None and slope < 0


async def test_a_failed_check_measures_again_densely(tmp_path, monkeypatch):
    from app.modules.audiosync import analyze as engine
    from app.modules.audiosync import episodes as dubs
    from app.modules.audiosync import transfer as muxer
    from app.modules.audiosync import video
    target, source = tmp_path / "E06 [CS].mkv", tmp_path / "E06 [SK].avi"
    target.write_bytes(b"t")
    source.write_bytes(b"s")

    class Result:
        def to_dict(self):
            return {"verdict": "speed", "speed": 0.959, "offset": 0.04, "confidence": 0.9,
                    "pieces": [{"start": 0, "end": 100, "offset": 0.04}]}
    monkeypatch.setattr(engine, "analyze", lambda *a, **k: Result())
    modes = []

    def fake_dense(ref, rt, other, ot, first, mode, progress=None):
        modes.append(mode)
        return {**first, "verdict": "cuts", "pieces": [{"start": 0, "end": 50, "offset": 0.04},
                                                       {"start": 50, "end": 100, "offset": 0.25}]}
    monkeypatch.setattr(engine, "dense", fake_dense)
    monkeypatch.setattr(video, "refine", lambda data, *a: data)

    def fake_build(ref, rt, sources, work, out_name, *a):
        if len(sources[0]["analysis"]["pieces"]) == 1:
            raise muxer.TransferError("Kontrola výsledku neprošla (posuny +0.22 s)")
        work.mkdir(parents=True, exist_ok=True)
        out = work / out_name
        out.write_bytes(b"new")
        return str(out)
    monkeypatch.setattr(muxer, "transfer_many", fake_build)

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(dubs, "_name_own_track", nothing)
    monkeypatch.setattr(dubs, "_library", nothing)
    item = {"target_path": str(target), "source_path": str(source), "target_track": 0, "source_track": 0}
    r = await dubs.move_dub(item, "sk", False)
    assert r["status"] == "ok" and modes == ["loose"] and (tmp_path / "E06 [CS].mkv").read_bytes() == b"new"
