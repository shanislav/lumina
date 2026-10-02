"""Fit subtitles to a video by its sound: where people speak vs where the subtitles are.

Speech activity of each audio track (10 ms frames louder than the running median in the voice band)
is cross-correlated with the subtitle intervals, for the usual frame-rate conversions (25 ↔ 23.976,
24 ↔ 23.976 …). The best (scale, shift) wins when it fits clearly better than the subtitles as they are.
The thirds of the film are then checked on their own: a different cut shows as different shifts.
"""

import logging
import re
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

HOP = 0.01                 # 10 ms frames
MAX_SHIFT_S = 120.0
MAX_TRACKS = 3
SCALES = {                 # subtitles' timing × scale fits the video
    "stejné fps": 1.0,
    "25 → 23,976 fps": 25 / 23.976, "23,976 → 25 fps": 23.976 / 25,
    "24 → 23,976 fps": 24 / 23.976, "23,976 → 24 fps": 23.976 / 24,
    "25 → 24 fps": 25 / 24, "24 → 25 fps": 24 / 25,
    "29,97 → 23,976 fps": 29.97 / 23.976, "23,976 → 29,97 fps": 23.976 / 29.97,
}
MIN_GAIN = 0.02            # a fit must beat "as they are" by this much (correlation per frame)

_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def intervals(srt: str) -> list[tuple[float, float]]:
    out = []
    for m in _TIME.finditer(srt):
        g = m.groups()
        a = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
        b = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
        if b > a:
            out.append((a, b))
    return out


def speech_tracks(video: str, tracks: int) -> list[np.ndarray]:
    """Speech activity (0/1 per 10 ms) of the first audio tracks — one pass over the file."""
    tracks = max(1, min(tracks, MAX_TRACKS))
    # every track: mono 16 kHz s16, the voice band (amerge needs the formats fixed)
    chains = ";".join(f"[0:a:{i}]aresample=16000,highpass=f=250,lowpass=f=3500,"
                      f"aformat=sample_fmts=s16:sample_rates=16000:channel_layouts=mono[t{i}]" for i in range(tracks))
    if tracks > 1:
        chains += ";" + "".join(f"[t{i}]" for i in range(tracks)) + f"amerge=inputs={tracks}[out]"
        out_label = "[out]"
    else:
        out_label = "[t0]"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-filter_complex", chains, "-map", out_label,
                          "-ac", str(tracks), "-f", "s16le", "-"], capture_output=True, timeout=1800).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32).reshape(-1, tracks)
    out = []
    for t in range(tracks):
        y = x[:, t]
        n = len(y) // 160
        if not n:
            continue
        db = 10 * np.log10((y[: n * 160].reshape(n, 160) ** 2).mean(axis=1) + 1e-3)
        # louder than the surroundings (3 s running median) = speech, roughly
        from numpy.lib.stride_tricks import sliding_window_view
        w = 301
        med = np.median(sliding_window_view(np.pad(db, (w // 2, w // 2), mode="edge"), w), axis=1)
        out.append((db > med + 3).astype(np.float32))
    return out


def _mask(iv, n, scale=1.0, shift=0.0) -> np.ndarray:
    m = np.zeros(n, np.float32)
    for a, b in iv:
        i, j = int((a * scale + shift) / HOP), int((b * scale + shift) / HOP)
        if j > 0 and i < n:
            m[max(i, 0):min(j, n)] = 1
    return m


def _best_shift(sp: np.ndarray, iv, scale: float) -> tuple[float, float]:
    """(shift in s, fit) — the shift of the subtitles that fits the speech best."""
    n = len(sp)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    a, b = sp * 2 - 1, _mask(iv, n, scale) * 2 - 1
    c = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    k = int(MAX_SHIFT_S / HOP)
    window = np.concatenate([c[-k:], c[:k + 1]])
    lag = int(np.argmax(window)) - k
    return lag * HOP, float(c[lag % size] / n)


def _fit_at(sp: np.ndarray, iv, scale: float, shift: float) -> float:
    n = len(sp)
    return float(((sp * 2 - 1) * (_mask(iv, n, scale, shift) * 2 - 1)).sum() / n)


def fit(video: str, srt: str, audio_tracks: int = 1) -> dict:
    """The best timing of the subtitles on this video, and how sure it is."""
    iv = intervals(srt)
    if len(iv) < 10:
        return {"ok": False, "reason": "málo titulků na porovnání"}
    best = None
    for t, sp in enumerate(speech_tracks(video, audio_tracks)):
        as_is = _fit_at(sp, iv, 1.0, 0.0)
        for name, scale in SCALES.items():
            shift, score = _best_shift(sp, iv, scale)
            if best is None or score > best["score"]:
                best = {"track": t, "scale": scale, "scale_name": name, "shift": shift, "score": score, "as_is": as_is,
                        "sp": sp}
    if best is None:
        return {"ok": False, "reason": "film nemá zvuk"}
    sp = best.pop("sp")
    # the thirds alone, already rescaled: a different cut / drift shows as different shifts
    third = len(iv) // 3
    parts = []
    for part in (iv[:third], iv[third:2 * third], iv[2 * third:]):
        shift, _ = _best_shift(sp, part, best["scale"])
        parts.append(round(shift, 2))
    best["parts"] = parts
    best["cut_warning"] = max(parts) - min(parts) > 1.0
    gain = best["score"] - best["as_is"]
    changed = abs(best["shift"]) >= 0.1 or best["scale"] != 1.0
    best["ok"] = gain >= MIN_GAIN or not changed
    best["changed"] = changed and best["ok"]
    if not best["ok"]:
        best["reason"] = "zvuk nedává jednoznačný výsledek — titulky nechány, jak jsou"
    return best


def apply(srt: str, scale: float, shift: float) -> str:
    def fix(m: re.Match) -> str:
        g = m.groups()
        out = []
        for h, mi, s, ms in (g[:4], g[4:]):
            t = (int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000) * scale + shift
            t = max(0, int(round(t * 1000)))
            out.append(f"{t // 3600000:02d}:{t // 60000 % 60:02d}:{t // 1000 % 60:02d},{t % 1000:03d}")
        return f"{out[0]} --> {out[1]}"
    return _TIME.sub(fix, srt)
