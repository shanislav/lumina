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
NEAR_CUT_S = (3, 15, 30)      # where next to a cut the result is checked (the first trustworthy one counts)


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
            start = speed * p["start"] + p["offset"]
            lead = 0.0
            if start < 0:                      # the other version starts later — silence first
                lead, start = -start / speed, 0.0
            args += ["-ss", f"{start:.3f}", "-t", f"{speed * (length - lead):.3f}", "-i", other_path]
            chain = f"[{n}:a:{other_track}]"
            chain += f"atempo={speed:.8f}," if abs(speed - 1) > 1e-9 else ""
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


def mux(ref_path: str, added: list[dict], out_path: str, progress=None, ref_keep: list[int] | None = None) -> None:
    """Reference file + the added tracks ({file, tid, delay_ms, language, name}); the first added track
    becomes the default audio. ``ref_keep``: audio tracks of the reference to keep (None = all)."""
    cmd = ["mkvmerge", "--gui-mode", "-o", out_path]
    ids = audio_track_ids(ref_path)
    kept = ids if ref_keep is None else [ids[i] for i in ref_keep]
    if ref_keep is not None:
        cmd += ["--audio-tracks", ",".join(str(t) for t in kept)]
    for tid in kept:
        cmd += ["--default-track-flag", f"{tid}:{0 if added else (1 if tid == kept[0] else 0)}"]
    cmd.append(ref_path)
    by_file: dict[str, list[dict]] = {}
    for t in added:
        by_file.setdefault(t["file"], []).append(t)
    first = True
    for path, tracks in by_file.items():
        cmd += ["--no-video", "--no-subtitles", "--no-chapters", "--no-attachments", "--no-global-tags",
                "--audio-tracks", ",".join(str(t["tid"]) for t in tracks)]
        for t in tracks:
            tid = t["tid"]
            cmd += ["--sync", f"{tid}:{t['delay_ms']}", "--track-name", f"{tid}:{t['name']}",
                    "--default-track-flag", f"{tid}:{1 if first else 0}"]
            if t["language"]:
                cmd += ["--language", f"{tid}:{t['language']}"]
            first = False
        cmd.append(path)
    _run_mkvmerge(cmd, progress)


def verify(out_path: str, ref_track: int, new_track: int, duration: float,
           pieces: list[dict] | None = None) -> list[engine.Window]:
    """The new track must line up with the reference track of the same file."""
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
    windows = [engine._measure(out_path, ref_track, out_path, new_track, at, 1.0) for at in positions]
    good = [w for w in windows if w.good]
    typical = float(np.median([abs(w.offset) for w in good])) if good else 99.0
    if len(good) < VERIFY_WINDOWS - 1 or typical > VERIFY_TYPICAL or any(abs(w.offset) > VERIFY_MAX for w in good):
        offs = ", ".join(f"{w.offset:+.2f}" for w in good) or "žádná shoda"
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
                w = engine._measure(out_path, ref_track, out_path, new_track, at, 1.0)
                windows.append(w)
                if not w.good:
                    continue
                if abs(w.offset) > VERIFY_MAX:
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


def track_name(info: dict) -> str:
    """The original title keeps the dub apart („CZ dabing Nova“), else the language."""
    title = (info.get("title") or "").strip()
    lang = (info.get("language") or "?").upper()
    return f"{title} (Lumina sync)" if title else f"{lang} (Lumina sync)"


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


def transfer_many(ref_path: str, ref_track: int, sources: list[dict], workdir: Path, out_name: str,
                  progress=None, report: dict | None = None, ref_keep: list[int] | None = None,
                  dedupe: bool = True) -> str:
    """Tracks from several versions into the reference in one go: ``sources`` =
    [{path, tracks, analysis}] (analysis = reference → that version). One mux, every added track checked."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = str(workdir / out_name)
    if progress:
        progress("prepare", 0, 1)
    ref = engine.probe(ref_path)
    if ref_keep is not None and ref_track not in ref_keep:
        raise TransferError("Stopa, se kterou se porovnává, musí zůstat")
    added, checks = [], []
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
            if cut:
                file, tid, delay_ms = assemble_audio(src["path"], t, analysis["pieces"], analysis["speed"], srcdir,
                                                     info.get("channels") or 2, ref["start"])
            else:
                file, tid, delay_ms = prepare_audio(src["path"], t, analysis["speed"], analysis["offset"], srcdir,
                                                    info.get("channels") or 2, ref["start"], other["start"])
            added.append({"file": file, "tid": tid, "delay_ms": delay_ms, "language": info.get("language") or "",
                          "name": track_name(info), "source": src["path"]})
            checks.append(analysis.get("pieces") if cut else None)
    if not added:
        raise NothingToAdd("Tyto dabingy už soubor má — není co přidat")
    if report is not None:
        report["added"] = [a["name"] for a in added]
    mux(ref_path, added, out_path, progress, ref_keep)
    for a in added:
        if a["file"] != a["source"]:
            os.remove(a["file"])

    if progress:
        progress("verify", 0, 1)
    duration = engine.probe(out_path)["duration"]
    kept = list(range(len(ref["audio"]))) if ref_keep is None else sorted(ref_keep)
    for i, pieces in enumerate(checks):
        verify(out_path, kept.index(ref_track), len(kept) + i, duration, pieces)
    logger.info("audiosync: %s built (%d tracks from %d versions)", out_name, len(added), len(sources))
    return out_path


def strip(ref_path: str, keep: list[int], workdir: Path, out_name: str, progress=None) -> str:
    """A copy of the file without the audio tracks not in ``keep`` (nothing re-encoded)."""
    ref = engine.probe(ref_path)
    keep = sorted(set(keep))
    if not keep:
        raise TransferError("Aspoň jedna zvuková stopa musí zůstat")
    if any(k >= len(ref["audio"]) for k in keep):
        raise TransferError("Neznámá zvuková stopa")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = str(workdir / out_name)
    mux(ref_path, [], out_path, progress, keep)
    if len(engine.probe(out_path)["audio"]) != len(keep):
        raise TransferError("Výsledek nemá očekávané stopy — soubor nepoužit")
    return out_path
