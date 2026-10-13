"""Smaller files at a chosen quality (decisions/0009 §2a).

The user chooses the quality, not the size: a VMAF target (95 = no difference seen on a TV). VMAF predicts how a
viewer sees the difference from the original, 0–100. For each file a few short samples of its video are encoded
at several CRFs; the highest CRF (= the smallest file) whose samples still reach the target is the one. From the
samples comes the estimate (size, time) and still frames of the original and the new one side by side — before
anything is encoded for real. The speed preset changes the time and the size, not the quality (the target holds).

Audio and subtitles are copied (or big audio tracks made smaller, by choice); HDR10 stays (10-bit, its static
metadata). Dolby Vision would be lost — such files are left alone."""

import functools
import json
import logging
import math
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

SPEEDS = {"small": ("libx265", "slow"), "balanced": ("libx265", "medium"), "fast": ("libx265", "fast"),
          "gpu": ("hevc_nvenc", "p7")}         # NVIDIA's encoder (a worker's graphics card): fast, a bigger file
WHERE = ("server", "worker", "any", "together")   # who encodes: the server, a worker (the PC), whoever is free
#                                                   first, or all of them at once — the file in parts
QUALITIES = (95, 93, 90)
# mode "quality": the user's VMAF target, the size follows; mode "rate": the user's video bitrate (Mb/s) picks the
# CRF (by the samples), the preview tells the quality it gives — the user decides whether it is good enough. Either
# way the film is encoded at that one CRF: an even quality, the bitrate about the chosen one (not held to it)
DEFAULTS = {"vmaf": 95, "speed": "balanced", "max_height": 0, "audio": "copy", "min_saving": 25, "go": False,
            "mode": "quality", "mbps": 4.0, "where": "server"}
RATE_CRF_MAX = 45                          # rate mode: a low bitrate may need a CRF above the quality search's range
RATE_CLOSE = 0.07                          # a sample bitrate this close to the target is the one
CRF_PER_HALF_SIZE = 6                      # x265: about +6 CRF halves the size (first guess of the next CRF)
RATE_SLACK = 4.0                           # rate mode: the result's VMAF may be this much under the preview's
SAMPLES = (0.15, 0.38, 0.62, 0.85)        # where in the film the samples are (share of its length)
SAMPLE_S = 8
PREROLL_S = 5                              # decoded before a sample and thrown away (see cut_samples)
SAMPLE_EVERY = 3                           # samples' VMAF from every 3rd frame
THREADS_PER_SAMPLE = 6                     # samples encoded at once: one per this many CPU threads
VMAF_PER_CRF = 0.6                         # VMAF falls about this much per CRF step (x265; first guess only)
CRF_RANGE = (14, 34)
CHECK_AT = (0.3, 0.7)                      # the full result's own check: VMAF of these windows …
CHECK_S = 10
CHECK_SLACK = 2.0                          # … may be this much under the target (samples are an estimate)
BIG_AUDIO = ("truehd", "dts", "pcm", "flac")
NICE = ["nice", "-n", "10"] if os.name == "posix" else []   # the encoder gives way to everything else …
# … on Windows too (a worker: BELOW_NORMAL), and no console window popping up (the worker's tray app has none)
SPAWN = {"creationflags": 0x00004000 | 0x08000000} if os.name == "nt" else {}


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
    o["mode"] = "rate" if raw.get("mode") == "rate" else "quality"
    o["where"] = raw.get("where") if raw.get("where") in WHERE else "server"
    if o["speed"] == "gpu" and o["where"] in ("server", "together"):
        o["where"] = "worker"                       # the server has no NVIDIA card; parts: x265 only
    if raw.get("threads"):                          # a worker's own limit (its settings), not the user's choice
        try:
            o["threads"] = max(1, int(raw["threads"]))
        except (TypeError, ValueError):
            pass
    if raw.get("expect_vmaf") is not None:          # rate mode: the quality the preview showed (the result's check)
        try:
            o["expect_vmaf"] = float(raw["expect_vmaf"])
        except (TypeError, ValueError):
            pass
    try:
        m = float(raw.get("mbps", o["mbps"]))
        o["mbps"] = m if 0.2 <= m <= 80 else o["mbps"]
    except (TypeError, ValueError):
        pass
    return o


# ── what the file is ──

def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size,bit_rate:stream=index,codec_type,codec_name,profile,width,height,pix_fmt,channels,"
         "bit_rate,color_primaries,color_transfer,color_space,avg_frame_rate,r_frame_rate:stream_tags=language,BPS,BPS-eng:"
         "stream_side_data", "-of", "json", path], capture_output=True, text=True, timeout=120, **SPAWN)
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
    """x265 10-bit at ``crf`` (NVENC: its CQ)."""
    encoder, preset = SPEEDS[opts["speed"]]
    if encoder == "hevc_nvenc":
        # the slowest, best NVENC: two passes in full resolution, lookahead, adaptive quantization in space and
        # time, B-frames as references; its CQ scale is about x265's CRF. HDR: colour tags and the 10-bit pass
        # through (ffmpeg writes the static metadata from the frames)
        return ["-c:v", "hevc_nvenc", "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", f"{crf:g}", "-b:v", "0",
                "-multipass", "fullres",
                "-rc-lookahead", "32", "-spatial-aq", "1", "-temporal-aq", "1", "-b_ref_mode", "middle",
                "-pix_fmt", "p010le", *_scale_filter(info, opts)]
    params = x265_params(info) + (f":pools={opts['threads']}" if opts.get("threads") else "")
    return ["-c:v", encoder, "-preset", preset, "-crf", f"{crf:g}", "-pix_fmt", "yuv420p10le",
            "-x265-params", params, *_scale_filter(info, opts)]


def video_mbps(info: dict) -> float:
    """The video's own bitrate — the file's minus its audio tracks."""
    audio = sum(a["bit_rate"] or (640_000 if a["channels"] > 2 else 192_000) for a in info["audio"])
    return max(0.0, (info["size"] * 8 / max(1.0, info["duration"]) - audio) / 1e6)


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
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **SPAWN)
    if run.returncode != 0:
        raise ShrinkError(f"ffmpeg: {run.stderr.strip()[-300:] or 'chyba'}")
    return run


def cut_samples(path: str, info: dict, work: str) -> list[str]:
    """The video of a few places of the film, each into its own file — the references.
    Decoded from a few seconds before the place and kept losslessly (FFV1): right after a seek the decoder gives
    broken frames until its references fill up (open GOP), and noise like that cannot be encoded well — a sample
    beginning with it measured VMAF 65 at any CRF. Not joined into one file either: a transport stream's
    timestamps break at the joins and frames slip against each other."""
    parts = [os.path.join(work, f"sample{k}.mkv") for k in range(len(SAMPLES))]
    _together(lambda k: window(path, info["duration"] * SAMPLES[k], SAMPLE_S, parts[k]), len(SAMPLES))
    return parts


def window(path: str, at: float, length: float, out: str) -> str:
    """``length`` s of the video from ``at``, decoded from a few seconds earlier and cut by the decoded timestamps
    (frame-exact; a transport stream's own seek lands on another frame), kept losslessly (FFV1)."""
    start = max(0.0, at - PREROLL_S)
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", path, "-ss", f"{at - start:.2f}",
          "-t", f"{length:g}", "-map", "0:v:0", "-c:v", "ffv1", "-level", "3", "-g", "1", out], 1800)
    return out


def vmaf(distorted: str, reference: str, every: int = 1) -> float:
    """VMAF of ``distorted`` against ``reference`` — two files of the same frames (a sample and its encode, or two
    windows cut the same way), at most 1080p — the model is made for it; a smaller result is scaled up.
    ``every``: only each n-th frame measured (neighbouring frames are alike — 3× faster, the score barely moves)."""
    graph = ("[1:v]setpts=PTS-STARTPTS,scale='min(1920,iw)':-2:flags=bicubic,format=yuv420p10le,split[r1][r2];"
             "[0:v]setpts=PTS-STARTPTS[d0];[d0][r1]scale=rw:rh:flags=bicubic,format=yuv420p10le[d];"
             f"[d][r2]libvmaf=n_threads={os.cpu_count() or 2}:n_subsample={every}")
    run = subprocess.run([*NICE, "ffmpeg", "-nostdin", "-hide_banner", "-i", distorted, "-i", reference,
                          "-lavfi", graph, "-f", "null", "-"], capture_output=True, text=True, timeout=6 * 3600, **SPAWN)
    found = re.findall(r"VMAF score: ([\d.]+)", run.stderr)
    if run.returncode != 0 or not found:
        raise ShrinkError(f"VMAF: {run.stderr.strip()[-300:] or 'nezměřeno'}")
    return float(found[-1])


def _together(fn, count: int) -> None:
    """``fn(0) … fn(count-1)``, as many at once as the CPU has room for (about one per 6 threads; the server's
    4 threads: one by one). The first error is raised."""
    at_once = max(1, min(count, (os.cpu_count() or 1) // THREADS_PER_SAMPLE))
    if at_once == 1:
        for k in range(count):
            fn(k)
        return
    with ThreadPoolExecutor(max_workers=at_once) as pool:
        for f in [pool.submit(fn, k) for k in range(count)]:
            f.result()


def encode_sample(refs: list[str], info: dict, opts: dict, crf: float, work: str) -> dict:
    """Every sample at ``crf``, each measured against its own reference → the joint trial (VMAF weighted by the
    samples' lengths, sizes and times summed)."""
    paths = [os.path.join(work, f"crf{crf:g}_{k}.mkv") for k in range(len(refs))]

    def encode_one(k: int) -> None:
        _run([*NICE, "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", refs[k], "-map", "0:v:0",
              *_video_args(info, opts, crf), paths[k]], 6 * 3600)

    # an 8 s sample never fills a many-core CPU (the encoder's pipeline barely starts): several at once there;
    # the wall time of all of them is what the whole film will take per frame
    t0 = time.time()
    _together(encode_one, len(refs))
    spent = time.time() - t0
    scores = [0.0] * len(refs)

    def measure(k: int) -> None:
        scores[k] = vmaf(paths[k], refs[k], SAMPLE_EVERY)
    _together(measure, len(refs))
    weights = [probe(ref)["duration"] or SAMPLE_S for ref in refs]
    size = sum(os.path.getsize(p) for p in paths)
    score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    return {"crf": crf, "vmaf": round(score, 2), "size": size, "seconds": spent, "paths": paths,
            "duration": sum(weights), "worst": round(min(scores), 2)}


def find_crf(ref: list[str], info: dict, opts: dict, work: str, progress=None) -> tuple[dict | None, list[dict]]:
    """The highest CRF whose samples reach the target → (that trial or None, every trial). VMAF falls about evenly
    with CRF, so the next CRF is where the measured ones point (two nearest the target, else the usual slope) —
    and the one above a passing CRF is tried to be sure: mostly 2–3 encodes of the samples."""
    target = opts["vmaf"]
    trials: list[dict] = []
    best = None
    lo, hi = CRF_RANGE[0] - 1, CRF_RANGE[1] + 1   # the highest CRF known to pass, the lowest known to fail
    crf = 24 if info["height"] > 1200 else 22
    for _ in range(7):
        if progress:
            progress("sample", len(trials) + 1)
        t = encode_sample(ref, info, opts, crf, work)
        trials.append(t)
        if t["vmaf"] >= target and crf > lo:
            lo, best = crf, t
        elif t["vmaf"] < target:
            hi = min(hi, crf)
        if hi - lo <= 1:
            break
        near = sorted(trials, key=lambda x: abs(x["vmaf"] - target))[:2]
        slope = -VMAF_PER_CRF
        if len(near) == 2 and near[0]["crf"] != near[1]["crf"]:
            measured = (near[1]["vmaf"] - near[0]["vmaf"]) / (near[1]["crf"] - near[0]["crf"])
            slope = measured if measured < 0 else slope
        guess = math.floor(near[0]["crf"] + (target - near[0]["vmaf"]) / slope)
        crf = min(max(guess, lo + 1), hi - 1)
        if any(x["crf"] == crf for x in trials):
            crf = lo + 1 if lo >= CRF_RANGE[0] else hi - 1
    return best, trials


def find_crf_for_rate(refs: list[str], info: dict, opts: dict, work: str, progress=None) -> tuple[dict, list[dict]]:
    """Rate mode: the CRF whose samples come out at the user's bitrate → (that trial, every trial). The size halves
    about every 6 CRF steps, so the next CRF is where the measured size points; mostly 2–3 encodes."""
    target = opts["mbps"] * 1e6
    trials: list[dict] = []
    crf = 24 if info["height"] > 1200 else 22
    for _ in range(5):
        if progress:
            progress("sample", len(trials) + 1)
        t = encode_sample(refs, info, opts, crf, work)
        t["mbps"] = round(t["size"] * 8 / max(0.1, t["duration"]) / 1e6, 2)
        trials.append(t)
        ratio = t["mbps"] * 1e6 / target
        if abs(ratio - 1) <= RATE_CLOSE:
            break
        nxt = min(max(round(crf + CRF_PER_HALF_SIZE * math.log2(ratio)), CRF_RANGE[0]), RATE_CRF_MAX)
        if any(x["crf"] == nxt for x in trials):
            break
        crf = nxt
    best = min(trials, key=lambda x: abs(math.log(x["mbps"] * 1e6 / target)))
    return best, trials


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
    rate = opts["mode"] == "rate"
    src_mbps = round(video_mbps(info), 2)
    base["src_mbps"] = src_mbps
    if rate and src_mbps <= opts["mbps"] * (1 + RATE_CLOSE):
        return {**base, "decision": "already", "note": f"Obraz má už teď jen {src_mbps:g} Mb/s — soubor nechávám"}
    os.makedirs(work, exist_ok=True)
    if progress:
        progress("samples")
    refs = cut_samples(path, info, work)
    if rate:
        best, trials = find_crf_for_rate(refs, info, opts, work, progress)
    else:
        best, trials = find_crf(refs, info, opts, work, progress)
    view = [{k: t.get(k) for k in ("crf", "vmaf", "size", "worst", "mbps")} for t in trials]
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
            "saving": saving, "est_seconds": est_seconds, "trials": view, "stills": shots,
            "mbps": round(best["size"] * 8 / ref_s / 1e6, 2)}


# ── the real thing ──

def encode(path: str, crf: float, opts: dict, work: str, progress=None) -> tuple[str, dict]:
    """The whole file at ``crf`` into ``work`` → (path, its check). Raises ShrinkError when the result is not
    good enough (length, tracks, VMAF of two windows) — then nothing of it is left."""
    info = probe(path)
    if info["dv"]:
        raise ShrinkError("Dolby Vision by zaniklo — soubor nechávám")
    out = _out_path(path, work)
    os.makedirs(work, exist_ok=True)
    cmd = [*NICE, "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a?",
           "-map", "0:s?", "-map", "0:t?", "-c", "copy", *_video_args(info, opts, crf), *_audio_args(info, opts),
           "-progress", "pipe:1", "-nostats", out]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **SPAWN)
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
        check = verify(path, out, info, opts, work)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        if os.path.exists(out):
            os.remove(out)
        raise
    return out, check


def _out_path(path: str, work: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return os.path.join(work, os.path.splitext(os.path.basename(path))[0] + (".mp4" if ext in (".mp4", ".m4v") else ".mkv"))


def verify(src: str, out: str, info: dict, opts: dict, work: str, frames: int = 0, seams: list[float] = ()) -> dict:
    """The result against the original: length, audio tracks, VMAF of two windows — of parts joined also the
    frame count (not one lost or doubled at a join) and windows across a few joins."""
    b = probe(out)
    if abs(info["duration"] - b["duration"]) > 2:
        raise ShrinkError(f"Délka nesedí ({info['duration']:.0f} s vs {b['duration']:.0f} s)")
    if len(b["audio"]) != len(info["audio"]):
        raise ShrinkError(f"Zvukové stopy nesedí ({len(info['audio'])} vs {len(b['audio'])})")
    if frames and (got := count_frames(out)) != frames:
        raise ShrinkError(f"Počet snímků nesedí ({frames} vs {got})")
    scores = []
    places = [info["duration"] * share for share in CHECK_AT] + [max(0.0, s - CHECK_S / 2) for s in seams]
    for k, at in enumerate(places):
        a = window(src, at, CHECK_S, os.path.join(work, f"check{k}a.mkv"))
        z = window(out, at, CHECK_S, os.path.join(work, f"check{k}b.mkv"))
        scores.append(round(vmaf(z, a), 2))
        for p in (a, z):
            os.remove(p)
    if opts["mode"] == "rate":
        # the quality is the user's choice by the preview — only a result far below it is broken
        floor = float(opts.get("expect_vmaf") or 0) - RATE_SLACK
    else:
        floor = opts["vmaf"] - CHECK_SLACK
    if min(scores) < floor:
        raise ShrinkError(f"Kvalita výsledku pod cílem (VMAF {', '.join(f'{s:g}' for s in scores)})")
    return {"vmaf_check": scores, "size": os.path.getsize(out), "mbps": round(video_mbps(b), 2)}


# ── one file in parts: the server and the workers at once ──
#
# The film is cut at keyframes of the original into parts of about PART_S; each part is encoded on its own — by
# whoever is free (the server, a worker) — and the parts are joined without encoding again (concat), with the
# original's audio, subtitles and chapters. A part begins with a keyframe of the new stream too: a cut at a scene
# change costs nothing (the encoder puts a keyframe there anyway). Only the same encoder makes parts of one file
# (x265 on the CPU: the same picture whoever encodes it).

PART_S = 120
PART_LOOK_S = 15                           # a cut may move this far from PART_S to sit on a keyframe
SCENE_CUT = 0.15                           # ffmpeg's scene score of a frame that begins a new shot (soft cuts: ~0.2)
CUT_FREE = (".ts", ".m2ts", ".mts")        # a transport stream's timestamps cannot be cut frame-exact


def frame_times(path: str, absolute: bool = False) -> tuple[list[float], list[float]]:
    """Every frame's presentation time and the keyframes', in s from the file's start (what ffmpeg's -ss counts
    from; ``absolute``: as the file has them) — read from the packets, nothing decoded."""
    run = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time,flags:format=start_time", "-of", "compact=p=0:nk=0", path],
                         capture_output=True, text=True, timeout=3 * 3600, **SPAWN)
    if run.returncode != 0:
        raise ShrinkError(f"ffprobe: {run.stderr.strip()[-200:]}")
    times, keys, start = [], [], None
    for line in run.stdout.splitlines():
        f = dict(x.partition("=")[::2] for x in line.split("|"))
        if "start_time" in f:
            start = float(f["start_time"]) if f["start_time"] not in ("", "N/A") else None
            continue
        try:
            t = float(f.get("pts_time", ""))
        except ValueError:
            continue
        times.append(t)
        if "K" in f.get("flags", ""):
            keys.append(t)
    if not times:
        raise ShrinkError("Soubor nemá snímky s časem")
    times.sort()
    zero = 0.0 if absolute else start if start is not None else times[0]
    return [t - zero for t in times], sorted(k - zero for k in keys)


def split(path: str, every: float = PART_S) -> list[dict]:
    """The parts: [{"idx", "start" (a keyframe, s from the file's start), "frames"}] — [] when the file is not cut
    (a transport stream, a short file)."""
    if os.path.splitext(path)[1].lower() in CUT_FREE:
        return []
    times, keys = frame_times(path)
    if times[-1] < every * 1.5:
        return []
    cuts, t = [], every
    while t < times[-1] - every / 2:
        near = [k for k in keys if abs(k - t) <= PART_LOOK_S and (not cuts or k > cuts[-1])]
        if near:
            scores = scene_scores(path, near[0], near[-1]) if len(near) > 1 else {}

            def scene(k: float) -> float:
                close = [s for at, s in scores.items() if abs(at - k) < 0.02]
                return max(close, default=0.0)
            # the nearest keyframe at a scene change (the new stream needs one there anyway), else the nearest
            cuts.append(max(near, key=lambda k: (scene(k) >= SCENE_CUT, -abs(k - t))))
            t = cuts[-1] + every
        else:
            t += PART_LOOK_S                  # no keyframe near (a long shot): a bit further
    bounds = [times[0], *cuts, math.inf]
    parts, at = [], 0
    for k in range(len(bounds) - 1):
        end = at
        while end < len(times) and times[end] < bounds[k + 1]:
            end += 1
        parts.append({"idx": k, "start": round(bounds[k], 6), "frames": end - at})
        at = end
    return [p for p in parts if p["frames"]]


def scene_scores(path: str, start: float, end: float) -> dict[float, float]:
    """How much each frame from ``start`` to ``end`` differs from the one before (0–1, a scene change: ~0.3+) →
    {time rounded to 0.01 s: score}. A small picture decoded — a few seconds per cut."""
    pre = max(0.0, start - 1)
    run = subprocess.run([*NICE, "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{pre:.3f}", "-i", path, "-t",
                          f"{end - pre + 0.5:.3f}", "-map", "0:v:0", "-vf",
                          "scale=160:-2,select='gte(scene\\,0)',metadata=print:file=-", "-an", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=600, **SPAWN)
    scores, at = {}, None
    for line in run.stdout.splitlines():
        if line.startswith("frame:"):
            found = re.search(r"pts_time:([\d.]+)", line)
            at = pre + float(found.group(1)) if found else None
        elif "scene_score=" in line and at is not None:
            try:
                scores[round(at, 2)] = float(line.split("=")[1])
            except ValueError:
                pass
    return scores


@functools.cache
def x265_version() -> str:
    """This machine's x265 ("4.2+1-e444744"), "" without it."""
    try:
        run = subprocess.run(["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "color=size=64x64:d=0.04", "-frames:v", "1",
                              "-c:v", "libx265", "-f", "null", "-"], capture_output=True, text=True, timeout=60, **SPAWN)
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"HEVC encoder version (\S+)", run.stderr)
    return found.group(1) if found else ""


def count_frames(path: str) -> int:
    run = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                "stream=nb_read_packets", "-of", "csv=p=0", path], 3600)
    try:
        return int(run.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def encode_part(path: str, start: float, frames: int, info: dict, opts: dict, crf: float, out: str,
                progress=None, cancel=None) -> float:
    """One part: ``frames`` frames from the keyframe at ``start`` — decoded from a few seconds earlier and cut by
    the decoded timestamps (frame-exact, no broken frames after the seek) → the seconds it took. ``cancel``: a
    threading.Event — set, the encoder stops (another got the part done first)."""
    pre = max(0.0, start - PREROLL_S)
    skip = max(0.0, start - pre - 0.5 / info["fps"])      # half a frame early: the keyframe itself is kept
    args = _video_args(info, opts, crf)
    if "-x265-params" in args:                            # each part carries its own stream headers
        k = args.index("-x265-params") + 1
        args[k] += "" if "repeat-headers" in args[k] else ":repeat-headers=1"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cmd = [*NICE, "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{pre:.6f}", "-i", path, "-ss", f"{skip:.6f}",
           "-frames:v", str(frames), "-map", "0:v:0", *args, "-progress", "pipe:1", "-nostats", out]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **SPAWN)
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                raise ShrinkError("Část už je hotová jinde")
            if line.startswith("frame=") and progress:
                try:
                    progress("encode", min(99.0, int(line.split("=")[1]) / max(1, frames) * 100))
                except ValueError:
                    pass
        err = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise ShrinkError(f"ffmpeg: {err.strip()[-300:] or 'chyba'}")
        if (got := count_frames(out)) != frames:
            raise ShrinkError(f"Část má {got} snímků místo {frames}")
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if os.path.exists(out):
            os.remove(out)
        raise
    return time.time() - t0


def assemble(path: str, files: list[str], counts: list[int], info: dict, opts: dict, work: str, seams: list[float],
             progress=None) -> tuple[str, dict]:
    """The encoded parts joined (no encoding again) with the original's audio, subtitles, attachments and
    chapters → (path, its check: every frame at the original's time, VMAF across a few joins too).

    Each part's length is given (its frames / fps): taken from the part's file, the join counts the encoder's
    delay of the first frame in — a frame's gap at every join, the picture a frame later after each one (a film of
    60 parts 2.5 s behind its sound). The whole picture starts where the original's did."""
    if progress:
        progress("join")
    src_times, _ = frame_times(path, absolute=True)
    listing = os.path.join(work, "parts.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for p, n in zip(files, counts):
            f.write("file '" + p.replace("'", "'\\''") + "'\n" + f"duration {n / info['fps']:.9f}\n")
    out = _out_path(path, work)
    try:
        _run([*NICE, "ffmpeg", "-nostdin", "-v", "error", "-y", "-itsoffset", f"{src_times[0]:.6f}", "-f", "concat",
              "-safe", "0", "-i", listing, "-i", path,
              "-map", "0:v:0", "-map", "1:a?", "-map", "1:s?", "-map", "1:t?", "-map_metadata", "1", "-map_chapters", "1",
              "-c", "copy", *_audio_args(info, opts), out], 6 * 3600)
        if progress:
            progress("verify")
        out_times, _ = frame_times(out, absolute=True)
        if len(out_times) != len(src_times):
            raise ShrinkError(f"Počet snímků nesedí ({len(src_times)} vs {len(out_times)})")
        drift = max(abs(a - b) for a, b in zip(out_times, src_times))
        if drift > 0.5 / info["fps"]:
            raise ShrinkError(f"Snímky se posunuly proti originálu (až o {drift * 1000:.0f} ms) — zvuk by neseděl")
        check = verify(path, out, info, opts, work, 0, seams)
    except BaseException:
        if os.path.exists(out):
            os.remove(out)
        raise
    return out, check
