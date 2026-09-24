import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from app.core.mediainfo import _LANG_MAP
from app.db import get_automation

logger = logging.getLogger(__name__)

WRITABLE_STATUSES = ("matched", "manual")
NFO_VERSION = "1"

# ISO 639-1 → ISO 639-2/T ("ces", "slk" — as tinyMediaManager writes them). _LANG_MAP lists
# the /B code before the /T code, so the last 3-letter code of each language wins.
_LANG3 = {two: three for three, two in _LANG_MAP.items() if len(three) == 3}


def _lang3(code: str) -> str:
    return _LANG3.get(code, code)


def build_nfo(payload: dict) -> str:
    tmdb = payload.get("tmdb") or {}
    media = payload.get("media") or {}
    root = ET.Element("movie")

    def add(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
        if text not in (None, ""):
            el.text = str(text)
        return el

    add(root, "title", tmdb.get("title"))
    add(root, "originaltitle", tmdb.get("original_title"))
    add(root, "year", tmdb.get("year"))
    add(root, "runtime", tmdb.get("runtime") or "")
    add(root, "plot", tmdb.get("overview"))
    add(root, "uniqueid", payload["tmdb_id"], type="tmdb", default="true")
    if tmdb.get("imdb_id"):
        add(root, "uniqueid", tmdb["imdb_id"], type="imdb", default="false")
    add(root, "tmdbid", payload["tmdb_id"])  # read by tinyMediaManager-style parsers

    stream = add(add(root, "fileinfo"), "streamdetails")
    if media.get("width"):
        video = add(stream, "video")
        add(video, "codec", (media.get("video_codec") or "").lower())
        add(video, "width", media.get("width"))
        add(video, "height", media.get("height"))
        add(video, "durationinseconds", media.get("duration_s"))
        if media.get("hdr") and media["hdr"] != "SDR":
            add(video, "hdrtype", media["hdr"].lower())
    for track in media.get("audio", []):
        audio = add(stream, "audio")
        add(audio, "codec", (track.get("codec") or "").lower())
        add(audio, "language", _lang3(track.get("lang") or ""))
        add(audio, "channels", track.get("channels"))
    for lang in media.get("subtitles", []):
        add(add(stream, "subtitle"), "language", _lang3(lang))

    lumina = add(root, "lumina")
    add(lumina, "version", NFO_VERSION)
    add(lumina, "status", payload.get("status"))
    add(lumina, "confidence", payload.get("confidence"))
    add(lumina, "matched_by", payload.get("matched_by"))
    add(lumina, "written", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    # the user's words about each version and the preferred one — restored by a library scan
    for v in payload.get("versions") or []:
        if v.get("note") or v.get("preferred"):
            add(lumina, "file", v.get("note") or "", name=v["filename"], preferred=str(bool(v.get("preferred"))).lower())

    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def nfo_targets(payload: dict) -> tuple[str, list[str]]:
    """(path to write, NFO files to delete). A folder that belongs to this movie alone gets movie.nfo
    and all other NFO files in it are removed; otherwise only <video name>.nfo is written."""
    folder, video = payload["folder"], payload["file_path"]
    own_folder = (not payload.get("folder_is_library_root")
                  and all(t == payload["tmdb_id"] for t in payload.get("folder_tmdb_ids") or []))
    if not own_folder:
        return os.path.splitext(video)[0] + ".nfo", []
    target = os.path.join(folder, "movie.nfo")
    try:
        old = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".nfo")]
    except OSError:
        old = []
    return target, [p for p in old if os.path.normcase(p) != os.path.normcase(target)]


def _write_atomic(path: str, content: str) -> None:
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".lumina-", suffix=".nfo")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            st = os.stat(folder)
            os.chown(tmp, st.st_uid, st.st_gid)
        except (OSError, AttributeError):
            pass
        os.chmod(tmp, 0o664)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


async def on_movie_updated(payload: dict) -> None:
    automation = await get_automation("nfo")
    if not automation or not automation["enabled"]:
        return
    if payload.get("status") not in WRITABLE_STATUSES or not payload.get("tmdb_id") or not payload.get("tmdb"):
        return
    if not os.path.isfile(payload["file_path"]):
        return

    target, old = nfo_targets(payload)
    _write_atomic(target, build_nfo(payload))
    for path in old:
        try:
            os.remove(path)
            logger.info("Removed old NFO %s", path)
        except OSError as e:
            logger.warning("Cannot remove old NFO %s: %s", path, e)
    logger.info("Wrote %s", target)
