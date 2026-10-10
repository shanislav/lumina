"""The picture's help in placing a dub — only where the sound alone is not enough (audiosync/episodes).

- Each piece's offset is checked on ~30 s of picture: shot changes of both versions are paired (the speed is
  known from the sound). The sound measures the dub against the reference track, and a reference muxed in by an
  uploader ("(CzAudio)") may itself sit 0.1–0.3 s off its picture — the lips follow the picture, so a clear
  difference from the picture wins.
- A cut is trusted when the silence it inserts falls where the reference is quiet too (an ad break's black
  screen). One that lands in speech is placed again: around it (±45 s) the shot changes tell up to where the
  old offset fits and from where the new one does — the silence goes between them, into a black screen if
  there is one."""

import logging
import re
import subprocess

import numpy as np

from app.modules.audiosync import analyze as engine

logger = logging.getLogger(__name__)

SCALE = 160                   # px wide — shot changes need no detail, decoding stays cheap
SCENE = 0.3                   # ffmpeg's scene score of a shot change
MATCH_S = 0.06                # a shot change of the other version within this is the same one (~1.5 frame)
MIN_MATCHES = 3
PIECE_PROBE_S = 30.0
CUT_AROUND_S = 45.0
DIFFERS_S = 0.08              # the picture overrules the sound from this difference on
QUIET = 0.3                   # the inserted silence's place: below this share of the reference's usual level


def shots(path: str, start: float, duration: float) -> tuple[list[float], list[tuple[float, float]]]:
    """(shot changes, black screens) between ``start`` and ``start + duration`` — times from the file's start."""
    start = max(0.0, start)
    try:
        err = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", path, "-an",
             "-vf", f"scale={SCALE}:-2,blackdetect=d=0.2:pix_th=0.12,select='gt(scene,{SCENE})',showinfo",
             "-f", "null", "-"], capture_output=True, text=True, timeout=300).stderr
    except (subprocess.SubprocessError, OSError) as e:
        logger.info("Shots of %s: %s", path, e)
        return [], []
    scenes = [start + float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    blacks = [(start + float(a), start + float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    return scenes, blacks


def pair_offset(new: list[float], old: list[float], speed: float, guess: float, spread: float = 1.0
                ) -> tuple[float | None, int]:
    """The offset (t_old = speed · t_new + offset) most shot changes agree on, near ``guess`` → (offset, pairs)."""
    candidates = [o - speed * n for n in new for o in old if abs(o - speed * n - guess) <= spread]
    best, best_pairs = None, []
    for c in candidates:
        pairs = [o - speed * n for n in new for o in old if abs(o - speed * n - c) <= MATCH_S]
        if len(pairs) > len(best_pairs):
            best, best_pairs = c, pairs
    if best is None or len(best_pairs) < MIN_MATCHES:
        return None, len(best_pairs)
    return float(np.median(best_pairs)), len(best_pairs)


def piece_offset(new_path: str, old_path: str, speed: float, offset: float, start: float, end: float
                 ) -> float | None:
    """The piece's offset by the picture (at its middle), None when the picture cannot tell."""
    length = min(PIECE_PROBE_S, end - start - 10)
    if length < 10:
        return None
    at = (start + end - length) / 2
    new, _ = shots(new_path, at, length)
    old, _ = shots(old_path, speed * at + offset - 2, speed * length + 4)
    found, pairs = pair_offset(new, old, speed, offset)
    logger.info("audiosync picture %.0f–%.0f s: sound %+.3f, picture %s (%d shots)", start, end, offset,
                f"{found:+.3f}" if found is not None else "—", pairs)
    return found


def quiet(ref_path: str, ref_track: int, t1: float, t2: float) -> bool:
    """Is the reference quiet where the dub gets its silence (an ad break) — compared with the minute around?"""
    a = max(0.0, t1 - 30)
    s = engine.extract(ref_path, ref_track, a, (t2 - t1) + 60)
    if not len(s):
        return True
    n = max(1, int(engine.RATE * 0.25))
    rms = np.array([np.sqrt(np.mean(s[i:i + n] ** 2)) for i in range(0, len(s) - n + 1, n)])
    i1, i2 = int((t1 - a) / 0.25), max(int((t1 - a) / 0.25) + 1, int((t2 - a) / 0.25))
    usual = float(np.median(rms)) or 1e-6
    return float(np.mean(rms[i1:i2])) < QUIET * usual


def place_cut(new_path: str, old_path: str, speed: float, o1: float, o2: float, t1: float, gap: float
              ) -> float | None:
    """Where the cut is by the picture → t1 (o1 fits until t1, o2 from t1 + gap), None when it cannot tell."""
    a, b = t1 - CUT_AROUND_S, t1 + gap + CUT_AROUND_S
    new, blacks = shots(new_path, a, b - a)
    lo = speed * a + min(o1, o2) - 2
    old, _ = shots(old_path, lo, speed * (b - a) + abs(o1 - o2) + 4)
    if len(new) < 2 or len(old) < 2:
        return None

    def fits(n: float, off: float) -> bool:
        return any(abs(o - (speed * n + off)) <= MATCH_S for o in old)
    by1 = [n for n in new if fits(n, o1) and not fits(n, o2)]
    by2 = [n for n in new if fits(n, o2) and not fits(n, o1)]
    if not by1 or not by2:
        return None
    last1 = max((n for n in by1 if n < min(by2) + gap), default=None)
    first2 = min((n for n in by2 if last1 is None or n > last1), default=None)
    if last1 is None or first2 is None or first2 - last1 < gap:
        return None
    # into a black screen between them, else halfway
    for s, e in blacks:
        if e > last1 and s < first2 and min(e, first2) - max(s, last1) >= gap:
            return max(s, last1)
    return (last1 + first2 - gap) / 2


def refine(analysis: dict, ref_path: str, ref_track: int, other_path: str) -> dict:
    """The sound's mapping corrected by the picture where needed → the analysis (pieces) with ``expect``: how far the
    dub is meant to sit from the reference track (checked after muxing)."""
    data = dict(analysis)
    speed = data["speed"]
    notes = []
    if data["verdict"] != "cuts":
        p = piece_offset(ref_path, other_path, speed, data["offset"], 0.0, engine.probe(ref_path)["duration"])
        if p is not None and abs(p - data["offset"]) > DIFFERS_S:
            data["expect"] = -(p - data["offset"]) / speed
            notes.append(f"posun podle obrazu {p - data['offset']:+.2f} s")
            data["offset"] = p
        data["picture"] = notes
        return data
    pieces = [dict(p) for p in data["pieces"]]
    for p in pieces:
        if p.get("offset") is None:
            continue
        found = piece_offset(ref_path, other_path, speed, p["offset"], p["start"], p["end"])
        if found is not None and abs(found - p["offset"]) > DIFFERS_S:
            # the offset at the probe's middle → at the piece's start (a piece may drift: slope)
            delta = found - (p["offset"] + p.get("slope", 0.0) * ((p["start"] + p["end"]) / 2 - p["start"]))
            p["offset"] += delta
            p["expect"] = -delta / speed
            notes.append(f"{_clock(p['start'])}: posun podle obrazu {delta:+.2f} s")
    # cuts: the silence where the reference speaks is placed again
    for k in range(len(pieces) - 2):
        before, gap, after = pieces[k], pieces[k + 1], pieces[k + 2]
        if gap.get("offset") is not None or before.get("offset") is None or after.get("offset") is None:
            continue
        if quiet(ref_path, ref_track, gap["start"], gap["end"]):
            continue
        o1 = before["offset"] + before.get("slope", 0.0) * (gap["start"] - before["start"])
        o2 = after["offset"]
        length = gap["end"] - gap["start"]
        t1 = place_cut(ref_path, other_path, speed, o1, o2, gap["start"], length)
        if t1 is None:
            data["unsure_cut"] = _clock(gap["start"])
            notes.append(f"{_clock(gap['start'])}: střih v řeči, obraz nepomohl")
            continue
        notes.append(f"střih {_clock(gap['start'])} → {_clock(t1)} (podle obrazu)")
        moved = t1 + length - after["start"]
        before["end"], gap["start"], gap["end"] = t1, t1, t1 + length
        after["offset"] += after.get("slope", 0.0) * moved
        after["start"] = t1 + length
    data["pieces"] = pieces
    data["picture"] = notes
    return data


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"
