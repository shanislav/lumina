"""A whole TV season: files grouped into releases, a plan that keeps one release and the language."""

from app.core.offers.season import group_sets, plan_season, release_key, season_queries


def row(name, size, score=60, tier=3, source="webshare", res="1080p"):
    from app.core.episode_match import parse_episode
    return {"name": name, "size": size, "quality_score": score, "lang_tier": tier, "source": source,
            "resolution": res, "audio_langs": ["cs"] if tier >= 2 else ["en"], "film": "yes",
            "pack": parse_episode(name).is_pack, "verified": False, "bitrate": 0}


def test_release_key_ignores_the_episode_and_its_title():
    a = release_key("Final Space_S01E03_Kapitola třetí.mkv")
    b = release_key("Final Space_S01E04_Kapitola čtvrtá.mkv")
    c = release_key("Final Space S01E01 cz titulky.mkv")
    assert a == b and a != c
    assert release_key("Sexuální výchova S03E02-08 2021 CZ dab 1080p - Epizoda 2.mkv") == \
        release_key("Sexuální výchova S03E05-08 2021 CZ dab 1080p - Epizoda 5.mkv")


def test_sets_and_plan_keep_one_release_but_language_first():
    cz = [row(f"Show S01E0{e} CZ dab 1080p.mkv", 1_400_000_000) for e in (1, 2, 3)]          # E04 missing
    en = [row(f"Show.S01E0{e}.1080p.WEB-DL.x264-GRP.mkv", 1_300_000_000, score=70, tier=0) for e in (1, 2, 3, 4)]
    single = [row("Show S01E04 CZ 720p.avi", 700_000_000, score=40, res="720p")]
    reencode = [row("Show S01E02 CZ dab 1080p.mkv", 5_000_000_000)]                          # same name, 3.5× the data
    sets = group_sets(cz + en + single + reencode, 1, [1, 2, 3, 4], 45)
    assert sets[0]["label"] == "Show S01E·· CZ dab 1080p.mkv" and sets[0]["covered"] == [1, 2, 3]
    assert len(sets) == 4                         # CZ, CZ re-encode, EN, the single CZ file
    plan = plan_season(sets, [1, 2, 3, 4])
    assert [p["row"]["name"] for p in plan] == [
        "Show S01E01 CZ dab 1080p.mkv", "Show S01E02 CZ dab 1080p.mkv", "Show S01E03 CZ dab 1080p.mkv",
        "Show S01E04 CZ 720p.avi"]                # the gap from a CZ file, not from the EN release


def test_season_queries():
    ddl, torrent = season_queries(["Sexuální výchova", "Sex Education"], 3)
    assert ddl == ["Sexuální výchova S03", "Sex Education S03", "Sexuální výchova"]
    assert torrent == ["Sex Education S03", "Sex Education"]
