"""TV show automation: which episodes it looks for, which file it takes, the nightly check."""

import pytest

from app.core import events, registry
from app.core.profiles import Profile
from app.db import init_db
from app.modules.series import auto, store


def season(n, states, specials=False):
    return {"season_number": n, "specials": specials,
            "episodes": [{"episode": i + 1, "state": st} for i, st in enumerate(states)]}


SEASONS = [season(1, ["owned", "temp", "owned"]), season(2, ["owned", "missing", "missing", "upcoming"]),
           season(0, ["missing", "missing"], specials=True)]


def test_new_episodes_from_the_last_owned_one_or_every_missing_one():
    eff = {"auto_new": "notify", "auto_from": "next", "auto_dub": "off", "lang_mode": "local_or_temp"}
    assert auto.wanted_episodes(SEASONS, eff) == [(2, 2, "new"), (2, 3, "new")]
    gap = [season(1, ["missing", "owned", "missing"])]
    assert auto.wanted_episodes(gap, eff) == [(1, 3, "new")]                    # the old gap is not "new"
    assert auto.wanted_episodes(gap, {**eff, "auto_from": "all"}) == [(1, 1, "new"), (1, 3, "new")]
    assert auto.wanted_episodes([season(1, ["missing"])], eff) == []           # nothing owned: nothing is "new"
    assert auto.wanted_episodes(SEASONS, {**eff, "auto_new": "off"}) == []     # specials never


def test_dub_only_for_english_episodes_and_the_mode_that_waits_for_it():
    eff = {"auto_new": "off", "auto_from": "next", "auto_dub": "download", "lang_mode": "local_or_temp"}
    assert auto.wanted_episodes(SEASONS, eff) == [(1, 2, "dub")]
    assert auto.wanted_episodes(SEASONS, {**eff, "lang_mode": "original"}) == []


def row(ident, tier=2, film="yes", score=50, pack=False, resolution="1080p"):
    return {"ident": ident, "source": "webshare", "source_id": 1, "name": f"{ident}.mkv", "lang_tier": tier, "film": film, "quality_score": score, "pack": pack,
            "resolution": resolution, "codec": "H.264", "hdr": "", "size": 10**9, "audio_langs": ["cs"]}


def test_pick_sure_allowed_local_file_of_the_best_release():
    profile = Profile(id=1, name="Seriály", kind="tv", min_resolution="720p")
    sets = [{"episodes": {3: row("en", tier=0, score=80)}},
            {"episodes": {3: row("cz-unsure", film="unsure")}},
            {"episodes": {3: row("cz-sd", resolution="480p")}},
            {"episodes": {3: row("cz-pack", pack=True)}},
            {"episodes": {3: row("cz", score=40)}}]
    assert auto.pick(sets, 3, profile, need_local=True)["ident"] == "cz"
    assert auto.pick(sets, 3, profile, need_local=False)["ident"] == "cz"      # Czech first even when not needed
    assert auto.pick(sets[:1], 3, profile, need_local=False)["ident"] == "en"
    assert auto.pick(sets[:1], 3, profile, need_local=True) is None
    assert auto.pick(sets, 3, profile, need_local=True, skip={"cz"}) is None   # the user dismissed it
    assert auto.pick(sets, 4, profile, need_local=False) is None


@pytest.fixture
async def db():
    await init_db(registry.discover())


async def test_check_downloads_or_keeps_what_it_found(db, monkeypatch):
    import sys
    router = sys.modules["app.modules.series.router"]

    detail = {"show": {"title": "Seriál", "year": 2020}, "in_library": True,
              "seasons": [season(1, ["owned", "temp", "missing"])]}

    async def fake_detail(tmdb_id, fresh=False):
        return detail

    class Offers:
        sets = [{"episodes": {2: row("dub2"), 3: row("new3", tier=0)}}]

    async def fake_search(tmdb_id, season, wanted, torrent):
        return Offers()

    requests = []

    async def on_request(payload):
        requests.append(payload)
        payload["started"] = {"gid": "x"}

    monkeypatch.setattr(router, "series_detail", fake_detail)
    monkeypatch.setattr(router, "search_season", fake_search)
    monkeypatch.setattr(events, "_handlers", {"download.request": [(0, "test", on_request)]})
    monkeypatch.setattr(auto, "_busy", lambda tmdb_id: _false())

    await store.save_settings(77, {"auto_new": "notify", "auto_dub": "download"}, {"title": "Seriál", "year": 2020})
    result = await auto.check_show(77)
    assert result == {"wanted": 2, "found": 1, "downloading": 1, "note": ""}
    assert [(r["library_action"]["episode"], r["library_action"]["replace"]) for r in requests] == [(2, True)]
    recs = {(r["episode"], r["kind"]): r["status"] for r in await auto.records(77)}
    assert recs == {(2, "dub"): "downloading", (3, "new"): "found"}

    # the user downloads what was found; the next check does not start the dub again
    assert (await auto.download_found(77, [(1, 3, "new")]))["started"] == 1
    requests.clear()
    result = await auto.check_show(77)
    assert result["downloading"] == 2 and not requests

    # the dub landed (owned in Czech now): its record goes away
    detail["seasons"] = [season(1, ["owned", "owned", "missing"])]
    await auto.check_show(77)
    assert {(r["episode"], r["kind"]) for r in await auto.records(77)} == {(3, "new")}

    await store.save_settings(77, {"auto_new": "off", "auto_dub": "off"})
    assert (await auto.check_show(77))["note"] == "automatika vypnutá" and not await auto.records(77)


async def _false():
    return False


def test_upgrade_only_episodes_below_the_profile_with_a_better_file():
    eff = {"auto_new": "off", "auto_from": "next", "auto_dub": "off", "auto_upgrade": "notify", "lang_mode": "local_or_temp"}
    seasons = [season(1, ["owned", "owned", "temp"])]
    assert auto.wanted_episodes(seasons, eff, {(1, 1): {}}) == [(1, 1, "upgrade")]
    assert auto.wanted_episodes(seasons, eff, {}) == []
    profile = Profile(id=1, name="Seriály", kind="tv", min_resolution="720p")
    from app.core.quality import Prefs
    prefs = Prefs(local_langs=("cs", "sk"))
    owned = {"quality_score": 30, "language": "CS", "file_size": 300_000_000}
    sets = [{"episodes": {1: row("en-better", tier=0, score=90)}}, {"episodes": {1: row("cz-worse", score=20)}},
            {"episodes": {1: row("cz-better", score=60)}}]
    assert auto.pick(sets, 1, profile, False, owned=owned, prefs=prefs)["ident"] == "cz-better"   # keeps Czech
    assert auto.pick(sets[:2], 1, profile, False, owned=owned, prefs=prefs) is None


async def test_below_profile_reads_the_files_media(db):
    from app.core.quality import Prefs
    from app.db import get_db
    db_ = await get_db()
    await db_.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, 1, 1, ?)",
                      ("/s/e1.avi", '{"width": 640, "height": 480, "video_codec": "XviD", "duration_s": 1300, "audio": []}'))
    await db_.execute("INSERT OR REPLACE INTO tv_media (file_path, size, mtime, media) VALUES (?, 1, 1, ?)",
                      ("/s/e2.mkv", '{"width": 1920, "height": 1080, "video_codec": "HEVC", "duration_s": 1300, "audio": []}'))
    await db_.commit()
    await db_.close()
    seasons = [{"season_number": 1, "episodes": [
        {"episode": 1, "state": "owned", "file": {"file_path": "/s/e1.avi", "filename": "e1.avi", "size": 200_000_000, "languages": ["cs"]}},
        {"episode": 2, "state": "owned", "file": {"file_path": "/s/e2.mkv", "filename": "e2.mkv", "size": 900_000_000, "languages": ["cs"]}},
        {"episode": 3, "state": "owned", "file": {"file_path": "/s/none.mkv", "filename": "x", "size": 1, "languages": []}}]}]
    got = await auto.below_profile(seasons, Profile(id=1, name="S", kind="tv", min_resolution="720p"), Prefs(local_langs=("cs",)))
    assert list(got) == [(1, 1)] and got[(1, 1)]["language"] == "CS"


def test_upgrade_of_an_episode_of_unknown_sound_wants_czech():
    eff = {"auto_new": "off", "auto_from": "next", "auto_dub": "off", "auto_upgrade": "notify", "lang_mode": "local_or_temp"}
    assert auto.wanted_episodes([season(1, ["unknown"])], eff, {(1, 1): {"language": "?"}}) == [(1, 1, "upgrade")]


async def test_quality_overview_of_a_shows_episodes(db, monkeypatch):
    import sys
    from app.core.quality import Prefs
    router = sys.modules["app.modules.series.router"]

    async def profiles():
        return [Profile(id=7, name="TV", kind="tv", is_default=True, min_resolution="720p")]
    monkeypatch.setattr(router, "load_profiles", profiles)
    sd = '{"width": 640, "height": 480, "video_codec": "XviD", "duration_s": 1300, "audio": []}'
    hd = '{"width": 1920, "height": 1080, "video_codec": "HEVC", "duration_s": 1300, "audio": []}'
    episodes = [(5, "cs", "e1.avi", 200_000_000, sd), (5, "cs", "e2.mkv", 900_000_000, hd), (5, "", "e3.mkv", 100, None)]
    q = (await router._quality(episodes, {}, {"profile_id": None}, Prefs(local_langs=("cs",))))[5]
    assert q["known"] == 2 and q["res"] == {"SD": 1, "1080p": 1} and q["below"] == 1
    assert q["size"] == 1_100_000_100 and q["min_score"] <= q["avg_score"]


def test_want_takes_a_pack_of_the_whole_show_only_when_it_fits():
    seasons = {1: 10, 2: 10, 3: 10, 4: 10, 5: 10}
    hd = Profile(id=7, name="HD", kind="tv", min_resolution="720p", max_size_gb=3)

    def pack(name, held, size_gb, tier=2, seeders=20, res="1080p"):
        return {"ident": name, "name": name, "seasons": held, "size": size_gb * 1e9, "seeders": seeders, "lang_tier": tier,
                "resolution": res, "codec": "H.264", "quality_score": 60, "film": "yes"}
    part = pack("S01-S02", [1, 2], 20)
    english = pack("Complete EN", [], 60, tier=0)
    huge = pack("Komplet REMUX", [], 600)                       # 12 GB an episode: over the profile
    good = pack("Komplet CZ", [1, 2, 3, 4], 40)                 # 4 of 5 seasons, 1 GB an episode
    dead = pack("Komplet CZ dead", [], 40, seeders=0)
    assert auto.choose_pack([part, english, huge, dead, good], seasons, hd, "local_or_temp", 45) is good
    assert auto.choose_pack([english], seasons, hd, "original", 45) is english          # English is fine then
    assert auto.choose_pack([part, english, huge], seasons, hd, "local_only", 45) is None
    assert auto.choose_pack([good], {}, hd, "local_or_temp") is None
