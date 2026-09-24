import pytest

from app.core.release_name import normalize_title, parse_name
from app.modules.library.nfo import find_nfo, read_nfo


@pytest.mark.parametrize("name, title, year", [
    ("S.W.A.T. (2003) [SDTV XviD].avi", "S.W.A.T.", 2003),
    ("The Matrix Revolutions (2003) [WEBDL-1080p h264] [CS+EN].mkv", "The Matrix Revolutions", 2003),
    ("Matrix Revolutions (sk).mkv", "Matrix Revolutions", None),
    ("Podfukáři 3 (2025) UHDRDV cz en.mp4", "Podfukáři 3", 2025),
    ("Rubber.2010.1080p.BluRay.x264.VPPV.mkv", "Rubber", 2010),
    ("rubber 2010", "rubber", 2010),
    ("DV Test (Jellyfin)", "DV Test", None),
    ("Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.CZ.DD5.1.EN.7.1..mkv", "Matrix", 1999),
    ("2001 - A Space Travesty (2000) [Bluray-576p DivX].avi", "2001 - A Space Travesty", 2000),
    ("1917 (2019)", "1917", 2019),
    ("Blade.Runner.2049.2017.1080p.mkv", "Blade Runner 2049", 2017),
    ("Mr. & Mrs. Smith (2005).mkv", "Mr. & Mrs. Smith", 2005),
])
def test_parse_name(name, title, year):
    facts = parse_name(name)
    assert (facts.title, facts.year) == (title, year)


def test_parse_name_ids_and_alt_titles():
    assert parse_name("The Matrix (1999) {tmdb-603}").tmdb_id == 603
    assert parse_name("Heat (1995) [tmdbid-949].mkv").tmdb_id == 949
    assert parse_name("Heat.1995.tt0113277.mkv").imdb_id == "tt0113277"
    assert parse_name("Duna - Dune 2021 HEVC 1080p EN+CZ.mkv").extra_titles == ["Duna", "Dune"]
    assert parse_name("2001 - A Space Travesty (2000).avi").extra_titles == []


def test_normalize_title():
    assert normalize_title("Podfukáři 3: Nové kouzlo!") == "podfukari 3 nove kouzlo"
    assert normalize_title("Mr. & Mrs. Smith") == "mr and mrs smith"


TMM_NFO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>S.W.A.T. – Jednotka rychlého nasazení</title>
  <originaltitle>S.W.A.T.</originaltitle>
  <year>2003</year>
  <id>tt0257076</id>
  <tmdbid>9257</tmdbid>
  <uniqueid default="false" type="tmdb">9257</uniqueid>
  <uniqueid default="true" type="imdb">tt0257076</uniqueid>
</movie>"""


def test_find_and_read_tmm_nfo_with_different_name(tmp_path):
    folder = tmp_path / "S.W.A.T. (2003)"
    folder.mkdir()
    video = folder / "S.W.A.T. (2003) [SDTV XviD].avi"
    video.write_bytes(b"x")
    (folder / "S.W.A.T. (2003) [-480p XVID].nfo").write_text(TMM_NFO, encoding="utf-8")

    path = find_nfo(str(video), videos_in_folder=1)
    facts = read_nfo(path)

    assert facts.tmdb_id == 9257
    assert facts.imdb_id == "tt0257076"
    assert facts.year == 2003


def test_single_nfo_is_ambiguous_with_more_videos(tmp_path):
    (tmp_path / "a.mkv").write_bytes(b"x")
    (tmp_path / "b.mkv").write_bytes(b"x")
    (tmp_path / "other.nfo").write_text(TMM_NFO, encoding="utf-8")
    assert find_nfo(str(tmp_path / "a.mkv"), videos_in_folder=2) is None
