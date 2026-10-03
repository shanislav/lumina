"""The files of a whole TV season, grouped into releases ("sets") — docs SERIALY, F2.

One uploader names every episode of a season the same way ("Sexuální výchova S03E## CZ dab 1080p") and
encodes them alike, so the episodes of one release share the name around the episode number and the
bitrate. A season is best downloaded from one release (the same quality, the same sound); language
comes first though: a CZ episode from another release beats an EN one from the main release.

    find_season_offers  search all sources for the season, judge every file as an episode of it
    group_sets          the files of one release together
    plan_season         which file for each wanted episode: the best release, gaps from the others
"""

import logging
import re
import statistics
from dataclasses import dataclass, field

from app.clients.tmdb import TMDBClient
from app.core.episode_match import _plain, parse_episode, show_fit
from app.core.film_match import tokens
from app.core.offers.details import cached_details, get_details
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.offers.search import _clean_title, _norm, _unique_names, search_sources
from app.core.quality import Prefs, prefs_from_settings
from app.sources.base import TORRENT_SOURCES
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

# words of a release name that say how it was made (the rest around the episode number is the episode's title)
TECH = {
    "480p", "576p", "720p", "1080p", "2160p", "4k", "uhd", "hd", "fhd", "sd",
    "x264", "x265", "h264", "h265", "hevc", "avc", "av1", "xvid", "divx", "10bit",
    "web", "webrip", "webdl", "dl", "bluray", "bdrip", "brrip", "dvdrip", "hdtv", "tvrip", "remux",
    "nf", "amzn", "hmax", "dsnp", "atvp", "skst", "pmtp",
    "cz", "sk", "en", "eng", "cze", "cs", "slo", "dab", "dabing", "tit", "titulky", "sub", "subs", "multi", "dual",
    "aac", "ac3", "eac3", "dd", "ddp", "dd5", "ddp5", "atmos", "dts", "truehd",
    "hdr", "hdr10", "dv", "proper", "repack",
}
SAMPLE_SETS = 6          # releases whose one sample file is verified at its source
BITRATE_SPLIT = 1.8      # an episode with 1.8× more / less data per minute than its set's median is another encode


@dataclass
class SeasonOffers:
    season: int
    episodes: list[int]                                   # the wanted ones
    ctx: MovieContext
    prefs: Prefs
    rows: list[dict] = field(default_factory=list)        # every file of the season, judged
    sets: list[dict] = field(default_factory=list)
    packs: list[dict] = field(default_factory=list)       # torrents of the whole season / show
    plan: list[dict] = field(default_factory=list)        # [{episode, set, row}]


def release_key(name: str, ext: str = "") -> str:
    """What the episodes of one release have in common: the show's name before the episode mark, the
    technical words after it, and the file type."""
    info = parse_episode(name)
    text = _plain(name)
    after = text[len(info.prefix):]
    tech = sorted(t for t in tokens(after) if t in TECH)
    ext = (ext or (name.rsplit(".", 1)[-1] if "." in name[-6:] else "")).lower()
    marker = "x" if re.search(r"\d+x\d+", text) and not re.search(r"s\d+ ?e\d+", text) else "se"
    return f"{_norm(info.prefix)}|{marker}|{' '.join(tech)}|{ext}|of{1 if info.of_total else 0}"


def _per_minute(row: dict, runtime: int) -> float:
    if row.get("bitrate"):
        return row["bitrate"]
    eps = max(1, len(parse_episode(row["name"]).episodes))
    return row["size"] * 8 / max(1, runtime * 60 * eps)


def group_sets(rows: list[dict], season: int, wanted: list[int], runtime: int) -> list[dict]:
    """Episode files of the season → releases, the most complete and best first."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("pack") or row.get("film") not in ("yes", "unsure", "length"):
            continue
        groups.setdefault(release_key(row["name"]), []).append(row)
    sets: list[dict] = []
    for key, members in groups.items():
        # the same name pattern, but another encode (a different bitrate) is another release
        med = statistics.median(_per_minute(r, runtime) for r in members) or 1
        parts: dict[str, list[dict]] = {}
        for r in members:
            ratio = _per_minute(r, runtime) / med
            sub = "" if 1 / BITRATE_SPLIT <= ratio <= BITRATE_SPLIT else ("+" if ratio > 1 else "-")
            parts.setdefault(sub, []).append(r)
        for sub, files in parts.items():
            by_episode: dict[int, dict] = {}
            for r in files:
                # the episodes the file was placed on (its name's episode name, another numbering), else its numbers
                for ep in r.get("episodes") or parse_episode(r["name"]).episodes:
                    have = by_episode.get(ep)
                    # copies of one file on two sources: WebShare first (verified details, one API call)
                    if not have or (r.get("verified"), r["source"] == "webshare", r.get("quality_score", 0)) > \
                            (have.get("verified"), have["source"] == "webshare", have.get("quality_score", 0)):
                        by_episode[ep] = r
            covered = sorted(e for e in by_episode if e in wanted)
            if not covered:
                continue
            scores = [by_episode[e].get("quality_score", 0) for e in covered]
            langs = sorted({l for e in covered for l in by_episode[e].get("audio_langs") or []})
            tiers = [by_episode[e].get("lang_tier", 0) for e in covered]
            sample = by_episode[covered[0]]
            sets.append({
                "key": key + sub,
                "label": _label(sample["name"]),
                "episodes": {e: by_episode[e] for e in sorted(by_episode)},
                "covered": covered,
                "coverage": len(covered),
                "score": round(statistics.median(scores)),
                "resolution": statistics.mode([by_episode[e].get("resolution") or "?" for e in covered]),
                "langs": langs,
                "local": statistics.median(tiers) >= 2,
                "local_subs": statistics.median(tiers) >= 1,      # Czech/Slovak subtitles at least
                "size": sum(by_episode[e]["size"] for e in covered),
                "sources": sorted({by_episode[e]["source"] for e in covered}),
            })
    sets.sort(key=lambda s: (-s["local"], -s["local_subs"], -s["coverage"], -s["score"]))
    return sets


def _label(name: str) -> str:
    """The release's name with the episode number left out: "Sexuální výchova S03E·· CZ dab 1080p"."""
    label = re.sub(r"(?i)(s\d{1,2} ?e)\d{1,3}(?:-\d{1,3})?", r"\1··", name)
    label = re.sub(r"(?i)(\d{1,2}x)\d{2,3}", r"\1··", label)
    label = re.sub(r"(?i)(epizoda|episode|díl|dil)([ ._])\d{1,3}", r"\1\2··", label)
    return label


def plan_season(sets: list[dict], wanted: list[int]) -> list[dict]:
    """A file for each wanted episode: from the best release; a gap — or an episode the best release
    has only without the wanted language — from the next one that has it (language first)."""
    plan: list[dict] = []
    if not sets:
        return plan
    best = sets[0]
    for ep in wanted:
        choice = None
        own = best["episodes"].get(ep)
        if own and (own.get("lang_tier", 0) >= 2 or not best["local"]):
            choice = (best, own)
        else:
            candidates = [(s, s["episodes"][ep]) for s in sets if ep in s["episodes"]]
            candidates.sort(key=lambda c: (-(c[1].get("lang_tier", 0) >= 2), -c[0]["coverage"], -c[1].get("quality_score", 0)))
            choice = candidates[0] if candidates else None
        if choice:
            plan.append({"episode": ep, "set": choice[0]["key"], "row": choice[1]})
    return plan


async def _verify_samples(out: SeasonOffers, titles: list[str], runtime: int) -> bool:
    """One file of each of the best releases verified at its source (real resolution, codec, sound);
    the other episodes of the release are encoded alike, so they take its findings (their own size).
    One request per release instead of one per episode."""
    samples = []
    for st in out.sets[:SAMPLE_SETS]:
        files = list(st["episodes"].values())
        if any(f.get("verified") for f in files):
            continue
        sample = next((f for f in files if f["source"] == "webshare"), files[0])
        if sample["source"] in ("webshare", "fastshare"):
            samples.append((st, sample))
    if not samples:
        return False
    got = await get_details([{"source_id": f["source_id"], "ident": f["ident"], "name": f["name"]} for _, f in samples])
    changed = False
    for st, sample in samples:
        details = got.get(f"{sample['source_id']}:{sample['ident']}")
        if not details:
            continue
        for f in st["episodes"].values():
            mine = details if f is sample else {**details, "bitrate": 0, "duration_s": 0}   # sizes differ
            episode = {"season": out.season, "episode": (f.get("episodes") or out.episodes)[0]}
            if f.get("name_hit"):
                episode["by_name"] = {f["name"]: f["name_hit"]}
            ctx = MovieContext(titles=titles, runtime=runtime, episode=episode)
            ev = evaluate(f["name"], f["size"], ctx, out.prefs, mine)
            f.update({**ev, "verified": f is sample, "sampled": True,
                      "quality": ev["resolution"] or f.get("quality") or "unknown"})
        changed = True
    return changed


def season_queries(titles: list[str], season: int) -> tuple[list[str], list[str]]:
    """WebShare finds a whole season by "Show S03" (measured: Sex Education S03 — all 8 episodes in
    100 results); FastShare answers the plain name; trackers name packs "Show 1. - S03", "komplet"."""
    names = [_clean_title(t) for t in _unique_names(titles)]
    names = [n for n in names if len(_norm(n)) >= 2]
    if not names:
        return [], []
    ss = f"S{season:02d}"
    # the local name first, then the English one (trackers know shows by it) — not every translation
    latin = next((n for n in names if n.isascii() and re.search(r"[A-Za-z]", n)), names[0])
    ddl = _unique_names([f"{names[0]} {ss}", f"{latin} {ss}"]) + [names[0]]
    return ddl, [f"{latin} {ss}", latin]


async def find_season_offers(cfg: dict, tmdb_id: int, season: int, wanted: list[int] | None = None,
                             torrent: bool = True, by_name=None, alt: dict | None = None) -> SeasonOffers:
    """``by_name``: file name → the TMDB episode its own episode name is (see ``find_offers``) — a file of
    another uploader's order goes to the episode its name says."""
    prefs = prefs_from_settings(cfg)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        show = await client.get_tv_full(tmdb_id)
        episodes = await client.get_season(tmdb_id, season)
    finally:
        await client.close()
    numbers = [e["episode_number"] for e in episodes]
    wanted = [e for e in (wanted or numbers) if e in numbers] or numbers
    by_lang = show.get("titles_by_lang") or {}
    # the name in the metadata language first (the one the user sees, cs) — uploaders name files by it
    titles = _unique_names([show.get("title", ""), *(by_lang.get(l, "") for l in prefs.local_langs),
                            by_lang.get("en", ""), show.get("original_title", ""), *show.get("alternative_titles", [])])
    runtimes = [e.get("runtime") for e in episodes if e.get("runtime")]
    runtime = round(statistics.median(runtimes)) if runtimes else (show.get("episode_runtime") or 0)
    ctx = MovieContext(titles=titles, runtime=runtime, episode={"season": season, "episode": wanted[0]})
    out = SeasonOffers(season, wanted, ctx, prefs)

    sources = SourceRegistry.get().sources
    if not torrent:
        sources = [s for s in sources if s.source_type.value not in TORRENT_SOURCES]
    ddl, torrent_queries = season_queries(titles, season)
    # ``alt``: (season, episode) elsewhere → TMDB's episode of this season (a season split elsewhere: S02E01 = E13)
    alt = alt or {}
    for other in sorted({k[0] for k in alt}):
        more_ddl, more_torrent = season_queries(titles, other)
        ddl += [q for q in more_ddl[:2] if q not in ddl]
        torrent_queries += [q for q in more_torrent[:1] if q not in torrent_queries]
    results = await search_sources(sources, ddl, torrent_queries)
    known = await cached_details([(r.source_type.value, r.ident) for r in results])
    for r in results:
        info = parse_episode(r.name)
        mine = [e for e in info.episodes if e in numbers] if info.season == season else []
        other_numbers = None
        if not mine and info.season != season and info.episodes and alt:
            mine = [alt[(info.season, e)] for e in info.episodes if (info.season, e) in alt]
            if not mine:
                continue
            other_numbers = {"alt": [[info.season, info.episodes[0]]], "absolute": None}
        elif info.is_pack and info.season is not None and info.season != season and (info.season, 1) in alt:
            continue                                   # a pack of the other numbering: its files would land wrong
        hit = by_name(r.name) if by_name and not info.is_pack and len(info.episodes) <= 1 else None
        if hit and hit[1]:
            mine = [hit[0][1]] if hit[0][0] == season and hit[0][1] in numbers else []
        if not mine and not info.is_pack:
            continue
        # judged as the first wanted episode it holds (a pack: as the first wanted one)
        episode = {"season": season, "episode": next((e for e in mine if e in wanted), mine[0] if mine else wanted[0])}
        if hit:
            episode["by_name"] = {r.name: [list(hit[0]), hit[1], hit[2]]}
        if other_numbers:
            episode["other"] = other_numbers
        ep_ctx = MovieContext(titles=titles, runtime=runtime, episode=episode)
        details = known.get((r.source_type.value, r.ident))
        ev = evaluate(r.name, r.size, ep_ctx, prefs, details, r.duration_s, r.width, r.height)
        if ev["film"] == "no":
            continue
        out.rows.append({"ident": r.ident, "name": r.name, "size": r.size, "source": r.source_type.value,
                         "source_id": r.source_id, "magnet_url": r.magnet_url, "seeders": r.seeders,
                         "quality": ev["resolution"] or "unknown", "relevance_score": 0, "episodes": mine, **ev,
                         **({"name_hit": episode["by_name"][r.name]} if hit else {})})
    out.sets = group_sets(out.rows, season, wanted, runtime)
    if await _verify_samples(out, titles, runtime):
        out.sets = group_sets(out.rows, season, wanted, runtime)
    out.packs = sorted((r for r in out.rows if r.get("pack")), key=lambda r: (-(r.get("lang_tier", 0) >= 2), -r["quality_score"]))
    out.plan = plan_season(out.sets, wanted)
    logger.info("Season %s S%02d: %d files, %d sets, %d packs, plan %d/%d", show.get("title"), season, len(out.rows),
                len(out.sets), len(out.packs), len(out.plan), len(wanted))
    return out


async def find_show_packs(cfg: dict, tmdb_id: int) -> dict:
    """Torrents of the whole show (or several seasons) — uploaders put a show up "komplet", "1-26. série",
    "S01-S10": searched by the show's names alone and with "komplet" / "complete", every pack of it judged
    (the show's name, the seasons it holds against TMDB's, language, quality). Packs of more seasons first,
    then the dub, then seeders. {"seasons": TMDB's seasons, "packs": [row + "seasons", "complete"]}"""
    prefs = prefs_from_settings(cfg)
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        show = await client.get_tv_full(tmdb_id)
    finally:
        await client.close()
    tmdb_seasons = sorted(s["season_number"] for s in show.get("seasons", []) if s.get("season_number"))
    by_lang = show.get("titles_by_lang") or {}
    titles = _unique_names([show.get("title", ""), *(by_lang.get(l, "") for l in prefs.local_langs),
                            by_lang.get("en", ""), show.get("original_title", ""), *show.get("alternative_titles", [])])
    names = [n for n in (_clean_title(t) for t in titles) if len(_norm(n)) >= 2]
    if not names:
        return {"seasons": tmdb_seasons, "packs": []}
    latin = next((n for n in names if n.isascii() and re.search(r"[A-Za-z]", n)), names[0])
    queries = _unique_names([latin, f"{latin} komplet", f"{latin} complete", names[0]])
    sources = [s for s in SourceRegistry.get().sources if s.source_type.value in TORRENT_SOURCES]
    results = await search_sources(sources, [], queries)
    runtime = show.get("episode_runtime") or 0
    first_year = int(str(show.get("first_air_date") or show.get("year") or "0")[:4] or 0)
    packs = []
    for r in results:
        info = parse_episode(r.name)
        if not info.is_pack and not info.episodes and first_year:
            # "Hra o trůny / Game of Thrones (2011–2019)[WebRip][1080p]": the show's years, no seasons — all of it
            years = re.search(r"(?<!\d)((?:19|20)\d{2}) ?[-–] ?((?:19|20)\d{2})(?!\d)", r.name)
            if years and int(years.group(1)) == first_year and int(years.group(2)) > first_year:
                info.complete = True
        if not info.is_pack:
            continue
        held = info.seasons or ([info.season] if info.season is not None else [])
        first = held[0] if held else (tmdb_seasons[0] if tmdb_seasons else 1)
        ctx = MovieContext(titles=titles, runtime=runtime, episode={"season": first, "episode": 1})
        if show_fit(r.name, titles) != "full":
            continue                                       # another show ("House of the Dragon" for "House")
        ev = evaluate(r.name, r.size, ctx, prefs, None, r.duration_s, r.width, r.height)
        ev["film"], ev["film_reasons"] = "yes", []        # the show's name, a pack: what it holds is "seasons"
        covered = [s for s in tmdb_seasons if s in held] if held else list(tmdb_seasons)
        packs.append({"ident": r.ident, "name": r.name, "size": r.size, "source": r.source_type.value,
                      "source_id": r.source_id, "magnet_url": r.magnet_url, "seeders": r.seeders,
                      "quality": ev["resolution"] or "unknown", "relevance_score": 0, **ev,
                      "seasons": held, "complete": info.complete or not held, "covers": len(covered)})
    packs.sort(key=lambda p: (-p["covers"], -(p.get("lang_tier", 0) >= 2), -(p.get("seeders") or 0), -p["quality_score"]))
    logger.info("Show packs %s: %d torrents, %d packs", show.get("title"), len(results), len(packs))
    return {"seasons": tmdb_seasons, "packs": packs[:20], "movie": MovieContext(titles=titles, runtime=runtime).as_dict()}
