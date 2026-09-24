from app.modules.search.router import _ddl_queries, _is_video_name


def test_ddl_queries_short_full_and_english_title():
    assert _ddl_queries("Podfukáři 3: Nové kouzlo 2025", "Now You See Me: Now You Don't", "Now You See Me: Now You Don't") == \
        ["Podfukáři 3", "Podfukáři 3 Nové kouzlo", "Now You See Me Now You Dont"]


def test_ddl_queries_without_subtitle_and_duplicates():
    assert _ddl_queries("Pelíšky 1999", "Pelíšky", "Cosy Dens") == ["Pelíšky", "Cosy Dens"]
    assert _ddl_queries("Matrix 1999", "The Matrix", "The Matrix") == ["Matrix", "The Matrix"]


def test_only_video_files_from_ddl():
    names = ["a.mkv", "b.rar", "c.torrent", "d.srt", "e.iso", "f.part1.rar", "g.MP4", "h.001", "i.zip", "no-extension"]
    assert [n for n in names if _is_video_name(n)] == ["a.mkv", "g.MP4", "no-extension"]
