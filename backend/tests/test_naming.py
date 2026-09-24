from app.core.naming import movie_paths, pick_title, render, sanitize

BR_MEDIA = {"width": 1280, "height": 536, "video_codec": "AVC", "hdr": "SDR", "audio": [{"lang": "cs"}, {"lang": "en"}]}


def test_default_layout_folder_without_id_file_with_id():
    folder, name = movie_paths({"tmdb_id": 335984, "year": 2017}, BR_MEDIA,
                               "Blade Runner (1982) [Bluray-720p x264] [CS+EN].mkv", "Blade Runner 2049", ".MKV")
    assert folder == "2017/Blade Runner 2049 (2017)"
    assert name == "Blade Runner 2049 (2017) [720p x264] [CS+EN] {tmdb-335984}.mkv"


def test_hdr_from_release_name_and_colon():
    media = {"width": 3840, "height": 2160, "video_codec": "HEVC", "hdr": "SDR", "audio": [{"lang": "cs"}]}
    folder, name = movie_paths({"tmdb_id": 425274, "year": 2025}, media, "Podfukáři 3 (2025) UHDRDV cz en.mp4",
                               "Now You See Me: Now You Don't", ".mp4")
    assert folder == "2025/Now You See Me - Now You Don't (2025)"
    assert name == "Now You See Me - Now You Don't (2025) [2160p x265 DV] [CS] {tmdb-425274}.mp4"


def test_title_dots_kept_but_not_trailing():
    folder, name = movie_paths({"tmdb_id": 9257, "year": 2003}, {}, "x.avi", "S.W.A.T.", ".avi")
    assert folder == "2003/S.W.A.T. (2003)"
    assert name == "S.W.A.T. (2003) {tmdb-9257}.avi"
    assert sanitize("Movie.") == "Movie"


def test_legacy_renamer_format():
    values = {"title": "Rubber", "year": "2010", "tmdb_id": "45649", "source": "", "res": "1080p",
              "codec": "x264", "langs": ""}
    assert render("{title} ({year}) [{source}-{res} {codec}] [{langs}] {tmdb-{id}}", values) == \
        "Rubber (2010) [1080p x264] {tmdb-45649}"


def test_pick_title_language_rules():
    pelisky = {"cs": "Pelíšky", "en": "Cosy Dens"}
    assert pick_title(pelisky, "cs", "Pelíšky", "en") == "Pelíšky"            # local film keeps original
    assert pick_title(pelisky, "cs", "Pelíšky", "en", keep_local_original=False) == "Cosy Dens"
    dragon = {"cs": "Můj kamarád drak", "en": "Pete's Dragon"}
    assert pick_title(dragon, "en", "Pete's Dragon", "cs") == "Můj kamarád drak"
    assert pick_title(dragon, "en", "Pete's Dragon", "en") == "Pete's Dragon"
    assert pick_title({"en": "Parasite"}, "ko", "기생충", "cs") == "Parasite"  # fallback to English
    assert pick_title({"en": "Parasite"}, "ko", "기생충", "orig") == "기생충"
