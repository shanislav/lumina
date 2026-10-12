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


# ── smaller files ──

from app.modules.convert import shrink  # noqa: E402


def test_the_users_choices_are_checked():
    o = shrink.options({"vmaf": "93", "speed": "warp", "max_height": "1080", "audio": "shrink", "min_saving": 500, "go": 1})
    assert o == {"vmaf": 93.0, "speed": "balanced", "max_height": 1080, "audio": "shrink", "min_saving": 80, "go": True,
                 "mode": "quality", "mbps": 4.0, "where": "server"}
    assert shrink.options({"mode": "rate", "mbps": "2.5"})["mbps"] == 2.5
    assert shrink.options({"vmaf": 20})["vmaf"] == 95                      # nonsense → the default
    assert shrink.options(None) == shrink.DEFAULTS


def test_hdr10_stays_hdr10():
    info = {"hdr10": True, "light": {"max_content": 1000, "max_average": 400},
            "mastering": {"red_x": "35400/50000", "red_y": "14600/50000", "green_x": "8500/50000",
                          "green_y": "39850/50000", "blue_x": "6550/50000", "blue_y": "2300/50000",
                          "white_point_x": "15635/50000", "white_point_y": "16450/50000",
                          "max_luminance": "10000000/10000", "min_luminance": "50/10000"}}
    p = shrink.x265_params(info)
    assert "hdr10=1" in p and "transfer=smpte2084" in p and "max-cll=1000,400" in p
    assert "master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,50)" in p
    assert shrink.x265_params({"hdr10": False}) == "log-level=error"


def test_the_highest_crf_that_reaches_the_target(monkeypatch):
    # quality falls by 1 per CRF step from 99 at CRF 14: CRF 18 → 95, 19 → 94
    def fake(ref, info, opts, crf, work):
        return {"crf": crf, "vmaf": 99 - (crf - 14), "size": 1000 - crf * 10, "seconds": 1, "path": f"crf{crf}"}
    monkeypatch.setattr(shrink, "encode_sample", fake)
    best, trials = shrink.find_crf("ref", {"height": 1080}, {"vmaf": 95}, "w")
    assert best["crf"] == 18 and len(trials) <= 5
    best, _ = shrink.find_crf("ref", {"height": 1080}, {"vmaf": 99.5}, "w")       # unreachable
    assert best is None
    best, _ = shrink.find_crf("ref", {"height": 1080}, {"vmaf": 70}, "w")         # even the top CRF passes
    assert best["crf"] == shrink.CRF_RANGE[1]


def test_frames_per_second_even_from_a_transport_stream():
    assert shrink._fps({"avg_frame_rate": "0/0", "r_frame_rate": "24000/1001"}) == 24000 / 1001
    assert shrink._fps({"avg_frame_rate": "25/1"}) == 25
    assert shrink._fps({}) == 25.0


def test_two_encodes_when_the_first_guess_is_close(monkeypatch):
    # Bones S01E05 as measured: 22 → 95.35, 23 → 94.85
    seen = {22: 95.35, 23: 94.85, 24: 94.28, 26: 92.88}
    monkeypatch.setattr(shrink, "encode_sample", lambda ref, info, opts, crf, work:
                        {"crf": crf, "vmaf": seen[crf], "size": 1, "seconds": 1})
    best, trials = shrink.find_crf("ref", {"height": 1080}, {"vmaf": 95}, "w")
    assert best["crf"] == 22 and [t["crf"] for t in trials] == [22, 23]


def test_rate_mode_finds_the_crf_giving_the_users_bitrate(monkeypatch):
    # the samples' bitrate halves every 6 CRF: 4 Mb/s at CRF 22 → 2 Mb/s at CRF 28
    def fake(ref, info, opts, crf, work):
        return {"crf": crf, "vmaf": 100 - crf, "size": int(4e6 / 8 * 32 * 2 ** ((22 - crf) / 6)), "seconds": 1,
                "duration": 32, "paths": []}
    monkeypatch.setattr(shrink, "encode_sample", fake)
    best, trials = shrink.find_crf_for_rate("ref", {"height": 1080}, {"mbps": 2.0}, "w")
    assert best["crf"] == 28 and best["vmaf"] == 72 and len(trials) == 2


def test_rate_mode_encodes_at_the_found_crf_not_a_held_bitrate():
    info = {"hdr10": False, "height": 1080}
    args = shrink._video_args(info, shrink.options({"mode": "rate", "mbps": 2}), 28)
    assert args[args.index("-crf") + 1] == "28" and "-b:v" not in args
    gpu = shrink._video_args(info, shrink.options({"mode": "rate", "mbps": 2, "speed": "gpu"}), 28)
    assert gpu[gpu.index("-cq") + 1] == "28" and gpu[gpu.index("-b:v") + 1] == "0"
