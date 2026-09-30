"""Step 2: put the other version's audio track into the reference file (decisions/0007).

- Same speed: the track is copied as it is (mkvmerge ``--sync`` shifts it), nothing is re-encoded.
- Different speed (PAL …): the track is re-encoded to AC-3 with ffmpeg ``atempo`` first — players
  handle a stretched timestamp track badly.
- Different cut: not yet (step 3).

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


class TransferError(Exception):
    pass


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


def mux(ref_path: str, added: list[dict], out_path: str, progress=None) -> None:
    """Reference file as it is + the added tracks ({file, tid, delay_ms, language, name}); the first
    added track becomes the default audio."""
    cmd = ["mkvmerge", "--gui-mode", "-o", out_path]
    for tid in audio_track_ids(ref_path):
        cmd += ["--default-track-flag", f"{tid}:0"]
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


def verify(out_path: str, ref_track: int, new_track: int, duration: float) -> list[engine.Window]:
    """The new track must line up with the reference track of the same file."""
    # the middle of the film — logos and credits are the least alike
    positions = [float(x) for x in np.linspace(duration * 0.08, duration * 0.88, VERIFY_WINDOWS)]
    windows = [engine._measure(out_path, ref_track, out_path, new_track, at, 1.0) for at in positions]
    good = [w for w in windows if w.good]
    typical = float(np.median([abs(w.offset) for w in good])) if good else 99.0
    if len(good) < VERIFY_WINDOWS - 1 or typical > VERIFY_TYPICAL or any(abs(w.offset) > VERIFY_MAX for w in good):
        offs = ", ".join(f"{w.offset:+.2f}" for w in good) or "žádná shoda"
        raise TransferError(f"Kontrola výsledku neprošla (posuny {offs} s) — soubor nepoužit")
    return windows


def transfer(ref_path: str, ref_track: int, other_path: str, other_tracks: int | list[int], analysis: dict,
             workdir: Path, out_name: str, progress=None) -> str:
    """Builds the new file in ``workdir`` and checks it → its path. Several tracks of the other file
    can go at once (they share its timing, so one analysis is enough)."""
    if analysis.get("verdict") not in ("constant", "speed"):
        raise TransferError("Přenést jde zatím jen zvuk, který sedí celý (jiný střih přijde později)")
    tracks = [other_tracks] if isinstance(other_tracks, int) else list(other_tracks)
    other = engine.probe(other_path)
    if not tracks or any(t >= len(other["audio"]) for t in tracks):
        raise TransferError("Zvuková stopa ve zdrojové verzi nenalezena")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = str(workdir / out_name)

    if progress:
        progress("prepare", 0, 1)
    ref = engine.probe(ref_path)
    added = []
    for t in tracks:
        info = other["audio"][t]
        file, tid, delay_ms = prepare_audio(other_path, t, analysis["speed"], analysis["offset"], workdir,
                                            info.get("channels") or 2, ref["start"], other["start"])
        added.append({"file": file, "tid": tid, "delay_ms": delay_ms, "language": info.get("language") or "",
                      "name": f"{(info.get('language') or '?').upper()} (Lumina sync)"})
    mux(ref_path, added, out_path, progress)
    for a in added:
        if a["file"] != other_path:
            os.remove(a["file"])

    if progress:
        progress("verify", 0, 1)
    duration = engine.probe(out_path)["duration"]
    for i in range(len(added)):
        verify(out_path, ref_track, len(ref["audio"]) + i, duration)
    logger.info("audiosync: %s built (%d tracks, delay %d ms, speed %.5f)", out_name, len(added),
                added[0]["delay_ms"], analysis["speed"])
    return out_path
