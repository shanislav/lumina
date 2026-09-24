"""Scoring scenarios modelled on real conflicts found in the library (docs/decisions/0002)."""

from app.modules.library.matcher import FileEvidence, decide, score_candidate


def movie(tmdb_id, title, year, runtime, lang="en", titles=None):
    return {"tmdb_id": tmdb_id, "title": title, "original_title": title, "year": year,
            "runtime": runtime, "original_language": lang, "titles": titles or [title]}


def run(ev, *cands):
    return decide([score_candidate(ev, c) for c in cands])


def test_wrong_hint_is_outvoted_by_duration_and_year():
    # Hypothetical: NFO points to 2049, but the file is 117 min long → the 1982 film.
    # (In the real library it was the other way round: Radarr named a 163 min file "(1982)".)
    ev = FileEvidence(
        titles=["Blade Runner", "Blade Runner 2049"], years={1982, 2017}, duration_min=117,
        audio_langs={"cs", "en"}, hints={335984: ["nfo"]},
    )
    status, ranked = run(ev, movie(78, "Blade Runner", 1982, 117), movie(335984, "Blade Runner 2049", 2017, 164))
    assert status == "matched"
    assert ranked[0].candidate["tmdb_id"] == 78


def test_short_film_mismatch_is_rejected():
    # A third-party tool matched "Waves (2024)" to a 2-minute short; the file is the Czech film Vlny (131 min, CS audio).
    ev = FileEvidence(titles=["Waves"], years={2024}, duration_min=131, audio_langs={"cs"},
                      hints={1251621: ["nfo"], 1467389: ["nfo"]})
    status, ranked = run(
        ev,
        movie(1467389, "Waves", 2024, 2),
        movie(1251621, "Vlny", 2024, 131, lang="cs", titles=["Vlny", "Waves"]),
    )
    assert status == "matched"
    assert ranked[0].candidate["tmdb_id"] == 1251621


def test_local_language_decides_between_same_titles():
    # "Let There Be Light": US film 2017 vs Slovak film 2019; file has only SK audio.
    ev = FileEvidence(titles=["Let There Be Light"], years={2017, 2019}, duration_min=93, audio_langs={"sk"},
                      hints={609164: ["nfo"], 480881: ["nfo"]})
    status, ranked = run(
        ev,
        movie(480881, "Let There Be Light", 2017, 100),
        movie(609164, "Budiž světlo", 2019, 93, lang="sk", titles=["Budiž světlo", "Nech je svetlo", "Let There Be Light"]),
    )
    assert ranked[0].candidate["tmdb_id"] == 609164
    assert status == "matched"


def test_close_candidates_go_to_review():
    # Two same-titled films, same year, no duration known → user must decide.
    ev = FileEvidence(titles=["Saints"], years={2014})
    status, _ = run(ev, movie(1, "Saints", 2014, 90), movie(2, "Saints", 2014, 95))
    assert status == "review"


def test_name_tag_is_trusted():
    ev = FileEvidence(titles=["The Matrix"], years={1999}, hints={603: ["name_tag"]})
    status, ranked = run(ev, movie(603, "The Matrix", 1999, 136), movie(624860, "The Matrix Resurrections", 2021, 148))
    assert status == "matched" and ranked[0].candidate["tmdb_id"] == 603


def test_no_candidates_is_unmatched():
    assert decide([])[0] == "unmatched"


def test_duration_mismatch_blocks_auto_match():
    # Real case "Saints (2014)": file 98 min, best candidate 79 min, strong NFO hint → still review.
    ev = FileEvidence(titles=["Saints"], years={2014}, duration_min=98, hints={509510: ["nfo", "nfo_imdb"]})
    status, ranked = run(ev, movie(509510, "Sveci", 2014, 79, titles=["Sveci", "Saints"]), movie(652203, "Saints", 2014, 8))
    assert ranked[0].candidate["tmdb_id"] == 509510
    assert status == "review"
