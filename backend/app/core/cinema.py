"""Recordings from a cinema — a camera in the hall (picture and sound) or only the sound recorded there over a
proper picture. Nobody wants them by default: the search hides a cinema picture, the Czech/Slovak sound recorded
in a cinema is no dub, and nothing automatic takes either.

Two signs:
  the name   CAM, HDCAM, TS, HDTS, TELESYNC, TC, TELECINE, "kinorip", "z kina" (picture) and "zvuk z kina",
             "dabing z kina", LiNE, MD (sound). "kino" alone is weak: in an old film's name ("CZ dabing kino")
             it is the cinema version of the dub, a normal one from the disc.
  the date   a film that has not come out digitally yet (TMDB release dates: digital / disc / TV, any country)
             exists only as a cinema recording — whatever its name says ("1080p WEB-DL" before the WEB release
             is a fake). No digital date known: only a recent premiere (MAX_FRESH_DAYS) counts as before it.
"""

import re
from datetime import date, timedelta

MAX_FRESH_DAYS = 120          # no digital date in TMDB: a premiere this recent still means "only in cinemas"

# the name in words: separators to spaces, the extension off ("Film.2025.HDTS.mkv" → "film 2025 hdts")
def _words(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name or "")
    return " " + re.sub(r"[\s._\-\[\]()+,]+", " ", stem.lower()).strip() + " "


_VIDEO = re.compile(r" (?:hd|hq)?cam(?:rip)? | hd ?ts | ts | telesync | hd ?tc | tc | telecine | kino ?rip "
                    r"| (?:obraz|video) z kina ")
_AUDIO_STRONG = re.compile(r" (?:zvuk|audio|dabing|dab|nahravka|nahrávka) z kina | z kina (?:zvuk|audio|dabing) "
                           r"| line(?: audio)? | md | audio cam | cam audio ")
_FROM_CINEMA = re.compile(r" z kina ")
_KINO = re.compile(r" kino ")
_RETAIL = re.compile(r" (?:web ?dl|web ?rip|webrip|web|bluray|blu ray|bdrip|brrip|bd ?remux|remux|dvdrip|hdrip|"
                     r"amzn|nf|dsnp|hmax|atvp) ")
_LOCAL = re.compile(r" (?:cz|cs|cze|czech|sk|slo|svk|slovak) ")


def marks(name: str) -> dict:
    """{"video": a cinema picture, "audio": "strong" | "weak" | None, "langs": [the recorded sound's languages]}"""
    w = _words(name)
    audio = "strong" if _AUDIO_STRONG.search(w) else None
    video = bool(_VIDEO.search(w)) or (bool(_FROM_CINEMA.search(w)) and not audio)
    if not audio and not video and _KINO.search(w):
        audio = "weak"
    langs = []
    if audio:
        langs = sorted({"sk" if l in (" sk ", " slo ", " svk ", " slovak ") else "cs" for l in
                        (m.group(0) for m in _LOCAL.finditer(w.replace(" ", "  ")))}) or ["cs", "sk"]
    return {"video": video, "audio": audio, "langs": langs}


def claims_retail(name: str) -> bool:
    """The name says WEB / Blu-ray / DVD — not possible before the film came out that way."""
    return bool(_RETAIL.search(_words(name)))


def release_info(release_dates: list[dict]) -> dict:
    """TMDB's /release_dates (every country) → {"theatrical": the first cinema premiere, "digital": the first
    digital / disc / TV release} as ISO dates ("" when unknown)."""
    first: dict[str, str] = {"theatrical": "", "digital": ""}
    for country in release_dates or []:
        for r in country.get("release_dates") or []:
            day = (r.get("release_date") or "")[:10]
            if not day:
                continue
            kind = "theatrical" if r.get("type") in (1, 2, 3) else "digital" if r.get("type") in (4, 5, 6) else ""
            if kind and (not first[kind] or day < first[kind]):
                first[kind] = day
    return first


def before_digital(info: dict | None, today: date | None = None) -> bool:
    """Only cinema recordings can exist: no digital / disc / TV release yet — a known future one, or none known
    while the premiere is recent (or still coming)."""
    if not info:
        return False
    today = today or date.today()
    digital, theatrical = info.get("digital") or "", info.get("theatrical") or ""
    if digital:
        return digital > today.isoformat()
    if not theatrical:
        return False
    return theatrical > (today - timedelta(days=MAX_FRESH_DAYS)).isoformat()


def judge(name: str, pre_digital: bool) -> dict:
    """The cinema verdict of a file: {"cinema": "video" | "audio" | "likely" | "suspect" | "", "langs": [...],
    "reason": "…"}. "video": a cinema picture (hidden); "audio": its Czech/Slovak sound is a recording, not a dub;
    "likely" / "suspect": the film is not out digitally yet, so the file is most likely a recording ("suspect":
    it even claims WEB / Blu-ray)."""
    m = marks(name)
    if m["video"] or (pre_digital and m["audio"] == "weak"):
        return {"cinema": "video", "langs": [], "reason": "obraz z kina (CAM / TS)"}
    if m["audio"] == "strong":
        return {"cinema": "audio", "langs": m["langs"], "reason": "zvuk nahraný v kině"}
    if pre_digital:
        if claims_retail(name):
            return {"cinema": "suspect", "langs": [], "reason": "podezřelé — film ještě nevyšel digitálně"}
        return {"cinema": "likely", "langs": [], "reason": "nejspíš z kina — film ještě nevyšel digitálně"}
    return {"cinema": "", "langs": [], "reason": ""}
