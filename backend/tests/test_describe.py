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
        assert await describe.usage(db, 1) == 3
        assert describe.left(3) == describe.DAILY_PER_USER - 3
        assert describe.left(99) == 0
        assert describe.left(99, admin=True) is None          # an admin is not limited


class _Resp:
    def __init__(self, headers):
        self.headers = headers


def test_groq_quota_from_headers(tmp_path, monkeypatch):
    from app.clients import groq_quota
    monkeypatch.setattr(groq_quota, "FILE", tmp_path / "q.json")
    monkeypatch.setattr(groq_quota, "_last", {})
    assert groq_quota.get() is None
    groq_quota.note(_Resp({"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "987",
                           "x-ratelimit-reset-requests": "1m10.5s", "x-ratelimit-limit-tokens": "8000",
                           "x-ratelimit-remaining-tokens": "6500", "x-ratelimit-reset-tokens": "11ms"}))
    q = groq_quota.get()
    assert (q["requests_left"], q["requests_limit"]) == (987, 1000)
    assert groq_quota._seconds("1h2m3.5s") == 3723.5 and groq_quota._seconds("120ms") == 0.12
    monkeypatch.setattr(groq_quota, "_last", {})              # a restart: read back from the file
    assert groq_quota.get()["requests_left"] == 987


class _FakeTmdb:
    def __init__(self, key):
        pass

    async def keyword(self, name):
        return {"telekinesis": {"id": 1, "name": "telekinesis"}, "genius": {"id": 2, "name": "genius"}}.get(name)

    async def by_keyword(self, kid, kind="movie", language=""):
        if kind == "tv":
            return []
        films = {1: [{"id": 10, "title": "Phenomenon", "release_date": "1996-07-05", "overview": "A bright light"},
                     {"id": 11, "title": "Carrie", "release_date": "1976-11-03", "overview": "Prom"}],
                 2: [{"id": 10, "title": "Phenomenon", "release_date": "1996-07-05", "overview": "A bright light"}]}
        return films[kid]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_candidates_most_keywords_first(monkeypatch):
    monkeypatch.setattr(describe, "TMDBClient", _FakeTmdb)
    out = await describe.candidates({"tmdb_api_key": "x"}, ["telekinesis", "genius", "nonsense"])
    assert out == ["Phenomenon (1996, film): A bright light", "Carrie (1976, film): Prom"]


@pytest.mark.asyncio
async def test_question_carries_the_candidates(monkeypatch):
    asked = []

    async def fake_groq(cfg, messages, max_tokens, temperature=0.3, deadline=None):
        asked.append(messages)
        if len(asked) == 1:
            return '["telekinesis"]'
        return '{"guesses": [{"title": "Phenomenon", "year": 1996, "type": "movie", "why": "světlo"}], "ask": ""}'

    monkeypatch.setattr(describe, "_groq", fake_groq)
    monkeypatch.setattr(describe, "TMDBClient", _FakeTmdb)
    guesses, _ = await describe.ask_groq({"tmdb_api_key": "x"}, [{"role": "user", "content": "hýbe věcmi myšlenkou"}])
    assert guesses[0]["title"] == "Phenomenon"
    assert "Phenomenon (1996, film)" in asked[1][-1]["content"]
