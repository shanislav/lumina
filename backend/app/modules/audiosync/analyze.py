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
With a different cut the exact places are found between the segments (``find_cut``) and the film
is described as ``pieces``: reference time ranges with their offset, or None where the other
version has no audio for the picture (a scene it lacks).
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
    """Duration, start time and audio streams (index among audio streams, language, codec, channels).

    ``start``: the first timestamp of the file. ffmpeg seeks relative to it, so the analysis measures
    times from the start of the file; muxing keeps the real timestamps (step 2 converts)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,start_time:stream=index,codec_type,codec_name,channels:stream_tags=language,title",
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
    fmt = data.get("format", {})
    return {"duration": float(fmt.get("duration") or 0), "start": float(fmt.get("start_time") or 0), "audio": audio}


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
    # the whole reference timeline: [{start, end, offset}] — offset None = the other version lacks it
    pieces: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _measure(ref_path: str, ref_track: int, other_path: str, other_track: int, at: float, speed: float,
             offset_guess: float = 0.0, margin: float = MARGIN_S) -> Window:
    needle = onsets(extract(ref_path, ref_track, at, WINDOW_S))
    center = speed * at + offset_guess
    start = max(0.0, center - margin)
    hay = stretch(onsets(extract(other_path, other_track, start, speed * WINDOW_S + 2 * margin)), speed)
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
        if abs(drift) > SAME_OFFSET_S:
            exact, offset = speed + slope, intercept
        else:
            # below lip-sync tolerance over the whole film: keep the nominal speed, take the typical offset
            exact, offset = speed, float(np.median([w.offset for w in on_line]))
        seg = [Segment(start=good[0].at, end=good[-1].at + WINDOW_S, offset=offset)]
        verdict = "constant" if abs(exact - 1) < 1e-9 else "speed"
        return verdict, exact, offset, seg, confidence, "", drift
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
    pieces: list[dict] = []
    if verdict in ("constant", "speed"):
        pieces = [{"start": 0.0, "end": ref["duration"], "offset": offset}]
    elif verdict == "cuts":
        if progress:
            progress("cuts", 0, len(segs) - 1)
        pieces = cut_pieces(ref_path, ref_track, other_path, other_track, speed, segs, ref["duration"], progress)
    return Result(verdict=verdict, speed=speed, offset=offset, confidence=confidence, segments=segs,
                  windows=windows, reference=ref, other=other, note=note, drift_s=drift, pieces=pieces)


# ── different cut ──

SMOOTH_S = 1.0          # agreement is averaged over this much time
MIN_GAP_S = 0.3         # shorter "missing" stretches are noise at the cut point
SCAN_STEP_S = 20.0      # coarse search for a cut: one window every this many seconds


def _aligned_other(other_path: str, other_track: int, speed: float, offset: float, lo: float,
                   length: float) -> np.ndarray:
    """The other file's onsets on the reference grid [lo, lo + length) under this offset."""
    start = speed * lo + offset
    pad = 0
    if start < 0:
        pad = int(round(-start / speed * FPS))
        start = 0.0
    f = stretch(onsets(extract(other_path, other_track, start, speed * length + 1)), speed)
    if pad:
        f = np.vstack([np.zeros((pad, N_BANDS), np.float32), f])
    return f


def _agreement(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = min(len(a), len(b))
    v = (a[:n] * b[:n]).sum(axis=1) / N_BANDS
    k = max(1, int(SMOOTH_S * FPS))
    return np.convolve(v, np.ones(k) / k, mode="same")


def _fine_end(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
              s1: float, o1: float) -> float:
    """The window at ``s1`` fits o1 → where exactly in [s1 + W/2, s1 + W + step] o1 stops fitting.
    Per-frame agreement is weak, so it is only read next to a stretch known to fit."""
    length = WINDOW_S + SCAN_STEP_S
    ref = onsets(extract(ref_path, ref_track, s1, length))
    c = _agreement(ref, _aligned_other(other_path, other_track, speed, o1, s1, length))
    half = int(WINDOW_S / 2 * FPS)
    level = max(float(np.mean(c[:half])), 1e-3)
    pre = np.cumsum(c - level / 2)
    i = half + int(np.argmax(pre[half:])) if len(pre) > half else len(pre)
    return s1 + i / FPS


def _fine_start(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
                s2: float, o2: float) -> float:
    """The window at ``s2`` fits o2 → where exactly in [s2 − step, s2 + W/2] o2 starts fitting."""
    lo = max(0.0, s2 - SCAN_STEP_S)
    length = s2 + WINDOW_S - lo
    ref = onsets(extract(ref_path, ref_track, lo, length))
    c = _agreement(ref, _aligned_other(other_path, other_track, speed, o2, lo, length))
    half = int(WINDOW_S / 2 * FPS)
    level = max(float(np.mean(c[-half:])), 1e-3)
    suf = np.cumsum((c - level / 2)[::-1])[::-1]
    limit = max(1, len(suf) - half)
    j = int(np.argmax(suf[:limit]))
    return lo + j / FPS


def find_cut(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
             lo: float, hi: float, o1: float, o2: float) -> tuple[float, float]:
    """Between the window at ``lo`` (offset o1 fits) and the one at ``hi`` (o2 fits) → (t1, t2):
    o1 fits until t1, o2 from t2 (between them the other version has nothing for the picture).

    1) windows every 20 s say which offset they fit — the last o1 and the first o2 one bound the cut;
    2) the exact points come from the per-frame agreement right next to those windows."""
    margin = abs(o1 - o2) / 2 + 20
    s1, s2 = lo, hi
    for at in np.arange(lo + SCAN_STEP_S, hi, SCAN_STEP_S):
        w = _measure(ref_path, ref_track, other_path, other_track, float(at), speed, (o1 + o2) / 2, margin)
        if not w.good:
            continue
        if abs(w.offset - o1) <= SAME_OFFSET_S:
            s1 = max(s1, float(at))
        elif abs(w.offset - o2) <= SAME_OFFSET_S:
            s2 = min(s2, float(at))
    t1 = _fine_end(ref_path, ref_track, other_path, other_track, speed, s1, o1)
    t2 = _fine_start(ref_path, ref_track, other_path, other_track, speed, s2, o2)
    if t1 > t2:            # the other version has extra material: one cut point
        t1 = t2 = (t1 + t2) / 2
    return t1, t2


def cut_pieces(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
               segments: list[Segment], duration: float, progress=None) -> list[dict]:
    pieces: list[dict] = []
    start = 0.0
    for k, (s1, s2) in enumerate(zip(segments, segments[1:])):
        lo, hi = s1.end - WINDOW_S, s2.start
        t1, t2 = find_cut(ref_path, ref_track, other_path, other_track, speed, lo, hi, s1.offset, s2.offset)
        pieces.append({"start": start, "end": t1, "offset": s1.offset})
        if t2 - t1 >= MIN_GAP_S:
            pieces.append({"start": t1, "end": t2, "offset": None})
        start = t2 if t2 - t1 >= MIN_GAP_S else t1
        if progress:
            progress("cuts", k + 1, len(segments) - 1)
    pieces.append({"start": start, "end": duration, "offset": segments[-1].offset})
    return pieces
