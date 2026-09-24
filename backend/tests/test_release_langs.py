import pytest

from app.core.mediainfo import normalize_language
from app.core.release_langs import parse_languages

# Real names from WebShare/FastShare searches; audio verified on the FastShare file page
# where noted (the name "Cz.Sub" file really has Lithuanian audio + Czech subtitles).
CASES = [
    ("Now.You.See.Me.Now.You.Dont.2025.Cz.Sub.mkv", [], ["cs"]),
    ("Now You See Me Now You Dont (2025) CZ titulky.mp4", [], ["cs"]),
    ("Now You See Me Now You Dont (2025) CZEN dabing.mp4", ["cs", "en"], []),
    ("Podfukári 3 - Now You See Me Now You Dont (2025) EN, SK+CZ sub.mkv", ["en"], ["sk", "cs"]),
    ("Podfukáři 3 - Now you see me now you don't (2025) EN+CZ DABING UHDRDV.mkv", ["en", "cs"], []),
    ("Podfukari 3 (2025) CZdab FHD.mkv", ["cs"], []),
    ("Podfukari.3.2025.HD1080.dab.CZ.mkv", ["cs"], []),
    ("Now.You.See.Me.Now.You.Dont.2025.2160p.iT.WEB-DL.Hybrid.DV.HDR10.CZtit.mkv", [], ["cs"]),
    ("Now You See Me Now You Dont 2025 BluRay 1080p DD 5 1 x264-BHDStudio.mp4", [], []),
    ("Now.You.See.Me.Now.You.Don't.2025.CZ-ENG.WEBDL.1080p.x264.mkv", ["cs", "en"], []),
    ("Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.CZ.DD5.1.EN.7.1..mkv", ["cs", "en"], []),
    ("Pelíšky (1999) titulky EN.avi", [], ["en"]),
    ("3:10 To Yuma (2007) UHD_BDRemux audio_CZ-EN tit_ CZ-EN titulky.mkv", ["cs", "en"], ["cs", "en"]),
    ("Rubber.2010.1080p.BluRay.x264.VPPV.mkv", [], []),
]


@pytest.mark.parametrize("name, audio, subs", CASES)
def test_parse_languages(name, audio, subs):
    assert parse_languages(name) == {"audio": audio, "subtitles": subs}


def test_normalize_language_czech_names_and_codes():
    assert [normalize_language(x) for x in ["česky", "anglicky", "ces", "lit", "CZE", "slovensky", ""]] == \
        ["cs", "en", "cs", "lt", "cs", "sk", ""]


FS_PAGE = """<div class="vf-wrap"><div class="vf-main"><div class="vf-row"><div class="vf-meta">
<div class="vf-resdur"> Rozlišení: 1920x800<br> Délka: 01:52:56<br> <b>Audio stopy:</b> lit<br><b>Titulky:</b> česky<br> </div>
<div class="vf-hd"><div class="icon-hd">Full HD</div></div> </div> <div class="vf-tech"> <div class="vf-tech-col">
<b>Video:</b><br>Kodek: H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10<br>Bitrate: 2636 kbps<br>Počet snímků za vteřinu: 24<br> </div>
<div class="vf-tech-col"> <b>Audio:</b><br>Kodek: AAC (Advanced Audio Coding)<br>Počet kanálů: 6<br>Layout: 5.1<br> </div>
</div></div></div><div class="vf-side">"""


def test_fastshare_page_parser_gives_real_tracks():
    from app.clients.fastshare import parse_file_page

    d = parse_file_page(FS_PAGE)
    # the name said "Cz.Sub" — the page tells it is Lithuanian audio with Czech subtitles
    assert d["audio"] == [{"lang": "lt", "codec": "AAC (Advanced Audio Coding)", "channels": 6}]
    assert d["subtitles"] == ["cs"]
    assert (d["width"], d["height"], d["duration_s"], d["bitrate"]) == (1920, 800, 6776, 2636000)
    assert d["video_codec"] == "H.264"
    assert parse_file_page("<html>no details</html>") is None


def test_fastshare_page_slug():
    from app.clients.fastshare import page_slug

    assert page_slug("Matrix 1 (1999).mkv") == "matrix-1-1999-.mkv"
    assert page_slug("Now.You.See.Me.Now.You.Don't.2025.mkv") == "now.you.see.me.now.you.don-t.2025.mkv"
    assert page_slug("Pelíšky.mkv") == "pelisky.mkv"
