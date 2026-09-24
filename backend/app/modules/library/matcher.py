"""Decide which TMDB movie a file is. Pure functions — no I/O, easy to test.

Every candidate gets points for evidence found in the file itself (duration,
audio languages) and in its names (year, title), plus small bonuses for hints
from other tools (NFO, Radarr) which can be wrong — see docs/decisions/0002.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.modules.library.naming import normalize_title

# Hint sources and their weight. An explicit {tmdb-ID} in the name is written by
# Lumina itself (or the user), so it is trusted much more than third-party NFO/Radarr.
HINT_WEIGHTS = {"name_tag": 40, "nfo": 10, "nfo_imdb": 10, "radarr": 10}

AUTO_MIN_SCORE = 60
AUTO_MIN_MARGIN = 15
LOCAL_LANGUAGES = {"cs", "sk"}


@dataclass
class FileEvidence:
    titles: list[str] = field(default_factory=list)
    years: set[int] = field(default_factory=set)
    duration_min: float | None = None
    audio_langs: set[str] = field(default_factory=set)
    hints: dict[int, list[str]] = field(default_factory=dict)  # tmdb_id -> hint sources


@dataclass
class Scored:
    candidate: dict
    score: int
    reasons: list[str]


def _title_similarity(names: list[str], candidate_titles: list[str]) -> float:
    best = 0.0
    for a in filter(None, (normalize_title(n) for n in names)):
        for b in filter(None, (normalize_title(t) for t in candidate_titles)):
            best = max(best, 1.0 if a == b else SequenceMatcher(None, a, b).ratio())
    return best


def score_candidate(ev: FileEvidence, cand: dict) -> Scored:
    score = 0
    reasons: list[str] = []

    runtime = cand.get("runtime") or 0
    if ev.duration_min and runtime:
        diff = abs(ev.duration_min - runtime)
        if diff <= 4:
            score += 35
            reasons.append(f"délka sedí ({ev.duration_min:.0f} / {runtime} min)")
        elif diff <= 10:
            score += 25
            reasons.append(f"délka skoro sedí ({ev.duration_min:.0f} / {runtime} min)")
        elif diff <= 20:
            score += 5
            reasons.append(f"délka se liší o {diff:.0f} min")
        else:
            score -= 30
            reasons.append(f"délka nesedí ({ev.duration_min:.0f} / {runtime} min)")
        if runtime < 45 and ev.duration_min > 60:
            score -= 50
            reasons.append("TMDB je krátký film")

    year = cand.get("year")
    if ev.years and year:
        closest = min(abs(year - y) for y in ev.years)
        if closest == 0:
            score += 20
            reasons.append(f"rok {year} sedí")
        elif closest == 1:
            score += 10
            reasons.append(f"rok {year} ±1")
        else:
            score -= 15
            reasons.append(f"rok {year} nesedí")

    similarity = _title_similarity(ev.titles, cand.get("titles") or [cand.get("title", "")])
    title_points = round(similarity * 25)
    score += title_points
    if similarity >= 0.9:
        reasons.append("název sedí")
    elif similarity < 0.5:
        reasons.append("název se liší")

    lang = cand.get("original_language", "")
    if ev.audio_langs and lang:
        if lang in ev.audio_langs:
            score += 5
            if lang in LOCAL_LANGUAGES:
                score += 10
                reasons.append(f"původní jazyk {lang} je ve zvuku")
        elif lang in LOCAL_LANGUAGES and not (ev.audio_langs & LOCAL_LANGUAGES):
            score -= 10
            reasons.append(f"původní jazyk {lang} chybí ve zvuku")

    for source in ev.hints.get(cand["tmdb_id"], []):
        score += HINT_WEIGHTS.get(source, 0)
        reasons.append(f"tip: {source}")

    return Scored(candidate=cand, score=score, reasons=reasons)


def decide(scored: list[Scored]) -> tuple[str, list[Scored]]:
    """Return (status, candidates sorted by score). Status: matched | review | unmatched."""
    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    if not ranked:
        return "unmatched", ranked
    best = ranked[0].score
    second = ranked[1].score if len(ranked) > 1 else None
    if best >= AUTO_MIN_SCORE and (second is None or best - second >= AUTO_MIN_MARGIN):
        return "matched", ranked
    return "review", ranked
