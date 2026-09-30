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


def test_keep_audio_takes_every_local_dub_and_names_them():
    from app.modules.audiosync.keep_audio import local_tracks
    from app.modules.audiosync.transfer import track_name
    old = [{"index": 0, "language": "cze", "title": "CZ dabing Nova"}, {"index": 1, "language": "eng"},
           {"index": 2, "language": "ces", "title": "CZ dabing Prima"}, {"index": 3, "language": "slo"}]
    assert local_tracks(old) == [0, 2, 3]                  # both CZ dubs + SK; which the new file has = content check
    assert track_name(old[0]) == "CZ dabing Nova (Lumina sync)"
    assert track_name(old[3]) == "SLO (Lumina sync)"


async def test_keep_audio_download_is_held_back_from_the_library(monkeypatch):
    from app.core import events
    from app.modules.audiosync import keep_audio
    from app.modules.library import imports

    started, imported = [], []

    async def fake_import(payload):
        imported.append(payload)

    from app.core import registry
    from app.db import init_db
    await init_db(registry.discover())

    async def fake_keep(payload, pending_id=None):
        started.append(payload)

    monkeypatch.setattr(keep_audio, "_keep_audio", fake_keep)
    monkeypatch.setattr(imports, "import_movie", fake_import)
    events.subscribe("download.completed", keep_audio.on_download_completed, 20, owner="audiosync")
    events.subscribe("download.completed", imports.on_download_completed, 30, owner="library")

    base = {"tmdb_id": 605, "title": "M", "content_type": "movie", "path": "/d/m.mkv"}
    held = await events.emit("download.completed",
                             {**base, "library_action": {"mode": "replace", "file_id": 3, "keep_audio": True}})
    import asyncio
    await asyncio.sleep(0)
    assert held["held_by"] == "audiosync" and not imported and len(started) == 1
    # a normal replace goes straight to the library
    await events.emit("download.completed", {**base, "library_action": {"mode": "replace", "file_id": 3}})
    assert len(imported) == 1 and len(started) == 1



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
