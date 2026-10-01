"""MediaInfo pasted on a tracker's detail page."""

from app.core import mediainfo_text

PAGE = """<div>General<br>Format : Matroska<br>Duration : 2 h 45 min<br>Overall bit rate : 6 364 kb/s<br><br>
Video #1<br>Format : HEVC<br>HDR format : SMPTE ST 2086, HDR10 compatible<br>Bit rate : 4 049 kb/s<br>
Width : 1 920 pixels<br>Height : 804&nbsp;pixels<br><br>Video #2<br>Format : V_MJPEG<br>Width : 853 pixels<br><br>
Audio #1<br>Format : E-AC-3<br>Commercial name : Dolby Digital Plus<br>Channel(s) : 6 channels<br>Title : Slovensky<br>Language : Slovak<br><br>
Audio #2<br>Format : E-AC-3<br>Channel(s) : 6 channels<br>Language : Czech<br><br>
Audio #3<br>Format : DTS<br>Channel(s) : 8 channels<br>Language : English<br><br>
Text #1<br>Format : UTF-8<br>Language : Czech<br></div><p>ČSFD 87%</p>"""


def test_mediainfo_from_a_detail_page():
    d = mediainfo_text.parse(mediainfo_text.page_text(PAGE))
    assert d["duration_s"] == 2 * 3600 + 45 * 60
    assert (d["width"], d["height"], d["video_codec"], d["hdr"], d["bitrate"]) == (1920, 804, "HEVC", "HDR10", 4_049_000)
    assert [a["lang"] for a in d["audio"]] == ["sk", "cs", "en"]
    assert d["audio"][0]["codec"] == "Dolby Digital Plus" and d["audio"][2]["channels"] == 8
    assert d["subtitles"] == ["cs"]


def test_a_page_without_mediainfo():
    assert mediainfo_text.parse(mediainfo_text.page_text("<p>ČSFD: 26 MINUT</p>")) is None


CZECH = """Jazyk: CZ, SK, RUS<br>Titulky: Ne<br>- Mediainfo<br>Morozko.mkv<br>Formát : Matroska<br>Duration : 1 h 22 min<br>
Celkový BitRate : 15,3 Mb/s<br><br>Video<br>ID : 1<br>Formát : AVC<br>Bit rate : 12,9 Mb/s<br>Šířka : 1 480 pixelů<br>
Výška : 1 080 pixelů<br>Jazyk : Russian<br><br>Audio #1<br>Formát : AC-3<br>Commercial name : Dolby Digital<br>
Kanál(y) : 6 kanálů<br>Titul : AC3-5.1 DD / DVD<br>Jazyk : Czech<br><br>Audio #2<br>Formát : AC-3<br>Jazyk : Slovak<br>"""


def test_mediainfo_in_czech_without_a_general_heading():
    d = mediainfo_text.parse(mediainfo_text.page_text(CZECH))
    assert d["duration_s"] == 82 * 60
    assert (d["width"], d["height"], d["video_codec"], d["bitrate"]) == (1480, 1080, "AVC", 12_900_000)
    assert [a["lang"] for a in d["audio"]] == ["cs", "sk"] and d["audio"][0]["channels"] == 6


def test_languages_from_the_uploaders_template_when_tracks_have_none():
    page = "Jazyk: CZ, EN<br>Video<br>Format : HEVC<br>Audio #1<br>Format : AC-3<br>Audio #2<br>Format : DTS<br>"
    assert [a["lang"] for a in mediainfo_text.parse(mediainfo_text.page_text(page))["audio"]] == ["cs", "en"]
