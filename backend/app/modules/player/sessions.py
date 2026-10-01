"""Streaming a library file to the browser: ffmpeg → HLS (fMP4 segments), from a chosen second.

Browsers open neither MKV nor DTS/TrueHD, so the file is repackaged on the fly — nothing is copied
to disk beyond a short window of segments:
- "original": the video is taken from the file as it is (stream copy — no decoding, almost no CPU,
  full resolution and HDR). Possible for H.264 8-bit, and for HEVC when the browser says it can
  decode it (hardware); Dolby Vision profile 5 only where the browser supports it (Safari).
- "transcode": scaled to 720p and encoded to H.264 (HDR scaled first, then tone-mapped to SDR).
The audio is always converted to AAC stereo (cheap). Reading runs at most at 2× real time; when the
stream gets more than AHEAD segments in front of what the player asked for, ffmpeg is paused, and
segments more than BEHIND behind are deleted — so a film never piles up on the disk.
Seeking outside what is ready and switching the audio start a new session at that second.
One session per user, two at most; an unused one stops after 90 s. Files live in data/player/<id>.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path("data/player")
MAX_SESSIONS = 2
IDLE_S = 90
SEGMENT_S = 6
AHEAD = 30          # segments in front of the player before ffmpeg is paused (3 min)
BEHIND = 20         # segments kept behind the player (2 min)
# POSIX signals (the server runs on Linux; the numbers keep the module importable elsewhere)
_STOP = getattr(signal, "SIGSTOP", 19)
_CONT = getattr(signal, "SIGCONT", 18)
_SEGMENT = re.compile(r"^s(\d{5})\.m4s$")


@dataclass
class Session:
    id: str
    user_id: int
    movie_id: int
    start: float
    audio: int
    dir: Path
    mode: str                              # original | transcode
    proc: asyncio.subprocess.Process | None = None
    last_access: float = field(default_factory=time.monotonic)
    last_segment: int = -1                 # the newest segment the player asked for
    paused: bool = False


_sessions: dict[str, Session] = {}


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name,pix_fmt,height,color_transfer,channels"
         ":stream_tags=language,title:stream_side_data_list", "-of", "json", path],
        capture_output=True, text=True, timeout=60, check=True).stdout
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = []
    for s in data.get("streams", []):
        if s.get("codec_type") == "audio":
            tags = s.get("tags") or {}
            audio.append({"index": len(audio), "language": (tags.get("language") or "").lower(),
                          "title": tags.get("title") or "", "codec": s.get("codec_name") or "",
                          "channels": s.get("channels") or 0})
    dv = next((sd.get("dv_profile") for sd in video.get("side_data_list") or [] if "dv_profile" in sd), None)
    return {"duration": float(data.get("format", {}).get("duration") or 0), "audio": audio,
            "video": {"codec": video.get("codec_name"), "height": video.get("height"),
                      "pix_fmt": video.get("pix_fmt"), "dv_profile": dv,
                      "hdr": video.get("color_transfer") in ("smpte2084", "arib-std-b67") or dv is not None}}


def choose_mode(video: dict, wanted: str, caps: dict) -> tuple[str, str]:
    """→ (mode, reason). ``caps``: what the browser said it can decode (hevc, dv5)."""
    if wanted == "transcode":
        return "transcode", "převod zvolen ručně"
    codec = video.get("codec")
    if codec == "h264":
        if video.get("pix_fmt") in ("yuv420p", "yuvj420p"):
            return "original", "H.264 — prohlížeč ho umí"
        return "transcode", "H.264 10-bit prohlížeče neumí"
    if codec == "hevc":
        if not caps.get("hevc"):
            return "transcode", "tento prohlížeč neumí HEVC (Firefox, nebo bez hardwarového dekodéru)"
        if video.get("dv_profile") == 5 and not caps.get("dv5"):
            return "transcode", "Dolby Vision 5 umí jen Safari — převod (barvy nemusí sedět)"
        return "original", "HEVC — prohlížeč ho umí"
    return "transcode", f"{codec} prohlížeče neumí"


def _video_args(info: dict, mode: str) -> list[str]:
    video = info["video"]
    if mode == "original":
        args = ["-c:v", "copy"]
        if video.get("codec") == "hevc":
            # browsers want the parameter sets in the header (hvc1); DV 5 needs its own tag
            args += ["-tag:v", "dvh1" if video.get("dv_profile") == 5 else "hvc1", "-strict", "unofficial"]
        return args
    height = min(720, video.get("height") or 720)
    if video.get("hdr"):
        # scale down first, then tone-map: 9× fewer pixels than 4K for the expensive float math
        chain = [f"zscale=w=-2:h={height}:t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709",
                 "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv", "format=yuv420p"]
    else:
        chain = [f"scale=-2:{height}", "format=yuv420p"]
    return ["-vf", ",".join(chain), "-c:v", "libx264", "-preset", "superfast", "-crf", "23",
            "-g", str(SEGMENT_S * 24), "-sc_threshold", "0"]


def keyframe_before(path: str, at: float) -> float:
    """Where a copied picture really starts when ffmpeg seeks to ``at``.

    A copied picture can only start at a keyframe — the one the demuxer's seek lands on (in MKV
    only keyframes listed in the index, seconds apart) — while ffmpeg cuts the audio exactly at
    ``at``. The stream then began with picture without sound and browsers (MSE) lined both starts
    up differently: the sound ended up that much early (Zkus mě rozesmát: 2.6 s). ffprobe seeks the
    same way as ffmpeg, so its first packet is that keyframe; starting there, both begin together."""
    if at <= 0:
        return 0.0
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", f"{at:.3f}%+#1",
                        "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=60)
    try:
        landed = float(r.stdout.split()[0].strip(","))
    except (IndexError, ValueError):
        return at
    # a hair after it: a time rounded to just before the keyframe would make ffmpeg seek one further back
    return landed + 0.02 if at - 30 < landed <= at + 0.05 else at


async def start(user_id: int, movie_id: int, path: str, at: float, audio: int, wanted: str = "auto",
                caps: dict | None = None) -> tuple[Session, dict, str]:
    info = await asyncio.to_thread(probe, path)
    mode, reason = choose_mode(info["video"], wanted, caps or {})
    if not info["audio"]:
        audio = -1
    elif audio >= len(info["audio"]):
        audio = 0
    at = max(0.0, min(at, max(0.0, info["duration"] - 5)))
    if mode == "original":
        at = await asyncio.to_thread(keyframe_before, path, at)
    for s in [s for s in _sessions.values() if s.user_id == user_id]:
        await stop(s.id)
    while len(_sessions) >= MAX_SESSIONS:
        await stop(min(_sessions.values(), key=lambda s: s.last_access).id)
    sid = secrets.token_urlsafe(9)
    d = ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-readrate", "2", "-readrate_initial_burst", "20",
           "-ss", f"{at:.3f}", "-i", path, "-map", "0:v:0"]
    if audio >= 0:
        cmd += ["-map", f"0:a:{audio}", "-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    cmd += _video_args(info, mode)
    cmd += ["-f", "hls", "-hls_time", str(SEGMENT_S), "-hls_list_size", "0", "-hls_playlist_type", "event",
            "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", str(d / "s%05d.m4s"), str(d / "index.m3u8")]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    s = Session(id=sid, user_id=user_id, movie_id=movie_id, start=at, audio=audio, dir=d, mode=mode, proc=proc)
    _sessions[sid] = s
    logger.info("player: %s from %.0f s (audio %d, %s — %s)", Path(path).name, at, audio, mode, reason)
    return s, info, reason


async def stop(sid: str) -> None:
    s = _sessions.pop(sid, None)
    if not s:
        return
    if s.proc and s.proc.returncode is None:
        if s.paused:
            _signal(s, _CONT)
        s.proc.kill()
        try:
            await asyncio.wait_for(s.proc.wait(), 5)
        except asyncio.TimeoutError:
            pass
    shutil.rmtree(s.dir, ignore_errors=True)


def get(sid: str) -> Session | None:
    s = _sessions.get(sid)
    if s:
        s.last_access = time.monotonic()
    return s


def requested(s: Session, name: str) -> None:
    """The player asked for this file — remember how far it is."""
    m = _SEGMENT.match(name)
    if m:
        s.last_segment = max(s.last_segment, int(m.group(1)))


def _signal(s: Session, sig: int) -> None:
    try:
        os.kill(s.proc.pid, sig)          # type: ignore[union-attr]
    except (ProcessLookupError, AttributeError):
        pass


def pace(s: Session) -> None:
    """Keep at most AHEAD segments in front of the player (pause ffmpeg) and BEHIND behind it (delete)."""
    produced = [int(m.group(1)) for f in s.dir.iterdir() if (m := _SEGMENT.match(f.name))]
    if not produced:
        return
    newest = max(produced)
    if s.proc and s.proc.returncode is None:
        if not s.paused and newest - s.last_segment > AHEAD:
            _signal(s, _STOP)
            s.paused = True
        elif s.paused and newest - s.last_segment < AHEAD // 2:
            _signal(s, _CONT)
            s.paused = False
    for n in produced:
        if n < s.last_segment - BEHIND:
            (s.dir / f"s{n:05d}.m4s").unlink(missing_ok=True)


async def reaper() -> None:
    while True:
        await asyncio.sleep(3)
        now = time.monotonic()
        for s in list(_sessions.values()):
            if now - s.last_access > IDLE_S:
                logger.info("player: session %s idle — stopped", s.id)
                await stop(s.id)
            else:
                try:
                    pace(s)
                except OSError:
                    pass


_reaper: asyncio.Task | None = None


def on_startup() -> None:
    global _reaper
    shutil.rmtree(ROOT, ignore_errors=True)      # leftovers of a previous run
    _reaper = asyncio.get_event_loop().create_task(reaper())


async def on_shutdown() -> None:
    for sid in list(_sessions):
        await stop(sid)
    if _reaper:
        _reaper.cancel()
