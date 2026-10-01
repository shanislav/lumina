"""Audio alignment: finding a piece of one track in another, and reading the pattern of offsets.
(End-to-end runs on real files: decisions/0007 — Matrix Revolutions CS vs SK and variants.)"""

import numpy as np

from app.modules.audiosync.analyze import FPS, RATE, Window, judge, cut_point, locate, onsets, stretch


def _events(seconds: float, seed: int) -> np.ndarray:
    """A noisy soundtrack with sharp events (hits, notes) at random times."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    x = rng.normal(0, 0.01, n).astype(np.float32)
    for at in rng.uniform(0, seconds, int(seconds * 3)):
        i = int(at * RATE)
        burst = rng.normal(0, 1, 800).astype(np.float32) * np.exp(-np.arange(800) / 150).astype(np.float32)
        x[i:i + 800] += burst[: max(0, min(800, n - i))]
    return x


def test_finds_the_shared_music_under_different_speech():
    music = _events(120, 1)
    reference = music + 0.8 * _events(120, 2)          # "CZ speech"
    shift = 7.37
    other = np.concatenate([np.zeros(int(shift * RATE), np.float32), music]) + 0.8 * _events(120 + shift, 3)
    needle = onsets(reference[int(40 * RATE): int(70 * RATE)])
    lag, score, sharp = locate(needle, onsets(other))
    found = lag / FPS - 40
    assert abs(found - shift) < 0.03, found
    assert sharp > 1.8

    unrelated = onsets(_events(130, 9))
    _, _, sharp_wrong = locate(needle, unrelated)
    assert sharp_wrong < 1.8


def test_stretch_maps_a_faster_version_back():
    f = np.arange(1000, dtype=np.float32)[:, None].repeat(2, axis=1)
    s = stretch(f, 1.25)        # the other file runs 1.25× as long → every 1.25th frame
    assert s[4, 0] == 5.0 and len(s) == 800


def _w(at, offset, good=True):
    return Window(at=at, offset=offset, score=0.2 if good else 0.01, sharpness=5 if good else 1.0)


def test_judge_constant_offset():
    ws = [_w(t, 2.5 + (0.02 if i % 2 else -0.02)) for i, t in enumerate(range(200, 7000, 300))]
    verdict, speed, offset, segs, conf, _, _ = judge(ws, 1.0, 7200)
    assert verdict == "constant" and speed == 1.0 and abs(offset - 2.5) < 0.03 and len(segs) == 1


def test_judge_small_drift_becomes_exact_speed():
    ws = [_w(t, 0.0001 * t) for t in range(200, 7000, 300)]      # 0.7 s over the film
    verdict, speed, offset, _, _, _, drift = judge(ws, 1.0, 7200)
    assert verdict == "speed" and abs(speed - 1.0001) < 1e-7 and abs(drift - 0.72) < 0.01


def test_judge_cut_and_no_match():
    ws = [_w(t, 0.0 if t < 3000 else -60.0) for t in range(200, 7000, 300)] + [_w(3100, 5.0, good=False)]
    ws.sort(key=lambda w: w.at)
    verdict, _, offset, segs, _, note, _ = judge(ws, 1.0, 7200)
    assert verdict == "cuts" and [round(s.offset) for s in segs] == [0, -60] and offset == -60.0
    assert "střih" in note

    bad = [_w(t, 0.0, good=i % 3 == 0) for i, t in enumerate(range(200, 7000, 300))]
    assert judge(bad, 1.0, 7200)[0] == "no_match"


def test_tracks_get_telling_names():
    from app.modules.audiosync.transfer import ref_track_names, track_name
    assert track_name({"language": "cze", "title": "CZ dabing Nova", "channels": 6, "codec": "ac3",
                       "bitrate": 448000}) == "CZ Nova 5.1 AC3 448 kbps"
    assert track_name({"language": "slo", "title": "Slovak AC3 2.0 @ 192 kbps", "channels": 2, "codec": "ac3"}) == "SK 2.0 AC3"
    # the file's own tracks: a title naming codec and channels stays, a poor one gets the details
    assert track_name({"language": "eng", "title": "Eng DTS 6ch 48kHz - 1510 kbps - 24bit", "channels": 6,
                       "codec": "dts"}, moved=False) is None
    assert track_name({"language": "cze", "title": "DD 5.1 CZ", "channels": 6, "codec": "ac3"}, moved=False) is None
    assert track_name({"language": "cze", "title": "cze 2.0", "channels": 2, "codec": "ac3", "bitrate": 192000},
                      moved=False) == "CZ 2.0 AC3 192 kbps"
    assert track_name({"language": "cze", "title": "Stereo", "channels": 2, "codec": "aac", "bitrate": 192000},
                      moved=False) == "CZ 2.0 AAC 192 kbps"
    assert track_name({"language": "eng", "title": "", "channels": 8, "codec": "dts", "profile": "DTS-HD MA"},
                      moved=False) == "EN 7.1 DTS-HD MA"
    # marks of older Lumina versions disappear
    assert track_name({"language": "slo", "title": "SLO (Lumina sync)", "channels": 2, "codec": "ac3"}, moved=False) == "SK 2.0 AC3"
    assert track_name({"language": "slo", "title": "SK 2.0 AC3 224 kbps [L]", "channels": 2, "codec": "ac3",
                       "bitrate": 224000}, moved=False) == "SK 2.0 AC3 224 kbps"
    # an untagged track keeps its name
    assert track_name({"language": "", "title": "Stereo", "channels": 2}, moved=False) is None
    ref = {"audio": [{"index": 0, "language": "", "title": "x", "channels": 6},
                     {"index": 1, "language": "cze", "title": "Stereo", "channels": 2, "codec": "aac"}]}
    assert ref_track_names(ref) == {1: "CZ 2.0 AAC"}


def test_default_reference_is_the_original_language_then_english():
    from app.modules.audiosync.router import default_track
    audio = [{"index": 0, "language": "cze"}, {"index": 1, "language": "eng"}, {"index": 2, "language": "fre"}]
    assert default_track(audio, "fr") == 2
    assert default_track(audio, "cs") == 0
    assert default_track(audio, "ja") == 1             # no Japanese track → English
    assert default_track(audio, "") == 1
    assert default_track([{"index": 0, "language": "slo"}], "en") == 0


def test_cut_point_uses_the_known_shape_of_a_cut():
    rng = np.random.default_rng(5)
    n, guard = 12000, 2000                       # 120 s of 10 ms frames
    frames = np.arange(n)
    noise = lambda: rng.normal(0, 0.05, n)       # noqa: E731
    # the other version has extra material: mapping 1 until 50 s, mapping 2 from 50 s, no gap
    c1 = noise() + np.where(frames < 5000, 0.2, 0.0)
    c2 = noise() + np.where(frames >= 5000, 0.2, 0.0)
    assert abs(cut_point(c1, c2, 0, guard) - 5000) < 100
    # it lacks 30 s: mapping 1 fits until 40 s, mapping 2 from 70 s — a quiet 3 s after 70 s; a quiet
    # stretch looks like a wrong mapping, so it bounds the error (nothing tells them apart)
    c1 = noise() + np.where(frames < 4000, 0.2, 0.0)
    c2 = noise() + np.where(frames >= 7300, 0.2, 0.0)          # 70–73 s: fits but quiet
    assert abs(cut_point(c1, c2, 3000, guard) - 4000) <= 300


def test_film_map_groups_the_same_dub_across_versions(monkeypatch):
    from app.modules.audiosync import filmmap

    # the "dub" key stands for the content: equal key = the same dub
    monkeypatch.setattr(filmmap, "same_dub", lambda a, b, d: a["info"]["dub"] == b["info"]["dub"])
    uhd = {"id": 1, "path": "uhd", "analysis": None, "audio": [
        {"index": 0, "language": "eng", "codec": "truehd", "channels": 8, "title": "", "dub": "en"}]}
    web = {"id": 2, "path": "web", "analysis": {"speed": 1.0, "offset": 0.0}, "audio": [
        {"index": 0, "language": "cze", "codec": "ac3", "channels": 6, "title": "CZ kino", "dub": "cz-kino"},
        {"index": 1, "language": "eng", "codec": "dts", "channels": 6, "title": "", "dub": "en"},
        {"index": 2, "language": "cze", "codec": "aac", "channels": 2, "title": "", "dub": "cz-kino"}]}
    tv = {"id": 3, "path": "tv", "analysis": {"speed": 1.0, "offset": 0.0}, "audio": [
        {"index": 0, "language": "cze", "codec": "ac3", "channels": 2, "title": "CZ Nova", "dub": "cz-nova"},
        {"index": 1, "language": "slo", "codec": "ac3", "channels": 2, "title": "", "dub": "sk"}]}
    dubs = filmmap.cluster([uhd, web, tv], 7000)
    by_name = {d["name"]: d for d in dubs}
    assert set(by_name) == {"EN", "CZ kino", "CZ Nova", "SK"}
    assert [(m["version_id"], m["track"]) for m in by_name["EN"]["members"]] == [(1, 0), (2, 1)]
    # the 5.1 and the 2.0 of one dub are one row, the better one first
    assert [(m["version_id"], m["track"]) for m in by_name["CZ kino"]["members"]] == [(2, 0), (2, 2)]


def test_dub_names_drop_the_technical_part():
    from app.modules.audiosync.filmmap import dub_name
    assert dub_name("cs", "Cze AC3 6ch 48kHz - 640 kbps - 16 bits") == "CZ"
    assert dub_name("cs", "CZ dabing Nova (Lumina sync)") == "CZ Nova"
    assert dub_name("sk", "SLO (Lumina sync)") == "SK"
    assert dub_name("en", "English DTS-HD MA 7.1") == "EN"
    assert dub_name("en", "Commentary by director") == "EN Commentary by director"
    assert dub_name("cs", "") == "CZ"
    assert dub_name("sk", "SK 2.0 [L]") == "SK"


def test_a_drifting_stretch_then_a_jump_is_two_segments_with_a_slope():
    """Measured on National Lampoon's Christmas Vacation (CZ x265 vs SK TV version): the first 37 min
    drift by ~0.4 s, then the offset jumps back and stays."""
    from app.modules.audiosync.analyze import segments_from
    measured = [(175, -2.12), (412, -2.08), (648, -2.01), (885, -1.95), (1121, -1.89), (1358, -1.90),
                (1595, -1.84), (1831, -1.79), (2068, -1.74), (2304, -2.06), (2778, -2.01), (3014, -2.03),
                (3251, -2.05), (3487, -2.03), (3724, -2.05), (3961, -2.08), (4197, -2.16), (4671, -2.09),
                (4907, -1.97), (5144, -2.00), (5380, -2.02), (5617, -2.04)]
    segs = segments_from([_w(at, off) for at, off in measured])
    assert len(segs) == 2
    a, b = segs
    assert 0.00015 < a.slope < 0.00025 and abs(a.at(2068) - (-1.74)) < 0.05      # follows the drift
    assert abs(b.at(4000) - (-2.04)) < 0.05


def test_a_third_offset_between_two_segments_is_another_stretch():
    """Christmas Vacation CZ vs EN: 0.79 before, 0.60 for ~55 s, then 0.28 — two cuts, not one."""
    from app.modules.audiosync.analyze import Window, middle_run
    ws = [Window(at=float(t), offset=o, score=0.2 if good else 0.01, sharpness=5.0 if good else 1.0)
          for t, o, good in [(2140, 0.78, True), (2150, 0.76, True), (2180, 0.60, True), (2195, 0.61, True),
                             (2210, 0.85, False), (2225, 0.63, True), (2240, 0.28, True)]]
    run = middle_run(ws, 0.79, 0.28)
    assert [w.at for w in run] == [2180, 2195, 2225]
    # a lone odd window is noise
    assert middle_run([ws[0], ws[2], ws[-1]], 0.79, 0.28) == []


async def test_tasks_of_the_audio_editor_show_running_and_recent_work():
    import importlib
    import time

    from app.modules.audiosync import tasks
    r = importlib.import_module("app.modules.audiosync.router")
    saved = dict(r._job)
    try:
        r._job.clear()
        assert await tasks.read() == []
        r._job.update(running=True, kind="map", phase="tracks", current="x.mkv", done=1, total=3, title="Film", tmdb_id=5)
        [t] = await tasks.read()
        assert t["running"] and t["link"] == "/library/audio?tmdb=5" and "měřím" in t["detail"]
        r._job.update(running=False, finished_at=time.time(), error="nesedí")
        [t] = await tasks.read()
        assert not t["running"] and t["error"] == "nesedí"
        r._job.update(finished_at=time.time() - 7200)
        assert await tasks.read() == []
    finally:
        r._job.clear()
        r._job.update(saved)


def test_dropping_the_reference_hands_it_to_a_track_that_fits():
    from app.modules.audiosync.router import _new_reference
    fmap = {"target_tracks": {"0": {"ok": False}, "1": {"ok": True}}}
    assert _new_reference(fmap, [0, 1, 3], 3, False) == 2           # kept: stays, new position
    assert _new_reference(fmap, [0, 1], 3, False) == 1              # dropped: track 1 fits it
    assert _new_reference({"target_tracks": {}}, [0], 3, True) == 1  # else the first added track
    assert _new_reference({"target_tracks": {}}, [0], 3, False) is None


def test_added_tracks_are_listed_in_the_order_mkvmerge_writes_them():
    from app.modules.audiosync.transfer import output_order
    # copied tracks of one source file go together by track id; a re-encoded one is its own file
    added = [("sd.mkv", 2), ("x-0", 0), ("sd.mkv", 0), ("uhd.mkv", 1)]
    assert output_order(added, lambda a: a[0], lambda a: a[1]) == [("sd.mkv", 0), ("sd.mkv", 2), ("x-0", 0), ("uhd.mkv", 1)]


def test_language_read_from_a_track_title_when_the_tag_is_missing():
    from app.modules.audiosync.analyze import language_from_title
    assert language_from_title("CZ 2.0 AAC 128 kbps") == "cze"
    assert language_from_title("Slovak AC3 2.0ch") == "slo"
    assert language_from_title("EN+CZ") == ""            # two languages — not a guess
    assert language_from_title("Stereo") == ""
