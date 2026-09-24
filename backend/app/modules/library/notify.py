"""Tell other modules that a library movie changed (identity, file path or media info).

Event ``library.movie_updated`` payload:
    movie_id, status, confidence, matched_by, file_path, folder, media (MediaInfo dict),
    tmdb (cached TMDB details: title, original_title, year, runtime, imdb_id, overview, titles_by_lang, ...),
    folder_tmdb_ids (tmdb ids of all library videos in the same folder — tells whether
    the folder belongs to this movie alone), folder_is_library_root
"""

import json
import os

from app.config import get_effective_settings, movies_library_dir
from app.core import events
from app.core.release_name import VIDEO_EXTS


async def emit_movie_updated(db, movie_id: int) -> None:
    cursor = await db.execute("SELECT * FROM library_movies WHERE id = ?", (movie_id,))
    row = await cursor.fetchone()
    if not row:
        return
    row = dict(row)
    tmdb = {}
    if row.get("tmdb_id"):
        cursor = await db.execute("SELECT data FROM tmdb_movies WHERE tmdb_id = ?", (row["tmdb_id"],))
        cached = await cursor.fetchone()
        tmdb = json.loads(cached[0]) if cached else {}
    folder = os.path.dirname(row["file_path"])
    # Based on the videos actually on disk: one not (yet) in the library counts as a foreign
    # movie (None), so the folder is never treated as this movie's alone by mistake.
    cursor = await db.execute("SELECT tmdb_id, file_path FROM library_movies")
    known = {r[1]: r[0] for r in await cursor.fetchall() if os.path.dirname(r[1]) == folder}
    try:
        on_disk = [os.path.join(folder, f) for f in os.listdir(folder)
                   if os.path.splitext(f)[1].lower() in VIDEO_EXTS and "sample" not in f.lower()]
    except OSError:
        on_disk = list(known)
    folder_tmdb_ids = [known.get(path) for path in on_disk]
    root = movies_library_dir(await get_effective_settings())

    await events.emit("library.movie_updated", {
        "movie_id": movie_id,
        "status": row.get("status"),
        "confidence": row.get("confidence") or 0,
        "matched_by": row.get("matched_by"),
        "tmdb_id": row.get("tmdb_id"),
        "file_path": row["file_path"],
        "folder": folder,
        "media": json.loads(row.get("media") or "{}"),
        "tmdb": tmdb,
        "folder_tmdb_ids": folder_tmdb_ids,
        "folder_is_library_root": bool(root) and os.path.normpath(folder) == os.path.normpath(root),
    })
