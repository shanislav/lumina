"""Fixing names on disk + NFO module, on a temporary library (no network)."""

import json
import os
import sqlite3

import pytest

from app.core import registry
from app.db import DB_PATH, get_db, init_db, update_automation
from app.modules.library import organize
from app.modules.library.nfo import read_nfo
from app.modules.library.notify import emit_movie_updated

DETAILS = {
    "tmdb_id": 335984, "title": "Blade Runner 2049", "original_title": "Blade Runner 2049", "year": 2017,
    "runtime": 164, "original_language": "en", "imdb_id": "tt1856101", "overview": "…", "poster_url": None,
    "titles": ["Blade Runner 2049"], "titles_by_lang": {"en": "Blade Runner 2049", "cs": "Blade Runner 2049"},
}
MEDIA = {"duration_s": 9807, "width": 1280, "height": 536, "video_codec": "AVC", "hdr": "SDR",
         "audio": [{"lang": "cs", "codec": "AC-3", "channels": 6}, {"lang": "en", "codec": "AC-3", "channels": 6}],
         "subtitles": ["cs"]}


@pytest.fixture
async def library(tmp_path):
    await init_db(registry.discover())
    registry.register_subscriptions(registry.discover())
    root = tmp_path / "Movies"
    old = root / "2017" / "Blade Runner 2049 (2017)"
    old.mkdir(parents=True)
    video = old / "Blade Runner (1982) [Bluray-720p x264] [CS+EN].mkv"
    video.write_bytes(b"x")
    (old / "Blade Runner (1982) [Bluray-720p x264] [CS+EN].cs.srt").write_text("sub")
    (old / "Blade Runner 2049 (2017) [-720p].nfo").write_text("<movie><tmdbid>335984</tmdbid></movie>")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('movies_library_dir', ?)", (str(root),))
        conn.execute("INSERT INTO tmdb_movies (tmdb_id, data, fetched_at) VALUES (?, ?, 9e12)",
                     (335984, json.dumps(DETAILS)))
        conn.execute(
            "INSERT INTO library_movies (tmdb_id, title, year, filename, file_path, status, media, file_size) "
            "VALUES (335984, 'Blade Runner 2049', '2017', ?, ?, 'matched', ?, 1)",
            (video.name, str(video), json.dumps(MEDIA)),
        )
    return root


class NoTMDB:
    async def get_movie_full(self, tmdb_id, language="cs-CZ"):
        raise AssertionError("details must come from the cache")


async def test_plan_apply_and_undo(library):
    root = str(library)
    db = await get_db()
    try:
        plan = await organize.plan_movie(db, NoTMDB(), 1, root)
        target = library / "2017" / "Blade Runner 2049 (2017)"
        new_video = target / "Blade Runner 2049 (2017) [720p x264] [CS+EN] {tmdb-335984}.mkv"
        assert {op["dst"] for op in plan["ops"]} >= {str(new_video), str(target / (new_video.stem + ".cs.srt"))}
        assert plan["conflicts"] == []

        batch = await organize.apply_plan(db, plan, root)
        assert new_video.exists()
        assert (target / (new_video.stem + ".cs.srt")).exists()
        row = await (await db.execute("SELECT file_path FROM library_movies WHERE id = 1")).fetchone()
        assert row[0] == str(new_video)

        # nothing left to do → idempotent
        assert (await organize.plan_movie(db, NoTMDB(), 1, root))["ops"] == []

        assert await organize.undo_batch(db, batch, root) >= 2
        assert (library / "2017" / "Blade Runner 2049 (2017)" / "Blade Runner (1982) [Bluray-720p x264] [CS+EN].mkv").exists()
    finally:
        await db.close()


async def test_conflict_blocks_everything(library):
    root = str(library)
    target = library / "2017" / "Blade Runner 2049 (2017)" / "Blade Runner 2049 (2017) [720p x264] [CS+EN] {tmdb-335984}.mkv"
    target.write_bytes(b"someone else")
    db = await get_db()
    try:
        plan = await organize.plan_movie(db, NoTMDB(), 1, root)
        assert plan["conflicts"]
        with pytest.raises(organize.OrganizeError):
            await organize.apply_plan(db, plan, root)
        assert target.read_bytes() == b"someone else"
    finally:
        await db.close()


async def test_folder_moves_when_title_changes(library):
    # User switches naming to Czech; "Můj kamarád drak"-style change of the folder name.
    details = dict(DETAILS, titles_by_lang={"en": "Blade Runner 2049", "cs": "Blade Runner 2049 CZ"})
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tmdb_movies SET data = ?", (json.dumps(details),))
    await update_automation("renamer", config={"language": "cs"})
    root = str(library)
    db = await get_db()
    try:
        plan = await organize.plan_movie(db, NoTMDB(), 1, root)
        await organize.apply_plan(db, plan, root)
    finally:
        await db.close()
    new_folder = library / "2017" / "Blade Runner 2049 CZ (2017)"
    assert new_folder.is_dir()
    assert not (library / "2017" / "Blade Runner 2049 (2017)").exists()  # old empty folder removed
    assert any(p.suffix == ".nfo" for p in new_folder.iterdir())        # other files moved along


async def test_nfo_module_writes_movie_nfo_and_removes_old(library):
    await update_automation("nfo", enabled=True)
    db = await get_db()
    try:
        await emit_movie_updated(db, 1)
    finally:
        await db.close()
    folder = library / "2017" / "Blade Runner 2049 (2017)"
    nfos = sorted(p.name for p in folder.iterdir() if p.suffix == ".nfo")
    assert nfos == ["movie.nfo"]
    facts = read_nfo(str(folder / "movie.nfo"))
    assert (facts.tmdb_id, facts.imdb_id, facts.by_lumina, facts.lumina_status) == (335984, "tt1856101", True, "matched")
    content = (folder / "movie.nfo").read_text(encoding="utf-8")
    assert "<language>ces</language>" in content and "<durationinseconds>9807</durationinseconds>" in content


async def test_nfo_module_disabled_does_nothing(library):
    db = await get_db()
    try:
        await emit_movie_updated(db, 1)
    finally:
        await db.close()
    folder = library / "2017" / "Blade Runner 2049 (2017)"
    assert sorted(p.name for p in folder.iterdir() if p.suffix == ".nfo") == ["Blade Runner 2049 (2017) [-720p].nfo"]


async def test_undo_with_nfo_module_leaves_no_empty_folder(library):
    await update_automation("nfo", enabled=True)
    await update_automation("renamer", config={"folder_format": "{year}/{title} ({year}) new"})
    root = str(library)
    db = await get_db()
    try:
        plan = await organize.plan_movie(db, NoTMDB(), 1, root)
        batch = await organize.apply_plan(db, plan, root)
        await emit_movie_updated(db, 1)                     # nfo module writes movie.nfo in the new folder
        new_folder = library / "2017" / "Blade Runner 2049 (2017) new"
        assert (new_folder / "movie.nfo").exists()
        await organize.undo_batch(db, batch, root)
        await emit_movie_updated(db, 1)                     # …and in the restored folder
    finally:
        await db.close()
    assert not new_folder.exists()
    old_folder = library / "2017" / "Blade Runner 2049 (2017)"
    assert (old_folder / "Blade Runner (1982) [Bluray-720p x264] [CS+EN].mkv").exists()
    assert (old_folder / "movie.nfo").exists()


@pytest.mark.parametrize("name, suffix", [
    ("Parasite (2021) [Bluray-1080p].ass", ""),
    ("Parasite.2019.CZE.forced.srt", ".cs.forced"),
    ("Parasite.cz.srt", ".cs"),
    ("Parasite 2019 English.srt", ".en"),
    ("Parasite.HD.srt", ""),
])
def test_subtitle_suffix(name, suffix):
    assert organize.subtitle_suffix(name) == suffix


async def test_foreign_named_subtitles_follow_the_video(library):
    old = library / "2017" / "Blade Runner 2049 (2017)"
    (old / "BR2049.2017.1080p.CZE.srt").write_text("a")
    (old / "whatever.ass").write_text("b")
    db = await get_db()
    try:
        plan = await organize.plan_movie(db, NoTMDB(), 1, str(library))
    finally:
        await db.close()
    stem = "Blade Runner 2049 (2017) [720p x264] [CS+EN] {tmdb-335984}"
    target = library / "2017" / "Blade Runner 2049 (2017)"
    dsts = {os.path.basename(op["src"]): op["dst"] for op in plan["ops"]}
    assert dsts["BR2049.2017.1080p.CZE.srt"] == str(target / f"{stem}.cs.2.srt")   # .cs.srt is the real sidecar's
    assert dsts["whatever.ass"] == str(target / f"{stem}.ass")
    assert not plan["conflicts"]

