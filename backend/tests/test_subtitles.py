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


def test_sync_finds_the_shift_and_the_frame_rate(monkeypatch):
    import numpy as np
    from app.modules.subtitles import sync

    rng = np.random.default_rng(1)
    # 40 min of "speech": random phrases; the subtitles were timed for 23.976 fps, the video runs at 24 fps
    # and starts 1.9 s earlier
    cues, t = [], 5.0
    while t < 2400:
        d = rng.uniform(1.0, 4.0)
        cues.append((t, t + d))
        t += d + rng.uniform(0.5, 6.0)
    scale, shift = 23.976 / 24, -1.9
    sp = np.zeros(int(2410 / sync.HOP), np.float32)
    for a, b in cues:
        sp[int((a * scale + shift) / sync.HOP):int((b * scale + shift) / sync.HOP)] = 1
    srt = "\n\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\nx" for i, (a, b) in enumerate(cues, 1))
    monkeypatch.setattr(sync, "speech_tracks", lambda video, n: [sp])
    r = sync.fit("v.mkv", srt)
    assert r["ok"] and r["changed"] and r["scale_name"] == "23,976 → 24 fps"
    assert abs(r["shift"] - shift) < 0.05 and not r["cut_warning"]
    fixed = sync.intervals(sync.apply(srt, r["scale"], r["shift"]))
    assert abs(fixed[100][0] - (cues[100][0] * scale + shift)) < 0.06


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def test_sync_of_forced_subtitles_by_their_starts(monkeypatch):
    import numpy as np
    from app.modules.subtitles import sync

    rng = np.random.default_rng(7)
    sp = np.zeros(int(3600 / sync.HOP), np.float32)
    # a film full of (unsubtitled) speech …
    t = 2.0
    while t < 3590:
        d = rng.uniform(0.8, 4.0)
        sp[int(t / sync.HOP):int((t + d) / sync.HOP)] = 1
        t += d + rng.uniform(0.3, 3.0)
    # … and 40 foreign lines after a quiet moment; the forced subtitles come 0.8 s too early
    cues = []
    for start in np.sort(rng.uniform(60, 3500, 40)):
        sp[int((start - 1.0) / sync.HOP):int(start / sync.HOP)] = 0
        sp[int(start / sync.HOP):int((start + 2.0) / sync.HOP)] = 1
        cues.append((start - 0.8, start + 1.2))
    srt = "\n\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\nx" for i, (a, b) in enumerate(cues, 1))
    monkeypatch.setattr(sync, "speech_tracks", lambda video, n: [sp])
    r = sync.fit("v.mkv", srt)
    assert r["ok"] and r["changed"] and r["scale"] == 1.0
    assert abs(r["shift"] - 0.8) < 0.06, r
