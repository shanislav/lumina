"""TV episodes: what a file name holds and whether it is the wanted episode."""

import pytest

from app.core.episode_match import judge_episode, parse_episode


@pytest.mark.parametrize("name, season, episodes, seasons, complete", [
    ("Sex.Education.S01E03.CZ.mkv", 1, [3], [], False),
    ("Shōgun S01E05 (EN)[WEB-DL][1080p]", 1, [5], [], False),
    ("Show S01E01-E03 720p.mkv", 1, [1, 2, 3], [], False),
    ("Show S01E01 - E03.mkv", 1, [1, 2, 3], [], False),
    ("Show.S02E01E02.mkv", 2, [1, 2], [], False),
    ("Show S01E03 720p x264.mkv", 1, [3], [], False),             # 720 is not an episode
    ("Black Books S1E03 - Grapes Of Wrath.avi", 1, [3], [], False),
    ("Pratele 3x05 CZ.avi", 3, [5], [], False),
    ("Shōgun S01 (EN)[WEBrip][1080p][HEVC]", 1, [], [], False),   # a season pack
    ("Přátelé 1. série komplet", 1, [], [], True),
    ("Hra o trůny 3. řada", 3, [], [], False),
    ("Breaking Bad 2.serie dil 5.mkv", 2, [5], [], False),
    ("The Office S01-S09 complete", None, [], list(range(1, 10)), True),
    ("Fotr na tripu - 03.mkv", None, [3], [], False),
    ("chalupari-01-chudak-dedecek-hd-1975-cs-78pt.mkv", None, [1], [], False),
    ("Dune.Part.One.2021.2160p.mkv", None, [], [], False),
])
def test_parse_episode(name, season, episodes, seasons, complete):
    info = parse_episode(name)
    assert (info.season, info.episodes, info.seasons, info.complete) == (season, episodes, seasons, complete)


@pytest.mark.parametrize("name, status, reason", [
    ("Sex.Education.S01E03.CZ.mkv", "yes", None),
    ("Sex Education S01E01-E04 1080p.mkv", "yes", "víc dílů v souboru (E01–E04)"),
    ("Sex.Education.S01E04.CZ.mkv", "no", "jiný díl (S01E04)"),
    ("Sex.Education.S02E03.CZ.mkv", "no", "jiná série (S02)"),
    ("Sex Education S01 1080p NF WEB-DL", "unsure", "celá série S01"),
    ("Sex Education S01-S04 complete", "unsure", "balík sérií S01–S04"),
    ("Sex Education S02-S04", "no", "jiné série (S02–S04)"),
    ("Banshee.S01E03.mkv", "no", "jiný seriál"),
    ("Sex Education - 03.mkv", "unsure", "díl bez čísla série"),
    ("Sex Education 2019 CZ.mkv", "no", "nepoznám díl"),
])
def test_judge_episode(name, status, reason):
    verdict = judge_episode(name, ["Sex Education"], 1, 3)
    assert verdict.status == status
    assert reason is None or reason in verdict.reasons


def test_any_name_of_the_show_and_an_incomplete_file():
    assert judge_episode("Pratele 3x05 CZ.avi", ["Přátelé", "Friends"], 3, 5).status == "yes"
    assert judge_episode("Friends.S03E05.720p.mkv", ["Přátelé", "Friends"], 3, 5).status == "yes"
    short = judge_episode("Friends.S03E05.mkv", ["Friends"], 3, 5, duration_s=600, runtime_min=22)
    assert short.status == "length"


def test_episode_queries():
    from app.core.offers.search import episode_queries
    ddl, torrent = episode_queries(["Přátelé", "Friends", "Friends"], 3, 5)
    assert ddl == ["Přátelé S03E05", "Friends S03E05", "Přátelé 3x05"]
    assert torrent == ["Friends S03E05", "Friends S03"]


def test_offer_of_an_episode_is_judged_as_an_episode():
    from app.core.offers.evaluate import MovieContext, evaluate
    from app.core.quality import Prefs
    ctx = MovieContext(titles=["Sex Education"], runtime=50, episode={"season": 1, "episode": 3})
    one = evaluate("Sex.Education.S01E03.1080p.CZ.mkv", 2_000_000_000, ctx, Prefs())
    pack = evaluate("Sex Education S01 1080p", 16_000_000_000, ctx, Prefs())
    assert one["film"] == "yes" and one["bitrate"] == int(2e9 * 8 / 3000)
    assert pack["film"] == "unsure" and pack["bitrate"] == 0          # a pack's size says nothing per episode
    assert MovieContext.from_dict(ctx.as_dict()).episode == {"season": 1, "episode": 3}
