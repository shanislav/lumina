"""Parse TV show filenames to extract show name, season, and episode numbers."""

import re
import unicodedata
from pathlib import Path

# Patterns to match season/episode in filenames (ordered by specificity)
_SE_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})"),           # S01E05
    re.compile(r"(\d{1,2})x(\d{1,3})"),                    # 1x05
    re.compile(r"[Ss]eason\s*(\d{1,2}).*[Ee]pisode\s*(\d{1,3})"),  # Season 1 Episode 5
    re.compile(r"[Ee](\d{1,3})\b"),                         # E05 (season unknown)
]

# Episode-only patterns (season comes from folder)
_EP_ONLY_PATTERNS = [
    re.compile(r"\s-\s(\d{1,3})\s-\s"),                    # " - 07 - " (common anime)
    re.compile(r"\s-\s(\d{1,3})\b"),                        # " - 07" at end
    re.compile(r"\s(\d{2,3})\s"),                            # " 07 " standalone number
]

# Season from folder path
_SEASON_FOLDER = re.compile(r"[Ss]eason\s*(\d{1,2})", re.IGNORECASE)
_SEASON_DIR = re.compile(r"(?i)^s(\d{1,2})$|(?:s[eé]rie|serie|sez[oó]na|řada|rada)\s*(\d{1,2})|(?:^|\s)(\d{1,2})\s*\.?\s*(?:s[eé]rie|serie|sez[oó]na|řada|rada)")

# Quality tags to strip from show name
_QUALITY_TAGS = re.compile(
    r"\b(2160p|1080p|720p|480p|4K|UHD|HDR|WEB-DL|WEBRip|BluRay|BDRip|"
    r"DVDRip|HDTV|x264|x265|H\.?264|H\.?265|HEVC|AVC|AAC|AC3|DTS|"
    r"FLAC|MULTI|MULTi|DUAL|REMUX|NF|AMZN|PROPER|REPACK)\b",
    re.IGNORECASE,
)

# Release group pattern (at end, in brackets)
_RELEASE_GROUP = re.compile(r"[\[\(]([^\]\)]+)[\]\)]")

# Year pattern
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

# Video extensions (one list for the whole app)
from app.core.release_name import VIDEO_EXTS  # noqa: E402,F401


def parse_tv_filename(filename: str, file_path: str = "") -> dict | None:
    """Parse a TV show filename into components.

    Args:
        filename: Just the filename (e.g. "Slayers Evolution-R - 07 - title.mkv")
        file_path: Full path (used to detect season from folder like "Season 05/")

    Returns dict with keys: show_name, season, episode, year (optional)
    or None if not a TV episode.
    """
    # a CRC checksum of anime releases ("[C190C5E5]") is not an episode number
    name = re.sub(r"\[[0-9A-Fa-f]{8}\]", "", filename)
    # Remove extension
    for ext in VIDEO_EXTS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break

    # Try to find season/episode from standard patterns
    season: int | None = None
    episode: int | None = None
    se_match_pos = len(name)  # position where S/E pattern starts

    for pattern in _SE_PATTERNS:
        match = pattern.search(name)
        if match:
            if len(match.groups()) == 2:
                season = int(match.group(1))
                episode = int(match.group(2))
            else:
                # Only episode number (E05 pattern)
                season = None
                episode = int(match.group(1))
            se_match_pos = match.start()
            break

    # If no standard pattern found, try episode-only patterns (anime style)
    if episode is None:
        for pattern in _EP_ONLY_PATTERNS:
            match = pattern.search(name)
            if match:
                episode = int(match.group(1))
                se_match_pos = match.start()
                break

    if episode is None:
        return None  # Not a TV episode

    # Try to get season from folder path (e.g. "Season 05/")
    if season is None and file_path:
        folder_match = _SEASON_FOLDER.search(file_path)
        if folder_match:
            season = int(folder_match.group(1))
        else:
            # the episode's own folder: "S10", "10. série", "1.série 2011", "Sezóna 01", "Kutil Tim 2.série CZ"
            parent = Path(file_path).parent.name
            m = _SEASON_DIR.search(parent)
            if m:
                season = int(next(g for g in m.groups() if g))

    # Extract show name: everything before the episode pattern
    show_part = name[:se_match_pos].strip()

    # Clean up show name
    show_part = _RELEASE_GROUP.sub("", show_part)  # Remove [group] tags
    show_part = _QUALITY_TAGS.sub("", show_part)    # Remove quality tags
    show_part = re.sub(r"[._]", " ", show_part)     # Dots/underscores to spaces
    show_part = re.sub(r"\s*[-–—]\s*$", "", show_part)  # Trailing dash
    show_part = re.sub(r"\s+", " ", show_part).strip()

    # If show name is empty, try getting it from folder structure
    if not show_part and file_path:
        # e.g. /downloads/tv/Slayers/Season 05/file.mkv → "Slayers"
        parts = Path(file_path).parts
        for i, p in enumerate(parts):
            if _SEASON_FOLDER.match(p) or p.lower() == "ova":
                if i > 0:
                    show_part = parts[i - 1]
                break

    # Extract year from show name
    year: str | None = None
    year_match = _YEAR.search(show_part)
    if year_match:
        year = year_match.group(1)
        if show_part.endswith(year):
            show_part = show_part[: year_match.start()].strip()

    if not show_part:
        return None

    return {
        "show_name": show_part,
        "season": season or 1,
        "episode": episode,
        "year": year,
    }


def parse_movie_filename(filename: str) -> dict | None:
    """Parse a movie filename into title and year.

    Returns dict with keys: title, year (optional)
    or None if unparseable.
    """
    name = filename
    for ext in VIDEO_EXTS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break

    # If it looks like a TV episode, skip
    if parse_tv_filename(filename) is not None:
        return None

    # Replace dots/underscores
    name = re.sub(r"[._]", " ", name)

    # Extract year
    year: str | None = None
    year_match = _YEAR.search(name)
    if year_match:
        year = year_match.group(1)
        # Take everything before the year as title
        name = name[: year_match.start()]

    # Remove quality tags and release groups
    name = _QUALITY_TAGS.sub("", name)
    name = _RELEASE_GROUP.sub("", name)
    # Remove anything in parentheses (actors, alt titles, etc.)
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\s*[-–—]\s*$", "", name)
    # Remove trailing/leading punctuation
    name = re.sub(r"[,;:]+$", "", name)
    name = re.sub(r"\s+", " ", name).strip()

    if not name:
        return None

    return {"title": name, "year": year}


def normalize_for_search(text: str) -> str:
    """Normalize text for fuzzy TMDB search matching."""
    # Strip diacritics
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    # Lowercase, strip special chars
    clean = re.sub(r"[^\w\s]", " ", ascii_text.lower())
    return re.sub(r"\s+", " ", clean).strip()
