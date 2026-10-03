import aiosqlite
import pytest

from app.models.schemas import TMDBMovie
from app.modules.search import describe


def test_parse_guesses_and_question():
    content = 'ok {"guesses": [{"title": "Back to the Future", "year": "1985", "type": "movie", "why": "DeLorean"},' \
              ' {"title": "Dark", "year": 2017, "type": "series", "why": "cestování časem"}, {"title": ""}],' \
              ' "ask": "Byl to film, nebo seriál?"}'
    guesses, ask = describe._parse(content)
    assert guesses == [
        {"title": "Back to the Future", "year": 1985, "type": "movie", "why": "DeLorean"},
        {"title": "Dark", "year": 2017, "type": "tv", "why": "cestování časem"},
    ]
    assert ask == "Byl to film, nebo seriál?"


def test_messages_keep_the_newest_and_roles():
    talk = [{"role": "user", "content": f"m{i}"} for i in range(12)] + [{"role": "system", "content": "x" * 900}]
    out = describe._messages(talk)
    assert len(out) == describe.MAX_TURNS
    assert out[-1] == {"role": "user", "content": "x" * describe.MAX_CHARS}     # nobody sends a system message


def _m(year):
    return TMDBMovie(tmdb_id=int(year), title="t", original_title="t", year=year, overview="", poster_url=None)


def test_best_wants_the_guessed_year():
    assert describe._best([_m("2010"), _m("1986")], 1985).year == "1986"
    assert describe._best([_m("2010")], 1985) is None
    assert describe._best([_m("2010")], None).year == "2010"


@pytest.mark.asyncio
async def test_daily_limit():
    async with aiosqlite.connect(":memory:") as db:
        await db.execute(describe.AI_USAGE)
        for _ in range(3):
            await describe.count(db, 1)
        await describe.count(db, 2)
        mine, every = await describe.usage(db, 1)
        assert (mine, every) == (3, 4)
        assert describe.left(mine, every) == describe.DAILY_PER_USER - 3
        assert describe.left(0, describe.DAILY_TOTAL) == 0
