"""MediaInfo as text — what uploaders paste on a tracker's detail page ("Audio #2 / Language : Czech").
Same shape as app.core.mediainfo.probe()."""

import html
import re

from app.core.mediainfo import normalize_language
from app.core.release_langs import _LANG_TOKENS

_SECTION = re.compile(r"^(General|Obecné|Obecne|Video|Audio|Zvuk|Text|Titulky)(?:\s*#\s*\d+)?\s*$", re.I)
_SECTION_KIND = {"obecné": "general", "obecne": "general", "zvuk": "audio", "titulky": "text"}
_FIELD = re.compile(r"^([^\W\d][\w ()/,.'-]*?)\s*:\s*(.+)$")
# MediaInfo localized to Czech / Slovak — field names as in English
_FIELD_NAMES = {
    "formát": "format", "jazyk": "language", "šířka": "width", "šírka": "width", "výška": "height",
    "kanál(y)": "channel(s)", "kanály": "channel(s)", "počet kanálů": "channel(s)", "počet kanálov": "channel(s)",
    "datový tok": "bit rate", "dátový tok": "bit rate", "délka": "duration", "dĺžka": "duration", "stopáž": "duration",
    "titul": "title", "název": "title", "obchodní název": "commercial name", "hdr formát": "hdr format",
}
# the uploader's own template: "Jazyk: CZ, SK, RUS"
_TEMPLATE_LANGS = re.compile(r"^(?:Jazyk|Jazyky|Audio|Zvuk)\s*:\s*([A-Za-z]{2,3}(?:\s*[,/+]\s*[A-Za-z]{2,3})*)\s*$", re.I)


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
    # a report may start without its "General" heading: what comes before the first section is general
    sections: list[tuple[str, dict]] = [("general", {})]
    template: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if m := _SECTION.match(line):
            kind = _SECTION_KIND.get(m.group(1).lower(), m.group(1).lower())
            if kind == "general":
                if len(sections) > 1:
                    break                                # a second report (another file) — the first one counts
                continue
            sections.append((kind, {}))
        elif t := _TEMPLATE_LANGS.match(line):
            template = template or [x for x in re.split(r"\s*[,/+]\s*", t.group(1)) if x]
        elif f := _FIELD.match(line):
            name = f.group(1).strip().lower()
            sections[-1][1].setdefault(_FIELD_NAMES.get(name, name), f.group(2).strip())
    videos = [f for k, f in sections if k == "video" and "jpeg" not in f.get("format", "").lower()
              and "png" not in f.get("format", "").lower()]
    audios = [f for k, f in sections if k == "audio"]
    if not videos and not audios:
        return None
    # tracks without a language tag, but the uploader listed them: take those (in the same order)
    langs = [normalize_language(a.get("language")) for a in audios]
    if template and not any(langs) and len(template) == len(audios):
        langs = [_LANG_TOKENS.get(x.lower(), normalize_language(x)) for x in template]
    general = next((f for k, f in sections if k == "general"), {})
    video = videos[0] if videos else {}
    return {
        "duration_s": _duration(general.get("duration") or video.get("duration") or ""),
        "width": int(_number(video.get("width", ""))),
        "height": int(_number(video.get("height", ""))),
        "video_codec": video.get("format", ""),
        "hdr": _hdr(" ".join(video.get(k, "") for k in ("hdr format", "transfer characteristics"))) if video else "",
        "bitrate": _bitrate(video.get("bit rate", "") or video.get("nominal bit rate", "")),
        "audio": [{"lang": lang, "codec": a.get("commercial name") or a.get("format", ""),
                   "channels": int(_number(a.get("channel(s)", "")))} for a, lang in zip(audios, langs)],
        "subtitles": [s for s in (normalize_language(f.get("language")) for k, f in sections if k == "text") if s],
    }
