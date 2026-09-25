"""Quality model, film match and recommended order on real search results (Dune 2021, Samotáři 2000),
captured with verified details from WebShare/FastShare (tests/fixtures_search_real.json)."""

import json
from pathlib import Path

import pytest

from app.core.film_match import judge
from app.core.quality import Prefs, facts_from_media, facts_from_name, score
from app.core.offers.evaluate import MovieContext, evaluate, recommended_key

DATA = json.loads((Path(__file__).parent / "fixtures_search_real.json").read_text(encoding="utf-8"))
CTX = {
    "dune": MovieContext(["Duna", "Dune", "Dune: Part One"], 2021, 155),
    "samotari": MovieContext(["Samotáři", "Loners"], 2000, 107),
}


def ranked(label, prefs=None):
    prefs = prefs or Prefs()
    blob = DATA[label]
    rows = []
    for f in blob["files"]:
        d = blob["details"].get(f"{f['source_id']}:{f['ident']}")
        rows.append({"name": f["name"], "size": f["size"], **evaluate(f["name"], f["size"], CTX[label], prefs, d)})
    return sorted(rows, key=lambda r: recommended_key(r, prefs))


def test_dune_best_czech_version_first_cut_file_last():
    rows = ranked("dune")
    assert rows[0]["name"] == "Duna - Part One CZ (4KHDR).mkv"          # 4K, has a CS track (8th of 10)
    assert "Dune-Part.One.2021.(1080p.BluRay" in rows[1]["name"]        # 1080p BluRay 15.8 Mb/s, EN+CS
    cut = next(r for r in rows if r["name"] == "Duna, Dune, 2021, SK.avi")
    assert cut["film"] == "length"                                      # 141 min instead of 155
    # English-only 4K REMUX: best picture, but no Czech/Slovak audio → after all local-audio files
    remux = next(i for i, r in enumerate(rows) if "WiLDCAT" in r["name"])
    assert all(r["lang_tier"] >= 2 for r in rows[:remux] if r["film"] == "yes")


def test_starved_1080p_scores_below_good_720p_and_upscale_is_penalised():
    starved = score(facts_from_media({"width": 1920, "height": 800, "video_codec": "AVC", "bitrate": 1_500_000}))
    good720 = score(facts_from_media({"width": 1280, "height": 720, "video_codec": "AVC", "bitrate": 5_000_000}))
    assert starved.score < good720.score
    upscale = score(facts_from_media({"width": 3840, "height": 2160, "video_codec": "HEVC", "bitrate": 4_500_000},
                                     "Samotáři.2000.UP.AI.CZ.mkv"))
    real_1080 = score(facts_from_media({"width": 1920, "height": 1080, "video_codec": "HEVC", "bitrate": 9_300_000}))
    assert upscale.score < real_1080.score
    assert ("upscale do 4K", -15) in upscale.parts


def test_h265_needs_half_the_bitrate():
    h264 = score(facts_from_media({"width": 1920, "height": 1080, "video_codec": "AVC", "bitrate": 8_000_000}))
    h265 = score(facts_from_media({"width": 1920, "height": 1080, "video_codec": "HEVC", "bitrate": 4_000_000}))
    assert abs(h264.score - h265.score) <= 3                              # same picture (+3 for the codec)
    assert h265.summary.startswith("1080p · H.265 · 4.0 Mb/s")


def test_samotari_upscaled_4k_below_real_1080p():
    rows = ranked("samotari")
    first_upscale = next(i for i, r in enumerate(rows) if "UP" in r["name"] and r["resolution"] == "2160p")
    first_real_1080 = next(i for i, r in enumerate(rows) if r["resolution"] == "1080p" and r["bitrate"] > 8e6)
    assert first_real_1080 < first_upscale


@pytest.mark.parametrize("name, status", [
    ("Duna - Dune (2021).mkv", "yes"),
    ("Dune.Part.One.2021.Hybrid.2160p.UHD.BluRay.REMUX.DV.mkv", "yes"),
    ("Dune Part Two 2024 2160p.mkv", "no"),            # other year
    ("Dune.Part.Two.mkv", "no"),                       # other part
    ("Dune - Part Two(2160p).mp4", "no"),
    ("Children of Dune E02.mkv", "no"),                # mini-series episode
    ("dune_part_one.mkv", "yes"),
    ("Duna 1 - Dune - Part One (2021).mkv", "yes"),
    ("Duna Cast druha.mkv", "no"),
    ("Dune Directors Cut.mkv", "unsure"),              # → AI
    ("Pelíšky.avi", "no"),                             # other name
    ("Duna Proroctví - Dune Prophecy S01E05 CZ DABING.mkv", "no"),   # TV episode
    ("Dune 1x03.mkv", "no"),
])
def test_film_match(name, status):
    assert judge(name, ["Duna", "Dune", "Dune: Part One"], 2021).status == status


def test_name_only_facts():
    f = facts_from_name("Duna - Part One CZ (4KHDR).mkv", 81_600_000_000)
    assert (f.resolution, f.hdr, f.audio_langs) == ("2160p", "HDR10", ["cs"])


def test_max_size_and_hdr_preferences():
    media = {"width": 3840, "height": 2160, "video_codec": "HEVC", "bitrate": 60_000_000, "hdr": "DV"}
    base = score(facts_from_media(media, size=80e9))
    limited = score(facts_from_media(media, size=80e9), Prefs(max_size_gb=30))
    no_hdr = score(facts_from_media(media, size=80e9), Prefs(hdr="avoid"))
    assert limited.score <= base.score - 25                             # −30, score is capped at 100
    assert no_hdr.score < base.score


def test_video_bitrate_leaves_out_audio():
    from app.core.quality import facts_from_media, video_bitrate
    # Samotáři on WebShare: 28.6 Mb/s overall, four DTS tracks (2× 5.1, 2× stereo) ≈ 4.6 Mb/s of sound
    four_dts = {"duration_s": 6543, "width": 1920, "height": 1080, "video_codec": "H264", "bitrate": 28589819,
                "audio": [{"lang": "cs", "codec": "DTS", "channels": 6}] * 2 + [{"lang": "cs", "codec": "DTS", "channels": 2}] * 2}
    assert 23.9e6 < video_bitrate(facts_from_media(four_dts, "Samotari.mkv")) < 24.1e6
    # unverified: one track per language in the name
    by_name = facts_from_name("Film.2020.1080p.CZ.SK.EN.mkv", size=4_500_000_000, duration_s=6000)
    assert video_bitrate(by_name) == by_name.bitrate - 3 * 384_000
    # a wrong guess never takes more than half
    tiny = facts_from_media({"duration_s": 6000, "width": 720, "height": 400, "bitrate": 1_000_000,
                             "audio": [{"lang": "en", "codec": "TrueHD", "channels": 8}]}, "x.mkv")
    assert video_bitrate(tiny) == 500_000


def test_user_weights_change_the_score():
    from app.core.quality import merge_weights, weights_from_setting
    media = {"duration_s": 6600, "width": 1920, "height": 1080, "video_codec": "HEVC", "bitrate": 3_400_000,
             "audio": [{"lang": "cs", "codec": "AC3", "channels": 6}]}
    f = facts_from_media(media, "Film.x265.mkv", size=2_800_000_000)
    default = score(f, Prefs()).score
    # H.265 counted as good as H.264 → lower bitrate equivalent → lower score
    assert score(f, Prefs(weights=merge_weights({"efficiency": {"H.265": 1.0}}))).score < default
    # 5.1 worth nothing
    assert score(f, Prefs(weights=merge_weights({"points": {"surround_51": 0}}))).score == default - 5
    # junk is ignored, defaults stay
    assert weights_from_setting('{"points": {"surround_51": "x", "nope": 1}, "bad": 2}') == merge_weights(None)
    assert weights_from_setting("not json") == merge_weights(None)


SWAT = ["S.W.A.T.", "S.W.A.T. – Jednotka rychlého nasazení", "SWAT"]
SWAT_PEOPLE = ["Samuel L. Jackson", "Colin Farrell", "Jeremy Renner", "Clark Johnson"]
SWAT_OTHERS = ["S.W.A.T.: Firefight", "S.W.A.T. - Pod palbou", "S.W.A.T.: Under Siege", "S.W.A.T. Obležení"]


@pytest.mark.parametrize("name, status", [
    ("S.W.A.T.-Jednotka rychlého nasazení (Samuel L. Jackson,Colin Farrell,Jeremy Renner).avi", "yes"),
    ("S.W.A.T. Pod palbou - S.W.A.T. Firefight CZDAB.avi", "no"),
    ("S.W.A.T. Obležení CZ TIT°.mp4", "no"),
    ("S.W.A.T. (2003) CZ EN 1080p.mkv", "yes"),
])
def test_film_match_people_and_series(name, status):
    assert judge(name, SWAT, 2003, people=SWAT_PEOPLE, other_parts=SWAT_OTHERS).status == status


def test_bitrate_from_the_film_runtime_when_the_source_knows_no_duration():
    ctx = MovieContext(titles=["Matrix"], year=1999, runtime=136)
    empty_ws_info = {"duration_s": 0, "width": 0, "height": 0, "video_codec": "", "bitrate": 0, "audio": [], "subtitles": []}
    ev = evaluate("Matrix.1999.1080p.mkv", 8_160_000_000, ctx, Prefs(), empty_ws_info)
    assert ev["bitrate"] == 8_000_000 and "~8.0 Mb/s" in ev["quality_summary"]
    assert not any("neznámý" in label for label, _ in ev["quality_parts"])
    assert ev["film"] == "yes"   # no length verdict from an estimate
