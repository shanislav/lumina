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
