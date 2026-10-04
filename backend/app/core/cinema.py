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
             Release names never prove a digital release: The Odyssey 2026 (digital 15. 11.) had "AMZN WEB-DL"
             files of several "release groups" in September — all fakes over a cinema recording.
  before     those files stay what they are after the release day: a search before it remembers them
             (PRE_RELEASE_FILES: the source's file, and its size — a re-upload elsewhere is the same file), a
             torrent tells its publication date. WebShare tells no upload date; FastShare's is nonsense
             ("2023-11-30" for a 2026 film) — so the memory.
  the dub    a Czech/Slovak dub exists only in Czech/Slovak cinemas until the film comes out digitally there
             (TMDB's CZ/SK dates; none known: MAX_FRESH_DAYS from the CZ/SK premiere) — a file with it before
             then has the sound recorded in a cinema over a WEB picture.
"""

import re
from datetime import date, datetime, timedelta

from app.db import get_db

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


LOCAL_COUNTRIES = ("CZ", "SK")


def release_info(release_dates: list[dict]) -> dict:
    """TMDB's /release_dates (every country) → {"theatrical": the first cinema premiere, "digital": the first
    digital / disc / TV release, "local_theatrical" / "local_digital": the same in Czechia / Slovakia} as ISO
    dates ("" when unknown)."""
    first: dict[str, str] = {"theatrical": "", "digital": "", "local_theatrical": "", "local_digital": ""}
    for country in release_dates or []:
        local = country.get("iso_3166_1") in LOCAL_COUNTRIES
        for r in country.get("release_dates") or []:
            day = (r.get("release_date") or "")[:10]
            if not day:
                continue
            kind = "theatrical" if r.get("type") in (1, 2, 3) else "digital" if r.get("type") in (4, 5, 6) else ""
            for key in ([kind, f"local_{kind}"] if local else [kind]) if kind else []:
                if not first[key] or day < first[key]:
                    first[key] = day
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


def before_local_digital(info: dict | None, today: date | None = None) -> bool:
    """The film is not out digitally in Czechia / Slovakia yet: its Czech/Slovak dub is only in cinemas."""
    if not info:
        return False
    local = {"digital": info.get("local_digital") or "",
             "theatrical": info.get("local_theatrical") or info.get("theatrical") or ""}
    return before_digital(local, today)



def judge(name: str, pre_digital: bool, pre_local: bool = False, audio_langs: list[str] | None = None) -> dict:
    """The cinema verdict of a file: {"cinema": "video" | "audio" | "likely" | "suspect" | "", "langs": [...],
    "reason": "…"}. "video": a cinema picture (hidden); "audio": its Czech/Slovak sound is a recording, not a dub;
    "likely" / "suspect": the film is not out digitally yet, so the file is most likely a recording ("suspect":
    it even claims WEB / Blu-ray)."""
    m = marks(name)
    if m["video"] or (pre_digital and m["audio"] == "weak"):
        return {"cinema": "video", "langs": [], "reason": "obraz z kina (CAM / TS)"}
    if m["audio"] == "strong":
        return {"cinema": "audio", "langs": m["langs"], "reason": "zvuk nahraný v kině"}
    local = [l for l in (audio_langs or []) if l in ("cs", "sk")]
    if pre_local and local:
        return {"cinema": "audio", "langs": local,
                "reason": "CZ/SK zvuk nejspíš z kina — v ČR/SK film zatím digitálně nevyšel"}
    if pre_digital:
        if claims_retail(name):
            return {"cinema": "suspect", "langs": [], "reason": "podezřelé — film ještě nevyšel digitálně"}
        return {"cinema": "likely", "langs": [], "reason": "nejspíš z kina — film ještě nevyšel digitálně"}
    return {"cinema": "", "langs": [], "reason": ""}


PRE_RELEASE_FILES = """
CREATE TABLE IF NOT EXISTS pre_release_files (
    tmdb_id INTEGER NOT NULL,
    file_key TEXT NOT NULL,             -- "<source_id>:<ident>"
    size INTEGER NOT NULL DEFAULT 0,    -- a DDL file of the same size elsewhere is the same file
    name TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL,
    PRIMARY KEY (tmdb_id, file_key)
);
CREATE INDEX IF NOT EXISTS pre_release_files_size ON pre_release_files (tmdb_id, size);
"""
TORRENTS = ("jackett", "prowlarr")
FORGET_DAYS = 30        # a month after the last sighting before the release the official versions outscore the
                        # recordings anyway — the memory forgets them


async def remember(tmdb_id: int, rows: list[dict]) -> None:
    """Before the digital release: the film's files (not the junk) — after it they are still recordings."""
    keep = [r for r in rows if r.get("film") != "no" or r.get("cinema") == "video"]
    if not tmdb_id or not keep:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO pre_release_files (tmdb_id, file_key, size, name, seen_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tmdb_id, file_key) DO UPDATE SET seen_at = excluded.seen_at",
            [(tmdb_id, f"{r['source_id']}:{r['ident']}", r.get("size") or 0, r.get("name") or "", now) for r in keep])
        await db.execute("DELETE FROM pre_release_files WHERE seen_at < ?",
                         ((datetime.now() - timedelta(days=FORGET_DAYS)).strftime("%Y-%m-%d %H:%M:%S"),))
        await db.commit()
    finally:
        await db.close()


async def remembered(tmdb_id: int) -> tuple[list[str], list[int]]:
    """(file keys, DDL sizes) seen before the film's digital release."""
    if not tmdb_id:
        return [], []
    db = await get_db()
    try:
        since = (datetime.now() - timedelta(days=FORGET_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        rows = await (await db.execute("SELECT file_key, size FROM pre_release_files WHERE tmdb_id = ? AND seen_at >= ?",
                                       (tmdb_id, since))).fetchall()
    except Exception:  # noqa: BLE001 — before the migration
        rows = []
    finally:
        await db.close()
    return [r[0] for r in rows], sorted({r[1] for r in rows if r[1]})


async def last_remembered(tmdb_id: int) -> str:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT MAX(seen_at) FROM pre_release_files WHERE tmdb_id = ?", (tmdb_id,))).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    finally:
        await db.close()
    return (row[0] if row else "") or ""


def before_release(row: dict, recorded: list[str] | set[str], sizes: list[int] | set[int], digital: str) -> bool:
    """The file existed before the film's digital release: seen then, the same file elsewhere (a DDL of the same
    size), or a torrent published before it."""
    if f"{row.get('source_id')}:{row.get('ident')}" in recorded:
        return True
    torrent = row.get("source") in TORRENTS
    if not torrent and row.get("size") and row["size"] in sizes:
        return True
    published = row.get("published") or ""
    return bool(torrent and digital and published and published < digital)


def mark_before_release(ev: dict, row: dict, recorded, sizes, digital: str) -> dict:
    """A file from before the digital release is a recording whatever its name says (not hidden, never taken)."""
    if ev.get("cinema") in ("video", "audio", "likely", "suspect") or not before_release(row, recorded, sizes, digital):
        return ev
    return {**ev, "cinema": "likely", "cinema_reason": "nahráno ještě před digitálním vydáním — nejspíš z kina"}
