"""Úpravy souborů: Dolby Vision MKV → MP4 — which files, the subtitles' names, the queue and the library after it."""

import json
import os
import sqlite3

from app.core import events, registry
from app.db import DB_PATH, init_db
from app.modules.convert import dv, handlers, jobs


def test_subtitles_go_next_to_the_video_as_plex_reads_them():
    info = {"subtitles": [
        {"index": 4, "codec_name": "subrip", "tags": {"language": "cze"}, "disposition": {"forced": 0}},
        {"index": 5, "codec_name": "subrip", "tags": {"language": "cze"}, "disposition": {"forced": 1}},
        {"index": 6, "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}, "disposition": {"hearing_impaired": 1}},
        {"index": 7, "codec_name": "ass", "tags": {}, "disposition": {}},
        {"index": 8, "codec_name": "subrip", "tags": {"language": "cze"}, "disposition": {}},
    ]}
    out, left = dv.subtitle_outputs(info, "Film (2024)", {"Film (2024).cs.srt"})
    names = [n for _, n, _ in out]
    assert names == ["Film (2024).cs.1.srt", "Film (2024).cs.forced.srt", "Film (2024).en.sdh.sup",
                     "Film (2024).und.ass", "Film (2024).cs.5.srt"]       # an existing file is never overwritten
    assert [c for _, _, c in out] == ["srt", "srt", "copy", "ass", "srt"]
    assert left == []
    _, left = dv.subtitle_outputs({"subtitles": [{"index": 3, "codec_name": "dvd_subtitle"}]}, "x", set())
    assert left == ["#3 dvd_subtitle"]                                       # the job stops, the file stays


def test_only_dolby_vision_in_mkv_wants_it():
    assert dv.needs_remux("a.mkv", {"dv_profile": 8})
    assert dv.needs_remux("a.mkv", {"dv_profile": 0})
    assert not dv.needs_remux("a.mkv", {"dv_profile": None})
    assert not dv.needs_remux("a.mp4", {"dv_profile": 8})


async def test_a_job_puts_the_mp4_in_the_mkv_place_and_the_library_follows(tmp_path, monkeypatch):
    await init_db(registry.discover())
    folder = tmp_path / "Film (2024)"
    folder.mkdir()
    src = folder / "Film (2024) [2160p] DV.mkv"
    src.write_bytes(b"m" * 100)
    (folder / "Film (2024) [2160p] DV.cs.srt").write_text("old")       # shares the stem: stays with the MP4
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO library_movies (tmdb_id, title, year, filename, file_path, status, media) "
                     "VALUES (1, 'Film', '2024', ?, ?, 'matched', '{}')", (src.name, str(src)))

    def fake_remux(path, work, stem, taken, progress=None):
        os.makedirs(work, exist_ok=True)
        out = os.path.join(work, stem + ".mp4")
        open(out, "wb").write(b"p" * 90)
        sub = os.path.join(work, stem + ".en.srt")
        open(sub, "w").write("new")
        return out, [sub]
    monkeypatch.setattr(dv, "remux", fake_remux)

    async def fake_probe(path):
        return {"hdr": "DV", "duration_s": 60}
    monkeypatch.setattr("app.core.mediainfo.probe_async", fake_probe)
    seen = []

    async def on_updated(p):
        seen.append(p["file_path"])
    events.subscribe("library.movie_updated", on_updated)
    try:
        await jobs.enqueue("dv", [str(src)])
        job = await jobs._next()
        result = await jobs.run(job)
    finally:
        events.clear()
    mp4 = folder / "Film (2024) [2160p] DV.mp4"
    assert result["path"] == str(mp4) and mp4.read_bytes() == b"p" * 90
    assert not src.exists() and not (folder / jobs.WORK_DIR).exists()
    assert (folder / "Film (2024) [2160p] DV.cs.srt").read_text() == "old"
    assert (folder / "Film (2024) [2160p] DV.en.srt").read_text() == "new"
    with sqlite3.connect(DB_PATH) as conn:
        path, name, media = conn.execute("SELECT file_path, filename, media FROM library_movies").fetchone()
    assert path == str(mp4) and name == mp4.name and json.loads(media)["hdr"] == "DV"
    assert seen == [str(mp4)]                                    # NFO and Plex hear of it


async def test_a_failed_check_leaves_the_original_alone(tmp_path, monkeypatch):
    await init_db(registry.discover())
    src = tmp_path / "Show S01E01.mkv"
    src.write_bytes(b"m" * 100)

    def failing(path, work, stem, taken, progress=None):
        os.makedirs(work, exist_ok=True)
        raise dv.RemuxError("Metadata Dolby Vision nesedí (RPU 10 vs 9)")
    monkeypatch.setattr(dv, "remux", failing)
    await jobs.enqueue("dv", [str(src)])
    job = await jobs._next()
    try:
        await jobs.run(job)
        raise AssertionError("expected RemuxError")
    except dv.RemuxError:
        pass
    assert src.read_bytes() == b"m" * 100 and not (tmp_path / jobs.WORK_DIR).exists()


async def test_after_a_download_its_dv_mkvs_are_queued(tmp_path, monkeypatch):
    await init_db(registry.discover())
    a, b, c = tmp_path / "E01.mkv", tmp_path / "E02.mkv", tmp_path / "E03.mp4"
    for f in (a, b, c):
        f.write_bytes(b"x")
    monkeypatch.setattr(dv, "needs_remux", lambda path, info=None: path.endswith("E01.mkv"))
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    await handlers.on_download_completed({"imported": True, "path": str(a), "placed_paths": [str(a), str(b), str(c)]})
    await handlers.on_download_completed({"imported": False, "path": str(b)})     # not in the library: nothing
    queued = [j["path"] for j in await jobs.recent()]
    assert queued == [str(a)]
