"""The audio of a whole film across its versions: which dubs exist, where, and which are the same.

For a target version (the picture to keep), every other version is aligned to it (the usual
analysis). Then every audio track of every version is placed on the target's timeline and tracks
of one language are compared: the same dub (also 5.1 vs 2.0, another encode) correlates ~0.8–0.9,
different dubs ~0.1–0.4 (decisions/0007). The result is a table: rows = dubs, columns = versions.
"""

import logging
import re

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


# words in track titles that only describe the technical side or the language
_TECH = re.compile(
    r"\b(ac-?3|e-?ac-?3|dd\+?|dts(-hd)?|ma|hra|truehd|atmos|aac|flac|opus|mp3|pcm|lpcm|\d+(\.\d)?\s*ch|"
    r"\d(\.\d)?|\d+\s*k(bps|hz)?|kbps|khz|\d+\s*bits?|bit|stereo|mono|surround|lumina\s+sync|"
    r"cze?|ces|czech|česky|cz|slo|slk|slovak|slovensky|sk|eng?|english|en|dabing|dub(bing)?|audio|track|stopa)\b",
    re.IGNORECASE)


# marks older Lumina versions put into names of moved tracks — dropped from names now
OLD_MARKS = re.compile(r"\[L\]|\(lumina sync\)", re.IGNORECASE)


_CODEC_NAME = {"ac3": "AC3", "eac3": "EAC3", "dts": "DTS", "truehd": "TrueHD", "aac": "AAC", "flac": "FLAC",
               "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis"}


def codec_label(codec: str | None, profile: str | None = None) -> str:
    """"DTS-HD MA", "EAC3", "AC3" …"""
    if codec == "dts" and profile and profile.upper().startswith("DTS-HD"):
        return profile
    if (codec or "").startswith("pcm"):
        return "PCM"
    return _CODEC_NAME.get(codec or "", (codec or "").upper())


def channels_label(channels: int | None) -> str:
    return {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(channels or 0, f"{channels}ch" if channels else "")


def dub_name(lang: str, title: str) -> str:
    """"CZ" or "CZ Nova" — the language plus whatever in the title is not codec/channels/language."""
    rest = _TECH.sub(" ", OLD_MARKS.sub(" ", title or ""))
    rest = re.sub(r"[\s\-_,.;:/|()\[\]]+", " ", rest).strip()
    code = {"cs": "CZ"}.get(lang, (lang or "?").upper())
    return f"{code} {rest}" if len(rest) >= 2 else code


def track_delta(target_path: str, ref_track: int, path: str, track: int, analysis: dict,
                duration: float) -> tuple[float, bool]:
    """How much later than the version's mapping this particular track sits → (delta s, fits).
    Six windows near the expected place; a track that matches nowhere does not fit."""
    speed = analysis.get("speed", 1.0)
    found = []
    for t in np.linspace(duration * 0.12, duration * 0.88, 6):
        off = engine.mapping_at(analysis, float(t))
        if off is None:
            continue
        w = engine._measure(target_path, ref_track, path, track, float(t), speed, off, margin=10.0)
        if w.good:
            found.append(w.offset - off)
    if len(found) < 3:
        return 0.0, False
    delta = float(np.median(found))
    spread = max(abs(d - delta) for d in found)
    return (round(delta, 3) if abs(delta) > 0.04 else 0.0), spread <= 0.15


def source_rank(info: dict) -> tuple:
    return (info.get("channels") or 0, _CODEC_RANK.get(info.get("codec") or "", 0))


def cluster(versions: list[dict], duration: float, progress=None) -> list[dict]:
    """``versions`` = [{id, path, analysis (None for the target), audio: [...]}] → dubs:
    [{id, lang, name, members: [{version_id, track, codec, channels, title, language}]}]."""
    tracks = []
    for v in versions:
        fits = v.get("tracks") or {}
        for a in v["audio"]:
            tf = fits.get(a["index"]) or {"delta": 0.0, "ok": True}
            if tf.get("own"):                  # measured on its own against the reference track
                analysis = tf["own"]
            elif v["analysis"] is None:
                analysis = None
            else:
                analysis = engine.shifted(v["analysis"], tf["delta"])
            tracks.append({"version_id": v["id"], "path": v["path"], "track": a["index"], "analysis": analysis,
                           "info": a, "target": v.get("target", v["analysis"] is None),
                           "usable": v.get("usable", True) and (tf["ok"] or bool(tf.get("own")))})
    # the target's tracks first (they name the groups), then the better sources
    tracks.sort(key=lambda x: (not x["target"], [-r for r in source_rank(x["info"])]))
    dubs: list[dict] = []
    for k, t in enumerate(tracks):
        lang = engine.lang_code(t["info"].get("language", ""))
        home = None
        if t["usable"]:
            for d in dubs:
                # a track without a language tag can be any dub — compare it with all of them
                if (lang and d["lang"] and d["lang"] != lang) or not d["rep"]["usable"]:
                    continue
                # a version never holds one dub twice under two different groups, unless it really has two copies
                if same_dub(d["rep"], t, duration):
                    home = d
                    break
        if home is None:
            home = {"lang": lang, "rep": t, "members": []}
            dubs.append(home)
        elif not home["lang"] and lang:
            home["lang"] = lang                  # the group learns its language from a tagged member
        home["members"].append(t)
        if progress:
            progress("compare", k + 1, len(tracks))
    out = []
    for i, d in enumerate(dubs):
        names = [dub_name(d["lang"], m["info"].get("title") or "") for m in d["members"]]
        # the most telling title of the members ("CZ Nova" beats "CZ")
        out.append({
            "id": i, "lang": d["lang"], "name": max(names, key=len),
            "members": [{"version_id": m["version_id"], "track": m["track"], "codec": m["info"].get("codec"),
                         "channels": m["info"].get("channels"), "bitrate": m["info"].get("bitrate"), "title": m["info"].get("title"),
                         "language": m["info"].get("language")} for m in d["members"]],
        })
    # two different dubs with the same name get numbers ("CZ 1", "CZ 2")
    seen: dict[str, int] = {}
    for d in out:
        seen[d["name"]] = seen.get(d["name"], 0) + 1
    counter: dict[str, int] = {}
    for d in out:
        if seen[d["name"]] > 1:
            counter[d["name"]] = counter.get(d["name"], 0) + 1
            d["name"] = f"{d['name']} {counter[d['name']]}"
    return out
