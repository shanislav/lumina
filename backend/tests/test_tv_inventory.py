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
    46260: ("Naruto", "NARUTO -ナルト-", "2002", {1: 52, 2: 52, 3: 54, 4: 62}),
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
        if "naruto" in t:
            out.append(46260)
        return [S(tmdb_id=i, title=SHOWS[i][0], original_title=SHOWS[i][1], year=SHOWS[i][2]) for i in out]

    async def get_tv_details(self, tmdb_id, language="cs-CZ"):
        title, orig, year, seasons = SHOWS[tmdb_id]
        return {"title": title, "original_title": orig, "first_air_date": f"{year}-01-01", "poster_url": None, "overview": "",
                "total_seasons": len(seasons), "total_episodes": sum(seasons.values()),
                "seasons": [{"season_number": n} for n in seasons]}

    through = False     # TMDB numbers the episodes on through the seasons (Naruto S02 = E53–E104)

    async def get_season(self, tmdb_id, n):
        seasons = SHOWS[tmdb_id][3]
        first = sum(seasons[s] for s in seasons if s < n) if self.through else 0
        return [{"episode_number": first + e, "name": f"Epizoda {e}", "air_date": "2005-01-01"} for e in range(1, seasons[n] + 1)]


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


async def scan(root, tmdb=None):
    db = await get_db()
    try:
        await importer._scan_tv(tmdb or FakeTMDB(), db, root, {"shows_found": 0, "episodes_matched": 0})
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


@pytest.mark.parametrize("through", [False, True])
async def test_anime_absolute_numbers(tv, through):
    import pathlib
    root = pathlib.Path(tv)
    names = [f"Naruto/Naruto CZ dabing 1-20/Naruto_CZ_{n:03d}-01x{n:02d}.avi" for n in range(1, 11)]
    for f in names + ["Naruto/Naruto CZ dabing 20-40/Naruto_CZ_040-02x14.avi", "Naruto/Naruto CZ dabing 104-120/Naruto 104.mp4",
              "Naruto/6  129-153 cz tit/[CNT]_Naruto_130_[B4A3C9AA].mkv", "Naruto/Naruto CZ dabing 120-135/Naruto 135-cz-dabing.avi"]:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"x")
    tmdb = FakeTMDB()
    tmdb.through = through
    await scan(tv, tmdb)
    import sqlite3
    from app.db import DB_PATH
    with sqlite3.connect(DB_PATH) as conn:
        got = sorted(conn.execute("SELECT season, episode FROM library_episodes WHERE show_tmdb_id = 46260 AND has_file = 1").fetchall())
    # the whole show by its absolute numbers (TMDB: 52 + 52 + 54 + 62): 1–10 → S01; 040 (named 02x14) → S01E40;
    # 104 → S02E52; 130 → S03E26; 135 → S03E31 — or with TMDB's through numbering S02E104, S03E130, S03E135
    if through:
        assert got == [(1, n) for n in range(1, 11)] + [(1, 40), (2, 104), (3, 130), (3, 135)]
    else:
        assert got == [(1, n) for n in range(1, 11)] + [(1, 40), (2, 52), (3, 26), (3, 31)]


def test_names_of_other_parts_are_other_episodes():
    assert not tv_inventory.same_episode("Heart of Archness Part I", "Heart of Archness - Part II", strict=True)
    assert tv_inventory.same_episode("Heart of Archness: Part II", "Heart of Archness - Part II", strict=True)
    assert tv_inventory.same_episode("Stockholmský syndrom", "Proměnlivá konstanta / Stockholmský syndrom", strict=True)


def test_bare_number_with_a_name_is_found_by_the_name():
    titles = {(1, 37): "Dittův tajemný dům", (2, 36): "Dávný protivník", (1, 67): "Protivníci"}
    assert importer._episode_by_title(importer._bare_title("37.Davný protivník.avi"), titles) == (2, 36)
    assert importer._episode_by_title(importer._bare_title("Naruto 104.mp4"), titles) is None


def test_czech_parts():
    assert tv_inventory._part("Zkažený žaludek 1část") == 1 and tv_inventory._part("Zkažený žaludek, část druhá") == 2
    assert tv_inventory.title_score("Zkažený žaludek 2část", "Zkažený žaludek, část první") == 0
    assert tv_inventory.title_score("Zkažený žaludek 2část", "Zkažený žaludek, část druhá") == 1


def test_one_shared_word_is_no_match():
    """Kutil Tim S04E05 "V očích to není" is not TMDB's E05 "Není tak zlý, je nezodpovědný" (only "není")."""
    assert tv_inventory.title_score("V očích to není", "Není tak zlý, je nezodpovědný") < tv_inventory.STRONG
    assert importer._bare_title("02.Panika v Oblázkovém městě.avi") == "Panika v Oblázkovém městě"
