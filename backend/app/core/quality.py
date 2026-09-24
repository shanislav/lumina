"""Quality of a video file — one model and one score for everything Lumina compares:
search offers (from names, WebShare file_info, FastShare pages) and library files
(MediaInfo). Deterministic, no AI.

Facts come from the best available source (verified > name). The score is built from
visible parts, so the UI can explain it ("1080p 70 · bitrate 9 Mb/s +8 · 5.1 +5").

Why bitrate matters more than the resolution label (measured on real offers):
"4K" AI upscales at 4.5 Mb/s carry ~20× less data per pixel than a decent 1080p, and
a "1080p" at 1.5 Mb/s looks worse than a good 720p. Bitrate is compared as H.264
equivalent — H.265 needs about half the bitrate of H.264 for the same picture.
"""

import re
from dataclasses import dataclass, field

from app.core.release_langs import parse_languages

RESOLUTIONS = ("2160p", "1080p", "720p", "SD")
RES_BASE = {"2160p": 90, "1080p": 70, "720p": 45, "SD": 20}
# H.264-equivalent bitrate (bit/s) at which a resolution looks "good" and "excellent"
GOOD = {"2160p": 25e6, "1080p": 8e6, "720p": 4e6, "SD": 1.5e6}
EXCELLENT = {"2160p": 50e6, "1080p": 20e6, "720p": 8e6, "SD": 3e6}
# how much picture a codec gets from one bit, relative to H.264
EFFICIENCY = {"H.265": 2.0, "AV1": 2.3, "H.264": 1.0, "VC-1": 0.9, "MPEG-2": 0.5, "XviD": 0.6}
LOSSLESS_AUDIO = ("truehd", "dts-hd", "dts hd", "mlp", "flac", "pcm")

_CODEC_PATTERNS = [
    ("H.265", r"hevc|h\.?265|x265"),
    ("AV1", r"\bav1\b|av01"),
    ("H.264", r"avc|h\.?264|x264"),
    ("VC-1", r"vc-?1"),
    ("MPEG-2", r"mpeg-?2"),
    ("XviD", r"xvid|divx|mpeg-?4 visual|mpeg-4(?! avc)|\bmp4v\b"),
]
_RES_IN_NAME = [
    ("2160p", r"2160p|\b4k(?:\b|hdr)|\buhd"),
    ("1080p", r"1080[pi]|\bfhd\b|full ?hd"),
    ("720p", r"720p|\bhd\b(?!r)"),
    ("SD", r"480p|576p|\bsd\b|dvdrip|\bdvd\b|\bsdtv\b|\btvrip\b"),
]
# "4KHDR", "UHDRDV" — HDR tags are often glued to other tags; "{hdr}" is an unrendered template token
_HDR_IN_NAME = [("DV", r"(?<![a-z{])(dv|dovi|dolby[ .]?vision)(?![a-z])|uhdrdv"), ("HDR10+", r"hdr10\+|hdr10plus"),
                ("HDR10", r"(?<![a-z{])hdr(10)?(?![a-z])|4khdr")]
_UPSCALE = re.compile(r"(?<![a-z])(up|upscale[d]?|ai|upscaled)(?![a-z])", re.IGNORECASE)


def codec_family(codec: str) -> str:
    c = (codec or "").lower()
    for family, pattern in _CODEC_PATTERNS:
        if re.search(pattern, c):
            return family
    return ""


def resolution_of(width: int, height: int) -> str:
    if width >= 3200 or height >= 1600:
        return "2160p"
    if width >= 1800 or height >= 900:
        return "1080p"
    if width >= 1200 or height >= 650:
        return "720p"
    return "SD" if (width or height) else ""


def _first(patterns, text: str) -> str:
    for label, pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return ""


@dataclass
class Facts:
    resolution: str = ""
    width: int = 0
    height: int = 0
    codec: str = ""
    bitrate: int = 0          # bit/s, overall (audio included) - what every source reports
    hdr: str = ""             # "", HDR10, HDR10+, DV, HLG
    audio: list[dict] = field(default_factory=list)   # [{lang, codec, channels}]
    audio_langs: list[str] = field(default_factory=list)
    subtitle_langs: list[str] = field(default_factory=list)
    duration_s: int = 0
    size: int = 0
    upscale: bool = False     # AI/upscaled 4K (marker in the name + 4K resolution)
    upscale_marker: bool = False
    verified: bool = False    # facts come from the file itself / the source, not from its name


def facts_from_name(name: str, size: int = 0, duration_s: int = 0, width: int = 0, height: int = 0) -> Facts:
    """What the name (and the little a search result tells) says about a file."""
    langs = parse_languages(name)
    res = resolution_of(width, height) or _first(_RES_IN_NAME, name)
    bitrate = int(size * 8 / duration_s) if size and duration_s else 0
    return Facts(
        resolution=res, width=width, height=height, codec=codec_family(name), bitrate=bitrate,
        hdr=_first(_HDR_IN_NAME, name), audio_langs=langs["audio"], subtitle_langs=langs["subtitles"],
        duration_s=duration_s, size=size, upscale=bool(_UPSCALE.search(name)) and res == "2160p",
        upscale_marker=bool(_UPSCALE.search(name)),
    )


def facts_from_media(media: dict, name: str = "", size: int = 0) -> Facts:
    """Facts from MediaInfo / WebShare file_info / FastShare page (same dict shape).
    Missing pieces are filled from the name (WebShare does not report subtitles, pages
    do not report HDR)."""
    from_name = facts_from_name(name, size)
    width, height = media.get("width") or 0, media.get("height") or 0
    duration = media.get("duration_s") or 0
    bitrate = media.get("bitrate") or (int(size * 8 / duration) if size and duration else 0)
    audio = [a for a in media.get("audio", []) if a]
    audio_langs = list(dict.fromkeys(a["lang"] for a in audio if a.get("lang")))
    hdr = media.get("hdr")
    res = resolution_of(width, height) or from_name.resolution
    return Facts(
        resolution=res, width=width, height=height,
        codec=codec_family(media.get("video_codec") or "") or from_name.codec,
        bitrate=bitrate,
        # MediaInfo reports HDR reliably ("SDR" = none); sources that do not report it → the name
        hdr=("" if hdr == "SDR" else hdr) if hdr else from_name.hdr,
        audio=audio,
        audio_langs=audio_langs or from_name.audio_langs,
        subtitle_langs=list(dict.fromkeys(media.get("subtitles") or [])) or from_name.subtitle_langs,
        duration_s=duration, size=size, upscale=from_name.upscale_marker and res == "2160p",
        upscale_marker=from_name.upscale_marker,
        verified=bool(audio_langs or width),
    )


# Rough bitrate of one audio track (bit/s). WebShare, FastShare and MediaInfo all report the
# overall bitrate; a file with four DTS tracks carries ~6 Mb/s of sound that is not picture.
def _track_bitrate(codec: str, channels: int) -> int:
    c = (codec or "").lower()
    surround = channels >= 6 or not channels
    if any(x in c for x in LOSSLESS_AUDIO):
        return 3_500_000
    if "dts" in c or "dca" in c:
        return 1_509_000 if surround else 768_000
    if "a/52" in c or c in ("ac3", "ac-3"):                  # FastShare "ATSC A/52B (AC-3, E-AC-3)"
        return 448_000 if surround else 192_000
    if "eac3" in c or "e-ac-3" in c:
        return 640_000 if surround else 256_000
    if "aac" in c:
        return 256_000 if surround else 128_000
    if any(x in c for x in ("mp3", "mpeg audio", "opus", "vorbis")):
        return 128_000
    return 384_000 if surround else 192_000                  # unknown codec (FastShare: 2nd+ tracks)


def audio_bitrate(f: Facts) -> int:
    """Estimated bitrate of all audio tracks: from the tracks when known, else one track per
    language in the name (at least one)."""
    if f.audio:
        return sum(_track_bitrate(a.get("codec", ""), a.get("channels") or 0) for a in f.audio)
    return max(1, len(f.audio_langs)) * _track_bitrate("", 0)


def video_bitrate(f: Facts) -> int:
    """Overall bitrate minus the estimated audio: what the picture gets. Never below half of
    the overall bitrate, so a wrong audio guess cannot sink a file."""
    if not f.bitrate:
        return 0
    return max(f.bitrate - audio_bitrate(f), f.bitrate // 2)


@dataclass
class Prefs:
    local_langs: tuple[str, ...] = ("cs", "sk")
    prefer_local_audio: bool = True
    max_size_gb: float = 0          # 0 = no limit
    hdr: str = "neutral"            # prefer | neutral | avoid


def prefs_from_settings(cfg: dict) -> Prefs:
    languages = [l.strip() for l in cfg.get("languages", "cs").split(",") if l.strip()]
    local = tuple(l for l in languages if l != "en") or ("cs", "sk")
    try:
        max_size = float(cfg.get("quality_max_size_gb") or 0)
    except ValueError:
        max_size = 0
    return Prefs(
        local_langs=local,
        prefer_local_audio=cfg.get("quality_prefer_local", "true") != "false",
        max_size_gb=max_size,
        hdr=cfg.get("quality_hdr") or "neutral",
    )


@dataclass
class Score:
    score: int
    parts: list[tuple[str, int]]
    summary: str


def score(f: Facts, prefs: Prefs | None = None) -> Score:
    prefs = prefs or Prefs()
    parts: list[tuple[str, int]] = []
    if not f.resolution:
        return Score(0, [("kvalita neznámá", 0)], summary(f))

    base = RES_BASE[f.resolution]
    parts.append((f.resolution, base))
    eff = EFFICIENCY.get(f.codec, 1.0)
    if f.bitrate:
        vb = video_bitrate(f)
        eq = vb * eff
        good, excellent = GOOD[f.resolution], EXCELLENT[f.resolution]
        if eq < good:
            penalty = round(base * 0.6 * (1 - eq / good))
            if penalty:
                parts.append((f"nízký bitrate videa ~{vb / 1e6:.1f} Mb/s", -penalty))
        else:
            bonus = round(10 * min(1.0, (eq - good) / (excellent - good)))
            if bonus:
                parts.append((f"bitrate videa ~{vb / 1e6:.1f} Mb/s", bonus))
    else:
        parts.append(("bitrate neznámý", -round(base * 0.1)))

    if f.codec in ("H.265", "AV1"):
        parts.append((f.codec, 3))           # same picture in about half the space
    elif f.codec == "XviD":
        parts.append(("XviD", -5))           # old codec, weak player support

    if f.hdr:
        points = {"prefer": 7 if f.hdr == "DV" else 5, "neutral": 2, "avoid": -10}[prefs.hdr]
        parts.append((f.hdr, points))
    if f.upscale:
        parts.append(("upscale do 4K", -15))

    channels = max((a.get("channels") or 0) for a in f.audio) if f.audio else 0
    if channels >= 8:
        parts.append(("7.1", 6))
    elif channels >= 6:
        parts.append(("5.1", 5))
    if any(any(x in (a.get("codec") or "").lower() for x in LOSSLESS_AUDIO) for a in f.audio):
        parts.append(("bezztrátový zvuk", 3))

    if prefs.max_size_gb and f.size > prefs.max_size_gb * 1e9:
        parts.append((f"nad limit {prefs.max_size_gb:g} GB", -30))

    total = max(0, min(100, sum(p for _, p in parts)))
    return Score(total, parts, summary(f))


def summary(f: Facts) -> str:
    bits = [f.resolution or "?", f.codec]
    if f.bitrate:
        bits.append(f"{f.bitrate / 1e6:.1f} Mb/s")
    bits.append(f.hdr)
    channels = max((a.get("channels") or 0) for a in f.audio) if f.audio else 0
    if channels:
        bits.append({1: "mono", 2: "2.0", 6: "5.1", 8: "7.1"}.get(channels, f"{channels}ch"))
    return " · ".join(b for b in bits if b)


def language_tier(f: Facts, prefs: Prefs | None = None) -> int:
    """3 verified local audio · 2 local audio by name · 1 local subtitles only · 0 other."""
    prefs = prefs or Prefs()
    local = set(prefs.local_langs)
    if any(l in local for l in f.audio_langs):
        return 3 if f.verified else 2
    if any(l in local for l in f.subtitle_langs):
        return 1
    return 0
