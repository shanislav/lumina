"""The audio of a whole film across its versions: which dubs exist, where, and which are the same.

For a target version (the picture to keep), every other version is aligned to it (the usual
analysis). Then every audio track of every version is placed on the target's timeline and tracks
of one language are compared: the same dub (also 5.1 vs 2.0, another encode) correlates ~0.8–0.9,
different dubs ~0.1–0.4 (decisions/0007). The result is a table: rows = dubs, columns = versions.
"""

import logging

import numpy as np

from app.modules.audiosync import analyze as engine

logger = logging.getLogger(__name__)

# better source first: more channels, then a richer codec
_CODEC_RANK = {"truehd": 6, "dts": 5, "eac3": 4, "flac": 4, "ac3": 3, "opus": 2, "aac": 2, "mp3": 1}


def _time_in(analysis: dict | None, t: float) -> float | None:
    """Where target second ``t`` is in a version (None = the version lacks it)."""
    if analysis is None:                       # the target itself
        return t
    off = engine.mapping_at(analysis, t)
    return None if off is None else analysis.get("speed", 1.0) * t + off


def same_dub(a: dict, b: dict, duration: float) -> bool:
    """Two tracks ({path, track, analysis}) — the same dub? Measured on the target's timeline."""
    speed_a = 1.0 if a["analysis"] is None else a["analysis"].get("speed", 1.0)
    speed_b = 1.0 if b["analysis"] is None else b["analysis"].get("speed", 1.0)
    ratio = speed_b / speed_a
    scores = []
    for t in np.linspace(duration * 0.15, duration * 0.85, 6):
        ta, tb = _time_in(a["analysis"], float(t)), _time_in(b["analysis"], float(t))
        if ta is None or tb is None:
            continue
        guess = tb - ratio * ta
        w = engine._measure(a["path"], a["track"], b["path"], b["track"], ta, ratio, guess, margin=3.0)
        scores.append(w.score if abs(w.offset - guess) <= engine.SAME_OFFSET_S else 0.0)
    return bool(scores) and float(np.median(scores)) >= engine.SAME_DUB_SCORE


def source_rank(info: dict) -> tuple:
    return (info.get("channels") or 0, _CODEC_RANK.get(info.get("codec") or "", 0))


def cluster(versions: list[dict], duration: float, progress=None) -> list[dict]:
    """``versions`` = [{id, path, analysis (None for the target), audio: [...]}] → dubs:
    [{id, lang, name, members: [{version_id, track, codec, channels, title, language}]}]."""
    tracks = []
    for v in versions:
        for a in v["audio"]:
            tracks.append({"version_id": v["id"], "path": v["path"], "track": a["index"], "analysis": v["analysis"],
                           "info": a, "target": v["analysis"] is None, "usable": v.get("usable", True)})
    # the target's tracks first (they name the groups), then the better sources
    tracks.sort(key=lambda x: (not x["target"], [-r for r in source_rank(x["info"])]))
    dubs: list[dict] = []
    for k, t in enumerate(tracks):
        lang = engine.lang_code(t["info"].get("language", ""))
        home = None
        if t["usable"]:
            for d in dubs:
                if d["lang"] != lang or not d["rep"]["usable"]:
                    continue
                # a version never holds one dub twice under two different groups, unless it really has two copies
                if same_dub(d["rep"], t, duration):
                    home = d
                    break
        if home is None:
            home = {"lang": lang, "rep": t, "members": []}
            dubs.append(home)
        home["members"].append(t)
        if progress:
            progress("compare", k + 1, len(tracks))
    out = []
    for i, d in enumerate(dubs):
        title = next((m["info"].get("title") for m in d["members"] if (m["info"].get("title") or "").strip()), "")
        out.append({
            "id": i, "lang": d["lang"], "name": title.replace(" (Lumina sync)", "").strip(),
            "members": [{"version_id": m["version_id"], "track": m["track"], "codec": m["info"].get("codec"),
                         "channels": m["info"].get("channels"), "title": m["info"].get("title"),
                         "language": m["info"].get("language")} for m in d["members"]],
        })
    # name the unnamed ones "CZ dabing 1, 2 …" within a language
    counts: dict[str, int] = {}
    for d in out:
        if not d["name"]:
            counts[d["lang"]] = counts.get(d["lang"], 0) + 1
            d["name"] = f"{(d['lang'] or '?').upper()} {counts[d['lang']]}"
    return out
