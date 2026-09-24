"""Parse movie folder/file names into search facts (title, year, ids).

Works on both folder names ("The Matrix (1999) {tmdb-603}") and release-style
file names ("Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.CZ.mkv").
"""

import re
import unicodedata
from dataclasses import dataclass, field

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m4v", ".wmv", ".flv", ".mov", ".webm", ".mpg", ".mpeg"}

_TMDB_TAG = re.compile(r"[\[{(]\s*tmdb(?:id)?[-=: ]\s*(\d+)\s*[\]})]", re.IGNORECASE)
_IMDB_ID = re.compile(r"\b(tt\d{7,8})\b")
_YEAR_IN_PARENS = re.compile(r"\((19\d{2}|20\d{2})\)")
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
# Everything from the first technical tag on is not part of the title.
_TECH = re.compile(
    r"\b(2160p|1080[pi]|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dv|dovi|remux|blu-?ray|bdrip|brrip|web-?dl|webrip|web|"
    r"hdtv|dvdrip|dvd|sdtv|tvrip|hdrip|x264|x265|h\.?264|h\.?265|hevc|avc|xvid|divx|aac|ac3|dts|atmos|truehd|"
    r"dd5\.?1|dd2\.?0|10bit|multi|dual|proper|repack|extended|unrated|remastered|cz|sk|en|eng|cze|czech|"
    r"slovak|dabing|dab|titulky|tit|subs?)\b",
    re.IGNORECASE,
)


@dataclass
class NameFacts:
    title: str = ""
    year: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    extra_titles: list[str] = field(default_factory=list)


def strip_ext(name: str) -> str:
    for ext in VIDEO_EXTS:
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


def normalize_title(text: str) -> str:
    """Lowercase, no diacritics, no punctuation — for comparing titles."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = ascii_text.replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def _separators_to_spaces(text: str, full_name: str) -> str:
    # Release names use dots/underscores instead of spaces ("Blade.Runner.2049").
    # Names that already contain spaces keep their dots ("S.W.A.T. (2003)").
    if " " in full_name.strip():
        return text.replace("_", " ")
    return re.sub(r"[._]+", " ", text)


def _clean_title(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\{[^}]*\}|\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—,;:")
    return text.strip()


def parse_name(name: str) -> NameFacts:
    """Extract title/year/ids from a folder or file name."""
    facts = NameFacts()
    base = strip_ext(name)

    if m := _TMDB_TAG.search(base):
        facts.tmdb_id = int(m.group(1))
    if m := _IMDB_ID.search(base):
        facts.imdb_id = m.group(1)

    # Year: prefer "(YYYY)"; otherwise the last plausible year that is not the whole
    # title (so "2001 - A Space Odyssey" or "1917 (2019)" keep their numeric titles).
    title_end = len(base)
    if m := _YEAR_IN_PARENS.search(base):
        facts.year = int(m.group(1))
        title_end = m.start()
    else:
        for m in reversed(list(_YEAR.finditer(base))):
            if m.start() > 0:
                facts.year = int(m.group(1))
                title_end = m.start()
                break

    head = _separators_to_spaces(base[:title_end], base)
    head = re.sub(r"\[[^\]]*\]|\{[^}]*\}|\([^)]*\)", " ", head)
    tech = _TECH.search(head)
    if tech and head[: tech.start()].strip():  # a tag at the very start is part of the title
        head = head[: tech.start()]
    facts.title = _clean_title(head)

    # "Czech title - English title" → keep both as candidates
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+|\s*/\s*", facts.title) if p.strip()]
    if len(parts) == 2 and all(re.search(r"[^\W\d]{2,}", p) for p in parts):
        facts.extra_titles = parts

    return facts
