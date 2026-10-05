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


async def test_cancel_all_forgets_the_found_and_switches_the_automation_off(db, monkeypatch):
    import json as _json
    import sys
    from app.db import get_db
    cancelled = []

    async def fake_cancel(did, backend):
        cancelled.append(did)
    monkeypatch.setattr(sys.modules["app.modules.downloads.router"], "cancel_tracked", fake_cancel)
    await store.save_settings(873, {"auto_new": "download", "auto_from": "all"})
    conn = await get_db()
    for ep, status in ((1, "found"), (2, "downloading"), (3, "dismissed")):
        await conn.execute("INSERT INTO series_auto (tmdb_id, season, episode, kind, status, row, updated_at) "
                           "VALUES (873, 1, ?, 'new', ?, '{}', datetime('now'))", (ep, status))
    await conn.execute("INSERT INTO download_queue (request, requested_by, created_at) VALUES (?, 'x', '')",
                       (_json.dumps({"tmdb_id": 873, "content_type": "tv"}),))
    await conn.execute("INSERT INTO download_queue (request, requested_by, created_at) VALUES (?, 'x', '')",
                       (_json.dumps({"tmdb_id": 603, "content_type": "movie"}),))
    await conn.execute("INSERT INTO download_tracker (id, tmdb_id, title, year, backend, status, target_dir, processed, "
                       "content_type) VALUES ('g1', 873, 'Columbo', 1971, 'aria2', 'active', '/x', 0, 'tv')")
    await conn.commit()
    await conn.close()
    got = await auto.cancel_all(873)
    assert got == {"dropped": 1, "cancelled": 1, "forgotten": 2, "stopped": True} and cancelled == ["g1"]
    assert (await store.get_settings(873))["own"]["auto_new"] == "off"
    assert [r["status"] for r in await auto.records(873)] == ["dismissed"]
    conn = await get_db()
    left = await (await conn.execute("SELECT request FROM download_queue")).fetchall()
    await conn.close()
    assert len(left) == 1 and "603" in left[0][0]


def test_one_uploader_for_the_whole_show_where_it_can_be():
    def st(key, coverage, local=True, score=50):
        return {"key": key, "coverage": coverage, "local": local, "score": score}
    seasons = {1: [st("A", 10), st("B", 10)],
               2: [st("B", 8, score=70), st("A", 8)],              # B a bit better here, A has it all too
               3: [st("A", 9), st("C", 9)],
               4: [st("C", 6, local=True), st("A", 6, local=False)]}  # A without the sound here: does not count
    assert auto.preferred_release(seasons) == "A"
    assert [x["key"] for x in auto.prefer(seasons[2], "A")] == ["A", "B"]
    assert auto.preferred_release({1: [st("A", 10)]}) is None              # one season: nothing to unify
    assert auto.prefer(seasons[2], None) == seasons[2]


async def test_overview_wants_the_season_packs_that_suit_and_the_rest_by_episodes(db, monkeypatch):
    import sys
    from app.modules.series import overview
    router = sys.modules["app.modules.series.router"]
    hd = Profile(id=7, name="HD", kind="tv", min_resolution="720p")
    sd_pack = {"ident": "p1", "name": "Show S01 SD", "size": 10e9, "seeders": 9, "lang_tier": 2, "resolution": "SD"}
    hd_pack = {"ident": "p2", "name": "Show S02 1080p CZ", "size": 10e9, "seeders": 9, "lang_tier": 2,
               "resolution": "1080p", "codec": "H.264"}
    en_pack = {**hd_pack, "ident": "p3", "lang_tier": 0}
    assert not overview.pack_summary(sd_pack, 10, hd, "local_or_temp")["fits"]
    assert overview.pack_summary(hd_pack, 10, hd, "local_or_temp")["fits"]
    assert not overview.pack_summary(en_pack, 10, hd, "local_only")["fits"]
    assert overview.pack_summary(en_pack, 10, hd, "original")["fits"]

    taken, queued = [], []

    async def fake_pack(tmdb_id, body):
        taken.append(body.row["ident"])
    monkeypatch.setattr(router, "pack_download", fake_pack)
    monkeypatch.setattr(auto, "enqueue", lambda ids: queued.extend(ids))
    rows = [{"season": 1, "aired": 10, "owned": 0, "packs": [overview.pack_summary(sd_pack, 10, hd, "local_or_temp")],
             "_pack_rows": [sd_pack]},
            {"season": 2, "aired": 10, "owned": 0, "packs": [overview.pack_summary(hd_pack, 10, hd, "local_or_temp")],
             "_pack_rows": [hd_pack]},
            {"season": 3, "aired": 10, "owned": 10, "packs": [overview.pack_summary(hd_pack, 10, hd, "local_or_temp")],
             "_pack_rows": [hd_pack]}]                           # owned already: nothing
    assert await overview.act({"tmdb_id": 55, "seasons": rows}) == {"packs": [2]}
    assert taken == ["p2"] and queued == [55]
    eff = (await store.get_settings(55))["effective"]
    assert eff["auto_new"] == "download" and eff["auto_from"] == "all"


def test_a_seasons_pack_holds_that_season_only():
    from app.modules.series.overview import only_season
    assert only_season("Columbo  3.  série (1973-1974)(CZ/EN)[1080p]", 3)
    assert not only_season("Columbo S01-S10 (1971-2003)(CZ)", 1)         # the whole show: not a season's pack
    assert not only_season("Show S03 1080p", 2)


def test_the_space_estimate():
    from app.modules.series.overview import estimate
    st = {"key": "A", "episode_size": 1_000_000_000}
    rows = [{"aired": 10, "owned": 0, "pick": "A", "sets": [st], "packs": [{"fits": True, "size": 20e9}]},
            {"aired": 10, "owned": 5, "pick": "A", "sets": [st], "packs": [{"fits": False, "size": 30e9}]},
            {"aired": 4, "owned": 4, "sets": [], "packs": []},
            {"aired": 6, "owned": 0, "sets": [], "packs": []}]
    got = estimate(rows, [])
    assert got == {"chci": 20e9 + 5e9, "way": "seasons", "episodes": 15e9, "unknown": 1}
    assert estimate(rows, [{"fits": True, "size": 90e9}])["chci"] == 90e9


async def test_a_cancelled_episode_is_not_downloading_any_more(db):
    """Cancelled in the downloads (or removed in qBittorrent): the record does not stay "downloading" for days —
    the file is dismissed, the next check looks again. A film of the same TMDB id is not touched."""
    from app.modules.downloads import queue
    registry.register_subscriptions(registry.discover())
    await auto._save(1408, 2, 5, "new", "downloading", {"ident": "a"})
    await auto._save(1408, 2, 6, "new", "downloading", {"ident": "b"})
    await events.emit("download.cancelled", queue.cancelled([
        {"tmdb_id": 1408, "content_type": "tv", "library_action": {"mode": "episode", "season": 2, "episode": 5}}]))
    got = {(r["season"], r["episode"]): r["status"] for r in await auto.records(1408)}
    assert got == {(2, 5): "dismissed", (2, 6): "downloading"}
    assert events.cancelled_films(queue.cancelled([{"tmdb_id": 1408, "content_type": "tv"}, {"tmdb_id": 603}])) == [603]
    assert events.cancelled_films({"tmdb_ids": [603]}) == [603]              # an older emitter
