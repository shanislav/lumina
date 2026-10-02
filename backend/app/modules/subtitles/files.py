"""What subtitles a library film has, and saving new ones next to the video.

In the file: subtitle tracks (ffprobe — language, forced flag, title). Next to it: "<video stem>.<lang>
[.forced].srt" and the like (Plex reads them the same way). Burnt-in subtitles are not looked for.
"""

import json
import os
import re
import subprocess

from app.core.mediainfo import normalize_language

SUB_EXT = (".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup")


def embedded(path: str) -> list[dict]:
    """Subtitle tracks inside the video: [{lang, forced, title, codec}]."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
             "stream=codec_name:stream_tags=language,title:stream_disposition=forced", "-of", "json", path],
            capture_output=True, text=True, timeout=60)
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except Exception:
        return []
    tracks = []
    for s in streams:
        tags = s.get("tags") or {}
        title = tags.get("title") or ""
        tracks.append({"lang": normalize_language(tags.get("language") or "") or "", "title": title,
                       "forced": bool((s.get("disposition") or {}).get("forced")) or "forced" in title.lower(),
                       "codec": s.get("codec_name") or ""})
    return tracks


def external(video_path: str) -> list[dict]:
    """Subtitle files next to the video that belong to it: [{file, lang, forced}]."""
    folder, name = os.path.split(video_path)
    stem = os.path.splitext(name)[0]
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    videos = [e for e in entries if e.lower().endswith((".mkv", ".mp4", ".avi", ".m4v", ".ts"))]
    out = []
    for e in sorted(entries):
        if not e.lower().endswith(SUB_EXT):
            continue
        # one video in the folder: every subtitle file is its; more videos: only the ones named after it
        if len(videos) > 1 and not e.startswith(stem + "."):
            continue
        rest = e[len(stem):] if e.startswith(stem) else os.path.splitext(e)[0]
        parts = [p for p in re.split(r"[._\-\s]+", rest.lower()) if p]
        lang = next((normalize_language(p) for p in reversed(parts) if normalize_language(p)), "")
        out.append({"file": e, "lang": lang or "", "forced": "forced" in parts or "forced" in e.lower()})
    return out


def video_fps(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60)
        num, _, den = out.stdout.strip().partition("/")
        return float(num) / float(den or 1)
    except Exception:
        return 0.0


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1250", "iso-8859-2"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})")


def rescale(srt: str, factor: float) -> str:
    """Times × factor — subtitles made for a 25 fps release on a 23.976 fps file (and back)."""
    def fix(m: re.Match) -> str:
        h, mi, s, ms = map(int, m.groups())
        t = round(((h * 60 + mi) * 60 + s) * 1000 + ms) * factor
        t = int(round(t))
        return f"{t // 3600000:02d}:{t // 60000 % 60:02d}:{t // 1000 % 60:02d},{t % 1000:03d}"
    return _TIME.sub(fix, srt)


def target_name(video_path: str, lang: str, forced: bool) -> str:
    stem = os.path.splitext(video_path)[0]
    return f"{stem}.{lang}{'.forced' if forced else ''}.srt"
