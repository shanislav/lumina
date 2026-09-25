"""Is a file the film we are looking for? Decided by rules; only unclear cases need AI.

    yes     all words of one of the film's names are in the file name and nothing else
    unsure  extra words ("Part Two", "2", a subtitle …) or only a partial match → ask AI
    no      no word of any name, or a year in the name that is not the film's
    length  verified duration does not fit the film (±6 %, at least 8 min) — probably
            a different cut or an incomplete file; shown, but last

Years: a name carrying years is another film only when none of them fits (±1), so
titles with a number ("1917 (2019)", "2001 - …") stay fine.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from app.core.release_name import parse_name

STOPWORDS = {"the", "a", "an", "of", "and", "a", "i", "la", "le", "les", "der", "die", "das", "el", "il"}
_YEAR = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-9]{2})(?!\d)")
_EPISODE = re.compile(
    r"(?<![a-z0-9])s\d{1,2}[ ._-]?e\d{1,3}(?![0-9])"   # S01E05
    r"|(?<![a-z0-9])\d{1,2}x\d{2}(?![0-9])"             # 1x05
    r"|(?<![a-z0-9])e(p)?\d{2,3}(?![0-9a-z])",           # E02, Ep02 (mini-series parts)
    re.IGNORECASE,
)
# Words that make a name another part of a series when the film's own names do not have them.
SEQUEL_MARKERS = {"2", "3", "4", "5", "6", "ii", "iii", "iv", "two", "three", "four", "five",
                  "druha", "druhy", "treti", "ctvrta", "ctvrty", "second", "third"}
# Extra words that only say "the first part" ("Duna 1 - Dune Part One") — still the film.
FIRST_PART = {"1", "one", "i", "prvni", "first", "part", "cast", "dil"}
LENGTH_TOLERANCE = 0.06
LENGTH_MIN_DIFF_MIN = 8


@dataclass
class Verdict:
    status: str                       # yes | unsure | no | length
    reasons: list[str] = field(default_factory=list)


def tokens(text: str) -> set[str]:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    ascii_text = re.sub(r"['’ʼ]", "", ascii_text)
    return {t for t in re.split(r"[^a-z0-9]+", ascii_text) if t}


def years_mismatch(name: str, year: int | None) -> bool:
    if not year:
        return False
    years = [int(y) for y in _YEAR.findall(name)]
    return bool(years) and all(abs(y - year) > 1 for y in years)


def length_verdict(duration_s: int, runtime_min: int) -> str | None:
    """Reason when a verified duration does not fit the film, else None."""
    if not duration_s or not runtime_min:
        return None
    diff = duration_s / 60 - runtime_min
    if abs(diff) > max(LENGTH_MIN_DIFF_MIN, runtime_min * LENGTH_TOLERANCE):
        return f"délka {duration_s // 60} min, film má {runtime_min} min"
    return None


def judge(name: str, titles: list[str], year: int | None = None,
          duration_s: int = 0, runtime_min: int = 0,
          people: list[str] | None = None, other_parts: list[str] | None = None) -> Verdict:
    """people: cast/director names — allowed extra words in a name.
    other_parts: titles of the other films of the same series — a name covering one of them
    (with the words that make it that film) is another part."""
    if _EPISODE.search(name):
        return Verdict("no", ["epizoda seriálu"])
    if years_mismatch(name, year):
        found = ", ".join(sorted(set(_YEAR.findall(name))))
        return Verdict("no", [f"jiný rok ({found})"])

    facts = parse_name(name)
    file_words = tokens(" ".join([facts.title, *facts.extra_titles])) - STOPWORDS
    title_sets = [tokens(t) - STOPWORDS for t in titles if t]
    title_sets = [t for t in title_sets if t]
    all_title_words = set().union(*title_sets) if title_sets else set()

    length = length_verdict(duration_s, runtime_min)
    if not file_words or not title_sets:
        return Verdict("length" if length else "unsure", [length] if length else ["název bez titulu"])

    covered = any(t <= file_words for t in title_sets)
    for other in other_parts or []:
        other_words = tokens(other) - STOPWORDS
        distinct = other_words - all_title_words
        if distinct and other_words <= file_words:
            return Verdict("no", [f"jiný díl série ({other})"])
    people_words = set().union(*(tokens(p) for p in people or [])) if people else set()
    extra = file_words - all_title_words - people_words
    if not file_words & all_title_words:
        return Verdict("no", ["jiný název"])
    other_part = extra & SEQUEL_MARKERS
    if other_part:
        return Verdict("no", [f"jiný díl ({' '.join(sorted(other_part))})"])
    if length:
        return Verdict("length", [length])
    if covered and not extra:
        return Verdict("yes", ["název sedí"])
    if covered and extra <= FIRST_PART:
        return Verdict("yes", ["název sedí (1. díl)"])
    if covered:
        return Verdict("unsure", [f"navíc: {' '.join(sorted(extra))}"])
    return Verdict("unsure", ["název sedí jen částečně"])
