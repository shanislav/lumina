"""identify_movie + _process_movie_file with a fake TMDB client (no network)."""

import sqlite3

from app.core import registry
from app.db import DB_PATH, get_db, init_db
from app.modules.library import importer

TMM_NFO = """<?xml version="1.0" encoding="UTF-8"?>
<movie><title>Blade Runner 2049</title><year>2017</year><tmdbid>335984</tmdbid></movie>"""

MOVIES = {
    78: {"tmdb_id": 78, "title": "Blade Runner", "original_title": "Blade Runner", "year": 1982, "runtime": 117,
         "original_language": "en", "imdb_id": "tt0083658", "overview": "", "poster_url": None, "titles": ["Blade Runner"]},
    335984: {"tmdb_id": 335984, "title": "Blade Runner 2049", "original_title": "Blade Runner 2049", "year": 2017,
             "runtime": 164, "original_language": "en", "imdb_id": "tt1856101", "overview": "", "poster_url": None,
             "titles": ["Blade Runner 2049"]},
}


class FakeTMDB:
    def __init__(self):
        self.detail_calls = 0

    async def search_movie_raw(self, title, year=None, language="cs-CZ"):
        return [{"id": i} for i, m in MOVIES.items() if title.lower() in m["title"].lower()]

    async def find_by_imdb(self, imdb_id):
        return None

    async def get_movie_full(self, tmdb_id, language="cs-CZ"):
        self.detail_calls += 1
        return MOVIES[tmdb_id]


async def _setup_library(tmp_path):
    await init_db(registry.discover())
    root = tmp_path / "Movies"
    folder = root / "2017" / "Blade Runner 2049 (2017)"  # folder wrongly renamed by tMM
    folder.mkdir(parents=True)
    video = folder / "Blade Runner (1982) [Bluray-720p x264] [CS+EN].mkv"
    video.write_bytes(b"x")
    (folder / "Blade Runner 2049 (2017) [-720p].nfo").write_text(TMM_NFO, encoding="utf-8")
    return root, video


async def test_identify_prefers_file_evidence_over_wrong_nfo(tmp_path):
    root, video = await _setup_library(tmp_path)
    db = await get_db()
    try:
        media = {"duration_s": 117 * 60, "audio": [{"lang": "cs"}, {"lang": "en"}]}
        result = await importer.identify_movie(FakeTMDB(), db, str(video), 1, str(root), media)
    finally:
        await db.close()
    assert result["status"] == "matched"
    assert result["ranked"][0].candidate["tmdb_id"] == 78
    assert "tip: nfo" in result["ranked"][1].reasons


async def test_process_file_stores_status_and_keeps_manual_choice(tmp_path, monkeypatch):
    root, video = await _setup_library(tmp_path)
    importer._job.update({"stats": {"matched": 0, "review": 0, "unmatched": 0, "skipped": 0}})

    async def fake_probe(path):
        return {"duration_s": 117 * 60, "height": 720, "width": 1280, "audio": [{"lang": "cs"}, {"lang": "en"}]}

    monkeypatch.setattr(importer, "probe_async", fake_probe)
    tmdb = FakeTMDB()

    db = await get_db()
    try:
        await importer._process_movie_file(tmdb, db, str(video), 1, str(root), force=False)
        await db.commit()
    finally:
        await db.close()

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT tmdb_id, status, quality, language FROM library_movies").fetchone()
        assert row == (78, "matched", "720p", "CS,EN")
        # user overrides the choice
        conn.execute("UPDATE library_movies SET tmdb_id = 335984, status = 'manual'")

    db = await get_db()
    try:
        await importer._process_movie_file(tmdb, db, str(video), 1, str(root), force=True)
        await db.commit()
    finally:
        await db.close()

    with sqlite3.connect(DB_PATH) as conn:
        assert conn.execute("SELECT tmdb_id, status FROM library_movies").fetchone() == (335984, "manual")
    # details were cached: 2 candidates fetched once
    assert tmdb.detail_calls == 2
