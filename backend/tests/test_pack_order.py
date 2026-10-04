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
