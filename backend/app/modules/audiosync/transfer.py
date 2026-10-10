"""Step 2: put the other version's audio track into the reference file (decisions/0007).

- Same speed: the track is copied as it is (mkvmerge ``--sync`` shifts it), nothing is re-encoded.
- Different speed (PAL …): the track is re-encoded to AC-3 with ffmpeg ``atempo`` first — players
  handle a stretched timestamp track badly.
- Different cut: the track is assembled from the pieces of the analysis (ffmpeg: every piece from its
  own place, silence where the other version lacks the scene) and encoded to AC-3.

The result is a new MKV (video and all tracks of the reference + the new track, marked default).
Before it goes anywhere it is checked: the new track must line up with the reference track in the
same file (offset ≈ 0). Then the library takes it over like a finished download.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from app.modules.audiosync import analyze as engine

logger = logging.getLogger(__name__)

VERIFY_WINDOWS = 6
VERIFY_TYPICAL = 0.06         # seconds — the typical (median) offset must be within lip-sync tolerance
VERIFY_MAX = 0.2              # single pieces may be noisier (measured the same way as the analysis)
NEAR_CUT_S = (3, 15, 30, 45, 60)  # where next to a cut the result is checked (the first trustworthy one counts)


class TransferError(Exception):
    pass


class NothingToAdd(TransferError):
    """Every chosen track is a dub the reference already has."""


def mkv_tracks(path: str) -> list[dict]:
    out = subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True, timeout=120).stdout
    try:
        return json.loads(out).get("tracks", [])
    except ValueError:
        raise TransferError(f"mkvmerge nepřečetl {os.path.basename(path)}")


def audio_track_ids(path: str) -> list[int]:
    """mkvmerge track ids of the audio tracks, in the order ffprobe counts them."""
    return [t["id"] for t in mkv_tracks(path) if t.get("type") == "audio"]


def _run_mkvmerge(cmd: list[str], progress=None) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.strip()
        m = re.match(r"#GUI#progress (\d+)%", line)
        if m and progress:
            progress("mux", int(m.group(1)), 100)
        elif line:
            lines.append(line)
    code = proc.wait()
    if code >= 2:   # 1 = warnings only
        raise TransferError("mkvmerge selhal: " + " / ".join(lines[-3:]))


def prepare_audio(other_path: str, other_track: int, speed: float, offset: float, workdir: Path,
                  channels: int, ref_start: float = 0.0, other_start: float = 0.0) -> tuple[str, int, int]:
    """→ (file with the track, its mkvmerge track id, delay in ms for mkvmerge --sync).

    Mapping from the analysis (times from the start of each file): t_other = speed · t_ref + offset.
    Files may start at a timestamp > 0 (e.g. 0.417 s); mkvmerge keeps real timestamps, so a copied
    track needs delay = −(offset + other_start − speed · ref_start). A re-encoded track starts at 0
    and already runs at the reference speed: delay = ref_start − offset / speed."""
    if abs(speed - 1) < 1e-9:
        ids = audio_track_ids(other_path)
        if other_track >= len(ids):
            raise TransferError("Zvuková stopa ve zdrojové verzi nenalezena")
        return other_path, ids[other_track], round(-(offset + other_start - ref_start) * 1000)
    out = workdir / f"audio-{other_track}.mka"
    bitrate = "640k" if channels > 2 else "224k"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", other_path, "-map", f"0:a:{other_track}",
         "-af", f"atempo={speed:.8f}", "-c:a", "ac3", "-b:a", bitrate, str(out)],
        check=True, capture_output=True, timeout=3 * 3600)
    return str(out), 0, round((ref_start - offset / speed) * 1000)


_LAYOUTS = {1: "mono", 2: "stereo", 6: "5.1"}


def assemble_audio(other_path: str, other_track: int, pieces: list[dict], speed: float, workdir: Path,
                   channels: int, ref_start: float = 0.0) -> tuple[str, int, int]:
    """A track for a different cut: each piece of the reference timeline is taken from the other file
    at its own offset (silence where the other version has nothing) → (file, track id, delay ms).
    Times of the pieces are from the start of the reference; the result starts at reference 0."""
    layout = _LAYOUTS.get(channels, "5.1" if channels > 2 else "stereo")
    args, labels, filters = ["ffmpeg", "-nostdin", "-v", "error", "-y"], [], []
    n = 0
    for p in pieces:
        length = p["end"] - p["start"]
        if length <= 0.01:
            continue
        if p.get("offset") is None:
            args += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", f"anullsrc=r=48000:cl={layout}"]
            filters.append(f"[{n}:a]aformat=sample_rates=48000:channel_layouts={layout}[p{n}]")
        else:
            # a drifting piece runs at its own speed: t_other = (speed + slope)·t + …
            local = speed + p.get("slope", 0.0)
            start = speed * p["start"] + p["offset"]
            lead = 0.0
            if start < 0:                      # the other version starts later — silence first
                lead, start = -start / local, 0.0
            args += ["-ss", f"{start:.3f}", "-t", f"{local * (length - lead):.3f}", "-i", other_path]
            chain = f"[{n}:a:{other_track}]"
            chain += f"atempo={local:.8f}," if abs(local - 1) > 1e-9 else ""
            chain += f"aresample=48000,aformat=sample_rates=48000:channel_layouts={layout}"
            if lead:
                chain += f",adelay={int(lead * 1000)}:all=1"
            # exact length: pad a short end, cut a long one
            chain += f",apad=whole_dur={length:.3f},atrim=0:{length:.3f}[p{n}]"
            filters.append(chain)
        labels.append(f"[p{n}]")
        n += 1
    if not labels:
        raise TransferError("Nic k poskládání")
    graph = ";".join(filters) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    out = workdir / f"audio-{other_track}.mka"
    bitrate = "640k" if channels > 2 else "224k"
    subprocess.run(args + ["-filter_complex", graph, "-map", "[out]", "-c:a", "ac3", "-b:a", bitrate, str(out)],
                   check=True, capture_output=True, timeout=3 * 3600)
    return str(out), 0, round(ref_start * 1000)


def mux(ref_path: str, added: list[dict], out_path: str, progress=None, ref_keep: list[int] | None = None,
        ref_names: dict[int, str] | None = None, default: int | None = None) -> None:
    """Reference file + the added tracks ({file, tid, delay_ms, language, name}). ``ref_keep``: audio
    tracks of the reference to keep (None = all); ``ref_names``: new names of the reference's tracks
    (by audio index); ``default``: the default audio track by its position in the result (kept
    tracks, then added ones) — None = the first added one, without any the first kept one."""
    cmd = ["mkvmerge", "--gui-mode", "-o", out_path]
    ids = audio_track_ids(ref_path)
    keep = list(range(len(ids))) if ref_keep is None else list(ref_keep)
    kept = [ids[i] for i in keep]
    if ref_keep is not None:
        cmd += ["--audio-tracks", ",".join(str(t) for t in kept)]
    if default is None:
        default = len(kept) if added else 0
    for pos, (i, tid) in enumerate(zip(keep, kept)):
        cmd += ["--default-track-flag", f"{tid}:{1 if pos == default else 0}"]
        if (ref_names or {}).get(i):
            cmd += ["--track-name", f"{tid}:{ref_names[i]}"]
    cmd.append(ref_path)
    by_file: dict[str, list[dict]] = {}
    for t in added:
        by_file.setdefault(t["file"], []).append(t)
    pos = len(kept)
    for path, tracks in by_file.items():
        cmd += ["--no-video", "--no-subtitles", "--no-chapters", "--no-attachments", "--no-global-tags",
                "--audio-tracks", ",".join(str(t["tid"]) for t in tracks)]
        for t in tracks:
            tid = t["tid"]
            cmd += ["--sync", f"{tid}:{t['delay_ms']}", "--track-name", f"{tid}:{t['name']}",
                    "--default-track-flag", f"{tid}:{1 if pos == default else 0}"]
            if t["language"]:
                cmd += ["--language", f"{tid}:{t['language']}"]
            pos += 1
        cmd.append(path)
    _run_mkvmerge(cmd, progress)


def verify(out_path: str, ref_track: int, new_track: int, duration: float,
           pieces: list[dict] | None = None, expect: float = 0.0) -> list[engine.Window]:
    """The new track must line up with the reference track of the same file — or sit where it is meant to
    (``expect`` / a piece's ``expect``: placed by the picture, the reference itself being off its picture)."""

    def meant(at: float) -> float:
        for p in pieces or []:
            if p["start"] <= at < p["end"] and p.get("offset") is not None:
                return p.get("expect", 0.0)
        return expect
    # the middle of the film — logos and credits are the least alike
    positions = [float(x) for x in np.linspace(duration * 0.08, duration * 0.88, VERIFY_WINDOWS)]
    if pieces and len(pieces) > 1:
        # not across a cut or in a stretch the other version lacks: inside pieces with audio
        inside = [(p["start"] + 30, p["end"] - engine.WINDOW_S - 30) for p in pieces if p.get("offset") is not None]
        inside = [(a, b) for a, b in inside if b > a]
        total = sum(b - a for a, b in inside)
        positions = []
        for k in range(VERIFY_WINDOWS):
            x = total * (k + 0.5) / VERIFY_WINDOWS
            for a, b in inside:
                if x <= b - a:
                    positions.append(a + x)
                    break
                x -= b - a
    windows = []
    errors = []
    for at in positions:
        w = engine._measure(out_path, ref_track, out_path, new_track, at, 1.0, meant(at))
        for alt in (at + 60, at - 60):          # a quiet place cannot be measured — try next to it
            if w.good or not (0 < alt < duration - engine.WINDOW_S):
                continue
            w = engine._measure(out_path, ref_track, out_path, new_track, alt, 1.0, meant(alt))
        windows.append(w)
        if w.good:
            errors.append(w.offset - meant(w.at))
    good = [w for w in windows if w.good]
    typical = float(np.median([abs(e) for e in errors])) if errors else 99.0
    if len(good) < VERIFY_WINDOWS - 1 or typical > VERIFY_TYPICAL or any(abs(e) > VERIFY_MAX for e in errors):
        offs = ", ".join(f"{e:+.2f}" for e in errors) or "žádná shoda"
        raise TransferError(f"Kontrola výsledku neprošla (posuny {offs} s) — soubor nepoužit")
    # a different cut: next to every cut the audio must fit too. A quiet stretch cannot be measured,
    # so a few places are tried; any trustworthy one that is off fails, and so does none at all.
    for p in (pieces or []) if pieces and len(pieces) > 1 else []:
        if p.get("offset") is None:
            continue
        sides = []
        if p["start"] > 0:
            sides.append([p["start"] + d for d in NEAR_CUT_S if p["start"] + d + engine.WINDOW_S <= p["end"]])
        if p["end"] < duration - 1:
            sides.append([p["end"] - d - engine.WINDOW_S for d in NEAR_CUT_S if p["end"] - d - engine.WINDOW_S >= p["start"]])
        for spots in sides:
            confirmed = False
            for at in spots:
                w = engine._measure(out_path, ref_track, out_path, new_track, at, 1.0, p.get("expect", 0.0))
                windows.append(w)
                err = abs(w.offset - p.get("expect", 0.0))
                if not w.good:
                    # dialogue after an ad break: two dubs differ, the match stays weak — but one that lands
                    # right where the track is meant to be is no chance
                    if w.score >= engine.GOOD_SCORE and err <= VERIFY_TYPICAL:
                        confirmed = True
                        break
                    continue
                if err > VERIFY_MAX:
                    raise TransferError(f"U střihu kolem {_clock(at)} zvuk nesedí ({w.offset:+.2f} s) — soubor nepoužit")
                confirmed = True
                break
            if spots and not confirmed:
                raise TransferError(f"U střihu kolem {_clock(spots[0])} nejde ověřit, že zvuk sedí — soubor nepoužit")
    return windows


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}"


def distinct_tracks(ref_path: str, ref: dict, other_path: str, other: dict, tracks: list[int], analysis: dict,
                    report: dict | None = None, ref_keep: list[int] | None = None) -> list[int]:
    """The chosen tracks without dubs the reference already has (among the kept tracks), and without
    repeating one dub (5.1 + 2.0 of the same). Different dubs of one language (TV stations …) all stay."""
    keep: list[int] = []
    identity = {"speed": 1.0, "offset": 0.0}
    ref_tracks = [r for r in ref["audio"] if ref_keep is None or r["index"] in ref_keep]
    for t in tracks:
        lang = engine.lang_code(other["audio"][t].get("language", ""))
        twin = next((r["index"] for r in ref_tracks if engine.lang_code(r.get("language", "")) == lang
                     and engine.same_audio(ref_path, r["index"], other_path, t, analysis, ref["duration"])), None)
        if twin is None:
            twin = next((k for k in keep if engine.lang_code(other["audio"][k].get("language", "")) == lang
                         and engine.same_audio(other_path, k, other_path, t, identity, other["duration"])), None)
            if twin is not None and report is not None:
                report.setdefault("skipped", []).append({"track": t, "reason": f"stejný dabing jako stopa {twin + 1}"})
        elif report is not None:
            report.setdefault("skipped", []).append({"track": t, "reason": "tento dabing soubor už má"})
        if twin is None:
            keep.append(t)
    return keep


# a title that names the codec and the channels says enough ("DD 5.1 CZ", "Eng DTS 6ch 48kHz - 1510 kbps")
_CODEC_WORD = re.compile(r"\b(e-?ac-?3|ac-?3|dd\+?|dts(-hd)?|truehd|atmos|aac|flac|opus|mp3|pcm|lpcm)\b", re.IGNORECASE)
_CHANNEL_WORD = re.compile(r"\b(\d\.\d|\d\s*ch|stereo|mono)\b", re.IGNORECASE)


def telling(title: str) -> bool:
    return bool(_CODEC_WORD.search(title) and _CHANNEL_WORD.search(title))


def track_name(info: dict, moved: bool = True) -> str | None:
    """A telling name: "CZ Nova 5.1 AC3 448 kbps" — always for a moved or fixed track (``moved``).
    A track of the file keeps a telling title ("Eng DTS 6ch 48kHz - 1510 kbps - 24bit") — None;
    a poor one ("cze 2.0", "Stereo", empty, an old Lumina mark) gets the details. A track without a
    language tag keeps its name."""
    from app.modules.audiosync.filmmap import OLD_MARKS, channels_label, codec_label, dub_name
    original = info.get("title") or ""
    title = OLD_MARKS.sub(" ", original).strip()
    lang = engine.lang_code(info.get("language", ""))
    if not moved and (not lang or (telling(title) and title == original)):
        return None
    kbps = round((info.get("bitrate") or 0) / 1000)
    parts = (dub_name(lang, title) if lang else "?", channels_label(info.get("channels")),
             codec_label(info.get("codec"), info.get("profile")), f"{kbps} kbps" if kbps else "")
    return " ".join(x for x in parts if x)


def rename_tracks(path: str, names: dict[int, str], languages: dict[int, str] | None = None) -> None:
    """Names and language tags, written into the header in place (audio position → name / ISO 639-2)."""
    languages = languages or {}
    if not names and not languages:
        return
    cmd = ["mkvpropedit", path]
    for pos in sorted(set(names) | set(languages)):
        cmd += ["--edit", f"track:a{pos + 1}"]
        if pos in names:
            cmd += ["--set", f"name={names[pos]}"]
        if pos in languages:
            cmd += ["--set", f"language={languages[pos]}"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode >= 2:
        raise TransferError("Přejmenování stop selhalo: " + (out.stdout or out.stderr).strip()[-200:])


def ref_track_names(ref: dict) -> dict[int, str]:
    """New names for the reference's own tracks whose title says too little."""
    return {a["index"]: n for a in ref["audio"] if (n := track_name(a, moved=False))}


def transfer(ref_path: str, ref_track: int, other_path: str, other_tracks: int | list[int], analysis: dict,
             workdir: Path, out_name: str, progress=None, report: dict | None = None,
             ref_keep: list[int] | None = None, dedupe: bool = True) -> str:
    """Builds the new file in ``workdir`` and checks it → its path. Several tracks of the other file
    can go at once (they share its timing, so one analysis is enough); dubs the reference already
    has are left out (``report["skipped"]``). ``ref_keep``: the reference's audio tracks to keep
    (None = all) — to drop unwanted ones, or a track that is being replaced by its fixed copy."""
    tracks = [other_tracks] if isinstance(other_tracks, int) else list(other_tracks)
    return transfer_many(ref_path, ref_track, [{"path": other_path, "tracks": tracks, "analysis": analysis}],
                         workdir, out_name, progress, report, ref_keep, dedupe)


def reencoded(analysis: dict) -> bool:
    """A track for another cut or speed is re-encoded (AC-3); one with the same timing is copied."""
    return analysis.get("verdict") == "cuts" or abs(analysis.get("speed", 1.0) - 1) > 1e-9


def as_added(info: dict, analysis: dict) -> dict:
    """The track as it ends up in the result (a re-encoded one is AC-3 640/224 kbps)."""
    if not reencoded(analysis):
        return info
    ch = info.get("channels") or 2
    return {**info, "codec": "ac3", "profile": "", "bitrate": 640000 if ch > 2 else 224000}


def output_order(items: list, file_of, tid_of) -> list:
    """The order mkvmerge writes added tracks in: by input file (first appearance), within one file
    by track id. The plan shown to the user and the checks after muxing use the same order."""
    files: list = []
    for x in items:
        if file_of(x) not in files:
            files.append(file_of(x))
    return sorted(items, key=lambda x: (files.index(file_of(x)), tid_of(x)))


def transfer_many(ref_path: str, ref_track: int, sources: list[dict], workdir: Path, out_name: str,
                  progress=None, report: dict | None = None, ref_keep: list[int] | None = None,
                  dedupe: bool = True, default: int | None = None) -> str:
    """Tracks from several versions into the reference in one go: ``sources`` =
    [{path, tracks, analysis}] (analysis = reference → that version). One mux, every added track checked."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = str(workdir / out_name)
    if progress:
        progress("prepare", 0, 1)
    ref = engine.probe(ref_path)
    if ref_keep is not None and ref_track not in ref_keep:
        raise TransferError("Stopa, se kterou se porovnává, musí zůstat")
    added = []
    for k, src in enumerate(sources):
        analysis = src["analysis"]
        cut = analysis.get("verdict") == "cuts"
        if analysis.get("verdict") not in ("constant", "speed", "cuts") or (cut and not analysis.get("pieces")):
            raise TransferError("Zvuk k tomuto obrazu nesedí — není co přenést")
        other = engine.probe(src["path"])
        tracks = list(src["tracks"])
        if not tracks or any(t >= len(other["audio"]) for t in tracks):
            raise TransferError("Zvuková stopa ve zdrojové verzi nenalezena")
        if dedupe:
            tracks = distinct_tracks(ref_path, ref, src["path"], other, tracks, analysis, report, ref_keep)
        srcdir = workdir / f"src{k}"
        srcdir.mkdir(exist_ok=True)
        for t in tracks:
            info = other["audio"][t]
            if src.get("language") and info.get("language") in ("", "und"):
                # an AVI keeps no language; Lumina knows it (the user said, a show's episodes)
                info = {**info, "language": src["language"]}
            if cut:
                file, tid, delay_ms = assemble_audio(src["path"], t, analysis["pieces"], analysis["speed"], srcdir,
                                                     info.get("channels") or 2, ref["start"])
            else:
                file, tid, delay_ms = prepare_audio(src["path"], t, analysis["speed"], analysis["offset"], srcdir,
                                                    info.get("channels") or 2, ref["start"], other["start"])
            info = as_added(info, analysis)
            added.append({"file": file, "tid": tid, "delay_ms": delay_ms, "language": info.get("language") or "",
                          "name": track_name(info, moved=True), "source": src["path"],
                          "check": (analysis.get("pieces") if cut else None, analysis.get("expect", 0.0))})
    if not added:
        raise NothingToAdd("Tyto dabingy už soubor má — není co přidat")
    added = output_order(added, lambda a: a["file"], lambda a: a["tid"])
    checks = [a.pop("check") for a in added]
    if report is not None:
        report["added"] = [a["name"] for a in added]
    mux(ref_path, added, out_path, progress, ref_keep, ref_track_names(ref), default)
    for a in added:
        if a["file"] != a["source"]:
            os.remove(a["file"])

    if progress:
        progress("verify", 0, 1)
    duration = engine.probe(out_path)["duration"]
    kept = list(range(len(ref["audio"]))) if ref_keep is None else sorted(ref_keep)
    for i, (pieces, expect) in enumerate(checks):
        verify(out_path, kept.index(ref_track), len(kept) + i, duration, pieces, expect)
    logger.info("audiosync: %s built (%d tracks from %d versions)", out_name, len(added), len(sources))
    return out_path


def strip(ref_path: str, keep: list[int], workdir: Path, out_name: str, progress=None,
          default: int | None = None) -> str:
    """A copy of the file without the audio tracks not in ``keep`` (nothing re-encoded)."""
    ref = engine.probe(ref_path)
    keep = sorted(set(keep))
    if not keep:
        raise TransferError("Aspoň jedna zvuková stopa musí zůstat")
    if any(k >= len(ref["audio"]) for k in keep):
        raise TransferError("Neznámá zvuková stopa")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = str(workdir / out_name)
    mux(ref_path, [], out_path, progress, keep, ref_track_names(ref), default)
    if len(engine.probe(out_path)["audio"]) != len(keep):
        raise TransferError("Výsledek nemá očekávané stopy — soubor nepoužit")
    return out_path
