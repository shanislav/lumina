import logging
import re
from pymediainfo import MediaInfo
from pathlib import Path

logger = logging.getLogger("app.utils.media")

def get_media_tags(file_path: str, original_filename: str = "") -> dict:
    tags = {"resolution": "", "codec": "", "languages": [], "source": ""}
    name_for_search = original_filename or Path(file_path).name
    source_match = re.search(r'(WEB-?DL|BluRay|BRRip|BDrip|DVDRip|HDTV|HDRip)', name_for_search, re.IGNORECASE)
    tags["source"] = source_match.group(1).upper().replace("-", "") if source_match else "WEBDL"

    try:
        if Path(file_path).exists():
            media_info = MediaInfo.parse(file_path)
            for track in media_info.tracks:
                if track.track_type == "Video":
                    width = int(track.width or 0)
                    if width >= 3840: tags["resolution"] = "2160p"
                    elif width >= 1920: tags["resolution"] = "1080p"
                    elif width >= 1280: tags["resolution"] = "720p"
                    else: tags["resolution"] = "SD"
                    
                    codec = str(track.format or "").lower()
                    if "hevc" in codec or "h.265" in codec: tags["codec"] = "x265"
                    elif "avc" in codec or "h.264" in codec: tags["codec"] = "x264"
                    else: tags["codec"] = codec

                elif track.track_type == "Audio":
                    lang = str(track.language or "").upper()
                    if lang:
                        lang_map = {"CES": "CS", "CZE": "CS", "SLO": "SK", "SLK": "SK", "ENG": "EN"}
                        short = lang_map.get(lang, lang[:2])
                        if short not in tags["languages"]: tags["languages"].append(short)
    except Exception as e:
        logger.error("MediaInfo failed: %s", e)
    return tags

def format_filename(original_name: str, tmdb_id: int, tags: dict, title: str = "", year: int = 0, pattern: str = "") -> str:
    """Format filename using a template pattern."""
    if not pattern:
        # Default fallback if pattern is empty
        pattern = "{title} ({year}) [{source}-{res} {codec}] [{langs}] {tmdb-{id}}"
    
    ext = Path(original_name).suffix
    langs = "+".join(tags.get("languages", []))
    
    # Replace placeholders
    result = pattern.replace("{title}", title.replace(":", " - ").strip())
    result = result.replace("{year}", str(year) if year else "")
    result = result.replace("{res}", tags.get("resolution", "1080p"))
    result = result.replace("{source}", tags.get("source", "WEBDL"))
    result = result.replace("{codec}", tags.get("codec", ""))
    result = result.replace("{langs}", langs)
    result = result.replace("{id}", str(tmdb_id))
    
    # Clean up multiple spaces or empty brackets
    result = re.sub(r'\s+', ' ', result).strip()
    result = result.replace("[]", "").replace("()", "").replace("  ", " ")
    
    return f"{result}{ext}"
