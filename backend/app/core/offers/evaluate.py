"""Evaluate a found file: which film is it, how good is it, which languages does it have.

Used for the first answer of a search (names + whatever is already cached) and again
when verified details arrive from the source — the same rules, one place.
"""

import re
from dataclasses import dataclass, field

from app.core.episode_match import judge_episode, parse_episode
from app.core.film_match import judge
from app.core.quality import Prefs, facts_from_media, facts_from_name, language_tier, prefs_from_settings, score, video_bitrate  # noqa: F401

SAMPLE_MAX_BYTES = 300 * 1024 * 1024
# relevance shown for rule-based verdicts (AI gives its own number for unclear ones)
RELEVANCE = {"yes": 95, "length": 60, "unsure": 50, "no": 10}
# order of verdicts in the recommended sort
FILM_ORDER = {"yes": 0, "unsure": 1, "length": 2, "no": 3}


@dataclass
class MovieContext:
    titles: list[str] = field(default_factory=list)
    year: int | None = None
    runtime: int = 0
    people: list[str] = field(default_factory=list)        # cast/director — allowed in file names
    other_parts: list[str] = field(default_factory=list)   # other films of the series
    namesakes: list[dict] = field(default_factory=list)    # other films of the same name and year
    # a TV episode instead of a film: {"season": 1, "episode": 3}; titles = the show's names,
    # runtime = the episode's
    episode: dict | None = None

    def as_dict(self) -> dict:
        return {"titles": self.titles, "year": self.year, "runtime": self.runtime,
                "people": self.people, "other_parts": self.other_parts, "namesakes": self.namesakes,
                "episode": self.episode}

    @classmethod
    def from_dict(cls, d: dict | None) -> "MovieContext":
        d = d or {}
        return cls(titles=d.get("titles") or [], year=d.get("year"), runtime=d.get("runtime") or 0,
                   people=d.get("people") or [], other_parts=d.get("other_parts") or [],
                   namesakes=d.get("namesakes") or [], episode=d.get("episode") or None)


def evaluate(name: str, size: int, ctx: MovieContext, prefs: Prefs, details: dict | None = None,
             duration_s: int = 0, width: int = 0, height: int = 0) -> dict:
    facts = (facts_from_media(details, name, size) if details
             else facts_from_name(name, size, duration_s, width, height))
    parts, pack = 1, False
    if ctx.episode:
        info = parse_episode(name)
        pack = info.is_pack
        # a pack of a whole season: its size says nothing about one episode's bitrate
        parts = 0 if info.is_pack else max(1, len(info.episodes))
    if not facts.bitrate and size and ctx.runtime and parts:
        # WebShare search (and some file_info answers) know no duration — the film's runtime from
        # TMDB gives the bitrate well enough to compare. Not used for the length check.
        facts.bitrate = int(size * 8 / (ctx.runtime * 60 * parts))
        facts.bitrate_estimated = True
    q = score(facts, prefs)
    if "sample" in name.lower() and 0 < size < SAMPLE_MAX_BYTES:
        film, reasons = "no", ["sample"]
    elif ctx.episode:
        verdict = judge_episode(name, ctx.titles, int(ctx.episode.get("season") or 0),
                                int(ctx.episode.get("episode") or 0), facts.duration_s, ctx.runtime)
        film, reasons = verdict.status, verdict.reasons
    else:
        verdict = judge(name, ctx.titles, ctx.year, facts.duration_s, ctx.runtime, ctx.people, ctx.other_parts,
                        ctx.namesakes)
        film, reasons = verdict.status, verdict.reasons
    tier = language_tier(facts, prefs)
    ev = {
        "film": film,
        "film_reasons": reasons,
        "quality_score": q.score,
        "quality_summary": q.summary,
        "quality_parts": [[label, points] for label, points in q.parts],
        "resolution": facts.resolution,
        "codec": facts.codec,
        "bitrate": facts.bitrate,
        "video_bitrate": video_bitrate(facts),
        "hdr": facts.hdr,
        "duration_s": facts.duration_s,
        "audio_langs": facts.audio_langs,
        "subtitle_langs": facts.subtitle_langs,
        "audio": facts.audio,
        "verified": facts.verified,
        "lang_tier": tier,
        "is_dubbed": tier >= 2,
        "pack": pack,               # a TV season / show pack holding the episode, not the episode alone
    }
    if ctx.episode and ctx.episode.get("by_name") is not None:
        want = (int(ctx.episode.get("season") or 0), int(ctx.episode.get("episode") or 0))
        hit = ctx.episode["by_name"].get(name)
        ev = judge_episode_name(ev, want, hit)
        if not hit and ctx.episode.get("mixed") and ev["film"] == "yes" and not pack:
            # named files of this number are other episodes too: a file without a name may be either
            ev["film"], ev["film_reasons"] = "unsure", [*ev["film_reasons"], "bez názvu dílu — uploadeři tento díl číslují různě"]
    return ev


def judge_episode_name(ev: dict, want: tuple[int, int], hit) -> dict:
    """The episode's own name in a release against TMDB's names — ``hit`` [[season, episode], sure, the
    name] (the library's ``episode_names.release_episode``, kept in ``MovieContext.episode["by_name"]`` per
    file name, so a re-evaluation with verified details judges the same): uploaders number by another order
    ("Městečko South Park - S01E02 - Sopka" is TMDB's S01E03), the name is mostly right. A name of the wanted
    episode makes a file of another number right; a name surely of another episode makes it wrong.
    Packs and other shows stay as judged."""
    if not hit or ev.get("pack") or "jiný seriál" in (ev.get("film_reasons") or []):
        return ev
    (s, e), sure, title = hit
    key = (int(s), int(e))
    if key == want:
        ev["name_ok"] = True
        if ev["film"] == "no":
            ev["film"], ev["film_reasons"] = "yes", [f"podle názvu dílu „{title}“ (číslo v souboru je jiné)"]
        else:
            ev["film_reasons"] = [*ev["film_reasons"], f"název dílu sedí („{title}“)"]
    elif sure:
        ev["name_ok"] = False
        ev["film"], ev["film_reasons"] = "no", [f"podle názvu jiný díl: „{title}“ = S{key[0]:02d}E{key[1]:02d}"]
    return ev


def recommended_key(row: dict, prefs: Prefs) -> tuple:
    """Right film first → wanted language (if preferred) → an episode whose own name says it is the one →
    quality → smaller file."""
    return (
        FILM_ORDER.get(row.get("film"), 1),
        -(row.get("lang_tier", 0) if prefs.prefer_local_audio else 0),
        -int(bool(row.get("name_ok"))),
        -row.get("quality_score", 0),
        row.get("size", 0),
    )


def year_of(text: str) -> int | None:
    years = re.findall(r"(?<!\d)(19[0-9]{2}|20[0-9]{2})(?!\d)", text)
    return int(years[-1]) if years else None
