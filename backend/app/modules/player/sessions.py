"""Streaming a library file to the browser: ffmpeg → HLS (H.264 + AAC stereo), from a chosen second.

Browsers play neither MKV nor HEVC/DTS, so the file is turned into HLS on the fly. H.264 8-bit
video is copied (no work); anything else is scaled to 720p and encoded (HDR tone-mapped to SDR).
Reading runs at most at 2× real time, so a paused film does not keep the CPU busy. Seeking outside
what is ready and switching the audio track start a new session at that second. One session per
user, two at most; an unused one stops after 90 s. Files live in data/player/<session>.
"""

import asyncio
import json
import logging
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path("data/player")
MAX_SESSIONS = 2
IDLE_S = 90
SEGMENT_S = 6


@dataclass
class Session:
    id: str
    user_id: int
    movie_id: int
    start: float
    audio: int
    dir: Path
    proc: asyncio.subprocess.Process | None = None
    last_access: float = field(default_factory=time.monotonic)


_sessions: dict[str, Session] = {}


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name,pix_fmt,height,color_transfer,channels"
         ":stream_tags=language,title", "-of", "json", path],
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
    hdr = video.get("color_transfer") in ("smpte2084", "arib-std-b67")
    copy = video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p" and (video.get("height") or 0) <= 1080
    return {"duration": float(data.get("format", {}).get("duration") or 0), "audio": audio,
            "video": {"codec": video.get("codec_name"), "height": video.get("height"), "hdr": hdr, "copy": copy}}


def _video_args(info: dict) -> list[str]:
    if info["video"]["copy"]:
        return ["-c:v", "copy"]
    chain = []
    if info["video"]["hdr"]:
        chain.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                     "zscale=t=bt709:m=bt709:r=tv")
    chain.append("scale=-2:'min(720,ih)'")
    chain.append("format=yuv420p")
    return ["-vf", ",".join(chain), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-g", str(SEGMENT_S * 24), "-sc_threshold", "0"]


async def start(user_id: int, movie_id: int, path: str, at: float, audio: int) -> tuple[Session, dict]:
    info = await asyncio.to_thread(probe, path)
    if not info["audio"]:
        audio = -1
    elif audio >= len(info["audio"]):
        audio = 0
    at = max(0.0, min(at, max(0.0, info["duration"] - 5)))
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
    cmd += _video_args(info)
    cmd += ["-f", "hls", "-hls_time", str(SEGMENT_S), "-hls_list_size", "0", "-hls_playlist_type", "event",
            "-hls_segment_filename", str(d / "s%05d.ts"), str(d / "index.m3u8")]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    s = Session(id=sid, user_id=user_id, movie_id=movie_id, start=at, audio=audio, dir=d, proc=proc)
    _sessions[sid] = s
    logger.info("player: %s from %.0f s (audio %d, %s)", Path(path).name, at, audio,
                "copy" if info["video"]["copy"] else "encode")
    return s, info


async def stop(sid: str) -> None:
    s = _sessions.pop(sid, None)
    if not s:
        return
    if s.proc and s.proc.returncode is None:
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


async def reaper() -> None:
    while True:
        await asyncio.sleep(15)
        now = time.monotonic()
        for s in list(_sessions.values()):
            if now - s.last_access > IDLE_S:
                logger.info("player: session %s idle — stopped", s.id)
                await stop(s.id)


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
