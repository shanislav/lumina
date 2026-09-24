"""core/offers: verifying offers, the upgrade rule, and the library's background upgrade check."""

import json
import sqlite3

import pytest

from app.core import registry
from app.core.offers import search as offers_search
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.offers.search import Offers, upgrade_block, verify_offers
from app.core.quality import Prefs
from app.db import DB_PATH, init_db
from app.modules.library import upgrades

CTX = MovieContext(titles=["Matrix", "The Matrix"], year=1999, runtime=136)
PREFS = Prefs()


def row(name, size, source="webshare", ident=None):
    return {"ident": ident or name, "name": name, "size": size, "source": source, "source_id": 1 if source == "webshare" else 2,
            **evaluate(name, size, CTX, PREFS)}


GOOD_DETAILS = {"duration_s": 8160, "width": 1920, "height": 1080, "video_codec": "HEVC", "bitrate": 12_000_000,
                "audio": [{"lang": "cs", "codec": "AC3", "channels": 6}], "subtitles": []}


async def test_verify_skips_junk_and_asks_each_file_once(monkeypatch):
    asked = []

    async def fake_details(files):
        asked.extend(f["ident"] for f in files)
        # WebShare copy gives nothing → the FastShare copy of the same file is asked next
        return {f"{f['source_id']}:{f['ident']}": (None if f["ident"] == "ws-copy" else GOOD_DETAILS) for f in files}

    monkeypatch.setattr(offers_search, "get_details", fake_details)
    offers = Offers(CTX, PREFS, [
        row("The.Matrix.1999.1080p.CZ.mkv", 5_000_000_000, "webshare", "ws-copy"),
        row("The.Matrix.1999.1080p.CZ.mkv", 5_000_000_000, "fastshare", "fs-copy"),
        row("Matrix.Reloaded.2003.mkv", 4_000_000_000, "webshare", "junk"),       # another year → no
    ])
    await verify_offers(offers)
    assert asked == ["ws-copy", "fs-copy"]
    fs = next(r for r in offers.rows if r["ident"] == "fs-copy")
    assert fs["verified"] and fs["codec"] == "H.265"


def test_upgrade_rule():
    owned = {"quality_score": 50, "language": "CS", "file_size": 1}
    better_cz = {"film": "yes", "size": 2, "quality_score": 51, "lang_tier": 3}
    assert upgrade_block(better_cz, owned, PREFS) is None                      # any higher score is enough
    assert upgrade_block({**better_cz, "quality_score": 50}, owned, PREFS) == "quality"
    assert upgrade_block({**better_cz, "lang_tier": 0}, owned, PREFS) == "language"   # would lose CZ audio
    assert upgrade_block({**better_cz, "lang_tier": 0}, {**owned, "language": "EN"}, PREFS) is None
    assert upgrade_block({**better_cz, "size": 1}, owned, PREFS) == "same_file"
    assert upgrade_block({**better_cz, "film": "no"}, owned, PREFS) == "film"


@pytest.fixture
async def library_movie():
    await init_db(registry.discover())
    media = {"duration_s": 8160, "width": 1280, "height": 544, "video_codec": "AVC", "bitrate": 2_000_000,
             "audio": [{"lang": "cs", "codec": "AC3", "channels": 2}]}
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('tmdb_api_key', 'x')")
        conn.execute("INSERT INTO library_movies (tmdb_id, title, original_title, year, filename, file_path, file_size, "
                     "language, status, media) VALUES (603, 'Matrix', 'The Matrix', '1999', 'm.mkv', '/m.mkv', 2000000000, "
                     "'CS', 'matched', ?)", (json.dumps(media),))


async def test_check_movie_stores_the_best_upgrade(library_movie, monkeypatch):
    async def fake_find(cfg, query, **kw):
        assert kw["tmdb_id"] == 603
        rows = [row("The.Matrix.1999.2160p.HDR.CZ.mkv", 30_000_000_000, "webshare", "4k"),
                row("The.Matrix.1999.720p.EN.mkv", 1_000_000_000, "webshare", "worse")]
        return Offers(CTX, PREFS, rows)

    async def no_verify(offers, limit=10):
        return None

    monkeypatch.setattr(upgrades, "find_offers", fake_find)
    monkeypatch.setattr(upgrades, "verify_offers", no_verify)
    result = await upgrades.check_movie(603)
    assert result == {"status": "better", "upgrades": 1}
    stored = (await upgrades.results())["603"]
    assert stored["status"] == "better" and stored["best"]["ident"] == "4k"
