"""The user's word on an episode's sound language — written into the file where the format has a place for
it, so Plex and every player know it too:

- MKV: ``mkvpropedit`` sets the track's language in place (nothing is copied)
- MP4 / M4V / MOV: ffmpeg copies the streams into a new file with the language, which replaces the old one
- AVI and others: no language in the format — Lumina keeps it (``tv_media``), the renamer writes "[CS]"

Only tracks without a language are changed unless the user names one.
"""

import asyncio
import json
import logging
import os

from app.core.mediainfo import probe_async

logger = logging.getLogger(__name__)

LANGS = {"cs": "cze", "sk": "slo", "en": "eng", "de": "ger", "pl": "pol", "hu": "hun", "fr": "fre", "ja": "jpn"}
MKV = (".mkv", ".mka")
MP4 = (".mp4", ".m4v", ".mov")


async def _run(*args: str, timeout: float = 600) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timeout"
    return proc.returncode, out.decode("utf-8", errors="replace")[-400:]


def _targets(audio: list[dict], track: int | None) -> list[int]:
    """Audio tracks (0-based among the audio ones) to set: the one named, else every one without a language."""
    if track is not None:
        return [track] if 0 <= track < len(audio) else []
    return [i for i, a in enumerate(audio) if not (a.get("lang") or "").strip() or a.get("lang") in ("und", "unk")]


async def set_language(path: str, lang: str, track: int | None = None, media: dict | None = None) -> dict:
    """Write ``lang`` (cs, sk, en …) to the file's audio track(s). Returns {written: in the file, media: the
    file's MediaInfo after it, tracks: how many}."""
    if lang not in LANGS:
        raise ValueError(f"Neznámý jazyk {lang}")
    media = media if media is not None else await probe_async(path) or {}
    audio = media.get("audio") or []
    which = _targets(audio, track) or ([0] if audio and track is None and len(audio) == 1 else [])
    if not which:
        return {"written": False, "media": media, "tracks": 0}
    ext = os.path.splitext(path)[1].lower()
    written = False
    if ext in MKV:
        args = ["mkvpropedit", path]
        for i in which:
            args += ["--edit", f"track:a{i + 1}", "--set", f"language={LANGS[lang]}"]
        code, out = await _run(*args, timeout=120)
        if code != 0:
            raise RuntimeError(f"mkvpropedit: {out.strip()[-200:]}")
        written = True
    elif ext in MP4:
        tmp = os.path.join(os.path.dirname(path), f".lumina-lang-{os.path.basename(path)}")
        args = ["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0", "-c", "copy"]
        for i in which:
            args += [f"-metadata:s:a:{i}", f"language={LANGS[lang]}"]
        args += ["-movflags", "+faststart", tmp]
        code, out = await _run(*args, timeout=1800)
        # the copy must hold the same: every video and audio track, the whole length (its size may be
        # smaller — an MP4 often carries padding)
        copy = await probe_async(tmp) if code == 0 and os.path.exists(tmp) else {}
        same = bool(copy) and len(copy.get("audio") or []) == len(audio) \
            and abs((copy.get("duration_s") or 0) - (media.get("duration_s") or 0)) <= 2
        if not same:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"ffmpeg: {out.strip()[-200:] or 'kopie nesedí s originálem — soubor nechávám'}")
        os.replace(tmp, path)
        written = True
    if written:
        media = await probe_async(path) or media
    else:
        audio = [dict(a) for a in audio]
        for i in which:
            audio[i]["lang"] = lang
        media = {**media, "audio": audio}
    logger.info("Sound language %s of %s: tracks %s%s", lang, os.path.basename(path), which,
                "" if written else " (Lumina only)")
    return {"written": written, "media": media, "tracks": len(which)}


async def save(db, path: str, media: dict) -> str:
    """The new MediaInfo and the episode's languages in Lumina (the scan reads them again only if the file
    changes — a written file did, with the language in it)."""
    stat = os.stat(path)
    await db.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, ?, ?, ?)",
                     (path, stat.st_size, stat.st_mtime, json.dumps(media)))
    langs = ",".join(sorted({a["lang"].upper() for a in media.get("audio", []) if a.get("lang")}))
    await db.execute("UPDATE library_episodes SET language = ?, file_size = ? WHERE file_path = ?",
                     (langs, stat.st_size, path))
    return langs
