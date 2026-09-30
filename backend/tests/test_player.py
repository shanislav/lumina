"""Browser player: when the original picture can go as it is, and that a stream never piles up."""

from app.modules.player import sessions
from app.modules.player.sessions import Session, choose_mode


def test_original_picture_only_where_the_browser_can_decode_it():
    h264 = {"codec": "h264", "pix_fmt": "yuv420p"}
    hevc = {"codec": "hevc", "pix_fmt": "yuv420p10le", "hdr": True}
    dv5 = {**hevc, "dv_profile": 5}
    assert choose_mode(h264, "auto", {})[0] == "original"
    assert choose_mode({**h264, "pix_fmt": "yuv420p10le"}, "auto", {"hevc": True})[0] == "transcode"
    assert choose_mode(hevc, "auto", {})[0] == "transcode"                      # Firefox
    assert choose_mode(hevc, "auto", {"hevc": True})[0] == "original"           # Chrome/Edge with hardware
    assert choose_mode(dv5, "auto", {"hevc": True})[0] == "transcode"           # DV 5 only in Safari
    assert choose_mode(dv5, "auto", {"hevc": True, "dv5": True})[0] == "original"
    assert choose_mode(hevc, "transcode", {"hevc": True})[0] == "transcode"     # chosen by hand
    assert choose_mode({"codec": "mpeg4"}, "original", {"hevc": True})[0] == "transcode"


def test_segments_behind_the_player_are_deleted(tmp_path):
    s = Session(id="x", user_id=1, movie_id=1, start=0, audio=0, dir=tmp_path, mode="original")
    for n in range(60):
        (tmp_path / f"s{n:05d}.m4s").write_bytes(b"x")
    (tmp_path / "init.mp4").write_bytes(b"x")
    sessions.requested(s, "s00050.m4s")
    sessions.requested(s, "index.m3u8")
    sessions.pace(s)
    left = sorted(int(f.name[1:6]) for f in tmp_path.glob("s*.m4s"))
    assert left[0] == 50 - sessions.BEHIND and left[-1] == 59 and (tmp_path / "init.mp4").exists()


def test_ffmpeg_is_paused_ahead_and_resumed(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(sessions, "_signal", lambda s, sig: sent.append(sig))

    class Proc:
        returncode = None
        pid = 1

    s = Session(id="x", user_id=1, movie_id=1, start=0, audio=0, dir=tmp_path, mode="original", proc=Proc())
    for n in range(sessions.AHEAD + 2):
        (tmp_path / f"s{n:05d}.m4s").write_bytes(b"x")
    sessions.requested(s, "s00000.m4s")
    sessions.pace(s)
    assert s.paused and len(sent) == 1
    sessions.requested(s, f"s{sessions.AHEAD - 5:05d}.m4s")      # the player caught up
    sessions.pace(s)
    assert not s.paused and len(sent) == 2
