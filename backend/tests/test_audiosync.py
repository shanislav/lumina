"""Audio alignment: finding a piece of one track in another, and reading the pattern of offsets.
(End-to-end runs on real files: decisions/0007 — Matrix Revolutions CS vs SK and variants.)"""

import numpy as np

from app.modules.audiosync.analyze import FPS, RATE, Window, judge, locate, onsets, stretch


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
