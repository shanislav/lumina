"""A dub moved between two versions of a show's episodes, a season at once (decisions/0007 does it for a film).

The new version (better picture — Bones in HEVC, downloaded "as another version") is the episode's file; an old
one (an XviD AVI) has the dub the new one lacks (the Slovak one). For each episode: the old dub is measured against
the new file's sound (speed — PAL 25 fps against 23.976, offset, cuts of a TV version), moved in (copied, or
re-encoded to AC-3 when the speed or the cut differs), the result checked (the dub must sit within lip-sync
tolerance) and only then it takes the episode's place. The old version is deleted when the user asked so.
An episode the measurement is not sure of is left alone (the audio editor of a film can do it by hand)."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from app.db import get_db
from app.modules.audiosync import analyze as engine
from app.modules.audiosync import transfer as muxer

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.6           # a measurement less sure than this is left for the user
WORK_DIR = ".lumina-dub"       # next to the episode (the same disk: the result is moved, not copied)


def _langs(media: dict) -> list[str]:
    return [(a.get("lang") or "").lower() for a in (media or {}).get("audio") or []]


async def pairs(tmdb_id: int, season: int, lang: str) -> list[dict]:
    """Each owned episode of the season: its file (the target) and another version with the dub (the source),
    or why there is nothing to do."""
    from app.modules.library.episodes import versions
    lang = lang.lower()
    out = []
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT * FROM library_episodes WHERE show_tmdb_id = ? AND season = ? AND has_file = 1 ORDER BY episode",
            (tmdb_id, season))).fetchall()
        for row in rows:
            ep = dict(row)
            vs = await versions(db, ep)
            item = {"episode": ep["episode"], "target": "", "source": "", "status": "", "note": ""}
            out.append(item)
            if any(v.get("part") for v in vs):
                item.update(status="skip", note="díl ve dvou částech — v editoru zvuku")
                continue
            target = next((v for v in vs if v["current"]), None)
            if not target:
                item.update(status="skip", note="soubor dílu nenalezen")
                continue
            item.update(target=target["filename"], target_path=target["file_path"])
            if lang in _langs(target["media"]):
                item.update(status="done", note="dabing už má")
                continue
            sources = [v for v in vs if not v["current"] and lang in _langs(v["media"])]
            if not sources:
                item.update(status="skip", note="žádná jiná verze s tímto dabingem")
                continue
            src = sources[0]
            item.update(source=src["filename"], source_path=src["file_path"], status="ready",
                        source_track=_langs(src["media"]).index(lang),
                        target_track=_reference_track(target["media"]))
    finally:
        await db.close()
    return out


def _reference_track(media: dict) -> int:
    """The target's track the dub is measured against: its first one (the new release's own sound)."""
    return 0


async def move_dub(item: dict, lang: str, delete_source: bool, progress=None) -> dict:
    """One episode: measure, build, check, replace. → {status, note, ...}."""
    target, source = item["target_path"], item["source_path"]
    if not (os.path.isfile(target) and os.path.isfile(source)):
        return {"status": "error", "note": "soubor zmizel"}
    folder = Path(os.path.dirname(target))
    if shutil.disk_usage(folder).free < os.path.getsize(target) * 1.1 + 1024**3:
        return {"status": "error", "note": "málo místa na disku"}
    result = await asyncio.to_thread(engine.analyze, target, item["target_track"], source, item["source_track"],
                                     progress)
    data = result.to_dict()
    if data["verdict"] not in ("constant", "speed", "cuts") or (data.get("confidence") or 0) < MIN_CONFIDENCE:
        return {"status": "unsure", "note": f"zvuk k obrazu nesedí jistě ({data.get('note') or data['verdict']})",
                "verdict": data["verdict"], "confidence": data.get("confidence")}
    work = folder / WORK_DIR
    out_name = f"{os.path.splitext(os.path.basename(target))[0]}.mkv"
    try:
        built = await asyncio.to_thread(
            muxer.transfer_many, target, item["target_track"],
            [{"path": source, "tracks": [item["source_track"]], "analysis": data,
              "language": engine.ISO3.get(lang, lang)}],
            work, out_name, progress, None, None, False)
    except muxer.TransferError as e:
        shutil.rmtree(work, ignore_errors=True)
        return {"status": "error", "note": str(e)}
    final = str(folder / out_name)
    os.replace(built, final)
    shutil.rmtree(work, ignore_errors=True)
    if final != target:
        os.remove(target)                       # an MP4 became an MKV
    await _library(target, final, source if delete_source else None)
    return {"status": "ok", "path": final, "verdict": data["verdict"], "speed": data["speed"],
            "offset": data["offset"], "confidence": data.get("confidence")}


async def _library(old: str, new: str, delete: str | None) -> None:
    """Lumina learns the new file (its sound, size, name) and forgets the old version when it went."""
    from app.core.mediainfo import probe_async
    from app.modules.library.organize import path_moved
    db = await get_db()
    try:
        if new != old:
            await path_moved(db, old, new)
        media = await probe_async(new)
        st = os.stat(new)
        await db.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, ?, ?, ?)",
                         (new, st.st_size, st.st_mtime, json.dumps(media)))
        langs = ",".join(sorted({(a.get("lang") or "").upper() for a in media.get("audio", []) if a.get("lang")}))
        await db.execute("UPDATE library_episodes SET file_size = ?, language = ? WHERE file_path = ?",
                         (st.st_size, langs, new))
        if delete and os.path.isfile(delete):
            from app.modules.library.imports import _delete_version
            logger.info("Dub moved: deleted the old version %s", _delete_version(delete, new))
            for table in ("tv_files", "tv_media", "tv_episode_overrides"):
                await db.execute(f"DELETE FROM {table} WHERE file_path = ?", (delete,))
        await db.commit()
    finally:
        await db.close()
