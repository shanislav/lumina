"""Smaller files at a chosen quality (decisions/0009 §2a).

The user chooses the quality, not the size: a VMAF target (95 = no difference seen on a TV). VMAF predicts how a
viewer sees the difference from the original, 0–100. For each file a few short samples of its video are encoded
at several CRFs; the highest CRF (= the smallest file) whose samples still reach the target is the one. From the
samples comes the estimate (size, time) and still frames of the original and the new one side by side — before
anything is encoded for real. The speed preset changes the time and the size, not the quality (the target holds).

Audio and subtitles are copied (or big audio tracks made smaller, by choice); HDR10 stays (10-bit, its static
metadata). Dolby Vision would be lost — such files are left alone."""

import json
import logging
import os
import re
import subprocess
import time

logger = logging.getLogger(__name__)

SPEEDS = {"small": ("libx265", "slow"), "balanced": ("libx265", "medium"), "fast": ("libx265", "fast")}
QUALITIES = (95, 93, 90)
DEFAULTS = {"vmaf": 95, "speed": "balanced", "max_height": 0, "audio": "copy", "min_saving": 25, "go": False}
SAMPLES = (0.15, 0.38, 0.62, 0.85)        # where in the film the samples are (share of its length)
SAMPLE_S = 8
CRF_RANGE = (14, 34)
CHECK_AT = (0.3, 0.7)                      # the full result's own check: VMAF of these windows …
CHECK_S = 10
CHECK_SLACK = 2.0                          # … may be this much under the target (samples are an estimate)
BIG_AUDIO = ("truehd", "dts", "pcm", "flac")
NICE = ["nice", "-n", "10"] if os.name == "posix" else []


class ShrinkError(Exception):
    pass


def options(raw: dict | None) -> dict:
    """The user's choices, checked; anything missing or odd → the default."""
    o = dict(DEFAULTS)
    raw = raw or {}
    try:
        v = float(raw.get("vmaf", o["vmaf"]))
        o["vmaf"] = v if 80 <= v <= 99 else o["vmaf"]
    except (TypeError, ValueError):
        pass
    o["speed"] = raw.get("speed") if raw.get("speed") in SPEEDS else o["speed"]
    o["max_height"] = int(raw.get("max_height") or 0) if str(raw.get("max_height") or 0) in ("0", "1080", "720") else 0
    o["audio"] = raw.get("audio") if raw.get("audio") in ("copy", "shrink") else o["audio"]
    try:
        o["min_saving"] = max(0, min(80, int(raw.get("min_saving", o["min_saving"]))))
    except (TypeError, ValueError):
        pass
    o["go"] = bool(raw.get("go"))
    return o


# ── what the file is ──

def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size,bit_rate:stream=index,codec_type,codec_name,profile,width,height,pix_fmt,channels,"
         "bit_rate,color_primaries,color_transfer,color_space,avg_frame_rate,r_frame_rate:stream_tags=language,BPS,BPS-eng:"
         "stream_side_data", "-of", "json", path], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise ShrinkError(f"ffprobe: {out.stderr.strip()[-200:]}")
    data = json.loads(out.stdout or "{}")
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("codec_name") not in ("mjpeg", "png")), None)
    if not video:
        raise ShrinkError("Soubor nemá obraz")
    side = {d.get("side_data_type", ""): d for d in video.get("side_data_list") or []}
    audio = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags") or {}
        rate = int(s.get("bit_rate") or tags.get("BPS") or tags.get("BPS-eng") or 0)
        audio.append({"index": s["index"], "codec": s.get("codec_name") or "", "profile": s.get("profile") or "",
                      "channels": s.get("channels") or 2, "bit_rate": rate})
    return {"duration": float(fmt.get("duration") or 0), "size": int(fmt.get("size") or os.path.getsize(path)),
            "video": video, "width": video.get("width") or 0, "height": video.get("height") or 0,
            "codec": video.get("codec_name") or "", "fps": _fps(video),
            "dv": any("DOVI" in k for k in side), "hdr10": video.get("color_transfer") == "smpte2084",
            "mastering": side.get("Mastering display metadata"), "light": side.get("Content light level metadata"),
            "audio": audio}


def _fps(video: dict) -> float:
    """Frames per second — a transport stream may say "0/0" in one of the two fields."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        n, _, d = str(video.get(key) or "").partition("/")
        try:
            if float(n) > 0 and float(d or 1) > 0:
                return float(n) / float(d or 1)
        except ValueError:
            continue
    return 25.0


def _ratio(v, scale: int) -> int:
    """ffprobe's "34000/50000" → x265's integer units (0.00002 for chromaticity, 0.0001 for luminance)."""
    try:
        n, _, d = str(v).partition("/")
        return round(float(n) / float(d or 1) * scale)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


def x265_params(info: dict) -> str:
    """HDR10 stays HDR10: its colour signalling and static metadata written into the stream."""
    p = ["log-level=error"]
    if info["hdr10"]:
        p += ["hdr10=1", "repeat-headers=1", "colorprim=bt2020", "transfer=smpte2084", "colormatrix=bt2020nc"]
        m = info.get("mastering")
        if m and m.get("red_x"):
            c = 50000
            p.append("master-display=" + "".join([
                f"G({_ratio(m['green_x'], c)},{_ratio(m['green_y'], c)})",
                f"B({_ratio(m['blue_x'], c)},{_ratio(m['blue_y'], c)})",
                f"R({_ratio(m['red_x'], c)},{_ratio(m['red_y'], c)})",
                f"WP({_ratio(m['white_point_x'], c)},{_ratio(m['white_point_y'], c)})",
                f"L({_ratio(m['max_luminance'], 10000)},{_ratio(m['min_luminance'], 10000)})"]))
        light = info.get("light")
        if light:
            p.append(f"max-cll={light.get('max_content', 0)},{light.get('max_average', 0)}")
    return ":".join(p)


def _scale_filter(info: dict, opts: dict) -> list[str]:
    h = opts.get("max_height") or 0
    return ["-vf", f"scale=-2:{h}:flags=lanczos"] if h and info["height"] > h else []


def _video_args(info: dict, opts: dict, crf: float) -> list[str]:
    encoder, preset = SPEEDS[opts["speed"]]
    return ["-c:v", encoder, "-preset", preset, "-crf", f"{crf:g}", "-pix_fmt", "yuv420p10le",
            "-x265-params", x265_params(info), *_scale_filter(info, opts)]


def _audio_args(info: dict, opts: dict) -> list[str]:
    """Copied — or a big lossless/DTS track made E-AC-3 (640 kb/s surround, 256 stereo) when chosen."""
    args = ["-c:a", "copy"]
    if opts["audio"] != "shrink":
        return args
    for i, a in enumerate(info["audio"]):
        if a["codec"].startswith(BIG_AUDIO):
            args += [f"-c:a:{i}", "eac3", f"-b:a:{i}", "640k" if a["channels"] > 2 else "256k"]
    return args


def _audio_bytes(info: dict, opts: dict) -> int:
    total = 0
    for a in info["audio"]:
        rate = a["bit_rate"] or (640_000 if a["channels"] > 2 else 192_000)
        if opts["audio"] == "shrink" and a["codec"].startswith(BIG_AUDIO):
            rate = min(rate, 640_000 if a["channels"] > 2 else 256_000)
        total += rate * info["duration"] / 8
    return int(total)


# ── samples, VMAF ──

def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise ShrinkError(f"ffmpeg: {run.stderr.strip()[-300:] or 'chyba'}")
    return run


def cut_samples(path: str, info: dict, work: str) -> list[str]:
    """The video of a few places of the film, each copied (no re-encoding) into its own file — the references.
    Not joined into one: a transport stream's timestamps break at the joins and frames slip against each other."""
    parts = []
    for k, share in enumerate(SAMPLES):
        part = os.path.join(work, f"sample{k}.mkv")
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{info['duration'] * share:.2f}", "-i", path,
              "-t", str(SAMPLE_S), "-map", "0:v:0", "-c", "copy", part], 600)
        parts.append(part)
    return parts


def vmaf(distorted: str, reference: str, seek: float = 0, length: float = 0) -> float:
    """VMAF of ``distorted`` against ``reference`` (both read from ``seek`` for ``length`` s when given), at most
    1080p — the model is made for it; a smaller result is scaled up to the reference."""
    win = (["-ss", f"{seek:.2f}"] if seek else []) + (["-t", f"{length:g}"] if length else [])
    graph = ("[1:v]scale='min(1920,iw)':-2:flags=bicubic,format=yuv420p10le,setpts=PTS-STARTPTS,split[r1][r2];"
             "[0:v][r1]scale=rw:rh:flags=bicubic,format=yuv420p10le,setpts=PTS-STARTPTS[d];"
             f"[d][r2]libvmaf=n_threads={os.cpu_count() or 2}")
    run = subprocess.run([*NICE, "ffmpeg", "-nostdin", "-hide_banner", *win, "-i", distorted, *win, "-i", reference,
                          "-lavfi", graph, "-f", "null", "-"], capture_output=True, text=True, timeout=6 * 3600)
    found = re.findall(r"VMAF score: ([\d.]+)", run.stderr)
    if run.returncode != 0 or not found:
        raise ShrinkError(f"VMAF: {run.stderr.strip()[-300:] or 'nezměřeno'}")
    return float(found[-1])


def encode_sample(refs: list[str], info: dict, opts: dict, crf: float, work: str) -> dict:
    """Every sample at ``crf``, each measured against its own reference → the joint trial (VMAF weighted by the
    samples' lengths, sizes and times summed)."""
    paths, scores, weights, size, spent = [], [], [], 0, 0.0
    for k, ref in enumerate(refs):
        out = os.path.join(work, f"crf{crf:g}_{k}.mkv")
        t0 = time.time()
        _run([*NICE, "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", ref, "-map", "0:v:0",
              *_video_args(info, opts, crf), out], 6 * 3600)
        spent += time.time() - t0
        size += os.path.getsize(out)
        scores.append(vmaf(out, ref))
        weights.append(probe(ref)["duration"] or SAMPLE_S)
        paths.append(out)
    score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    return {"crf": crf, "vmaf": round(score, 2), "size": size, "seconds": spent, "paths": paths,
            "duration": sum(weights), "worst": round(min(scores), 2)}


def find_crf(ref: list[str], info: dict, opts: dict, work: str, progress=None) -> tuple[dict | None, list[dict]]:
    """The highest CRF whose samples reach the target → (that trial or None, every trial). Bisection over whole
    numbers: ~4 encodes of the samples."""
    target = opts["vmaf"]
    lo_ok, hi_bad = None, None                  # best passing trial, lowest failing CRF
    trials: list[dict] = []
    crf = 24 if info["height"] > 1200 else 22
    lo, hi = CRF_RANGE
    for _ in range(7):
        if progress:
            progress("sample", len(trials) + 1)
        t = encode_sample(ref, info, opts, crf, work)
        trials.append(t)
        if t["vmaf"] >= target:
            lo_ok = t if (lo_ok is None or t["crf"] > lo_ok["crf"]) else lo_ok
            lo = crf
        else:
            hi_bad = crf if hi_bad is None else min(hi_bad, crf)
            hi = crf
        if lo_ok is None:
            nxt = crf - 4 if crf - 4 >= CRF_RANGE[0] else (CRF_RANGE[0] if crf > CRF_RANGE[0] else None)
        elif hi_bad is None:
            nxt = crf + 4 if crf + 4 <= CRF_RANGE[1] else (CRF_RANGE[1] if crf < CRF_RANGE[1] else None)
        else:
            nxt = (lo + hi) // 2 if hi - lo > 1 else None
        if nxt is None or any(x["crf"] == nxt for x in trials):
            break
        crf = nxt
    return lo_ok, trials


def stills(refs: list[str], encoded: list[str], info: dict, out_dir: str) -> list[dict]:
    """Frames of the original and the new one at the same moments (the middle of three samples) — tone-mapped
    for a normal screen when the film is HDR."""
    os.makedirs(out_dir, exist_ok=True)
    tone = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,") if info["hdr10"] else ""
    shots = []
    for k in range(min(3, len(refs))):
        at = SAMPLE_S / 2
        names = {}
        for label, src in (("a", refs[k]), ("b", encoded[k])):
            name = f"{k}{label}.jpg"
            _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", src, "-frames:v", "1",
                  "-vf", f"{tone}scale='min(1920,iw)':-2", "-q:v", "2", os.path.join(out_dir, name)], 300)
            names[label] = name
        shots.append(names)
    return shots


def plan(path: str, opts: dict, work: str, preview_dir: str, progress=None) -> dict:
    """The estimate for one file: CRF, VMAF, size, time, frames to compare — nothing of the file changes."""
    info = probe(path)
    base = {"src_size": info["size"], "duration": info["duration"], "codec": info["codec"],
            "height": info["height"], "options": opts}
    if info["dv"]:
        return {**base, "decision": "dv", "note": "Dolby Vision by překódováním zaniklo — soubor nechávám"}
    if info["duration"] < 60:
        return {**base, "decision": "short", "note": "Příliš krátké"}
    os.makedirs(work, exist_ok=True)
    if progress:
        progress("samples")
    refs = cut_samples(path, info, work)
    best, trials = find_crf(refs, info, opts, work, progress)
    view = [{k: t[k] for k in ("crf", "vmaf", "size", "worst")} for t in trials]
    if not best:
        return {**base, "decision": "quality", "trials": view,
                "note": f"Ani CRF {CRF_RANGE[0]} nedosáhne VMAF {opts['vmaf']:g} — soubor nechávám"}
    audio = _audio_bytes(info, opts)
    ref_s = best["duration"] or SAMPLE_S * len(SAMPLES)
    video_new = best["size"] / ref_s * info["duration"]
    est = int(video_new + audio + info["size"] * 0.002)          # + container, subtitles
    saving = round(100 * (1 - est / info["size"]), 1) if info["size"] else 0
    frames = info["duration"] * info["fps"]
    sample_frames = ref_s * info["fps"]
    est_seconds = int(best["seconds"] / max(1.0, sample_frames) * frames * 1.05)
    if progress:
        progress("stills")
    shots = stills(refs, best["paths"], info, preview_dir)
    decision = "ok" if saving >= opts["min_saving"] else "low_saving"
    note = "" if decision == "ok" else f"Ušetří jen {saving:g} % (méně než {opts['min_saving']} %)"
    return {**base, "decision": decision, "note": note, "crf": best["crf"], "vmaf": best["vmaf"], "est_size": est,
            "saving": saving, "est_seconds": est_seconds, "trials": view, "stills": shots}


# ── the real thing ──

def encode(path: str, crf: float, opts: dict, work: str, progress=None) -> tuple[str, dict]:
    """The whole file at ``crf`` into ``work`` → (path, its check). Raises ShrinkError when the result is not
    good enough (length, tracks, VMAF of two windows) — then nothing of it is left."""
    info = probe(path)
    if info["dv"]:
        raise ShrinkError("Dolby Vision by zaniklo — soubor nechávám")
    ext = os.path.splitext(path)[1].lower()
    out = os.path.join(work, os.path.splitext(os.path.basename(path))[0] + (".mp4" if ext in (".mp4", ".m4v") else ".mkv"))
    os.makedirs(work, exist_ok=True)
    cmd = [*NICE, "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a?",
           "-map", "0:s?", "-map", "0:t?", "-c", "copy", *_video_args(info, opts, crf), *_audio_args(info, opts),
           "-progress", "pipe:1", "-nostats", out]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and info["duration"] and progress:
                try:
                    progress("encode", min(99.0, int(line.split("=")[1]) / 1e6 / info["duration"] * 100))
                except ValueError:
                    pass
        err = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise ShrinkError(f"ffmpeg: {err.strip()[-300:] or 'chyba'}")
        if progress:
            progress("verify")
        check = verify(path, out, info, opts)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        if os.path.exists(out):
            os.remove(out)
        raise
    return out, check


def verify(src: str, out: str, info: dict, opts: dict) -> dict:
    b = probe(out)
    if abs(info["duration"] - b["duration"]) > 2:
        raise ShrinkError(f"Délka nesedí ({info['duration']:.0f} s vs {b['duration']:.0f} s)")
    if len(b["audio"]) != len(info["audio"]):
        raise ShrinkError(f"Zvukové stopy nesedí ({len(info['audio'])} vs {len(b['audio'])})")
    scores = [round(vmaf(out, src, info["duration"] * share, CHECK_S), 2) for share in CHECK_AT]
    if min(scores) < opts["vmaf"] - CHECK_SLACK:
        raise ShrinkError(f"Kvalita výsledku pod cílem (VMAF {', '.join(f'{s:g}' for s in scores)})")
    return {"vmaf_check": scores, "size": os.path.getsize(out)}
