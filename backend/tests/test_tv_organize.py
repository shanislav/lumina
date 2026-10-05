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
            plex=[12, 24, "Rozloučení"], title="Stockholmský syndrom", tmdb_episode=23, tmdb_sure=True),
        row(e25, "Big Bang Theory", 12, [25], status="not_in_tmdb", file=[12, [25]], plex=[12, 25, "Episode 25"],
            title="Rozlouceni", tmdb_episode=24, tmdb_sure=True),
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
    # another file of the episode lies there already: this one is its next version, nothing is overwritten
    assert not plan["conflicts"]
    assert os.path.basename(plan["ops"][0]["dst"]) == "Loki - S01E01 (2).mkv"


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


async def test_episode_versions_and_deleting_one(db_tv):
    from app.modules.library import episodes
    db, root = db_tv
    a = touch(root, "Loki/S01/Loki.S01E01.mkv")
    touch(root, "Loki/S01/Loki.S01E01.cs.srt")
    b = touch(root, "Loki/S01 4K/Loki.S01E01.2160p.mkv")
    await db.execute("INSERT INTO library_shows (tmdb_id, title) VALUES (84958, 'Loki')")
    await db.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, filename, has_file) "
                     "VALUES (84958, 1, 1, ?, 'Loki.S01E01.mkv', 1)", (a,))
    for p in (a, b):
        await db.execute("INSERT INTO tv_files (file_path, folder, show_tmdb_id, season, episodes, status) VALUES (?, 'Loki', 84958, 1, '[1]', 'ok')", (p,))
    await db.commit()
    ep = await episodes.episode(db, 1)
    assert [v["file_path"] for v in await episodes.versions(db, ep)] == [a, b]
    with pytest.raises(ValueError):
        await episodes.delete_file(db, ep, os.path.join(root, "Loki", "jiny.mkv"), root)
    deleted = await episodes.delete_file(db, ep, a, root)
    assert sorted(os.path.basename(p) for p in deleted) == ["Loki.S01E01.cs.srt", "Loki.S01E01.mkv"]
    ep = await episodes.episode(db, 1)
    assert ep["file_path"] == b and ep["has_file"] == 1                          # the other version takes its place
    await episodes.delete_file(db, ep, b, root)
    assert (await episodes.episode(db, 1))["has_file"] == 0
def test_subtitles_keep_language_sdh_and_never_collide():
    rest = organize_tv.subtitle_rest
    assert rest("S01E01.CHe-CZtit.srt", "S01E01.CHe") == ".cs.srt"
    assert rest("S01E01.CHe.srt", "S01E01.CHe") == ".srt"
    assert rest("Urgent S01E01 07 00.eng.sdh.srt", "Urgent S01E01 07 00") == ".en.sdh.srt"
    assert rest("Urgent S01E01 07 00.cze.forced.srt", "Urgent S01E01 07 00") == ".cs.forced.srt"


def test_two_subtitles_of_one_name_get_numbers(tmp_path):
    root = str(tmp_path)
    a = touch(root, "Chernobyl/S01E01.CHe.avi")
    touch(root, "Chernobyl/S01E01.CHe.srt")
    touch(root, "Chernobyl/S01E01.CHe.cs.srt")
    touch(root, "Chernobyl/S01E01.CHe-CZtit.srt")
    plan = organize_tv.plan_folder([row(a, "Chernobyl", 1, [1], file=[1, [1]])], {"tmdb_id": 87108, "title": "Černobyl", "year": 2019},
                                   {}, {}, root, SETTINGS)
    names = sorted(os.path.basename(op["dst"]) for op in plan["ops"] if op["kind"] == "sidecar")
    assert names == ["Černobyl - S01E01.cs.2.srt", "Černobyl - S01E01.cs.srt", "Černobyl - S01E01.srt"]
    assert not plan["conflicts"]


def test_file_title_when_plex_names_another_part(tmp_path):
    root = str(tmp_path)
    a = touch(root, "Archer/S03/Archer.S00E04.Heart.of.Archness.Part.I.mkv")
    b = touch(root, "Archer/S03/Archer.S01E01.Mole.Hunt.mkv")
    rows = [row(a, "Archer", 0, [4], status="not_in_tmdb", file=[0, [4]], plex=[0, 4, "Heart of Archness - Part II"],
                title="Heart of Archness Part I"),
            row(b, "Archer", 1, [1], file=[1, [1]], plex=[1, 1, "Hon na krtka"], title="Mole Hunt")]
    got = rel(organize_tv.plan_folder(rows, {"tmdb_id": 10283, "title": "Archer", "year": 2009}, {}, {}, root, SETTINGS), root)
    assert got["Archer/S03/Archer.S00E04.Heart.of.Archness.Part.I.mkv"].endswith("/Specials/Archer - S00E04 - Heart of Archness Part I.mkv")
    # another language is no other episode: Plex's (Czech) name stays
    assert got["Archer/S03/Archer.S01E01.Mole.Hunt.mkv"].endswith("/Season 01/Archer - S01E01 - Hon na krtka.mkv")


def test_numbering_by_names_trades_numbers_and_never_doubles_one(tmp_path):
    """South Park S01 in a Czech airing order: E02 "Posilovač 4000" is TMDB's E03, E03 "Sopka" TMDB's E02 —
    they trade numbers. E05's name says E04, but E04 keeps its number (its name is its own): E05 stays."""
    root = str(tmp_path)
    f = {n: touch(root, f"South Park/s01/S01E0{n} {t}.mkv") for n, t in
         ((2, "Posilovač 4000"), (3, "Sopka"), (4, "Velký Al"), (5, "Velký Al 2"))}
    rows = [row(f[2], "South Park", 1, [2], status="tmdb_other", file=[1, [2]], plex=[1, 2, "Sopka"], title="Posilovač 4000",
                tmdb_episode=3, tmdb_sure=True),
            row(f[3], "South Park", 1, [3], status="tmdb_other", file=[1, [3]], plex=[1, 3, "Posilovač 4000"], title="Sopka",
                tmdb_episode=2, tmdb_sure=True),
            row(f[4], "South Park", 1, [4], file=[1, [4]], plex=[1, 4, "Velký Al"], title="Velký Al"),
            row(f[5], "South Park", 1, [5], status="tmdb_other", file=[1, [5]], plex=[1, 5, "Zvířecí farma"], title="Velký Al 2",
                tmdb_episode=4, tmdb_sure=True)]
    titles = {(1, 2): "Sopka", (1, 3): "Posilovač 4000", (1, 4): "Velký Al", (1, 5): "Zvířecí farma"}
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 2190, "title": "South Park", "year": 1997}, {}, titles, root, SETTINGS, mode="tmdb")
    got = {os.path.basename(op["src"]): os.path.basename(op["dst"]) for op in plan["ops"]}
    assert got["S01E02 Posilovač 4000.mkv"] == "South Park - S01E03 - Posilovač 4000.mkv"
    assert got["S01E03 Sopka.mkv"] == "South Park - S01E02 - Sopka.mkv"
    assert got["S01E05 Velký Al 2.mkv"].startswith("South Park - S01E05 ")
    assert [(r["from"], r["to"]) for r in plan["renumber"]] == [("S01E03", "S01E02"), ("S01E02", "S01E03")] or \
        sorted((r["from"], r["to"]) for r in plan["renumber"]) == [("S01E02", "S01E03"), ("S01E03", "S01E02")]
    assert len(plan["unsure"]) == 1 and plan["unsure"][0]["file"] == f[5]
    assert not plan["conflicts"]


def test_move_many_trades_places(tmp_path):
    from app.modules.library.organize import move_many
    a, b = touch(str(tmp_path), "s/E02.mkv"), touch(str(tmp_path), "s/E03.mkv")
    open(a, "w").write("A")
    open(b, "w").write("B")
    move_many([(a, b), (b, a)])
    assert open(a).read() == "B" and open(b).read() == "A"


def test_parts_named_so_stay_parts(tmp_path):
    """After a rename by TMDB's numbering both files say S12E23 — "- pt1" / "- pt2" keeps them parts."""
    root = str(tmp_path)
    stem = "Teorie velkého třesku (2007) {tmdb-1418}/Season 12/Teorie velkého třesku - S12E23 - Proměnlivá konstanta Stockholmský syndrom"
    a, b = touch(root, stem + " - pt1.mkv"), touch(root, stem + " - pt2.mkv")
    rows = [row(p, "Teorie velkého třesku (2007) {tmdb-1418}", 12, [23], file=[12, [23]], plex=[12, 23, "Proměnlivá konstanta / Stockholmský syndrom"],
                title="Proměnlivá konstanta Stockholmský syndrom") for p in (a, b)]
    show = {"tmdb_id": 1418, "title": "Teorie velkého třesku", "year": 2007}
    for mode in ("files", "tmdb"):
        assert not organize_tv.plan_folder(rows, show, {}, {(12, 23): "Proměnlivá konstanta / Stockholmský syndrom"}, root, SETTINGS, mode)["ops"]


def test_numbering_by_names_never_gives_a_kept_number_another_name(tmp_path):
    """South Park S02E04 "Ikova obřízka" (TMDB E03) stays E04 when E03 is taken — still "Ikova obřízka",
    not TMDB's E04 "Milovník slepic"."""
    root = str(tmp_path)
    a = touch(root, "SP/s02/S02E03 Milovnik slepic.mkv")
    b = touch(root, "SP/s02/S02E04 Ikova obrizka.mkv")
    rows = [row(a, "SP", 2, [3], file=[2, [3]], plex=[2, 3, "Ikova obřízka"], title="Milovnik slepic"),
            row(b, "SP", 2, [4], status="tmdb_other", file=[2, [4]], plex=[2, 4, "Milovník slepic"], title="Ikova obrizka",
                tmdb_episode=3, tmdb_sure=True)]
    titles = {(2, 3): "Ikova obřízka", (2, 4): "Milovník slepic"}
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 2190, "title": "SP", "year": 1997}, {}, titles, root, SETTINGS, mode="tmdb")
    got = {os.path.basename(op["src"]): os.path.basename(op["dst"]) for op in plan["ops"]}
    assert "Milovník" not in got.get("S02E04 Ikova obrizka.mkv", "Ikova")
    # the one staying finds the number left free by its name: the two trade places
    assert got["S02E04 Ikova obrizka.mkv"].startswith("SP - S02E03 - Ikeova") or got["S02E04 Ikova obrizka.mkv"].startswith("SP - S02E03 - Ikova")
    assert got["S02E03 Milovnik slepic.mkv"].startswith("SP - S02E04 - Milovník slepic")


def test_a_staying_episode_with_another_translation_fills_the_free_number(tmp_path):
    """Pokémon S01E52–57: four names are sure, "Obtížný test" (TMDB E56 "Obtížná zkouška") and "Perfektní hrdina"
    (TMDB E53 "Odvážný hrdina") are other translations — they take the numbers left free."""
    root = str(tmp_path)
    own = {52: ("Případ jednotky K-9", 54), 53: ("Pokemonoví Paparazzi", 55), 54: ("Obtížný test", None),
           55: ("Tajemství chovatelského střediska", 57), 56: ("Princezna proti Princezně", 52), 57: ("Perfektní hrdina", None)}
    rows = []
    for n, (title, to) in own.items():
        path = touch(root, f"Pokemon/1/S01E{n} {title}.avi")
        if to:
            rows.append(row(path, "Pokemon", 1, [n], status="tmdb_other", file=[1, [n]], plex=[1, n, ""], title=title,
                            tmdb_episode=to, tmdb_sure=True))
        else:
            rows.append(row(path, "Pokemon", 1, [n], file=[1, [n]], plex=[1, n, ""], title=title))
    titles = {(1, 52): "Princezna proti princezně", (1, 53): "Odvážný hrdina", (1, 54): "Případ jednotky K-9",
              (1, 55): "Pokémonový paparazzi", (1, 56): "Obtížná zkouška", (1, 57): "Tajemství chovatelského střediska"}
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 60572, "title": "Pokémon", "year": 1997}, {}, titles, root, SETTINGS, mode="tmdb")
    moves = {r["from"]: r["to"] for r in plan["renumber"]}
    assert moves == {"S01E52": "S01E54", "S01E53": "S01E55", "S01E54": "S01E56", "S01E55": "S01E57",
                     "S01E56": "S01E52", "S01E57": "S01E53"}
    assert not plan["unsure"]


def test_numbering_by_names_into_the_specials(tmp_path):
    """Bluey S03E49 is "Cedule" — TMDB's special S00E27; the real "Překvápko" (S03E50) takes E49."""
    root = str(tmp_path)
    a = touch(root, "Blue/Season 03/Blue - S03E49 - Překvápko.mkv")
    b = touch(root, "Blue/Season 03/Blue - S03E50 - Překvápko.mkv")
    rows = [row(a, "Blue", 3, [49], status="tmdb_other", file=[3, [49]], plex=[3, 49, "Překvápko"], title="Cedule",
                tmdb_episode=27, tmdb_season=0, tmdb_sure=True),
            row(b, "Blue", 3, [50], status="not_in_tmdb", file=[3, [50]], plex=[3, 50, "Episode 50"], title="Překvápko",
                tmdb_episode=49, tmdb_season=3, tmdb_sure=True)]
    titles = {(0, 27): "Cedule", (3, 49): "Překvápko"}
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 82728, "title": "Blue", "year": 2018}, {}, titles, root, SETTINGS, mode="tmdb")
    got = rel(plan, root)
    assert got["Blue/Season 03/Blue - S03E49 - Překvápko.mkv"].endswith("Specials/Blue - S00E27 - Cedule.mkv")
    assert got["Blue/Season 03/Blue - S03E50 - Překvápko.mkv"].endswith("Season 03/Blue - S03E49 - Překvápko.mkv")


def test_the_users_word_on_an_episode_wins(tmp_path):
    """Znalec psí duše S02: Czech names of another order than TMDB's English ones — the user (or a reviewed AI
    mapping) says which episode a file is; the one there gives way."""
    root = str(tmp_path)
    a = touch(root, "Znalec/s02/S02E11 Boj o Eppie, svérázná Lady.avi")
    b = touch(root, "Znalec/s02/S02E13 Pes týmu Lakers.avi")
    rows = [row(a, "Znalec", 2, [11], file=[2, [11]], plex=[2, 11, "Princess, Prada and Bearz"], title="Boj o Eppie, svérázná Lady",
                manual=[2, 13, "Eppie, Lady and Snoopy"]),
            row(b, "Znalec", 2, [13], file=[2, [13]], plex=[2, 13, "Eppie, Lady and Snoopy"], title="Pes týmu Lakers",
                manual=[2, 11, ""])]
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 6326, "title": "Znalec psí duše", "year": 2004}, {}, {}, root, SETTINGS)
    got = {os.path.basename(op["src"]): os.path.basename(op["dst"]) for op in plan["ops"]}
    assert got["S02E11 Boj o Eppie, svérázná Lady.avi"].startswith("Znalec psí duše - S02E13 - Boj o Eppie")
    assert got["S02E13 Pes týmu Lakers.avi"].startswith("Znalec psí duše - S02E11 - Pes týmu Lakers")


def test_copies_of_one_episode_are_versions_not_parts(tmp_path):
    root = str(tmp_path)
    a = touch(root, "Z/s04/S04E26 Schiavona.mkv")
    b = touch(root, "Z/s05/S05E03 Schiavona.mkv")
    rows = [row(a, "Z", 4, [26], file=[4, [26]], title="Schiavona", manual=[5, 2, "kopie"]),
            row(b, "Z", 5, [3], file=[5, [3]], title="Schiavona", manual=[5, 2, ""])]
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 62884, "title": "Z ohnivé výhně", "year": 2015}, {}, {(5, 2): "The Schiavona"},
                                   root, SETTINGS, mode="tmdb")
    names = sorted(os.path.basename(op["dst"]) for op in plan["ops"])
    assert names == ["Z ohnivé výhně - S05E02 - Schiavona (2).mkv", "Z ohnivé výhně - S05E02 - Schiavona.mkv"]


def test_the_users_word_does_not_take_plexs_name_of_the_old_number(tmp_path):
    """Top Gear "14x06" is S14E07 (50 min, not the 76-min Bolivia special): Plex shows it as S14E06 "Bolivijský
    speciál" — that name is the old number's; TMDB's S14E07 has none, so the file goes without a name."""
    root = str(tmp_path)
    a = touch(root, "TG/Season 14/Top Gear - S14E06 - Bolivijský speciál.avi")
    rows = [row(a, "TG", 14, [6], file=[14, [6]], plex=[14, 6, "Bolivijský speciál"], title="", manual=[14, 7, ""])]
    plan = organize_tv.plan_folder(rows, {"tmdb_id": 45, "title": "Top Gear", "year": 2002}, {}, {(14, 7): "7. epizoda"},
                                   root, SETTINGS)
    assert [os.path.basename(op["dst"]) for op in plan["ops"]] == ["Top Gear - S14E07.avi"]


async def test_a_new_sound_language_goes_into_the_name_in_place(db_tv):
    """The user set the sound language of a file without one: the renamer's name for it, in its folder."""
    db, root = db_tv
    a = touch(root, "Bones/Season 04/Bones - S04E03 - Muž v budce [480p XviD].avi")
    touch(root, "Bones/Season 04/Bones - S04E03 - Muž v budce [480p XviD].cs.srt")
    other = touch(root, "Bones/Season 04/Bones - S04E04.avi")
    await db.execute("INSERT INTO tmdb_shows (tmdb_id, data, fetched_at) VALUES (?, ?, ?)",
                     (1911, json.dumps({"title": "Bones", "original_title": "Bones", "original_language": "en", "year": 2005,
                                        "titles_by_lang": {"en": "Bones"}}), time.time()))
    await db.execute("INSERT INTO tv_folders (folder, tmdb_id, source) VALUES ('Bones', 1911, 'plex')")
    for path, e in ((a, 3), (other, 4)):
        await db.execute("INSERT INTO tv_files (file_path, folder, show_tmdb_id, season, episodes, status, facts) "
                         "VALUES (?, 'Bones', 1911, 4, ?, 'ok', ?)", (path, json.dumps([e]), json.dumps({"file": [4, [e]], "title": "Muž v budce"})))
        await db.execute("INSERT INTO tv_media (file_path, size, mtime, media) VALUES (?, 1, 0, ?)",
                         (path, json.dumps({"width": 640, "height": 480, "video_codec": "XviD", "audio": [{"lang": "sk"}]})))
        await db.execute("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, has_file) VALUES (1911, 4, ?, ?, 1)",
                         (e, path))
    await db.commit()
    renamed = await organize_tv.rename_files(db, None, root, [a])
    new = renamed[a]
    assert os.path.dirname(new) == os.path.dirname(a) and "[SK]" in os.path.basename(new) and os.path.exists(new)
    assert os.path.exists(new[:-4] + ".cs.srt")                                       # its subtitles go along
    assert os.path.exists(other)                                                       # only the file asked for
    ep = await (await db.execute("SELECT file_path FROM library_episodes WHERE episode = 3")).fetchone()
    assert ep[0] == new


def test_a_shows_folder_name_stays_when_it_is_one_of_its_names():
    """M*A*S*H imported before Plex knew it: "MASH" (no stars in a file name). Plex then calls it "M.A.S.H." —
    the renamer does not rename the whole show for that."""
    details = {"title": "M*A*S*H", "original_title": "M*A*S*H", "original_language": "en",
               "titles_by_lang": {"en": "M*A*S*H", "fr": "M.A.S.H."}}
    st = {"language": "cs", "keep_local_original": True}
    assert organize_tv.folder_title("MASH (1972) {tmdb-918}") == "MASH"
    assert organize_tv.show_title(details, st, "M.A.S.H.", "MASH") == "M*A*S*H"
    assert organize_tv.show_title(details, st, "M.A.S.H.", "") == "M.A.S.H."           # a new show: Plex's name
    assert organize_tv.show_title(details, st, "", "Něco jiného") == "M*A*S*H"
