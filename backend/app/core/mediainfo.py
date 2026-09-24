"""Technical info about a local media file (MediaInfo).

Shared by modules that need to know what a file really contains (library import,
quality scoring, renamer). Reads only container headers — cheap even for large files.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ISO 639-2 (B/T) codes and English names → ISO 639-1
_LANG_MAP = {
    "cze": "cs", "ces": "cs", "czech": "cs",
    "slo": "sk", "slk": "sk", "slovak": "sk",
    "eng": "en", "english": "en",
    "ger": "de", "deu": "de", "german": "de",
    "fre": "fr", "fra": "fr", "french": "fr",
    "spa": "es", "spanish": "es",
    "ita": "it", "italian": "it",
    "pol": "pl", "polish": "pl",
    "hun": "hu", "hungarian": "hu",
    "rus": "ru", "russian": "ru",
    "ukr": "uk", "ukrainian": "uk",
    "jpn": "ja", "japanese": "ja",
    "kor": "ko", "korean": "ko",
    "chi": "zh", "zho": "zh", "chinese": "zh",
    "dut": "nl", "nld": "nl", "dutch": "nl",
    "swe": "sv", "swedish": "sv",
    "dan": "da", "danish": "da",
    "nor": "no", "norwegian": "no",
    "fin": "fi", "finnish": "fi",
    "rum": "ro", "ron": "ro", "romanian": "ro",
    "tur": "tr", "turkish": "tr",
    "gre": "el", "ell": "el", "greek": "el",
    "por": "pt", "portuguese": "pt",
}


def normalize_language(value: str | None) -> str:
    """'cze' / 'ces' / 'Czech' / 'cs' / 'cs-CZ' → 'cs'. Unknown → ''."""
    if not value:
        return ""
    v = str(value).strip().lower().split("-")[0].split("_")[0]
    if len(v) == 2:
        return v
    return _LANG_MAP.get(v, "")


def _hdr_label(track) -> str:
    hdr = " ".join(
        str(getattr(track, attr, "") or "")
        for attr in ("hdr_format", "hdr_format_commercial", "hdr_format_compatibility")
    )
    transfer = str(getattr(track, "transfer_characteristics", "") or "")
    if "Dolby Vision" in hdr:
        return "DV"
    if "HDR10+" in hdr or "2094" in hdr:
        return "HDR10+"
    if "HDR10" in hdr or "2086" in hdr or "PQ" in transfer:
        return "HDR10"
    if "HLG" in transfer or "HLG" in hdr:
        return "HLG"
    return "SDR"


def _int(value) -> int:
    try:
        return int(float(str(value).split("/")[0].strip()))
    except (TypeError, ValueError):
        return 0


def probe(path: str) -> dict:
    """Return technical info of a media file. Empty dict when MediaInfo cannot read it.

    Keys: duration_s, width, height, video_codec, hdr, bitrate,
          audio: [{lang, codec, channels}], subtitles: [lang]
    """
    if not Path(path).is_file():
        return {}
    try:
        from pymediainfo import MediaInfo

        info = MediaInfo.parse(path)
    except Exception as e:
        logger.warning("MediaInfo failed for %s: %s", path, e)
        return {}

    result: dict = {"duration_s": 0, "width": 0, "height": 0, "video_codec": "", "hdr": "",
                    "bitrate": 0, "audio": [], "subtitles": []}
    for track in info.tracks:
        kind = track.track_type
        if kind == "General":
            result["duration_s"] = _int(track.duration) // 1000
            result["bitrate"] = _int(track.overall_bit_rate)
        elif kind == "Video" and not result["video_codec"]:
            result["width"] = _int(track.width)
            result["height"] = _int(track.height)
            result["video_codec"] = str(track.format or "")
            result["hdr"] = _hdr_label(track)
            if not result["duration_s"]:
                result["duration_s"] = _int(track.duration) // 1000
        elif kind == "Audio":
            result["audio"].append({
                "lang": normalize_language(track.language),
                "codec": str(track.format or ""),
                "channels": _int(track.channel_s),
            })
        elif kind == "Text":
            lang = normalize_language(track.language)
            if lang:
                result["subtitles"].append(lang)
    return result


async def probe_async(path: str) -> dict:
    return await asyncio.to_thread(probe, path)
