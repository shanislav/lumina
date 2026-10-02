"""Names of TV episode files (core/naming, docs SERIALY)."""

from app.core import naming

MEDIA = {"width": 1280, "height": 720, "video_codec": "h264", "audio": [{"lang": "cs"}, {"lang": "en"}]}
SHOW = {"tmdb_id": 2290, "year": 2004}


def test_default_episode_paths():
    show, season, name = naming.episode_paths(SHOW, 2, [2], MEDIA, "SGA - S02E02-The Intruder.CZ.720p.mkv",
                                              "Hvězdná brána: Atlantida", ".MKV", "Vetřelec")
    assert show == "Hvězdná brána - Atlantida (2004) {tmdb-2290}"   # ":" as in film names
    assert season == "Season 02"
    assert name == "Hvězdná brána - Atlantida - S02E02 - Vetřelec [720p x264] [CS+EN].mkv"


def test_generic_episode_name_left_out_and_more_episodes():
    _, _, name = naming.episode_paths(SHOW, 1, [1, 2], MEDIA, "x.mkv", "Show", ".mkv", "Epizoda 1")
    assert name == "Show - S01E01-E02 [720p x264] [CS+EN].mkv"
    _, season, _ = naming.episode_paths(SHOW, 0, [3], MEDIA, "x.mkv", "Show", ".mkv", "3. díl")
    assert season == "Specials"
    assert naming.episode_title("Vánoce") == "Vánoce" and naming.episode_title("Episode 12") == ""


def test_film_names_unchanged():
    folder, name = naming.movie_paths({"tmdb_id": 603, "year": 1999}, MEDIA, "x.mkv", "Matrix", ".mkv")
    assert folder == "1999/Matrix (1999)" and name == "Matrix (1999) [720p x264] [CS+EN] {tmdb-603}.mkv"
