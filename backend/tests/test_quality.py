"""Quality model, film match and recommended order on real search results (Dune 2021, Samotáři 2000),
captured with verified details from WebShare/FastShare (tests/fixtures_search_real.json)."""

import json
from pathlib import Path

import pytest

from app.core.film_match import judge
from app.core.quality import Prefs, facts_from_media, facts_from_name, score
from app.modules.search.evaluate import MovieContext, evaluate, recommended_key

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
