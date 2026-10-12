"""Dolby Vision from MKV to MP4 — losslessly (decisions/0009).

Google TV Streamer (and other Android TV players) do not play Dolby Vision in MKV: the server allows direct play,
the client drops it after two seconds and asks for a transcode the server's CPU cannot do. In MP4 it plays.

ffmpeg ≥ 9 writes the DV configuration (dvvC) into MP4 with ``-strict unofficial`` (an older one did not — MP4Box
was needed), keeping HDR10's mdcv/clli. One pass: video and audio copied into the MP4, subtitles out into files next
to it (Plex reads them; an MP4 holds no PGS/ASS). The result must hold the same: the length, the DV configuration,
every audio track and the same number of DV metadata units (RPU) — the original goes only then."""

import json
import logging
import os
import subprocess

from app.core.mediainfo import normalize_language

logger = logging.getLogger(__name__)

TEXT_SUBS = {"subrip": "srt", "srt": "srt", "mov_text": "srt", "webvtt": "srt", "text": "srt", "ass": "ass", "ssa": "ass"}
PICTURE_SUBS = {"hdmv_pgs_subtitle": "sup"}      # a DVD's VobSub needs mkvextract (idx + sub)
RPU_PROBE_S = 60                                  # RPUs are counted in the first minute of both files
LENGTH_TOLERANCE_S = 2.0
RPU_NAL = 62


class RemuxError(Exception):
    pass


def probe(path: str) -> dict:
    """Streams and the DV configuration of a file (ffprobe)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name:stream_tags=language:"
         "stream_disposition=forced,hearing_impaired:stream_side_data=dv_profile,dv_bl_signal_compatibility_id",
         "-of", "json", path], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RemuxError(f"ffprobe: {out.stderr.strip()[-200:]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    dv = next((d for d in video.get("side_data_list") or [] if "dv_profile" in d), None)
    return {"duration": float((data.get("format") or {}).get("duration") or 0),
            "dv_profile": dv.get("dv_profile") if dv else None,
            "dv_compat": dv.get("dv_bl_signal_compatibility_id") if dv else None,
            "audio": [s for s in streams if s.get("codec_type") == "audio"],
            "subtitles": [s for s in streams if s.get("codec_type") == "subtitle"]}


def needs_remux(path: str, info: dict | None = None) -> bool:
    """A Dolby Vision video in an MKV."""
    if os.path.splitext(path)[1].lower() != ".mkv":
        return False
    info = info if info is not None else probe(path)
    return info["dv_profile"] is not None


def subtitle_outputs(info: dict, stem: str, taken: set[str]) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Each subtitle track → (stream index, file name next to the video, ffmpeg codec); and the tracks that cannot
    go out this way (a DVD's VobSub) — the caller decides. Names as Plex reads them: "<video>.cs.forced.srt"."""
    out, left = [], []
    names = set(taken)
    for n, s in enumerate(info["subtitles"], 1):
        codec = s.get("codec_name") or ""
        ext = TEXT_SUBS.get(codec) or PICTURE_SUBS.get(codec)
        if not ext:
            left.append(f"#{s['index']} {codec}")
            continue
        disp = s.get("disposition") or {}
        lang = normalize_language((s.get("tags") or {}).get("language")) or "und"
        suffix = lang + (".forced" if disp.get("forced") else "") + (".sdh" if disp.get("hearing_impaired") else "")
        name = f"{stem}.{suffix}.{ext}"
        if name in names:
            name = f"{stem}.{suffix}.{n}.{ext}"
        names.add(name)
        ffcodec = "copy" if ext == "sup" else ("ass" if ext == "ass" else "srt")
        out.append((s["index"], name, ffcodec))
    return out, left


def rpu_count(path: str, seconds: int = RPU_PROBE_S) -> int:
    """DV metadata units (NAL type 62) in the first ``seconds`` of the video — streamed, not held in memory."""
    proc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-t", str(seconds), "-map", "0:v:0",
                             "-c", "copy", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    count, tail = 0, b""
    try:
        while chunk := proc.stdout.read(1 << 22):
            data = tail + chunk
            i = 0
            while (j := data.find(b"\x00\x00\x01", i)) >= 0 and j + 3 < len(data):
                if (data[j + 3] >> 1) & 0x3F == RPU_NAL:
                    count += 1
                i = j + 3
            tail = data[-3:]            # a start code split between two chunks
    finally:
        proc.stdout.close()
        proc.wait(timeout=60)
    return count


def remux(src: str, work: str, stem: str, taken: set[str], progress=None) -> tuple[str, list[str]]:
    """``src`` (DV in MKV) → (the MP4 in ``work``, subtitle files in ``work``). Raises RemuxError when the result
    does not hold the same as the original — then nothing of it is left."""
    info = probe(src)
    if info["dv_profile"] is None:
        raise RemuxError("Soubor nemá Dolby Vision")
    subs, left = subtitle_outputs(info, stem, taken)
    if left:
        raise RemuxError(f"Titulky, které neumím vytáhnout ({', '.join(left)}) — soubor nechávám")
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, stem + ".mp4")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
           "-tag:v", "hvc1", "-strict", "experimental", "-movflags", "+faststart", out]
    sub_paths = []
    for index, name, codec in subs:
        path = os.path.join(work, name)
        cmd += ["-map", f"0:{index}", "-c:s", codec] + (["-f", "sup"] if name.endswith(".sup") else []) + [path]
        sub_paths.append(path)
    if progress:
        progress("remux")
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
    try:
        if run.returncode != 0:
            raise RemuxError(f"ffmpeg: {run.stderr.strip()[-300:] or 'chyba'}")
        if progress:
            progress("verify")
        verify(src, out, info)
    except RemuxError:
        for p in [out, *sub_paths]:
            if os.path.exists(p):
                os.remove(p)
        raise
    return out, [p for p in sub_paths if os.path.getsize(p) > 0]


def verify(src: str, out: str, info: dict | None = None) -> None:
    """The MP4 holds the same as the MKV: length, DV configuration, audio tracks, RPU count."""
    a = info or probe(src)
    b = probe(out)
    if abs(a["duration"] - b["duration"]) > LENGTH_TOLERANCE_S:
        raise RemuxError(f"Délka nesedí ({a['duration']:.0f} s vs {b['duration']:.0f} s)")
    if b["dv_profile"] != a["dv_profile"] or b["dv_compat"] != a["dv_compat"]:
        raise RemuxError("V MP4 chybí Dolby Vision")
    if len(b["audio"]) != len(a["audio"]):
        raise RemuxError(f"Zvukové stopy nesedí ({len(a['audio'])} vs {len(b['audio'])})")
    r1, r2 = rpu_count(src), rpu_count(out)
    if not r1 or r1 != r2:
        raise RemuxError(f"Metadata Dolby Vision nesedí (RPU {r1} vs {r2})")
