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
    out = workdir / "audio.mka"
    bitrate = "640k" if channels > 2 else "224k"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", other_path, "-map", f"0:a:{other_track}",
         "-af", f"atempo={speed:.8f}", "-c:a", "ac3", "-b:a", bitrate, str(out)],
        check=True, capture_output=True, timeout=3 * 3600)
    return str(out), 0, round((ref_start - offset / speed) * 1000)


def mux(ref_path: str, audio_file: str, audio_tid: int, delay_ms: int, language: str, name: str,
        out_path: str, progress=None) -> None:
    ref_audio = audio_track_ids(ref_path)
    cmd = ["mkvmerge", "--gui-mode", "-o", out_path]
    for tid in ref_audio:
        cmd += ["--default-track-flag", f"{tid}:0"]
    cmd.append(ref_path)
    cmd += ["--no-video", "--no-subtitles", "--no-chapters", "--no-attachments", "--no-global-tags",
            "--audio-tracks", str(audio_tid), "--sync", f"{audio_tid}:{delay_ms}",
            "--track-name", f"{audio_tid}:{name}", "--default-track-flag", f"{audio_tid}:1"]
    if language:
        cmd += ["--language", f"{audio_tid}:{language}"]
    cmd.append(audio_file)
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


def transfer(ref_path: str, ref_track: int, other_path: str, other_track: int, analysis: dict,
             workdir: Path, out_name: str, progress=None) -> str:
    """Builds the new file in ``workdir`` and checks it → its path."""
    if analysis.get("verdict") not in ("constant", "speed"):
        raise TransferError("Přenést jde zatím jen zvuk, který sedí celý (jiný střih přijde později)")
    other = engine.probe(other_path)
    if other_track >= len(other["audio"]):
        raise TransferError("Zvuková stopa ve zdrojové verzi nenalezena")
    track = other["audio"][other_track]
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = str(workdir / out_name)

    if progress:
        progress("prepare", 0, 1)
    ref_start = engine.probe(ref_path)["start"]
    audio_file, audio_tid, delay_ms = prepare_audio(other_path, other_track, analysis["speed"], analysis["offset"],
                                                    workdir, track.get("channels") or 2, ref_start, other["start"])
    name = f"{(track.get('language') or '?').upper()} (Lumina sync)"
    mux(ref_path, audio_file, audio_tid, delay_ms, track.get("language") or "", name, out_path, progress)
    if audio_file != other_path:
        os.remove(audio_file)

    if progress:
        progress("verify", 0, 1)
    ref_audio_count = len(engine.probe(ref_path)["audio"])
    verify(out_path, ref_track, ref_audio_count, engine.probe(out_path)["duration"])
    logger.info("audiosync: %s built (delay %d ms, speed %.5f)", out_name, delay_ms, analysis["speed"])
    return out_path
