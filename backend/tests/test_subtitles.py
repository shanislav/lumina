from app.clients.opensubtitles import movie_hash
from app.modules.subtitles import files


def test_subtitle_files_next_to_the_video(tmp_path):
    video = tmp_path / "Rychle a zběsile 5 (2011) [1080p x265] [CS] {tmdb-51497}.mkv"
    video.write_bytes(b"x")
    for name in ("Rychle a zběsile 5 (2011) [1080p x265] [CS] {tmdb-51497}.cs.forced.srt",
                 "Rychle a zběsile 5 (2011) [1080p x265] [CS] {tmdb-51497}.en.srt", "movie.nfo"):
        (tmp_path / name).write_text("1")
    got = {(f["lang"], f["forced"]) for f in files.external(str(video))}
    assert got == {("cs", True), ("en", False)}
    assert files.target_name(str(video), "sk", True).endswith("{tmdb-51497}.sk.forced.srt")


def test_other_versions_subtitles_are_not_counted(tmp_path):
    a, b = tmp_path / "Film [720p].mkv", tmp_path / "Film [1080p].mkv"
    a.write_bytes(b"x"); b.write_bytes(b"x")
    (tmp_path / "Film [720p].cs.srt").write_text("1")
    assert files.external(str(b)) == []
    assert [f["lang"] for f in files.external(str(a))] == ["cs"]


def test_rescale_and_decode():
    srt = "1\n00:00:10,000 --> 00:00:12,500\nAhoj\n"
    assert "00:00:10,427 --> 00:00:13,034" in files.rescale(srt, 25 / 23.976)
    assert files.decode("Příliš".encode("cp1250")) == "Příliš"
    assert files.decode("﻿Příliš".encode("utf-8")) == "Příliš"


def test_movie_hash(tmp_path):
    f = tmp_path / "v.bin"
    f.write_bytes(bytes(200_000))
    assert movie_hash(str(f)) == f"{200_000:016x}"      # zeros: just the size
    small = tmp_path / "s.bin"
    small.write_bytes(b"1")
    assert movie_hash(str(small)) == ""
