"""Is a file the TV episode we are looking for? The episode counterpart of film_match.

    yes     the show's name + the episode (S01E03, 1x03, a range S01E02-E04 that holds it)
    unsure  a pack of the whole season / all seasons (holds the episode, but much more),
            or only the episode number without a season, or the show's name only partly
    no      another episode / season, another show, nothing that says which episode
    length  verified duration far from the episode's runtime (incomplete file)

`parse_episode` reads what a name holds; it is also used to group files of one release (F2).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from app.core.film_match import STOPWORDS, Verdict, tokens

# more episodes: S01E02-E04, S01E02-04, S01E02E03, S04E01+02, "S01E02 - E04"; " - 07" with spaces is the
# episode's name ("Urgent - S01E01 - 07 - 00" = "07:00"), not a range
_SE = re.compile(r"(?<![a-z0-9])s(\d{1,2}) ?e(\d{1,3})((?:[-+]e?\d{1,3}| ?[-+] ?e\d{1,3}|e\d{1,3})*)(?![0-9])")
_SE_MORE = re.compile(r"\d{1,3}")
_X = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?:-(\d{2,3}))?(?![0-9])")
_SEASON_LIST = re.compile(r"(?<![a-z0-9])s\d{1,2}(?:(?: ?[/,+&] ?| )s\d{1,2}(?![0-9e]))+")
_SEASON_RANGE = re.compile(r"(?<![a-z0-9])(?:s(\d{1,2}) ?- ?s?|(\d{1,2}) ?\.? ?- ?s)(\d{1,2})(?![0-9e])")   # S01-S03, "1. - S03"
_SEASON = re.compile(
    r"(?<![a-z0-9])(?:s(\d{1,2})(?![0-9e])"                         # S01 (not S01E…)
    r"|season ?(\d{1,2})(?![0-9])"                                   # Season 1
    r"|(\d{1,2}) ?(?:serie|serija|sezona|sezon|rada)(?![a-z])"      # 1. série, 2.sezóna, 3. řada (dots gone)
    r"|(?:serie|serija|sezona|sezon|rada) ?(\d{1,2})(?![0-9]))"     # série 1
)
_COMPLETE = re.compile(r"(?<![a-z])(complete|kompletn\w*|komplet|all seasons|vsechny serie)(?![a-z])")
_EP_ONLY = re.compile(r"(?<![a-z0-9])(?:ep?|dil|cast|epizoda) ?(\d{1,3})(?![0-9])"
                      r"|(?:^| )- ?(\d{1,3})(?: |$)"                  # "Fotr na tripu - 03"
                      r"|(?<=[a-z])-(\d{2,3})-")                      # "chalupari-01-chudak-dedecek"
_BARE = re.compile(r"(?<=[a-z]) (\d{2,3}(?:-\d{2,3})*)(?= |$|-[a-z])(?! ?(?:p|fps|kbps|bit)\b)")
LENGTH_MIN_RATIO = 0.6          # a verified episode shorter than this share of its runtime is incomplete


@dataclass
class EpisodeInfo:
    season: int | None = None
    episodes: list[int] = field(default_factory=list)   # empty = a whole season / show pack
    seasons: list[int] = field(default_factory=list)    # a pack of several seasons
    complete: bool = False                               # "complete" / "kompletní"
    prefix: str = ""                                     # the name before the episode mark (the show)
    of_total: int = 0                                    # "S02E03-08": episode 3 of 8
    bare: bool = False                                   # only a number after the name (anime: maybe absolute)

    @property
    def is_pack(self) -> bool:
        return not self.episodes and (self.season is not None or bool(self.seasons) or self.complete)


def _plain(name: str) -> str:
    name = re.sub(r"\[[0-9A-Fa-f]{8}\]", "", name)        # an anime release's CRC ("[C190C5E5]")
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\.(mkv|mp4|avi|ts|m4v|wmv|mov|webm)$", "", text)
    return re.sub(r" +", " ", re.sub(r"[._\[\]()]+", " ", text))


def parse_episode(name: str) -> EpisodeInfo:
    text = _plain(name)
    complete = bool(_COMPLETE.search(text))
    m = _SE.search(text)
    if m:
        first = int(m.group(2))
        tail = m.group(3) or ""
        more = [int(x) for x in _SE_MORE.findall(tail)]
        episodes = [first]
        if re.fullmatch(r" ?- ?\d{1,3}", tail) and more[0] >= first:
            # "S02E03-08 … Epizoda 3": Czech uploaders' "episode 3 of 8", not a range (a range has E: E03-E05)
            return EpisodeInfo(int(m.group(1)), [first], prefix=text[:m.start()], complete=complete,
                               of_total=more[0])
        if more:
            last = more[-1]
            # S01E02-E04 / S01E02-04 is a range; S01E02E03 lists them
            episodes = list(range(first, last + 1)) if last > first and last - first < 30 else [first, *more]
        return EpisodeInfo(int(m.group(1)), episodes, prefix=text[:m.start()], complete=complete)
    m = _X.search(text)
    if m:
        first = int(m.group(2))
        last = int(m.group(3)) if m.group(3) else first
        episodes = list(range(first, last + 1)) if first <= last < first + 30 else [first]
        return EpisodeInfo(int(m.group(1)), episodes, prefix=text[:m.start()], complete=complete)
    m = _SEASON_LIST.search(text)
    if m:                                               # "S01/S02/S03", "S01 S02"
        seasons = sorted({int(x) for x in re.findall(r"s(\d{1,2})", m.group(0))})
        return EpisodeInfo(seasons=seasons, prefix=text[:m.start()], complete=complete)
    m = _SEASON_RANGE.search(text)
    first, last = (int(m.group(1) or m.group(2)), int(m.group(3))) if m else (0, 0)
    if m and first < last:
        return EpisodeInfo(seasons=list(range(first, last + 1)), prefix=text[:m.start()],
                           complete=complete)
    m = _SEASON.search(text)
    if m:
        season = int(next(g for g in m.groups() if g))
        rest = text[m.end():]
        e = _EP_ONLY.search(rest)
        if e:                                           # "Season 2 - 05", "2. série díl 5"
            return EpisodeInfo(season, [int(next(g for g in e.groups() if g))], prefix=text[:m.start()], complete=complete)
        return EpisodeInfo(season, prefix=text[:m.start()], complete=complete)
    e = _EP_ONLY.search(text)
    if e:
        return EpisodeInfo(None, [int(next(g for g in e.groups() if g))], prefix=text[:e.start()], complete=complete)
    e = _BARE.search(text)
    if e:                        # "naruto 104", "naruto 120 cz dabing", "naruto 203-204-205" (anime: absolute numbers)
        numbers = [int(x) for x in re.findall(r"\d{2,3}", e.group(1))]
        episodes = list(range(numbers[0], numbers[-1] + 1)) if len(numbers) > 1 and 0 < numbers[-1] - numbers[0] < 10 else numbers[:1]
        return EpisodeInfo(None, episodes, prefix=text[:e.start()], complete=complete, bare=True)
    return EpisodeInfo(prefix=text, complete=complete)


def _title_fit(prefix: str, name: str, titles: list[str]) -> str:
    """full | part | none — how well the show's name is in the file name (before the episode mark,
    or anywhere when there is nothing before it)."""
    have = tokens(prefix) or tokens(name)
    best = "none"
    for title in titles:
        want = tokens(title) - STOPWORDS or tokens(title)
        if not want:
            continue
        if want <= have:
            return "full"
        if want & have and len(want & have) >= max(1, len(want) // 2):
            best = "part"
    return best


def judge_episode(name: str, titles: list[str], season: int, episode: int,
                  duration_s: int = 0, runtime_min: int = 0) -> Verdict:
    info = parse_episode(name)
    fit = _title_fit(info.prefix, name, titles)
    if fit == "none":
        return Verdict("no", ["jiný seriál"])
    reasons = [] if fit == "full" else ["název seriálu sedí jen zčásti"]
    if info.episodes:
        if info.season is None:
            if episode not in info.episodes:
                return Verdict("no", [f"jiný díl ({info.episodes[0]})"])
            return Verdict("unsure", [*reasons, "díl bez čísla série"])
        if info.season != season:
            return Verdict("no", [f"jiná série (S{info.season:02d})"])
        if episode not in info.episodes:
            return Verdict("no", [f"jiný díl (S{info.season:02d}E{info.episodes[0]:02d})"])
        if len(info.episodes) > 1:
            reasons.append(f"víc dílů v souboru (E{info.episodes[0]:02d}–E{info.episodes[-1]:02d})")
        elif duration_s and runtime_min and duration_s < runtime_min * 60 * LENGTH_MIN_RATIO:
            return Verdict("length", [*reasons, f"jen {duration_s // 60} min z {runtime_min}"])
        return Verdict("unsure" if fit == "part" else "yes", reasons)
    if info.seasons:
        if season not in info.seasons:
            return Verdict("no", [f"jiné série (S{info.seasons[0]:02d}–S{info.seasons[-1]:02d})"])
        return Verdict("unsure", [*reasons, f"balík sérií S{info.seasons[0]:02d}–S{info.seasons[-1]:02d}"])
    if info.season is not None:
        if info.season != season:
            return Verdict("no", [f"jiná série (S{info.season:02d})"])
        return Verdict("unsure", [*reasons, f"celá série S{season:02d}"])
    if info.complete:
        return Verdict("unsure", [*reasons, "celý seriál"])
    return Verdict("no", ["nepoznám díl"])
