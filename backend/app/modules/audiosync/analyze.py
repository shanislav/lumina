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
CONTINUES_S = 0.15        # neighbouring windows this close continue one segment (window noise ±0.06)

# frame rate pairs seen in the wild: film 23.976 / 24, PAL 25
SPEEDS = sorted({1.0, 23.976 / 25, 25 / 23.976, 24 / 25, 25 / 24, 23.976 / 24, 24 / 23.976})


# ── decoding ──

def probe(path: str) -> dict:
    """Duration, start time and audio streams (index among audio streams, language, codec, channels).

    ``start``: the first timestamp of the file. ffmpeg seeks relative to it, so the analysis measures
    times from the start of the file; muxing keeps the real timestamps (step 2 converts)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,start_time:stream=index,codec_type,codec_name,profile,channels,bit_rate"
         ":stream_tags=language,title,BPS,BPS-eng",
         "-of", "json", path], capture_output=True, text=True, timeout=60, check=True).stdout
    data = json.loads(out)
    audio = []
    for s in data.get("streams", []):
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags") or {}
        try:   # MKV keeps the bitrate in a statistics tag
            bitrate = int(s.get("bit_rate") or tags.get("BPS") or tags.get("BPS-eng") or 0)
        except ValueError:
            bitrate = 0
        audio.append({"index": len(audio), "language": (tags.get("language") or "").lower(),
                      "title": tags.get("title") or "", "codec": s.get("codec_name") or "",
                      "channels": s.get("channels") or 0, "bitrate": bitrate, "profile": s.get("profile") or ""})
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
    offset: float        # at ``start``
    slope: float = 0.0   # the offset drifts this much per second (a stretch running a hair faster)

    def at(self, t: float) -> float:
        return self.offset + self.slope * (t - self.start)


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
             offset_guess: float = 0.0, margin: float = MARGIN_S, window: float | None = None) -> Window:
    window = window or WINDOW_S
    needle = onsets(extract(ref_path, ref_track, at, window))
    center = speed * at + offset_guess
    start = max(0.0, center - margin)
    hay = stretch(onsets(extract(other_path, other_track, start, speed * window + 2 * margin)), speed)
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
    """Consecutive trustworthy windows that continue each other form a segment. Within one, the
    offset may drift slowly (a TV recording, a stretch at a hair different speed) — then the
    segment gets a slope; a jump between neighbours is a cut."""
    good = [w for w in windows if w.good]
    groups: list[list[Window]] = []
    for w in good:
        if groups and abs(w.offset - groups[-1][-1].offset) <= CONTINUES_S:
            groups[-1].append(w)
        else:
            groups.append([w])
    segs = []
    for g in groups:
        start, end = g[0].at, g[-1].at + WINDOW_S
        if len(g) >= 3:
            slope, intercept = fit_line(g)
            if abs(slope * (end - start)) > SAME_OFFSET_S / 2:
                segs.append(Segment(start=start, end=end, offset=intercept + slope * start, slope=slope))
                continue
        segs.append(Segment(start=start, end=end, offset=float(np.median([w.offset for w in g]))))
    return segs


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

    # 1) speed: the nominal one first — most pairs have it, then the others need not be tried
    probes = _positions(ref["duration"], 5)
    first = [_measure(ref_path, ref_track, other_path, other_track, at, 1.0) for at in probes]
    best_speed, best_score = 1.0, float(np.median([w.score for w in first]))
    if sum(w.good for w in first) < 4:
        others = [s for s in SPEEDS if abs(s - 1) > 1e-9]
        for k, speed in enumerate(others):
            scores = [_measure(ref_path, ref_track, other_path, other_track, at, speed).score for at in probes]
            med = float(np.median(scores))
            logger.info("audiosync speed %.5f → median score %.3f", speed, med)
            if med > best_score:
                best_speed, best_score = speed, med
            if progress:
                progress("speed", k + 1, len(others))

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


def shifted(analysis: dict, delta: float) -> dict:
    """The mapping of another track of the same file that sits ``delta`` s later (uploaders mux dubs
    from other releases, not always in sync with the file's first track)."""
    if not delta:
        return analysis
    out = dict(analysis)
    out["offset"] = analysis.get("offset", 0.0) + delta
    if analysis.get("pieces"):
        out["pieces"] = [{**p, "offset": None if p["offset"] is None else p["offset"] + delta} for p in analysis["pieces"]]
    return out


def piece_offset(p: dict, at: float) -> float | None:
    """A piece's offset at reference second ``at`` (pieces may drift: "slope" per second)."""
    if p.get("offset") is None:
        return None
    return p["offset"] + p.get("slope", 0.0) * (at - p["start"])


def mapping_at(analysis: dict, at: float) -> float | None:
    """Offset of the other version at reference second ``at`` (None = it has no audio there)."""
    pieces = analysis.get("pieces") or [{"start": 0, "end": 1e12, "offset": analysis.get("offset", 0.0)}]
    for p in pieces:
        if p["start"] <= at < p["end"]:
            return piece_offset(p, at)
    return piece_offset(pieces[-1], at)


# The same dub in two tracks matches closely; different dubs share only music and effects.
# Median of 6 windows: same dub in one file (5.1 vs 2.0) 0.82–0.94 (Matrix Revolutions), the same dub
# from another release (streaming mix vs Blu-ray) 0.58 (Shrek the Third); different dubs 0.13–0.35.
SAME_DUB_SCORE = 0.45

_LANG = {"cze": "cs", "ces": "cs", "slo": "sk", "slk": "sk", "eng": "en", "ger": "de", "deu": "de",
         "fre": "fr", "fra": "fr", "pol": "pl", "hun": "hu", "rus": "ru", "ita": "it", "spa": "es"}


def lang_code(language: str) -> str:
    language = (language or "").lower()
    return _LANG.get(language, language[:2])


def same_audio(ref_path: str, ref_track: int, other_path: str, other_track: int, analysis: dict,
               duration: float) -> bool:
    """Is the other track the very same dub as the reference track (under the mapping)?"""
    scores = []
    for at in np.linspace(duration * 0.15, duration * 0.85, 6):
        off = mapping_at(analysis, float(at))
        if off is None:
            continue
        w = _measure(ref_path, ref_track, other_path, other_track, float(at), analysis.get("speed", 1.0), off, margin=3.0)
        scores.append(w.score if abs(w.offset - off) <= SAME_OFFSET_S else 0.0)
    return bool(scores) and float(np.median(scores)) >= SAME_DUB_SCORE


# ── different cut ──

SMOOTH_S = 1.0          # agreement is averaged over this much time
MIN_GAP_S = 0.3         # shorter "missing" stretches are noise at the cut point
SCAN_STEP_S = 20.0      # coarse search for a cut: one window every this many seconds
FINE_WINDOW_S = 10.0    # then short windows …
FINE_STEP_S = 5.0       # … this far apart narrow it to a few seconds
MID_WINDOW_S = 20.0     # between two segments: windows this long …
MID_STEP_S = 10.0       # … this far apart look for a third offset (two cuts close together)


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


def cut_point(c1: np.ndarray, c2: np.ndarray, gap: int, guard: int, lo: int | None = None,
              hi: int | None = None) -> int:
    """Frame t of the cut: o1 fits before t, o2 from t + gap (gap = frames the other version lacks,
    0 when it has extra material instead). Each curve is measured against half of its typical level
    (read at the ends, where the windows are known to fit); both curves decide together."""
    n = min(len(c1), len(c2))
    c1, c2 = c1[:n], c2[:n]
    l1 = max(float(np.mean(c1[:guard])), 1e-3)
    l2 = max(float(np.mean(c2[-guard:])), 1e-3)
    pre = np.concatenate([[0.0], np.cumsum(c1 - l1 / 2)])                # pre[t] = frames before t
    suf = np.concatenate([np.cumsum((c2 - l2 / 2)[::-1])[::-1], [0.0]])  # suf[t] = frames from t
    lo = guard if lo is None else max(0, lo)
    hi = n - guard - gap if hi is None else min(n - gap, hi)
    if hi < lo:
        return max(0, (n - gap) // 2)
    t = np.arange(lo, hi + 1)
    return int(t[np.argmax(pre[t] + suf[t + gap])])


def find_cut(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
             lo: float, hi: float, o1: float, o2: float) -> tuple[float, float]:
    """Between the window at ``lo`` (offset o1 fits) and the one at ``hi`` (o2 fits) → (t1, t2):
    o1 fits until t1, o2 from t2.

    The shape of a cut is known: if the other version lacks material, its audio continues at the
    same place — so t2 − t1 = (o1 − o2) / speed exactly; if it has extra material, t1 = t2.
    1) windows every 20 s say which offset they fit — the last o1 and the first o2 one bound the cut;
    2) the per-frame agreement under both offsets (weak alone, fine next to known fitting windows)
       picks the one point."""
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
    # 2) short windows: the last one that still fits o1, the first one that fits o2
    fine_o1: float | None = None
    fine_o2: float | None = None
    for at in np.arange(s1, s2 + WINDOW_S - FINE_WINDOW_S + 0.01, FINE_STEP_S):
        w = _measure(ref_path, ref_track, other_path, other_track, float(at), speed, (o1 + o2) / 2, margin,
                     window=FINE_WINDOW_S)
        if not w.good:
            continue
        if abs(w.offset - o1) <= SAME_OFFSET_S:
            fine_o1 = float(at) if fine_o1 is None else max(fine_o1, float(at))
        elif abs(w.offset - o2) <= SAME_OFFSET_S:
            fine_o2 = float(at) if fine_o2 is None else min(fine_o2, float(at))
    if fine_o1 is not None and fine_o2 is not None and fine_o2 + FINE_WINDOW_S <= fine_o1:
        fine_o1 = fine_o2 = None                 # contradicting short windows — trust the long ones
    # where the o1 side may end: a window that fits o1 lies mostly before the cut, one that fits o2
    # mostly after it (a long window still fits with ~half of it on the other side, a short one less)
    gap_s = max(0.0, (o1 - o2) / speed)
    earliest = s1 + 0.4 * WINDOW_S
    latest = s2 + 0.6 * WINDOW_S - gap_s
    if fine_o1 is not None:
        earliest = max(earliest, fine_o1 + 0.75 * FINE_WINDOW_S)
    if fine_o2 is not None:
        latest = min(latest, fine_o2 + 0.25 * FINE_WINDOW_S - gap_s)
    if latest < earliest:
        earliest = latest = (earliest + latest) / 2
    # 3) the point from the per-frame agreement of both sides within those bounds
    a = max(0.0, earliest - FINE_WINDOW_S / 2)
    b = latest + gap_s + FINE_WINDOW_S / 2
    ref = onsets(extract(ref_path, ref_track, a, b - a))
    c1 = _agreement(ref, _aligned_other(other_path, other_track, speed, o1, a, b - a))
    c2 = _agreement(ref, _aligned_other(other_path, other_track, speed, o2, a, b - a))
    t = cut_point(c1, c2, int(round(gap_s * FPS)), int(FINE_WINDOW_S / 2 * FPS),
                  int((earliest - a) * FPS), int((latest - a) * FPS))
    t1 = a + t / FPS
    logger.info("audiosync cut %.2f→%.2f: long windows %.0f/%.0f, short %s/%s → between %.1f and %.1f, at %.2f (+%.2f s gap)",
                o1, o2, s1, s2, fine_o1, fine_o2, earliest, latest, t1, gap_s)
    return t1, t1 + gap_s


def middle_run(windows: list[Window], o1: float, o2: float) -> list[Window]:
    """The longest run of trustworthy windows that agree on an offset that is neither o1 nor o2
    (untrustworthy windows in between do not break it, one fitting o1/o2 does). At least two."""
    runs: list[list[Window]] = [[]]
    for w in windows:
        if not w.good:
            continue
        if abs(w.offset - o1) <= SAME_OFFSET_S or abs(w.offset - o2) <= SAME_OFFSET_S:
            runs.append([])
        elif runs[-1] and abs(w.offset - runs[-1][-1].offset) <= SAME_OFFSET_S:
            runs[-1].append(w)
        else:
            runs.append([w])
    best = max(runs, key=len)
    return best if len(best) >= 2 else []


def _middle(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
            s1: Segment, s2: Segment) -> Segment | None:
    """Two cuts close together (Christmas Vacation CZ: −0.16 s, ~55 s later −0.33 s) look like one
    from far: between the segments short windows find the stretch with its own offset."""
    lo, hi = s1.end - WINDOW_S, s2.start + WINDOW_S
    if hi - lo < 2 * MID_WINDOW_S + MID_STEP_S:
        return None
    o1, o2 = s1.at(s1.end), s2.at(s2.start)
    margin = abs(o1 - o2) / 2 + 20
    windows = [_measure(ref_path, ref_track, other_path, other_track, float(at), speed, (o1 + o2) / 2, margin,
                        window=MID_WINDOW_S)
               for at in np.arange(lo + MID_STEP_S, hi - MID_WINDOW_S - MID_STEP_S + 0.01, MID_STEP_S)]
    run = middle_run(windows, o1, o2)
    if not run:
        return None
    offset = float(np.median([w.offset for w in run]))
    # as a segment of long windows: find_cut takes a cut ≥ 0.4 W after the last fitting window start and
    # ≤ 0.6 W after the first one; a short window may sit with a quarter of it across a cut
    start = run[0].at + 0.75 * MID_WINDOW_S - 0.6 * WINDOW_S
    end = run[-1].at + 0.25 * MID_WINDOW_S - 0.4 * WINDOW_S + WINDOW_S
    logger.info("audiosync: between %.2f and %.2f another offset %.2f (%.0f–%.0f s)", o1, o2, offset,
                run[0].at, run[-1].at + MID_WINDOW_S)
    return Segment(start=start, end=end, offset=offset)


def with_middles(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
                 segments: list[Segment], depth: int = 2) -> list[Segment]:
    out = [segments[0]]
    for s2 in segments[1:]:
        s1 = out[-1]
        mid = _middle(ref_path, ref_track, other_path, other_track, speed, s1, s2) if depth else None
        if mid:
            out[-1:] = with_middles(ref_path, ref_track, other_path, other_track, speed, [s1, mid, s2], depth - 1)
        else:
            out.append(s2)
    return out


def cut_pieces(ref_path: str, ref_track: int, other_path: str, other_track: int, speed: float,
               segments: list[Segment], duration: float, progress=None) -> list[dict]:
    segments = with_middles(ref_path, ref_track, other_path, other_track, speed, segments)
    pieces: list[dict] = []
    start = 0.0
    for k, (s1, s2) in enumerate(zip(segments, segments[1:])):
        lo, hi = s1.end - WINDOW_S, s2.start
        t1, t2 = find_cut(ref_path, ref_track, other_path, other_track, speed, lo, hi, s1.at(s1.end), s2.at(s2.start))
        pieces.append({"start": start, "end": t1, "offset": s1.at(start), "slope": s1.slope})
        if t2 - t1 >= MIN_GAP_S:
            pieces.append({"start": t1, "end": t2, "offset": None})
        start = t2 if t2 - t1 >= MIN_GAP_S else t1
        if progress:
            progress("cuts", k + 1, len(segments) - 1)
    last = segments[-1]
    pieces.append({"start": start, "end": duration, "offset": last.at(start), "slope": last.slope})
    return pieces
