"""Quality profiles — what the user wants for a film ("Full HD", "4K", "Pro děti — CZ dabing").

The conditions are a HARD filter (decisions/0005): an offer that breaks one is not suitable at all;
the score (core/quality) only orders what passes. `cutoff`: an owned version that passes and
reaches this score is good enough — no better version is looked for.

Used for offers (evaluated rows from core/offers) and for library files (the same row shape via
`row_from_media`), so both are judged the same way.
"""

import json
from dataclasses import asdict, dataclass, field

from app.core.quality import Prefs, facts_from_media, language_tier, score, video_bitrate

RESOLUTIONS = ["SD", "720p", "1080p", "2160p"]
HDR_MODES = ("any", "require", "forbid")

QUALITY_PROFILES = """
CREATE TABLE IF NOT EXISTS quality_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    is_default INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Profile:
    id: int = 0
    name: str = ""
    is_default: bool = False
    min_resolution: str = ""          # "" = any
    max_resolution: str = ""
    audio_langs: list[str] = field(default_factory=list)   # wanted audio languages ("cs", "sk", "en" …); empty = any
    audio_mode: str = "any"           # any = one of them is enough | all = every one of them
    codecs: list[str] = field(default_factory=list)   # allowed ("H.265", "AV1", "H.264" …); empty = any
    hdr: str = "any"                  # any | require | forbid
    max_size_gb: float = 0
    min_video_mbps: float = 0         # video bitrate (audio left out), 0 = no limit
    max_video_mbps: float = 0
    min_score: int = 0
    cutoff: int = 0                   # owned version at/above this score (and passing) = done; 0 = never done

    def as_dict(self) -> dict:
        return asdict(self)


CONFIG_FIELDS = [f for f in Profile.__dataclass_fields__ if f not in ("id", "name", "is_default")]


def profile_from_row(row) -> Profile:
    config = json.loads(row["config"] or "{}")
    if config.get("require_local_audio") and not config.get("audio_langs"):
        config["audio_langs"] = ["cs", "sk"]          # profiles saved before 2026-09-30: "must have CZ/SK"
    p = Profile(id=row["id"], name=row["name"], is_default=bool(row["is_default"]))
    for key in CONFIG_FIELDS:
        if key in config:
            setattr(p, key, config[key])
    return p


def profile_config(p: Profile) -> str:
    return json.dumps({k: getattr(p, k) for k in CONFIG_FIELDS})


DEFAULT_PROFILES = [
    Profile(name="Standard", is_default=True, min_resolution="720p", audio_langs=["cs", "sk"], cutoff=70),
    Profile(name="Full HD", min_resolution="1080p", max_resolution="1080p", audio_langs=["cs", "sk"], cutoff=75),
    Profile(name="4K", min_resolution="2160p", audio_langs=["cs", "sk"], cutoff=85),
]


def seed_default_profiles() -> str:
    """Migration: the three starting profiles, only into an empty table."""
    rows = ", ".join(
        f"('{p.name}', '{profile_config(p)}', {int(p.is_default)})" for p in DEFAULT_PROFILES
    )
    return (f"INSERT INTO quality_profiles (name, config, is_default) SELECT * FROM (VALUES {rows}) "
            f"WHERE NOT EXISTS (SELECT 1 FROM quality_profiles);")


def _lang_label(code: str) -> str:
    return "CZ" if code.lower() == "cs" else code.upper()


def _rank(resolution: str) -> int:
    return RESOLUTIONS.index(resolution) if resolution in RESOLUTIONS else -1


def block(row: dict, p: Profile) -> str | None:
    """Why a file does NOT suit the profile (short Czech reason for the UI), or None.

    row: an evaluated offer / library file — resolution, codec, hdr, size, video_bitrate, lang_tier,
    quality_score. Unknown values do not pass a condition that needs them.
    """
    res = row.get("resolution") or ""
    if p.min_resolution and _rank(res) < _rank(p.min_resolution):
        return f"rozlišení {res or '?'} < {p.min_resolution}"
    if p.max_resolution and (res == "" or _rank(res) > _rank(p.max_resolution)):
        return f"rozlišení {res or '?'} > {p.max_resolution}"
    if p.audio_langs:
        have = {l.lower() for l in row.get("audio_langs") or []}
        want = {l.lower() for l in p.audio_langs}
        if p.audio_mode == "all" and not want <= have:
            return "chybí zvuk " + "+".join(sorted(_lang_label(l) for l in want - have))
        if p.audio_mode != "all" and not want & have:
            return "bez zvuku " + "/".join(_lang_label(l) for l in p.audio_langs)
    if p.codecs and (row.get("codec") or "") not in p.codecs:
        return f"kodek {row.get('codec') or '?'}"
    if p.hdr == "require" and not row.get("hdr"):
        return "bez HDR"
    if p.hdr == "forbid" and row.get("hdr"):
        return f"{row['hdr']} nechceš"
    if p.max_size_gb and (row.get("size") or 0) > p.max_size_gb * 1e9:
        return f"větší než {p.max_size_gb:g} GB"
    vb = (row.get("video_bitrate") or 0) / 1e6
    if p.min_video_mbps and vb < p.min_video_mbps:
        return f"video {vb:.1f} Mb/s < {p.min_video_mbps:g}"
    if p.max_video_mbps and vb > p.max_video_mbps:
        return f"video {vb:.1f} Mb/s > {p.max_video_mbps:g}"
    if p.min_score and (row.get("quality_score") or 0) < p.min_score:
        return f"skóre {row.get('quality_score') or 0} < {p.min_score}"
    return None


def suitable(rows: list[dict], p: Profile) -> list[dict]:
    """Offers of the right film the profile allows, best first: verified ones first (an unverified
    name can promise too much), then the score."""
    ok = [r for r in rows if r.get("film") in ("yes", "unsure") and block(r, p) is None]
    return sorted(ok, key=lambda r: (not r.get("verified"), -(r.get("quality_score") or 0)))


def reached_cutoff(owned: dict, p: Profile) -> bool:
    """The owned version is good enough — stop looking for a better one."""
    return bool(p.cutoff) and block(owned, p) is None and (owned.get("quality_score") or 0) >= p.cutoff


def row_from_media(media: dict, filename: str, size: int, prefs: Prefs) -> dict:
    """A library file in the row shape `block` understands."""
    facts = facts_from_media(media, filename, size)
    return {
        "resolution": facts.resolution, "codec": facts.codec, "hdr": facts.hdr, "size": size,
        "video_bitrate": video_bitrate(facts), "lang_tier": language_tier(facts, prefs),
        "audio_langs": facts.audio_langs,
        "quality_score": score(facts, prefs).score,
    }


async def load_profiles() -> list[Profile]:
    from app.db import get_db
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM quality_profiles ORDER BY is_default DESC, id")
        return [profile_from_row(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_profile(profile_id: int | None) -> Profile:
    """The profile, or the default one when it is missing / was deleted."""
    profiles = await load_profiles()
    by_id = {p.id: p for p in profiles}
    if profile_id in by_id:
        return by_id[profile_id]
    return next((p for p in profiles if p.is_default), profiles[0] if profiles else Profile(name="—"))
