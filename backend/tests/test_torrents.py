"""Torrents: info hash of a .torrent file, paths between Lumina and qBittorrent, seeding copy."""

import hashlib
import os

from app.clients.qbittorrent import info_hash
from app.core.paths import map_path


def test_info_hash_of_a_torrent_file():
    info = b"d6:lengthi5e4:name5:a.mkv12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"e"
    torrent = b"d8:announce14:http://tracker4:info" + info + b"7:privatei1ee"
    assert info_hash(torrent) == hashlib.sha1(info).hexdigest()


def test_paths_between_lumina_and_qbittorrent():
    rule = "/data=/data/Share"
    assert map_path("/data/Downloads/plex", rule) == "/data/Share/Downloads/plex"
    assert map_path("/data/Share/Downloads/plex/Film.mkv", rule, to_lumina=True) == "/data/Downloads/plex/Film.mkv"
    assert map_path("/elsewhere/x", rule) == "/elsewhere/x"
    assert map_path("/data/Downloads", "") == "/data/Downloads"


def test_seeding_copy_is_a_hard_link_with_subtitles(tmp_path):
    from app.modules.downloads.monitor import _seeding_copy
    video = tmp_path / "Film.2020.mkv"
    video.write_bytes(b"data")
    (tmp_path / "Film.2020.cs.srt").write_text("sub")
    (tmp_path / "other.txt").write_text("x")
    copy = _seeding_copy(str(video), "abcdef0123456789ffff")
    assert os.path.samefile(copy, video)                          # the same data, the torrent keeps seeding
    folder = os.path.dirname(copy)
    assert sorted(os.listdir(folder)) == ["Film.2020.cs.srt", "Film.2020.mkv"]
