"""A show pack downloads its first wanted episodes first (watching can start before the rest is in)."""

from app.modules.downloads.monitor import pack_order


def test_the_first_wanted_episodes_first():
    files = [{"index": 0, "name": "Columbo/Season 02/Columbo S02E01.mkv"},
             {"index": 1, "name": "Columbo/Season 01/Columbo S01E02.mkv"},
             {"index": 2, "name": "Columbo/Season 01/Columbo S01E01.mkv"},
             {"index": 3, "name": "Columbo/Season 01/Columbo S01E01.cs.srt"},
             {"index": 4, "name": "Columbo/Extras/Making of.mkv"},
             {"index": 5, "name": "Columbo/Season 01/Columbo S01E03.mkv"}]
    assert pack_order(files, skip=[1], pack_season=None) == [2, 5, 0, 4]     # E02 owned (skipped), extras last


def test_the_first_season_high_its_first_episodes_the_highest():
    from app.modules.downloads.monitor import pack_priorities
    files = [{"index": i, "name": f"Show/Season 0{s}/Show S0{s}E0{e}.mkv"}
             for i, (s, e) in enumerate([(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (2, 2)])]
    assert pack_priorities(files, skip=[0], pack_season=None) == {7: [1, 2], 6: [3]}   # E01 owned: E02, E03 first
    assert pack_priorities(files[4:], skip=[], pack_season=None) == {7: [4, 5]}
    assert pack_priorities([], skip=[], pack_season=None) == {}


def test_a_pack_numbered_through_the_whole_show():
    from app.modules.library.imports import pack_by_name_or_order
    cat = {(0, 1): {"cs": "Speciál", "en": "Special"},
           (1, 1): {"cs": "Vražda na předpis", "en": "Prescription: Murder"},
           (1, 2): {"cs": "Výkupné za mrtvého", "en": "Ransom for a Dead Man"},
           (2, 1): {"cs": "Etuda v černém", "en": "Etude in Black"},
           (2, 2): {"cs": "Smrt v zrcadle", "en": "The Greenhouse Jungle"}}
    assert pack_by_name_or_order("Columbo (CS)/03 - Etuda v černém.avi", cat) == (2, [1])          # by its name
    assert pack_by_name_or_order("Columbo (CS)/04 - Něco jiného.avi", cat) == (None, [])        # a name TMDB lacks
    assert pack_by_name_or_order("Columbo (CS)/04.avi", cat) == (2, [2])                         # no name: the 4th
    assert pack_by_name_or_order("Columbo (CS)/09.avi", cat) == (None, [])
    assert pack_by_name_or_order("Columbo (CS)/01 - x.avi", {}) == (None, [])


def test_a_numbered_file_finds_its_episode_by_its_name_first():
    from app.modules.library.imports import pack_by_name_or_order
    cat = {(0, 1): {"cs": "Vražda na předpis", "en": "Prescription: Murder"},
           (0, 2): {"cs": "Výkupné za mrtvého", "en": "Ransom for a Dead Man"},
           (1, 1): {"cs": "Vražda podle knihy", "en": "Murder by the Book"},
           (1, 2): {"cs": "Smrt nabízí pomocnou ruku", "en": "Death Lends a Hand"},
           (1, 3): {"cs": "Semínko pochyb", "en": "Dead Weight"},
           (1, 4): {"cs": "Prázdný rám", "en": "Suitable for Framing"}}
    # TMDB has the two pilots among the specials: the pack's 06th is TMDB's S01E04 — its name says so
    assert pack_by_name_or_order("Columbo (CS)/06 - Prázdný rám.avi", cat) == (1, [4])
    assert pack_by_name_or_order("Columbo (CS)/01 - Vražda na předpis.avi", cat) == (0, [1])


def test_an_episode_long_file_vs_a_film_of_the_pack():
    from app.modules.library.imports import is_episode_length
    cat = {(1, e): {"runtime": 75} for e in range(1, 6)}
    assert is_episode_length(74 * 60, cat) and not is_episode_length(0, cat)
    assert not is_episode_length(140 * 60, cat) and not is_episode_length(5 * 60, cat)


def test_the_scan_and_the_import_read_the_same_names():
    from app.modules.library import episode_names, importer
    assert importer._bare_title is episode_names.bare_title
    assert "Davný protivník" in episode_names.release_titles("37.Davný protivník.avi")
    assert episode_names.absolute_in_name("[CNT]_Naruto_153_[1080p].mkv") == 153


def test_mash_two_parts_and_letters():
    from app.modules.library import episode_names as en
    cat = {(4, 13): {"cs": "Zbraň", "en": "The Gun", "runtime": 25},
           (6, 11): {"cs": "Hrobař", "en": "The Grave", "runtime": 25},
           (6, 12): {"cs": "Kamarádi ve zbrani, 1. část", "en": "Comrades in Arms: Part 1", "runtime": 25},
           (6, 13): {"cs": "Kamarádi ve zbrani, 2. část", "en": "Comrades in Arms: Part 2", "runtime": 25},
           (7, 20): {"cs": "J*E*S*K*Y*N*Ě", "en": "C*A*V*E", "runtime": 25},
           (7, 21): {"cs": "Pozor na Flagga, chlapci!", "en": "Hot Lips Is Back in Town", "runtime": 25}}
    hit = lambda n, s: en.release_episode(n, cat, s, ["M*A*S*H"])[:2]          # noqa: E731
    assert hit("S06E13[135].Kamarádi ve zbrani.mkv", 6) == ((6, 12), True)        # the 1st part, not S04 "Zbraň"
    assert hit("S06E14[136].Kamarádi ve zbrani.II.mkv", 6) == ((6, 13), True)
    assert hit("S07E21[168].J.E.S.K.Y.N.Ě.mkv", 7) == ((7, 20), True)


def test_a_pack_planned_at_once():
    """M*A*S*H's pack: the two-part episodes TMDB keeps as one become pt1 / pt2, nothing takes another's place; a
    sample is not downloaded, a bonus goes to the show's extras, a film-sized file is no episode."""
    from app.modules.library.pack_plan import first_indexes, plan_pack, skip_indexes
    cat = {(4, 1): {"cs": "Vítej v Koreji", "en": "Welcome to Korea", "runtime": 46},
           (4, 2): {"cs": "Změna velení", "en": "Change of Command", "runtime": 25},
           (4, 3): {"cs": "Stalo se jedné noci", "en": "It Happened One Night", "runtime": 25},
           (4, 4): {"cs": "Mrtvý kapitán Pierce", "en": "The Late Captain Pierce", "runtime": 25},
           (4, 5): {"cs": "Hej, doktore", "en": "Hey, Doc", "runtime": 25}}
    gb = 10 ** 9
    files = [{"index": 0, "name": "MASH/S04/S04E01[073].Vítej v Koreji.mkv", "size": gb},
             {"index": 1, "name": "MASH/S04/S04E02[074].Vítej v Koreji.II.mkv", "size": gb},
             {"index": 2, "name": "MASH/S04/S04E03[075].Změna velení.mkv", "size": gb},
             {"index": 3, "name": "MASH/S04/S04E04[076].Stalo se jedné noci.mkv", "size": gb},
             {"index": 4, "name": "MASH/S04/S04E05[077].Mrtvý kapitán Pierce.mkv", "size": gb},
             {"index": 5, "name": "MASH/S04/S04E06.mkv", "size": 9 * gb},                       # a film-sized one
             {"index": 6, "name": "MASH/Sample/sample.mkv", "size": 1},
             {"index": 7, "name": "MASH/Bonus/Making of.mkv", "size": gb // 5},
             {"index": 8, "name": "MASH/S04/S04E03[075].Změna velení.cs.srt", "size": 1}]
    plan = plan_pack(files, cat, owned_local={(4, 4)}, show_names=["M*A*S*H"])
    f = plan["files"]
    assert (f[files[0]["name"]]["episodes"], f[files[0]["name"]]["part"]) == ([1], 1)
    assert (f[files[1]["name"]]["episodes"], f[files[1]["name"]]["part"]) == ([1], 2)
    assert f[files[2]["name"]]["episodes"] == [2] and f[files[3]["name"]]["episodes"] == [3]
    assert f[files[4]["name"]]["kind"] == "owned"
    assert f[files[5]["name"]]["kind"] == "unknown"
    assert f[files[6]["name"]]["kind"] == "sample" and f[files[7]["name"]]["extra"] == "Behind The Scenes"
    assert skip_indexes(plan) == [4, 6]
    assert first_indexes(plan) == ([0, 1], [2, 3])


def test_the_parts_of_a_two_part_episode(tmp_path):
    from app.modules.library.episodes import part_path, parts_of
    for n in ("MASH - S07E04 - Naše nejlepší okamžiky [1080p] - pt1.mkv", "MASH - S07E04 - Naše nejlepší okamžiky [1080p] - pt2.mkv",
              "MASH - S07E04 - Naše nejlepší okamžiky [1080p] - pt1.cs.srt", "MASH - S07E05 - Jiný [1080p].mkv"):
        (tmp_path / n).write_bytes(b"x")
    pt1 = str(tmp_path / "MASH - S07E04 - Naše nejlepší okamžiky [1080p] - pt1.mkv")
    assert [n for n, _ in parts_of(pt1)] == [1, 2]
    assert part_path(pt1, 2).endswith(" - pt2.mkv") and part_path(pt1, None) == pt1
    assert parts_of(str(tmp_path / "MASH - S07E05 - Jiný [1080p].mkv")) == []


def test_owned_parts_of_a_two_part_episode_are_not_downloaded_again():
    """The same M*A*S*H pack added again: its pt1 / pt2 files of an episode owned as "- pt1" / "- pt2" are owned —
    only a part the user does not have, or a whole episode owned only by its first part, is downloaded."""
    from app.modules.library.pack_plan import _has, plan_pack
    cat = {(4, 1): {"cs": "Vítej v Koreji", "en": "Welcome to Korea", "runtime": 46},
           (4, 2): {"cs": "Změna velení", "en": "Change of Command", "runtime": 25},
           (4, 3): {"cs": "Stalo se jedné noci", "en": "It Happened One Night", "runtime": 25},
           (4, 4): {"cs": "Mrtvý kapitán Pierce", "en": "The Late Captain Pierce", "runtime": 25}}
    gb = 10 ** 9
    files = [{"index": 0, "name": "MASH/S04/S04E01[073].Vítej v Koreji.mkv", "size": gb},
             {"index": 1, "name": "MASH/S04/S04E02[074].Vítej v Koreji.II.mkv", "size": gb},
             {"index": 2, "name": "MASH/S04/S04E03[075].Změna velení.mkv", "size": gb},
             {"index": 3, "name": "MASH/S04/S04E04[076].Stalo se jedné noci.mkv", "size": gb},
             {"index": 4, "name": "MASH/S04/S04E05[077].Mrtvý kapitán Pierce.mkv", "size": gb}]
    both = plan_pack(files, cat, {(4, 1), (4, 2)}, ["M*A*S*H"], owned_parts={(4, 1): {1, 2}})["files"]
    assert [both[f["name"]]["kind"] for f in files] == ["owned", "owned", "owned", "episode", "episode"]
    first = plan_pack(files, cat, {(4, 1)}, ["M*A*S*H"], owned_parts={(4, 1): {1}})["files"]
    assert [first[f["name"]]["kind"] for f in files][:3] == ["owned", "episode", "episode"]   # the 2nd part is missing
    # one whole file of full length is both parts; one far too short (only the first part) is not
    assert _has(4, 1, 2, {(4, 1)}, {}, set()) and not _has(4, 1, 2, {(4, 1)}, {}, {(4, 1)})
    # a whole file in the pack: owned only when both parts are
    assert not _has(4, 1, None, {(4, 1)}, {(4, 1): {1}}, set()) and _has(4, 1, None, {(4, 1)}, {(4, 1): {1, 2}}, set())


async def test_a_file_the_library_has_is_not_downloaded_again(monkeypatch):
    """A pack cancelled halfway, started again with "replace owned": the episodes it brought (the very files — an
    import keeps a file as it is, the same size to the byte) are not downloaded again; another version of an owned
    episode (a better bitrate, another sound track — another size) is replaced."""
    import sqlite3

    from app.core import registry
    from app.db import DB_PATH, init_db
    from app.modules.library import episode_names, pack_plan
    await init_db(registry.discover())
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("INSERT INTO library_episodes (show_tmdb_id, season, episode, file_path, file_size, quality, "
                         "language, has_file) VALUES (2190, 1, ?, ?, ?, '1080p', 'CS', 1)",
                         [(1, "/x/e1.mkv", 1_234_567_891), (2, "/x/e2.mkv", 999_000_000)])
    cat = {(1, e): {"cs": f"Díl {e}", "en": "", "runtime": 22} for e in (1, 2, 3)}

    async def checker(*a, **k):
        return None, cat
    monkeypatch.setattr(episode_names, "release_checker", checker)
    files = [{"index": 0, "name": "Show/01. série/01. Díl 1.mkv", "size": 1_234_567_891},    # the very file
             {"index": 1, "name": "Show/01. série/02. Díl 2.mkv", "size": 1_500_000_000},    # a better version
             {"index": 2, "name": "Show/01. série/03. Díl 3.mkv", "size": 1_400_000_000}]    # not owned
    plan = await pack_plan.make_plan(2190, files, None, True, "Show")
    assert pack_plan.skip_indexes(plan) == [0]
    assert "tentýž soubor" in plan["files"]["Show/01. série/01. Díl 1.mkv"]["why"]
    plan = await pack_plan.make_plan(2190, files, None, False, "Show")       # not replacing: what is owned stays
    assert pack_plan.skip_indexes(plan) == [0, 1]
