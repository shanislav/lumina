"""Cinema recordings: the name's marks, the film's release dates, and what the search / profiles do with them."""

from datetime import date

from app.core import cinema
from app.core.offers.evaluate import MovieContext, evaluate
from app.core.profiles import Profile, block
from app.core.quality import Prefs

TODAY = date(2026, 10, 4)


def test_marks_of_the_name():
    assert cinema.judge("Duna 2 (2024) HDTS CZ.mkv", False)["cinema"] == "video"
    assert cinema.judge("Film.2025.CAM.x264.mkv", False)["cinema"] == "video"
    assert cinema.judge("Film.2025.TS.mp4", False)["cinema"] == "video"
    assert cinema.judge("Film.2025.1080p.mkv.ts", False)["cinema"] == ""          # an extension is no TS
    assert cinema.judge("Film 2025 1080p WEB-DL CZ dabing z kina.mkv", False) == \
        {"cinema": "audio", "langs": ["cs"], "reason": "zvuk nahraný v kině"}
    assert cinema.judge("Film 2025 1080p LiNE SK.mkv", False)["langs"] == ["sk"]
    # "kino" alone: an old film's cinema dub is fine; before the digital release it is a recording
    assert cinema.judge("Sloni 1990 CZ dabing kino.avi", False)["cinema"] == ""
    assert cinema.judge("Film 2025 CZ kino.avi", True)["cinema"] == "video"
    assert cinema.judge("Movie.2025.1080p.BluRay.x264.mkv", True)["cinema"] == "suspect"
    assert cinema.judge("Movie 2025 CZ dabing.mkv", True)["cinema"] == "likely"


def test_before_the_digital_release():
    dates = [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-08-20T00:00:00.000Z"},
                                                     {"type": 4, "release_date": "2026-11-10T00:00:00.000Z"}]},
             {"iso_3166_1": "CZ", "release_dates": [{"type": 3, "release_date": "2026-08-14T00:00:00.000Z"}]}]
    info = cinema.release_info(dates)
    assert info == {"theatrical": "2026-08-14", "digital": "2026-11-10", "local_theatrical": "2026-08-14", "local_digital": ""}
    assert cinema.before_digital(info, TODAY)
    assert not cinema.before_digital({"theatrical": "2026-08-14", "digital": "2026-09-30"}, TODAY)
    assert cinema.before_digital({"theatrical": "2026-08-14", "digital": ""}, TODAY)          # recent, no date
    assert not cinema.before_digital({"theatrical": "2025-01-10", "digital": ""}, TODAY)       # old: unknown ≠ none
    assert not cinema.before_digital({}, TODAY)


def test_search_hides_a_cinema_picture_and_no_profile_takes_a_recording():
    prefs = Prefs(local_langs=("cs", "sk"))
    ctx = MovieContext(titles=["Film"], year=2025, runtime=100)
    ev = evaluate("Film 2025 HDCAM CZ.mkv", 2 * 10**9, ctx, prefs)
    assert ev["film"] == "no" and ev["film_reasons"] == ["obraz z kina (CAM / TS)"]
    ev = evaluate("Film 2025 1080p WEB-DL CZ dabing z kina.mkv", 4 * 10**9, ctx, prefs)
    assert ev["cinema"] == "audio" and "cs" not in ev["audio_langs"] and ev["cinema_langs"] == ["cs"]
    assert ev["lang_tier"] < 2                                   # a recording is no dub
    assert block(ev, Profile(audio_langs=["cs", "sk"])) is not None
    pre = MovieContext(titles=["Film"], year=2025, runtime=100, pre_digital=True)
    ev = evaluate("Film 2025 1080p WEB-DL CZ dabing.mkv", 4 * 10**9, pre, prefs)
    assert ev["cinema"] == "suspect" and block(ev, Profile()) == "z kina"


async def test_wanted_waits_for_the_digital_release(monkeypatch):
    from app.core import registry
    from app.db import get_db, init_db
    from app.modules.wanted import store

    await init_db(registry.discover())
    db = await get_db()
    cur = await db.execute("INSERT INTO wanted (tmdb_id, title, year, added_at) VALUES (77, 'Film', '2026', 'x')")
    wid = cur.lastrowid
    await db.commit()
    await db.close()

    class Tmdb:
        def __init__(self, key):
            pass

        async def get_movie_full(self, tmdb_id):
            return {"releases": {"theatrical": date.today().isoformat(), "digital": "2099-11-10"}}

        async def close(self):
            pass


    monkeypatch.setattr(store, "TMDBClient", Tmdb)
    async def noop_search(*a, **kw):
        return None
    monkeypatch.setattr(store, "find_offers", noop_search)
    out = await store.check(wid)
    assert out["waiting"] == "čeká na digitální vydání (10. 11. 2099)"
    assert (await store.get(wid))["waiting"] == out["waiting"]


def test_a_fresh_dub_is_a_cinema_one():
    """The Odyssey 2026: digital 15. 11. (its September "AMZN WEB-DL" files were fakes over a cinema recording);
    its CZ dub only in Czech cinemas (premiere 16. 7.) — a "1080p CZ dabing" file has the sound recorded there."""
    info = cinema.release_info([
        {"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-07-17"}, {"type": 4, "release_date": "2026-11-17"}]},
        {"iso_3166_1": "CZ", "release_dates": [{"type": 3, "release_date": "2026-07-16"}]}])
    assert info["local_theatrical"] == "2026-07-16" and info["local_digital"] == ""
    assert cinema.before_local_digital(info, TODAY)                      # 80 days after the CZ premiere
    prefs = Prefs(local_langs=("cs", "sk"))
    ctx = MovieContext(titles=["Odyssea", "The Odyssey"], year=2026, runtime=170, pre_local=True)
    ev = evaluate("Odysea 2026 0DYSSEA 1080p CZ dabing.mkv", 4 * 10**9, ctx, prefs)
    assert ev["cinema"] == "audio" and ev["lang_tier"] < 2 and "cs" not in ev["audio_langs"]
    ev = evaluate("The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-Kitsune.mkv", 6 * 10**9, ctx, prefs)
    assert ev["cinema"] == "" and ev["film"] == "yes"
    ev = evaluate("Odyssea The Odyssey 2026 1080p CZ titulky.mkv", 4 * 10**9, ctx, prefs)
    assert ev["cinema"] == ""                                             # Czech subtitles are fine


async def test_files_from_before_the_release_stay_recordings():
    """The Odyssey's September "AMZN WEB-DL" fakes: after 15. 11. they are still recordings — remembered by
    their file (and size: a re-upload), a torrent by its publication date."""
    from app.core import registry
    from app.db import init_db
    await init_db(registry.discover())
    fake = {"source_id": 1, "ident": "ws1", "size": 6_000_000_000, "source": "webshare", "film": "yes",
            "name": "The.Odyssey.2026.1080p.AMZN.WEB-DL-Kitsune.mkv"}
    junk = {"source_id": 1, "ident": "ws2", "size": 5, "source": "webshare", "film": "no", "name": "Odyssea 2014.avi"}
    await cinema.remember(1368337, [fake, junk])
    keys, sizes = await cinema.remembered(1368337)
    assert keys == ["1:ws1"] and sizes == [6_000_000_000]
    assert await cinema.last_remembered(1368337)
    ev = {"cinema": "", "film": "yes"}
    assert cinema.mark_before_release(ev, fake, set(keys), set(sizes), "2026-11-15")["cinema"] == "likely"
    reupload = {**fake, "source": "fastshare", "source_id": 2, "ident": "fs9"}
    assert cinema.mark_before_release(ev, reupload, set(keys), set(sizes), "2026-11-15")["cinema"] == "likely"
    new = {**fake, "ident": "ws3", "size": 7_000_000_000}
    assert cinema.mark_before_release(ev, new, set(keys), set(sizes), "2026-11-15")["cinema"] == ""
    torrent = {"source_id": 3, "ident": "t1", "size": 1, "source": "prowlarr", "published": "2026-09-20"}
    assert cinema.mark_before_release(ev, torrent, set(), set(), "2026-11-15")["cinema"] == "likely"
    assert cinema.mark_before_release(ev, {**torrent, "published": "2026-11-18"}, set(), set(), "2026-11-15")["cinema"] == ""


async def test_wanted_remembers_on_the_eve_of_the_release_only(monkeypatch):
    from datetime import timedelta
    from app.core import registry
    from app.db import init_db
    from app.modules.wanted import store
    await init_db(registry.discover())
    digital = {"v": ""}

    class Tmdb:
        def __init__(self, key):
            pass

        async def get_movie_full(self, tmdb_id):
            return {"releases": {"digital": digital["v"]}}

        async def close(self):
            pass

    monkeypatch.setattr(store, "TMDBClient", Tmdb)
    digital["v"] = (date.today() + timedelta(days=20)).isoformat()
    assert not await store._remember_now({}, 555)                      # weeks ahead: not yet
    digital["v"] = (date.today() + timedelta(days=1)).isoformat()
    assert await store._remember_now({}, 555)                          # the eve
    await cinema.remember(555, [{"source_id": 1, "ident": "x", "size": 9, "film": "yes", "name": "x"}])
    assert not await store._remember_now({}, 555)                      # once
