"""File-system helpers shared by the library scanners (movies + TV)."""

import os
import re
import xml.etree.ElementTree as ET

from app.utils.tv_parser import VIDEO_EXTS

# Reuse quality/language detection from duplicates
_QUALITY_RE = {
    "2160p": r"2160p|4[Kk]|UHD",
    "1080p": r"1080[pi]|FHD|Full\s*HD",
    "720p": r"720[pi]|HD(?!R)",
    "480p": r"480[pi]|SD",
}

_LANG_RE = {
    "CZ": r"(?i)\b(CZ|[Čč]esk|czech|dabing|dab)\b",
    "SK": r"(?i)\b(SK|[Ss]lovensk|slovak)\b",
    "EN": r"(?i)\b(EN|ENG|english)\b",
    "JP": r"(?i)\b(JP|JPN|japanese)\b",
}


def _detect_quality(name: str) -> str:
    import re
    for label, pattern in _QUALITY_RE.items():
        if re.search(pattern, name):
            return label
    return "unknown"


def _detect_language(name: str) -> str:
    import re
    langs = []
    for code, pattern in _LANG_RE.items():
        if re.search(pattern, name):
            langs.append(code)
    return ",".join(langs) if langs else ""


def _parse_nfo(video_path: str) -> dict | None:
    """Try to find and parse a .nfo file next to the video.

    Returns dict with tmdb_id, title, original_title, year, media_type
    or None if no NFO or no useful data found.
    """
    base = os.path.splitext(video_path)[0]
    # Try: same name .nfo, then movie.nfo / tvshow.nfo in same dir
    candidates = [
        f"{base}.nfo",
        os.path.join(os.path.dirname(video_path), "movie.nfo"),
        os.path.join(os.path.dirname(video_path), "tvshow.nfo"),
    ]
    for nfo_path in candidates:
        if not os.path.isfile(nfo_path):
            continue
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            tag = root.tag.lower()  # "movie", "tvshow", "episodedetails"

            tmdb_id = None
            # Try <tmdbid> or <uniqueid type="tmdb">
            tmdb_el = root.find("tmdbid")
            if tmdb_el is not None and tmdb_el.text:
                tmdb_id = int(tmdb_el.text)
            else:
                for uid in root.findall("uniqueid"):
                    if uid.get("type", "").lower() == "tmdb" and uid.text:
                        tmdb_id = int(uid.text)
                        break

            if not tmdb_id:
                continue

            title = (root.findtext("title") or "").strip()
            original_title = (root.findtext("originaltitle") or "").strip()
            year = (root.findtext("year") or "").strip()

            media_type = "movie"
            if tag in ("tvshow", "episodedetails"):
                media_type = "tv"

            return {
                "tmdb_id": tmdb_id,
                "title": title,
                "original_title": original_title,
                "year": year,
                "media_type": media_type,
                "matched_by": "nfo",
            }
        except Exception:
            continue
    return None


def _parse_nfo_file(nfo_path: str) -> dict | None:
    """Parse a specific .nfo file and extract TMDB ID + metadata."""
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        tmdb_id = None
        tmdb_el = root.find("tmdbid")
        if tmdb_el is not None and tmdb_el.text:
            tmdb_id = int(tmdb_el.text)
        else:
            for uid in root.findall("uniqueid"):
                if uid.get("type", "").lower() == "tmdb" and uid.text:
                    tmdb_id = int(uid.text)
                    break

        if not tmdb_id:
            return None

        return {
            "tmdb_id": tmdb_id,
            "title": (root.findtext("title") or "").strip(),
            "original_title": (root.findtext("originaltitle") or "").strip(),
            "year": (root.findtext("year") or "").strip(),
        }
    except Exception:
        return None


def _parse_episode_nfo(video_path: str) -> dict | None:
    """Parse episode .nfo file next to video for season/episode/show info."""
    base = os.path.splitext(video_path)[0]
    nfo_path = f"{base}.nfo"
    if not os.path.isfile(nfo_path):
        return None
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        if root.tag.lower() != "episodedetails":
            return None

        season = root.findtext("season")
        episode = root.findtext("episode")
        if not season or not episode:
            return None

        # Try to get show TMDB ID from uniqueid
        show_tmdb_id = None
        for uid in root.findall("uniqueid"):
            if uid.get("type", "").lower() == "tmdb" and uid.text:
                # Episode NFO might have episode tmdb_id, not show tmdb_id
                # Show tmdb_id is in tvshow.nfo — we'll use it as hint
                pass

        show_name = (root.findtext("showtitle") or "").strip()

        return {
            "season": int(season),
            "episode": int(episode),
            "show_name": show_name,
            "show_tmdb_id": show_tmdb_id,
            "year": (root.findtext("year") or "").strip() or None,
        }
    except Exception:
        return None


def _scan_video_files(directory: str) -> list[dict]:
    """Walk directory and return list of video files with metadata."""
    files = []
    if not os.path.isdir(directory):
        return files
    for root, _dirs, filenames in os.walk(directory, followlinks=True):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            full_path = os.path.join(root, fname)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue
            files.append({
                "filename": fname,
                "file_path": full_path,
                "file_size": stat.st_size,
                "added_at": stat.st_mtime,
                "quality": _detect_quality(fname),
                "language": _detect_language(fname),
            })
    return files
