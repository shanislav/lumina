"""The TV renamer: plan (numbers, names, sidecars, extras, parts), apply and undo."""

import json
import os
import time

import pytest

from app.core import registry
from app.db import get_db, init_db
from app.modules.library import organize, organize_tv

SETTINGS = {"language": "cs", "keep_local_original": True,
            "tv_folder_format": "{title} ({year}) {tmdb-{tmdb_id}}", "tv_season_format": "Season {season}",
            "tv_file_format": "{title} - {se} - {episode_title} [{res} {codec} {hdr}] [{langs}]"}
MEDIA = {"width": 1920, "height": 1080, "video_codec": "AVC", "audio": [{"lang": "cs"}, {"lang": "en"}]}


def touch(root, rel):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")
    return path


def row(path, folder, season, episodes, status="ok", **facts):
    return {"file_path": path, "folder": folder, "season": season, "episodes": episodes, "status": status,
            "note": facts.pop("note", ""), "facts": facts}


def rel(plan, root):
    return {os.path.relpath(op["src"], root).replace(os.sep, "/"): os.path.relpath(op["dst"], root).replace(os.sep, "/")
            for op in plan["ops"]}


def test_users_numbers_names_sidecars_extras(tmp_path):
    root = str(tmp_path)
    a = touch(root, "Kosti/S04/Bones.s04e01+02.avi")
    touch(root, "Kosti/S04/Bones.s04e01+02.cs.srt")
    b = touch(root, "Kosti/S04/Bones.S04E03.avi")
    b2 = touch(root, "Kosti/S04 bis/Bones.S04E03.avi")            # a second version, the same name by the rules
    u = touch(root, "Kosti/divne.avi")
    touch(root, "Kosti/Bonus/Making of.mkv")
    touch(root, "Kosti/tvshow.nfo")
    rows = [
        row(a, "Kosti", 4, [1], file=[4, [1, 2]], plex=[4, 2, "Muž v plotě"], title=""),
        row(b, "Kosti", 4, [3], file=[4, [3]], plex=[4, 3, "Episode 3"], title=""),
        row(b2, "Kosti", 4, [3], file=[4, [3]], title=""),
        row(u, "Kosti", None, [], status="unknown"),
        row(os.path.join(root, "Kosti", "Bonus", "Making of.mkv"), "Kosti", None, [], status="extra"),
    ]
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 1911, "title": "Kosti", "year": 2005}, {a: MEDIA, b: MEDIA, b2: MEDIA},
                                   {(4, 3): "Hřích v kosti"}, root, SETTINGS)
    got = rel(plan, root)
    show = "Kosti (2005) {tmdb-1911}"
    assert got["Kosti/S04/Bones.s04e01+02.avi"] == f"{show}/Season 04/Kosti - S04E01-E02 - Muž v plotě [1080p x264] [CS+EN].avi"
    assert got["Kosti/S04/Bones.s04e01+02.cs.srt"] == f"{show}/Season 04/Kosti - S04E01-E02 - Muž v plotě [1080p x264] [CS+EN].cs.srt"
    # Plex's "Episode 3" says nothing — TMDB's name; the second version keeps "(2)"
    assert got["Kosti/S04/Bones.S04E03.avi"] == f"{show}/Season 04/Kosti - S04E03 - Hřích v kosti [1080p x264] [CS+EN].avi"
    assert got["Kosti/S04 bis/Bones.S04E03.avi"] == f"{show}/Season 04/Kosti - S04E03 - Hřích v kosti [1080p x264] [CS+EN] (2).avi"
    assert got["Kosti/Bonus/Making of.mkv"] == f"{show}/Other/Making of.mkv"          # Bonus → Other
    assert got["Kosti/tvshow.nfo"] == f"{show}/tvshow.nfo"
    assert got["Kosti/divne.avi"] == f"{show}/divne.avi"                              # unknown: keeps its place
    assert plan["skipped"][0]["file"] == u and not plan["conflicts"]


def test_tmdb_numbering_makes_parts(tmp_path):
    """Big Bang S12: TMDB joined E23 + E24 into E23; the farewell special E25 is TMDB's E24."""
    root = str(tmp_path)
    e23 = touch(root, "Big Bang Theory/12/S12E23 Proměnlivá konstanta.mkv")
    e24 = touch(root, "Big Bang Theory/12/S12E24 Stockholmský syndrom.mkv")
    e25 = touch(root, "Big Bang Theory/12/Teorie.velkeho.tresku.S12E25.Rozlouceni.mkv")
    rows = [
        row(e23, "Big Bang Theory", 12, [23], file=[12, [23]], plex=[12, 23, "Proměnlivá konstanta / Stockholmský syndrom"],
            title="Proměnlivá konstanta"),
        row(e24, "Big Bang Theory", 12, [24], status="tmdb_other", note="soubor: …", file=[12, [24]],
            plex=[12, 24, "Rozloučení"], title="Stockholmský syndrom", tmdb_episode=23),
        row(e25, "Big Bang Theory", 12, [25], status="not_in_tmdb", file=[12, [25]], plex=[12, 25, "Episode 25"],
            title="Rozlouceni", tmdb_episode=24),
    ]
    show = {"tmdb_id": 1418, "title": "Teorie velkého třesku", "year": 2007}
    titles = {(12, 23): "Proměnlivá konstanta / Stockholmský syndrom", (12, 24): "Rozloučení"}
    folder = "Teorie velkého třesku (2007) {tmdb-1418}/Season 12/Teorie velkého třesku"

    files = rel(organize_tv.plan_folder(rows, show, {}, titles, root, SETTINGS), root)
    # the user's numbers: E24 keeps its own name (Plex shows TMDB's E24 there)
    assert files["Big Bang Theory/12/S12E24 Stockholmský syndrom.mkv"] == f"{folder} - S12E24 - Stockholmský syndrom.mkv"
    assert files["Big Bang Theory/12/Teorie.velkeho.tresku.S12E25.Rozlouceni.mkv"] == f"{folder} - S12E25 - Rozlouceni.mkv"
    assert organize_tv.tips(rows)

    tmdb = rel(organize_tv.plan_folder(rows, show, {}, titles, root, SETTINGS, mode="tmdb"), root)
    assert tmdb["Big Bang Theory/12/S12E23 Proměnlivá konstanta.mkv"] == \
        f"{folder} - S12E23 - Proměnlivá konstanta Stockholmský syndrom - pt1.mkv"
    assert tmdb["Big Bang Theory/12/S12E24 Stockholmský syndrom.mkv"] == \
        f"{folder} - S12E23 - Proměnlivá konstanta Stockholmský syndrom - pt2.mkv"
    assert tmdb["Big Bang Theory/12/Teorie.velkeho.tresku.S12E25.Rozlouceni.mkv"] == f"{folder} - S12E24 - Rozloučení.mkv"


def test_conflict_blocks(tmp_path):
    root = str(tmp_path)
    a = touch(root, "Loki/S01/Loki.S01E01.mkv")
    touch(root, "Loki (2021) {tmdb-84958}/Season 01/Loki - S01E01.mkv")      # the target is taken by another file
    plan = organize_tv.plan_folder([row(a, "Loki", 1, [1], file=[1, [1]])], {"tmdb_id": 84958, "title": "Loki", "year": 2021},
                                   {}, {}, root, SETTINGS)
    assert plan["conflicts"] and "existuje" in plan["conflicts"][0]


@pytest.fixture
async def db_tv(tmp_path):
    await init_db(registry.discover())
    root = str(tmp_path / "Serials")
    db = await get_db()
    yield db, root
    await db.close()


async def test_apply_names_then_folders_and_undo(db_tv):
    db, root = db_tv
    a = touch(root, "Loki/S01/Loki.S01E01.mkv")
    touch(root, "Loki/S01/Loki.S01E01.en.srt")
    await db.execute("INSERT INTO tmdb_shows (tmdb_id, data, fetched_at) VALUES (?, ?, ?)",
                     (84958, json.dumps({"title": "Loki", "original_title": "Loki", "original_language": "en", "year": 2021,
                                         "titles_by_lang": {"en": "Loki"}}), time.time()))
    await db.execute("INSERT INTO tv_folders (folder, tmdb_id, source) VALUES ('Loki', 84958, 'plex')")
    await db.execute("INSERT INTO tv_folder_overrides (folder, tmdb_id) VALUES ('Loki', 84958)")
    await db.execute("INSERT INTO tv_files (file_path, folder, show_tmdb_id, season, episodes, status, facts) "
                     "VALUES (?, 'Loki', 84958, 1, '[1]', 'ok', ?)", (a, json.dumps({"file": [1, [1]], "plex": [1, 1, "Slavný účel"]})))
    await db.execute("INSERT INTO tv_media (file_path, size, mtime, media) VALUES (?, 1, 0, ?)", (a, json.dumps(MEDIA)))
    await db.execute("INSERT INTO library_shows (tmdb_id, title) VALUES (84958, 'Loki')")
    await db.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, has_file) VALUES (84958, 1, 1, ?, 1)", (a,))
    await db.commit()
    settings = {**SETTINGS, "language": "en"}

    # 1) names only: the file stays in its folder
    plan = organize_tv.names_only(await organize_tv.plan_show(db, None, "Loki", root, settings))
    batch1 = await organize_tv.apply_plan(db, plan, root)
    named = os.path.join(root, "Loki", "S01", "Loki - S01E01 - Slavný účel [1080p x264] [CS+EN].mkv")
    assert os.path.exists(named) and os.path.exists(named[:-4] + ".en.srt")
    # 2) folders: the plan is computed again from the renamed files
    plan = await organize_tv.plan_show(db, None, "Loki", root, settings)
    await organize_tv.apply_plan(db, plan, root)
    final = os.path.join(root, "Loki (2021) {tmdb-84958}", "Season 01", os.path.basename(named))
    assert os.path.exists(final) and os.path.exists(final[:-4] + ".en.srt")
    assert not os.path.exists(os.path.join(root, "Loki"))                            # the old folder is gone
    ep = await (await db.execute("SELECT file_path FROM library_episodes WHERE show_tmdb_id = 84958")).fetchone()
    assert ep[0] == final
    assert (await (await db.execute("SELECT folder FROM tv_folder_overrides")).fetchone())[0] == "Loki (2021) {tmdb-84958}"
    assert (await (await db.execute("SELECT file_path FROM tv_media")).fetchone())[0] == final

    # undo the folders, then the names: back as it was
    batches = [r[0] for r in await (await db.execute(
        "SELECT batch_id FROM file_operations GROUP BY batch_id ORDER BY MIN(id) DESC")).fetchall()]
    for b in batches:
        await organize.undo_batch(db, b, ["/elsewhere", root])
    assert batches[-1] == batch1
    assert os.path.exists(a) and os.path.exists(os.path.join(root, "Loki", "S01", "Loki.S01E01.en.srt"))
    assert not os.path.exists(os.path.join(root, "Loki (2021) {tmdb-84958}"))
    ep = await (await db.execute("SELECT file_path FROM library_episodes WHERE show_tmdb_id = 84958")).fetchone()
    assert ep[0] == a


def test_show_title_plex_when_tmdb_knows_it():
    """TMDB's Czech translation of Bluey is "Blue"; Plex shows "Bluey" — one of TMDB's names, so it is used."""
    details = {"title": "Blue", "original_title": "Bluey", "original_language": "en",
               "titles_by_lang": {"en": "Bluey", "cs": "Blue", "sk": "Bluey"}}
    assert organize_tv.show_title(details, SETTINGS, "Bluey") == "Bluey"
    assert organize_tv.show_title(details, SETTINGS, "") == "Blue"
    assert organize_tv.show_title(details, SETTINGS, "Modrý pes") == "Blue"        # not a name TMDB knows


def test_file_title_before_tmdb_generic_is_none(tmp_path):
    root = str(tmp_path)
    a = touch(root, "Final Space/S01/Final Space_S01E03_Kapitola třetí.mkv")
    b = touch(root, "Final Space/S01/Final Space S01E04.mkv")
    rows = [row(a, "Final Space", 1, [3], file=[1, [3]], title="Kapitola třetí"),
            row(b, "Final Space", 1, [4], file=[1, [4]], title="")]
    got = rel(organize_tv.plan_folder(rows, {"tmdb_id": 74387, "title": "Final Space", "year": 2018}, {},
                                      {(1, 3): "Epizoda 3", (1, 4): "Epizoda 4"}, root, SETTINGS), root)
    assert got["Final Space/S01/Final Space_S01E03_Kapitola třetí.mkv"].endswith("/Final Space - S01E03 - Kapitola třetí.mkv")
    assert got["Final Space/S01/Final Space S01E04.mkv"].endswith("/Final Space - S01E04.mkv")
