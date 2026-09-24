"""Names of movie folders and files, built from templates.

Shared by everything that puts movies on disk (renaming downloads, fixing the
library), so a movie is always named the same way no matter how it got there.

Tokens: {title} {year} {tmdb_id} {imdb_id} {res} {codec} {hdr} {source} {langs}
Legacy tokens {id} and {tmdb-{id}} (old renamer format) are still understood.
Empty tokens disappear together with leftover separators ("[ 720p]" → "[720p]", "[]" → "").
"""

import re

DEFAULT_FOLDER_FORMAT = "{year}/{title} ({year})"
DEFAULT_FILE_FORMAT = "{title} ({year}) [{res} {codec} {hdr}] [{langs}] {tmdb-{tmdb_id}}"
DEFAULT_TITLE_LANGUAGE = "en"
LOCAL_LANGUAGES = {"cs", "sk"}

_ILLEGAL = re.compile(r'[<>"/\\|?*\x00-\x1f]')
_SOURCE = re.compile(r"\b(remux|blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|sdtv|dvdrip|dvd|hdrip|tvrip)\b", re.IGNORECASE)
# Radarr-style labels — the existing library is named this way, so names change as little as possible.
_SOURCE_LABEL = {"bluray": "Bluray", "blu-ray": "Bluray", "bdrip": "Bluray", "brrip": "Bluray", "web-dl": "WEBDL",
                 "webdl": "WEBDL", "webrip": "WEBRip", "hdtv": "HDTV", "sdtv": "SDTV", "dvdrip": "DVD", "dvd": "DVD",
                 "hdrip": "HDTV", "tvrip": "SDTV", "remux": "Remux"}
# (?<!\{) — an unrendered template token like "{hdr}" is not an HDR marker
_HDR_IN_NAME = [("DV", re.compile(r"(?<!\{)\b(dv|dovi|dolby[ .]?vision)\b|uhdrdv", re.IGNORECASE)),
                ("HDR10+", re.compile(r"hdr10\+|hdr10plus", re.IGNORECASE)),
                ("HDR", re.compile(r"(?<!\{)\bhdr(10)?\b", re.IGNORECASE))]


def pick_title(titles_by_lang: dict[str, str], original_language: str, original_title: str,
               language: str = DEFAULT_TITLE_LANGUAGE, keep_local_original: bool = True) -> str:
    """Title in the wanted language: chosen → English → original.

    language="orig" always uses the original title. With keep_local_original, Czech and
    Slovak films keep their original title (Pelíšky, not "Cosy Dens").
    """
    if language == "orig" or (keep_local_original and original_language in LOCAL_LANGUAGES):
        return original_title or titles_by_lang.get(original_language, "")
    for lang in (language, "en", original_language):
        if titles_by_lang.get(lang):
            return titles_by_lang[lang]
    return original_title


def sanitize(part: str, component: bool = True) -> str:
    """Safe name: ':' → ' - ', illegal characters removed.

    component=True also strips trailing dots/spaces, which Windows/Samba clients cannot handle —
    only for a whole path component, not for a title inside it ("S.W.A.T. (2003)").
    """
    part = re.sub(r"\s*:\s*", " - ", part)
    part = _ILLEGAL.sub("", part)
    part = re.sub(r"\s+", " ", part).strip()
    return part.rstrip(". ").strip() if component else part


def resolution_label(media: dict) -> str:
    width, height = media.get("width") or 0, media.get("height") or 0
    if width >= 3200 or height >= 1600:
        return "2160p"
    if width >= 1800 or height >= 900:
        return "1080p"
    if width >= 1200 or height >= 650:
        return "720p"
    if width >= 900 or height >= 540:
        return "576p"
    return "480p" if (width or height) else ""


def codec_label(media: dict) -> str:
    codec = (media.get("video_codec") or "").lower()
    if "hevc" in codec or "265" in codec:
        return "x265"
    if "avc" in codec or "264" in codec:
        return "x264"
    if "av1" in codec:
        return "AV1"
    if "mpeg-4" in codec or "xvid" in codec:
        return "XviD"
    if "vc-1" in codec or "vc1" in codec:
        return "VC-1"
    return media.get("video_codec") or ""


def hdr_label(media: dict, name: str = "") -> str:
    """HDR/DV of a file. MediaInfo decides whenever it read the video track (it detects DV reliably);
    the name is only a fallback without MediaInfo — e.g. remote files before download."""
    if media.get("video_codec"):
        hdr = media.get("hdr") or ""
        return "" if hdr == "SDR" else hdr
    for label, pattern in _HDR_IN_NAME:
        if pattern.search(name):
            return label
    return ""


def source_label(name: str) -> str:
    m = _SOURCE.search(name)
    return _SOURCE_LABEL.get(m.group(1).lower(), m.group(1)) if m else ""


def langs_label(media: dict) -> str:
    langs: list[str] = []
    for track in media.get("audio", []):
        code = (track.get("lang") or "").upper()
        if code and code not in langs:
            langs.append(code)
    return "+".join(langs)


def render(template: str, values: dict[str, str]) -> str:
    text = template.replace("{tmdb-{id}}", "{tmdb-{tmdb_id}}")
    # {tmdb-{tmdb_id}} → "{tmdb-603}" literally in the name (Plex ID tag)
    tmdb = values.get("tmdb_id") or ""
    text = text.replace("{tmdb-{tmdb_id}}", "\x00TMDB\x00")
    for key, value in values.items():
        text = text.replace("{" + key + "}", value or "")
    text = text.replace("\x00TMDB\x00", f"{{tmdb-{tmdb}}}" if tmdb else "")
    # tidy empty groups and separators
    text = re.sub(r"\[\s*[-+ ]*\s*\]|\(\s*\)", "", text)
    text = re.sub(r"\[\s*[-+]*\s*", "[", text)
    text = re.sub(r"\s*[-+]*\s*\]", "]", text)
    return re.sub(r"\s+", " ", text).strip()


def movie_values(info: dict, media: dict, original_name: str, title: str) -> dict[str, str]:
    year = str(info.get("year") or "")
    return {
        "title": sanitize(title, component=False),
        "year": year,
        "tmdb_id": str(info.get("tmdb_id") or ""),
        "id": str(info.get("tmdb_id") or ""),  # legacy renamer token {id}
        "imdb_id": info.get("imdb_id") or "",
        "res": resolution_label(media),
        "codec": codec_label(media),
        "hdr": hdr_label(media, original_name),
        "source": source_label(original_name),
        "langs": langs_label(media),
    }


def movie_paths(info: dict, media: dict, original_name: str, title: str, ext: str,
                folder_format: str = DEFAULT_FOLDER_FORMAT, file_format: str = DEFAULT_FILE_FORMAT) -> tuple[str, str]:
    """(relative folder, file name) for a movie file."""
    values = movie_values(info, media, original_name, title)
    folder_parts = [sanitize(render(part, values)) for part in folder_format.split("/")]
    folder = "/".join(p for p in folder_parts if p)
    file_name = sanitize(render(file_format, values)) + ext.lower()
    return folder, file_name
