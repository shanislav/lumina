"""A season's dub moved from the old versions of its episodes: which file gets it from which version, or why not."""

import json
import sqlite3

from app.core import registry
from app.db import DB_PATH, init_db
from app.modules.audiosync import episodes as dubs


async def test_each_episode_its_target_and_the_version_with_the_dub(tmp_path):
    await init_db(registry.discover())
    season = tmp_path / "Bones" / "Season 01"
    season.mkdir(parents=True)
    files = {}
    for name in ("E01 HEVC.mkv", "E01 old.avi", "E02 HEVC.mkv", "E03 HEVC.mkv", "E03 old.avi", "E04 old.avi"):
        files[name] = season / name
        files[name].write_bytes(b"x" * 10)
    media = {"E01 HEVC.mkv": ["cs"], "E01 old.avi": ["sk"], "E02 HEVC.mkv": ["cs"], "E03 HEVC.mkv": ["cs", "sk"],
             "E03 old.avi": ["sk"], "E04 old.avi": ["sk"]}
    with sqlite3.connect(DB_PATH) as conn:
        for ep, main in ((1, "E01 HEVC.mkv"), (2, "E02 HEVC.mkv"), (3, "E03 HEVC.mkv"), (4, "E04 old.avi")):
            conn.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, filename, has_file) "
                         "VALUES (1911, 1, ?, ?, ?, 1)", (ep, str(files[main]), main))
        for name, path in files.items():
            ep = int(name[1:3])
            conn.execute("INSERT INTO tv_files (file_path, show_tmdb_id, season, episodes) VALUES (?, 1911, 1, ?)",
                         (str(path), json.dumps([ep])))
            conn.execute("INSERT INTO tv_media (file_path, size, mtime, media) VALUES (?, 10, 0, ?)",
                         (str(path), json.dumps({"audio": [{"lang": l} for l in media[name]]})))
    got = {p["episode"]: p for p in await dubs.pairs(1911, 1, "sk")}
    assert got[1]["status"] == "ready" and got[1]["target"] == "E01 HEVC.mkv" and got[1]["source"] == "E01 old.avi"
    assert got[1]["source_track"] == 0 and got[1]["target_track"] == 0
    assert got[2]["status"] == "skip"                      # no old version
    assert got[3]["status"] == "done"                      # the new one has the dub already
    assert got[4]["status"] == "done"                      # its only file is the old one with the dub
