"""Step 4: short preview clips to check lip sync by eye, and a manual correction (decisions/0007).

A clip = 20 s of the reference picture (scaled to 480p H.264, plays in any browser) with the other
version's track placed by the analysis mapping (+ the user's correction). Clips are throwaway:
kept a day in data/audiosync-previews.
"""

import os
import subprocess
import time
from pathlib import Path

from app.modules.audiosync import analyze as engine
from app.modules.audiosync.analyze import mapping_at

PREVIEW_DIR = Path("data/audiosync-previews")
CLIP_S = 20.0
KEEP_S = 24 * 3600


def with_adjustment(analysis: dict) -> dict:
    """The mapping with the user's correction: +ms = the other audio plays later."""
    adjust = (analysis.get("adjust_ms") or 0) / 1000
    if not adjust:
        return analysis
    out = dict(analysis)
    out["offset"] = analysis["offset"] - adjust
    if analysis.get("pieces"):
        out["pieces"] = [{**p, "offset": None if p["offset"] is None else p["offset"] - adjust}
                         for p in analysis["pieces"]]
    return out


def cleanup() -> None:
    if not PREVIEW_DIR.is_dir():
        return
    now = time.time()
    for f in PREVIEW_DIR.glob("*.mp4"):
        if now - f.stat().st_mtime > KEEP_S:
            f.unlink(missing_ok=True)


def make_clip(ref_path: str, other_path: str, other_track: int, analysis: dict, at: float, adjust_ms: int,
              name: str) -> Path:
    """→ path of an MP4 clip starting at reference second ``at``."""
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    cleanup()
    out = PREVIEW_DIR / f"{name}.mp4"
    speed = analysis.get("speed", 1.0)
    offset = mapping_at(analysis, at)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{at:.3f}", "-t", f"{CLIP_S:.3f}", "-i", ref_path]
    if offset is None:
        # the other version lacks this part — the clip is silent on purpose
        cmd += ["-f", "lavfi", "-t", f"{CLIP_S:.3f}", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a"]
        audio_filter = []
    else:
        start = speed * at + offset - adjust_ms / 1000
        lead = 0.0
        if start < 0:
            lead, start = -start / speed, 0.0
        cmd += ["-ss", f"{start:.3f}", "-t", f"{CLIP_S * speed:.3f}", "-i", other_path,
                "-map", "0:v:0", "-map", f"1:a:{other_track}"]
        chain = []
        if abs(speed - 1) > 1e-9:
            chain.append(engine.speed_filter(speed))
        if lead:
            chain.append(f"adelay={int(lead * 1000)}:all=1")
        audio_filter = ["-af", ",".join(chain)] if chain else []
    cmd += ["-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
            *audio_filter, "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    os.utime(out)
    return out
