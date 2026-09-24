"""Read ids from existing .nfo files (Kodi / tinyMediaManager / Radarr style).

NFO files are only a hint — they can be wrong or stale (see docs/decisions/0002).
"""

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class NfoFacts:
    path: str
    tmdb_id: int | None = None
    imdb_id: str | None = None
    title: str = ""
    original_title: str = ""
    year: int | None = None
    # NFO written by Lumina's nfo module (<lumina> block) — kept up to date, so it is trusted more
    by_lumina: bool = False
    lumina_status: str = ""
    # {video file name: {"note", "preferred"}} written by Lumina's NFO module
    lumina_files: dict = field(default_factory=dict)


def find_nfo(video_path: str, videos_in_folder: int) -> str | None:
    """NFO belonging to a video: same basename, movie.nfo, or the only .nfo in the folder.

    The "only .nfo" rule is used just when the folder holds a single video, otherwise
    it would be ambiguous which video it describes.
    """
    folder = os.path.dirname(video_path)
    same_name = os.path.splitext(video_path)[0] + ".nfo"
    if os.path.isfile(same_name):
        return same_name
    movie_nfo = os.path.join(folder, "movie.nfo")
    if os.path.isfile(movie_nfo):
        return movie_nfo
    try:
        nfos = [f for f in os.listdir(folder) if f.lower().endswith(".nfo")]
    except OSError:
        return None
    if len(nfos) == 1 and videos_in_folder == 1:
        return os.path.join(folder, nfos[0])
    return None


def read_nfo(path: str) -> NfoFacts | None:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    if root.tag.lower() != "movie":
        return None

    facts = NfoFacts(path=path)
    tmdb = (root.findtext("tmdbid") or "").strip()
    imdb = ""
    for uid in root.findall("uniqueid"):
        kind = (uid.get("type") or "").lower()
        if kind == "tmdb" and not tmdb:
            tmdb = (uid.text or "").strip()
        elif kind == "imdb":
            imdb = (uid.text or "").strip()
    imdb = imdb or (root.findtext("imdbid") or "").strip() or (root.findtext("id") or "").strip()

    facts.tmdb_id = int(tmdb) if tmdb.isdigit() else None
    facts.imdb_id = imdb if imdb.startswith("tt") else None
    facts.title = (root.findtext("title") or "").strip()
    facts.original_title = (root.findtext("originaltitle") or "").strip()
    year = (root.findtext("year") or "").strip()
    facts.year = int(year) if year.isdigit() else None
    lumina = root.find("lumina")
    if lumina is not None:
        facts.by_lumina = True
        facts.lumina_status = (lumina.findtext("status") or "").strip()
        for f in lumina.findall("file"):
            if f.get("name"):
                facts.lumina_files[f.get("name")] = {"note": (f.text or "").strip(),
                                                     "preferred": f.get("preferred") == "true"}
    if not (facts.tmdb_id or facts.imdb_id):
        return None
    return facts
