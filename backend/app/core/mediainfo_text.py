"""MediaInfo as text — what uploaders paste on a tracker's detail page ("Audio #2 / Language : Czech").
Same shape as app.core.mediainfo.probe()."""

import html
import re

from app.core.mediainfo import normalize_language

_SECTION = re.compile(r"^(General|Video|Audio|Text)(?:\s*#\s*\d+)?\s*$", re.I)
_FIELD = re.compile(r"^([A-Za-z][A-Za-z ()/'-]*?)\s*:\s*(.+)$")


def page_text(page: str) -> str:
    """Plain text of an HTML page, one line per <br> / block."""
    text = re.sub(r"<(br|/p|/div|/tr|/li)\b[^>]*>", "\n", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())


def _number(value: str) -> float:
    m = re.search(r"\d[\d ]*(?:[.,]\d+)?", value)
    return float(m.group(0).replace(" ", "").replace(",", ".")) if m else 0


def _duration(value: str) -> int:
    if m := re.match(r"\s*(\d+):(\d+):(\d+)", value):
        h, mi, s = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + s
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*(h|min|s|ms)\b", value):
        total += {"h": 3600, "min": 60, "s": 1, "ms": 0}[unit] * int(amount)
    return total


def _bitrate(value: str) -> int:
    n = _number(value)
    return int(n * 1_000_000) if "mb/s" in value.lower() else int(n * 1000)


def _hdr(value: str) -> str:
    if "Dolby Vision" in value:
        return "DV"
    if "HDR10+" in value or "2094" in value:
        return "HDR10+"
    if "HDR10" in value or "2086" in value:
        return "HDR10"
    if "HLG" in value:
        return "HLG"
    return "SDR"


def parse(text: str) -> dict | None:
    """The first MediaInfo report in ``text``; None when there is none."""
    sections: list[tuple[str, dict]] = []
    for line in text.splitlines():
        line = line.strip()
        if m := _SECTION.match(line):
            if m.group(1).lower() == "general" and any(k == "general" for k, _ in sections):
                break                                    # a second report (another file) — the first one counts
            sections.append((m.group(1).lower(), {}))
        elif sections and (f := _FIELD.match(line)):
            sections[-1][1].setdefault(f.group(1).strip().lower(), f.group(2).strip())
    videos = [f for k, f in sections if k == "video" and "jpeg" not in f.get("format", "").lower()
              and "png" not in f.get("format", "").lower()]
    audios = [f for k, f in sections if k == "audio"]
    if not videos and not audios:
        return None
    general = next((f for k, f in sections if k == "general"), {})
    video = videos[0] if videos else {}
    return {
        "duration_s": _duration(general.get("duration") or video.get("duration") or ""),
        "width": int(_number(video.get("width", ""))),
        "height": int(_number(video.get("height", ""))),
        "video_codec": video.get("format", ""),
        "hdr": _hdr(" ".join(video.get(k, "") for k in ("hdr format", "transfer characteristics"))) if video else "",
        "bitrate": _bitrate(video.get("bit rate", "") or video.get("nominal bit rate", "")),
        "audio": [{"lang": normalize_language(a.get("language")), "codec": a.get("commercial name") or a.get("format", ""),
                   "channels": int(_number(a.get("channel(s)", "")))} for a in audios],
        "subtitles": [s for s in (normalize_language(f.get("language")) for k, f in sections if k == "text") if s],
    }
