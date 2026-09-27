"""File names with broken Unicode must not break the search (JSON encoding)."""

import json

from app.core.text import clean_text


def test_lone_surrogate_from_a_sources_json():
    lone = json.loads('"Matrix \\ud83d 1999.mkv"')   # a source's JSON with half an emoji
    assert json.dumps(clean_text(lone), ensure_ascii=False).encode("utf-8")


def test_surrogate_pair_becomes_one_character():
    pair = "Matrix " + chr(0xD83D) + chr(0xDE00) + " 1999.mkv"
    assert clean_text(pair) == "Matrix \U0001F600 1999.mkv"
