"""How does the audio of one version of a film line up with another version? (decisions/0007)

Two files of the same film can drift apart in three ways:
- a constant offset (a different logo / intro at the start),
- a different speed (PAL 25 fps vs 23.976 — 4.3 % over the whole film),
- a different cut (extended / theatrical, removed scenes) — the offset jumps.

A dubbed track has other speech but the same music and effects. So both tracks are turned into
"onsets per frequency band" (where sound starts) and a short window of the reference is looked
up in the other file by cross-correlation — in ~24 places across the film. The pattern of the
found offsets tells which case it is; weak or ambiguous matches are reported, never guessed.

Mapping found: ``t_other = speed * t_reference + offset`` (per segment when the cut differs).
"""

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

RATE = 8000               # Hz — enough for onsets, cheap to decode
HOP = 80                  # 10 ms per feature frame
FRAME = 256               # 32 ms FFT window
FPS = RATE / HOP
N_BANDS = 16

WINDOW_S = 40.0           # reference piece looked up in the other file
MARGIN_S = 150.0          # how far around the expected place it is looked for
WINDOWS = 24
# Trustworthy match. Tuned on Matrix Revolutions CS vs SK dub: the correlation itself stays low
# (0.04–0.37, the speech differs) but the right place stands out 2–12× above any other one.
GOOD_SCORE = 0.04
GOOD_SHARPNESS = 1.8
SAME_OFFSET_S = 0.1       # offsets closer than this are the same (lip sync tolerance ~40–80 ms)

# frame rate pairs seen in the wild: film 23.976 / 24, PAL 25
SPEEDS = sorted({1.0, 23.976 / 25, 25 / 23.976, 24 / 25, 25 / 24, 23.976 / 24, 24 / 23.976})


# ── decoding ──

def probe(path: str) -> dict:
    """Duration and audio streams (index among audio streams, language, codec, channels)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name,channels:stream_tags=language,title",
         "-of", "json", path], capture_output=True, text=True, timeout=60, check=True).stdout
    data = json.loads(out)
    audio = []
    for s in data.get("streams", []):
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags") or {}
        audio.append({"index": len(audio), "language": (tags.get("language") or "").lower(),
                      "title": tags.get("title") or "", "codec": s.get("codec_name") or "",
                      "channels": s.get("channels") or 0})
    return {"duration": float(data.get("format", {}).get("duration") or 0), "audio": audio}


def extract(path: str, track: int, start: float, duration: float) -> np.ndarray:
    """Mono 8 kHz float samples of one audio track, from ``start`` for ``duration`` seconds."""
    start = max(0.0, start)
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", path,
         "-map", f"0:a:{track}", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        capture_output=True, timeout=300, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


# ── features ──

_BAND_EDGES: np.ndarray | None = None


def _bands() -> np.ndarray:
    global _BAND_EDGES
    if _BAND_EDGES is None:
        # log-spaced bands 60 Hz … 3.9 kHz over the rfft bins
        freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
        edges = np.geomspace(60, 3900, N_BANDS + 1)
        _BAND_EDGES = np.searchsorted(freqs, edges)
    return _BAND_EDGES


def onsets(samples: np.ndarray) -> np.ndarray:
    """(frames, bands) onset strength, each band normalized — where sound starts, per band.
    Loudness differences between the tracks do not matter, only the rhythm of events."""
    if len(samples) < FRAME * 4:
        return np.zeros((0, N_BANDS), dtype=np.float32)
    n = 1 + (len(samples) - FRAME) // HOP
    frames = np.lib.stride_tricks.as_strided(samples, shape=(n, FRAME),
                                             strides=(samples.strides[0] * HOP, samples.strides[0]))
    power = np.abs(np.fft.rfft(frames * np.hanning(FRAME).astype(np.float32), axis=1)) ** 2
    edges = _bands()
    energy = np.stack([power[:, edges[i]:max(edges[i + 1], edges[i] + 1)].sum(axis=1) for i in range(N_BANDS)], axis=1)
    log = np.log1p(energy * 1e4)
    flux = np.maximum(np.diff(log, axis=0, prepend=log[:1]), 0)
    flux -= flux.mean(axis=0)
    std = flux.std(axis=0)
    flux /= np.where(std > 1e-6, std, 1)
    return flux.astype(np.float32)


def stretch(features: np.ndarray, speed: float) -> np.ndarray:
    """Resample the other file's features onto the reference time grid (other runs ``speed``× as long)."""
    if abs(speed - 1) < 1e-9 or len(features) == 0:
        return features
    positions = np.arange(0, len(features) - 1, speed)
    idx = np.arange(len(features))
    return np.stack([np.interp(positions, idx, features[:, b]) for b in range(features.shape[1])], axis=1).astype(np.float32)


def locate(needle: np.ndarray, haystack: np.ndarray) -> tuple[int, float, float]:
    """Best position of ``needle`` in ``haystack`` → (frame lag, normalized correlation, sharpness)."""
    ln, lh = len(needle), len(haystack)
    if ln == 0 or lh < ln:
        return 0, 0.0, 0.0
    size = 1 << int(np.ceil(np.log2(ln + lh)))
    corr = np.zeros(lh - ln + 1)
    for b in range(needle.shape[1]):
        f = np.fft.rfft(haystack[:, b], size) * np.conj(np.fft.rfft(needle[:, b], size))
        corr += np.fft.irfft(f, size)[: lh - ln + 1]
    # normalize by the energy of the needle and of each haystack piece
    sq = np.concatenate([[0.0], np.cumsum((haystack.astype(np.float64) ** 2).sum(axis=1))])
    piece = sq[ln:] - sq[:-ln]
    norm = np.sqrt(float((needle.astype(np.float64) ** 2).sum()) * np.maximum(piece, 1e-9))
    score = corr / norm
    best = int(np.argmax(score))
    guard = int(FPS * 0.5)   # the peak itself is a few frames wide
    rest = np.concatenate([score[: max(0, best - guard)], score[best + guard + 1:]])
    second = float(rest.max()) if len(rest) else 0.0
    sharp = float(score[best] / second) if second > 1e-6 else 99.0
    return best, float(score[best]), sharp


# ── analysis ──

@dataclass
class Window:
    at: float            # seconds in the reference
    offset: float        # t_other - speed * t_reference
    score: float
    sharpness: float

    @property
    def good(self) -> bool:
        return self.score >= GOOD_SCORE and self.sharpness >= GOOD_SHARPNESS


@dataclass
class Segment:
    start: float         # reference seconds
    end: float
    offset: float


@dataclass
class Result:
    verdict: str                     # constant | speed | cuts | no_match
    speed: float                     # t_other = speed * t_reference + offset
    offset: float                    # the main (longest segment) offset
    confidence: float                # share of trustworthy windows
    segments: list[Segment] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    reference: dict = field(default_factory=dict)
    other: dict = field(default_factory=dict)
    note: str = ""
    drift_s: float = 0.0             # how much a pure-offset mapping would drift over the film

    def to_dict(self) -> dict:
        return asdict(self)


def _measure(ref_path: str, ref_track: int, other_path: str, other_track: int, at: float, speed: float,
             offset_guess: float = 0.0) -> Window:
    needle = onsets(extract(ref_path, ref_track, at, WINDOW_S))
    center = speed * at + offset_guess
    start = max(0.0, center - MARGIN_S)
    hay = stretch(onsets(extract(other_path, other_track, start, speed * WINDOW_S + 2 * MARGIN_S)), speed)
    lag, score, sharp = locate(needle, hay)
    found_other = start + lag / FPS * speed
    return Window(at=at, offset=found_other - speed * at, score=score, sharpness=sharp)


def _positions(duration: float, count: int) -> list[float]:
    usable = duration - WINDOW_S - 60
    if usable <= 0:
        return [0.0]
    # skip the first/last 3 % (logos, credits are the least alike)
    return [float(x) for x in np.linspace(duration * 0.03, min(usable, duration * 0.97 - WINDOW_S), count)]


def segments_from(windows: list[Window]) -> list[Segment]:
    """Consecutive trustworthy windows with the same offset form a segment."""
    good = [w for w in windows if w.good]
    segs: list[list[Window]] = []
    for w in good:
        if segs and abs(w.offset - np.median([x.offset for x in segs[-1]])) <= SAME_OFFSET_S:
            segs[-1].append(w)
        else:
            segs.append([w])
    return [Segment(start=s[0].at, end=s[-1].at + WINDOW_S, offset=float(np.median([w.offset for w in s])))
            for s in segs]


def fit_line(windows: list[Window]) -> tuple[float, float]:
    """Robust line offset = a + b·t (Theil–Sen: medians of pairwise slopes) → (b, a)."""
    if len(windows) < 2:
        return 0.0, windows[0].offset if windows else 0.0
    slopes = [(y.offset - x.offset) / (y.at - x.at) for i, x in enumerate(windows) for y in windows[i + 1:]
              if y.at > x.at]
    slope = float(np.median(slopes))
    return slope, float(np.median([w.offset - slope * w.at for w in windows]))


def judge(windows: list[Window], speed: float, duration: float
          ) -> tuple[str, float, float, list[Segment], float, str, float]:
    """→ (verdict, speed, offset, segments, confidence, note, drift_s)."""
    good = [w for w in windows if w.good]
    confidence = len(good) / max(1, len(windows))
    if confidence < 0.5:
        return ("no_match", speed, 0.0, [], confidence,
                "Zvuk se nedaří spárovat — jiný film, jiný střih nebo příliš odlišný zvuk", 0.0)
    slope, intercept = fit_line(good)
    on_line = [w for w in good if abs(w.offset - (intercept + slope * w.at)) <= SAME_OFFSET_S]
    if len(on_line) >= 0.85 * len(good):
        # one mapping for the whole film; a tiny slope is a speed a hair off the nominal one
        drift = slope * duration
        exact = speed + slope if abs(drift) > SAME_OFFSET_S / 2 else speed
        seg = [Segment(start=good[0].at, end=good[-1].at + WINDOW_S, offset=intercept)]
        verdict = "constant" if abs(exact - 1) < 1e-9 else "speed"
        return verdict, exact, intercept, seg, confidence, "", drift
    segs = segments_from(windows)
    # a lone window with its own offset is noise, not a cut
    real = [s for s in segs if sum(1 for w in good if s.start <= w.at < s.end) >= 2] or segs
    main = max(real, key=lambda s: s.end - s.start)
    return ("cuts", speed, main.offset, real, confidence,
            f"Posun se mění na {len(real) - 1} místech — jiný střih", 0.0)


def analyze(ref_path: str, ref_track: int, other_path: str, other_track: int, progress=None) -> Result:
    ref, other = probe(ref_path), probe(other_path)
    if not ref["audio"] or not other["audio"]:
        return Result("no_match", 1.0, 0.0, 0.0, note="Soubor nemá zvukovou stopu", reference=ref, other=other)

    # 1) speed: a few windows with every candidate speed
    probes = _positions(ref["duration"], 5)
    best_speed, best_score = 1.0, -1.0
    for speed in SPEEDS:
        scores = [_measure(ref_path, ref_track, other_path, other_track, at, speed).score for at in probes]
        med = float(np.median(scores))
        logger.info("audiosync speed %.5f → median score %.3f", speed, med)
        if med > best_score:
            best_speed, best_score = speed, med
        if progress:
            progress("speed", SPEEDS.index(speed) + 1, len(SPEEDS))

    # 2) the whole film with that speed
    windows = []
    positions = _positions(ref["duration"], WINDOWS)
    for i, at in enumerate(positions):
        windows.append(_measure(ref_path, ref_track, other_path, other_track, at, best_speed))
        if progress:
            progress("windows", i + 1, len(positions))

    verdict, speed, offset, segs, confidence, note, drift = judge(windows, best_speed, ref["duration"])
    return Result(verdict=verdict, speed=speed, offset=offset, confidence=confidence, segments=segs,
                  windows=windows, reference=ref, other=other, note=note, drift_s=drift)
