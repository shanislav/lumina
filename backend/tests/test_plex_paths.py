

def test_from_plex_maps_back():
    from app.modules.plex.paths import from_plex
    assert from_plex("/data/Share/Video/Movies/2000/A/a.mkv", "/data=/data/Share") == "/data/Video/Movies/2000/A/a.mkv"
    assert from_plex("/other/a.mkv", "/data=/data/Share") == "/other/a.mkv"
    assert from_plex("/data/a.mkv") == "/data/a.mkv"
