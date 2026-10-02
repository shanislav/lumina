"""The TV library scan with Plex's view: who decides which show a folder is, and the inventory."""

import os
from types import SimpleNamespace as S

import pytest

from app.core import events, registry
from app.db import get_db, init_db
from app.modules.library import importer, tv_inventory

SHOWS = {
    2290: ("Hvězdná brána: Atlantida", "Stargate Atlantis", "2004", {1: 20, 2: 20}),
    18123: ("Scooby Doo - Záhady s.r.o.", "Scooby-Doo! Mystery Incorporated", "2010", {1: 26}),
    1011: ("Scooby-Doo na stopě", "Scooby-Doo, Where Are You!", "1969", {1: 17}),
    127532: ("Solo Leveling", "Solo Leveling", "2024", {1: 25}),
}


class FakeTMDB:
    async def search_tv(self, title, language="cs-CZ"):
        t = title.lower()
        out = []
        if "atlantis" in t:
            out.append(2290)
        if "scooby" in t:
            out += [18123, 1011]
        if "solo" in t:
            out.append(127532)
        return [S(tmdb_id=i, title=SHOWS[i][0], original_title=SHOWS[i][1], year=SHOWS[i][2]) for i in out]

    async def get_tv_details(self, tmdb_id, language="cs-CZ"):
        title, orig, year, seasons = SHOWS[tmdb_id]
        return {"title": title, "original_title": orig, "first_air_date": f"{year}-01-01", "poster_url": None, "overview": "",
                "total_seasons": len(seasons), "total_episodes": sum(seasons.values()),
                "seasons": [{"season_number": n} for n in seasons]}

    async def get_season(self, tmdb_id, n):
        return [{"episode_number": e, "name": f"Epizoda {e}", "air_date": "2005-01-01"} for e in range(1, SHOWS[tmdb_id][3][n] + 1)]


@pytest.fixture
async def tv(tmp_path):
    await init_db(registry.discover())
    root = tmp_path / "Serials"
    files = ["StarGate Atlantis/1/Stargate.Atlantis.S01E03.720p.Cz.mkv",
             "StarGate Atlantis/2. HD/SGA - S02E02-The Intruder.CZ.720p.mkv",
             "Scooby Doo/1x01.Střez se bestie.avi",
             "Solo Leveling/Solo Leveling S02/S02E01.mp4",
             "Solo Leveling/Solo Leveling S01/Solo Leveling S01E01.mp4",
             "Divné/bez cisla.avi",
             "StarGate Atlantis/Other/Cast Reunion (part 1).mkv",
             "StarGate Atlantis/Bonus/Making of.mkv"]
    for f in files:
        path = root / f
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    plex = {
        str(root / "StarGate Atlantis/1/Stargate.Atlantis.S01E03.720p.Cz.mkv"): {"tmdb_id": 2290, "title": "Hvězdná brána: Atlantida", "season": 1, "episode": 3},
        str(root / "StarGate Atlantis/2. HD/SGA - S02E02-The Intruder.CZ.720p.mkv"): {"tmdb_id": 2290, "title": "Hvězdná brána: Atlantida", "season": 2, "episode": 2},
        str(root / "Scooby Doo/1x01.Střez se bestie.avi"): {"tmdb_id": 1011, "title": "Scooby-Doo na stopě", "season": 1, "episode": 1},
        str(root / "Solo Leveling/Solo Leveling S02/S02E01.mp4"): {"tmdb_id": 127532, "title": "Solo Leveling", "season": 2, "episode": 1},
    }
    plex = {os.path.normpath(k): v for k, v in plex.items()}

    async def hints(payload):
        for p in payload["paths"]:
            if os.path.normpath(p) in plex:
                payload["hints"][p] = plex[os.path.normpath(p)]
    events.subscribe("library.collect_tv_hints", hints)
    yield str(root)
    events._handlers.pop("library.collect_tv_hints", None)


async def scan(root):
    db = await get_db()
    try:
        await importer._scan_tv(FakeTMDB(), db, root, {"shows_found": 0, "episodes_matched": 0})
        return await tv_inventory.summary(db)
    finally:
        await db.close()


async def test_plex_decides_and_disagreements_show(tv):
    inv = await scan(tv)
    by = {f["folder"]: f for f in inv["folders"]}
    assert by["StarGate Atlantis"]["tmdb_id"] == 2290 and by["StarGate Atlantis"]["counts"] == {"ok": 2, "extra": 2}
    assert not by["StarGate Atlantis"]["problems"]                                               # bonuses are no problem
    scooby = by["Scooby Doo"]
    assert scooby["tmdb_id"] == 1011 and scooby["source"] == "plex" and scooby["disagree"]       # Lumina: 18123
    assert scooby["problems"][0]["status"] == "show"
    solo = by["Solo Leveling"]
    assert {p["status"] for p in solo["problems"]} == {"not_in_tmdb", "not_in_plex"}           # S02E01 / S01E01
    assert by["Divné"]["counts"] == {"unknown": 1}
    assert inv["folders"][0]["folder"] in ("Scooby Doo", "Divné")                                # problems first


async def test_users_fix_wins(tv):
    db = await get_db()
    try:
        await tv_inventory.set_override(db, "Scooby Doo", 18123)
    finally:
        await db.close()
    inv = await scan(tv)
    scooby = next(f for f in inv["folders"] if f["folder"] == "Scooby Doo")
    assert scooby["tmdb_id"] == 18123 and scooby["source"] == "user" and scooby["counts"] == {"ok": 1}
