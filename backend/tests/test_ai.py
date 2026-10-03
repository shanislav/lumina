"""Lumina's two AIs (app/clients/ai): who answers first, Gemini's answers and day, the other one when one fails."""

import json

import pytest

from app.clients import ai

BOTH = {"groq_api_key": "g", "groq_model": "m", "gemini_api_key": "k", "gemini_model": "gemini-flash-latest"}


@pytest.fixture(autouse=True)
def gemini_day(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "USAGE_FILE", tmp_path / "gemini.json")
    monkeypatch.setattr(ai, "_gemini", {})


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.headers = status, payload, {}
        self.text = text or json.dumps(payload or {})

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_http(monkeypatch, answer):
    """answer(url, json) → Resp; records the calls."""
    calls = []

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json=None, headers=None):
            calls.append((url, json))
            return answer(url, json)

    monkeypatch.setattr(ai.httpx, "AsyncClient", Client)
    return calls


def gemini_ok(text, searched=False):
    meta = {"webSearchQueries": ["film man learns language minutes"]} if searched else {}
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": "thinking…", "thought": True}, {"text": text}]},
                                      "groundingMetadata": meta}], "usageMetadata": {"totalTokenCount": 9}})


def groq_ok(text):
    return Resp(200, {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 5}})


def test_order_per_feature_and_setting():
    assert ai.order(BOTH, "describe") == ["gemini", "groq"]
    assert ai.order(BOTH, "scoring") == ["groq", "gemini"]
    assert ai.order({**BOTH, "ai_describe": "groq"}, "describe") == ["groq", "gemini"]
    assert ai.order({"groq_api_key": "g"}, "describe") == ["groq"]
    assert ai.order({}, "describe") == []


def test_gemini_turns_alternate_and_start_with_the_user():
    out = ai._gemini_contents([{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"},
                               {"role": "user", "content": "c"}])
    assert [c["role"] for c in out] == ["user", "model", "user"]
    assert out[2]["parts"][0]["text"] == "b\n\nc"


async def test_gemini_searches_google_skips_thoughts_and_counts_its_day(monkeypatch):
    calls = fake_http(monkeypatch, lambda url, body: gemini_ok('{"guesses": []}', searched=True))
    answer = await ai.chat(BOTH, "describe", "sys", [{"role": "user", "content": "q"}], search=True)
    assert (answer.text, answer.provider, answer.searched) == ('{"guesses": []}', "gemini", True)
    url, body = calls[0]
    assert url.endswith("/models/gemini-flash-latest:generateContent") and body["tools"] == [{"google_search": {}}]
    assert ai.gemini_usage()["calls"] == 1 and ai.gemini_usage()["searches"] == 1


async def test_gemini_out_for_the_day_groq_answers_and_gemini_goes_last(monkeypatch):
    def answer(url, body):
        if "generativelanguage" in url:
            return Resp(429, text='{"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}}')
        return groq_ok("from groq")
    calls = fake_http(monkeypatch, answer)
    got = await ai.chat(BOTH, "describe", "sys", [{"role": "user", "content": "q"}])
    # every Gemini model it knows of (the chosen one, the lite alias) out → Groq
    assert (got.text, got.provider) == ("from groq", "groq") and len(calls) == 3
    assert ai.gemini_out(BOTH) and ai.order(BOTH, "describe") == ["groq", "gemini"]


async def test_one_gemini_model_out_the_next_one_answers(monkeypatch):
    """The free tier: each model its own daily limit — "gemini-flash-latest" is counted as the model behind it."""
    def answer(url, body):
        if "gemini-flash-latest" in url:
            return Resp(429, text='{"error": {"details": [{"violations": [{"quotaId": '
                                  '"GenerateRequestsPerDayPerProjectPerModel-FreeTier", '
                                  '"quotaDimensions": {"model": "gemini-3.8-flash"}}]}]}}')
        if "generativelanguage" in url:
            return gemini_ok("Fenomén (1996)")
        return groq_ok("from groq")
    calls = fake_http(monkeypatch, answer)
    monkeypatch.setattr(ai, "_models", {"at": 9e12, "key": hash("k"), "names": [
        "gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-pro-latest", "gemini-flash-lite-latest",
        "gemma-4-31b-it"]})
    got = await ai.chat(BOTH, "describe", "sys", [{"role": "user", "content": "q"}])
    assert (got.text, got.provider, got.model, got.label) == ("Fenomén (1996)", "gemini", "gemini-3.7-flash", "Gemini 3.7 flash")
    assert len(calls) == 2 and set(ai.gemini_usage()["out"]) == {"gemini-flash-latest", "gemini-3.8-flash"}
    assert ai.gemini_chain(BOTH) == ["gemini-3.7-flash", "gemini-flash-lite-latest", "gemma-4-31b-it"]  # no pro
    assert ai.order(BOTH, "describe") == ["gemini", "groq"]


async def test_gemma_gets_the_system_text_in_the_question(monkeypatch):
    calls = fake_http(monkeypatch, lambda url, body: gemini_ok("ok"))
    await ai._gemini_ask(BOTH, "sys", [{"role": "user", "content": "q"}], 100, 0.3, True, "gemma-4-31b-it")
    body = calls[0][1]
    assert "systemInstruction" not in body and "tools" not in body
    assert body["contents"][0]["parts"][0]["text"] == "sys\n\nq"


async def test_no_ai_answers():
    with pytest.raises(ai.AIError):
        await ai.chat({}, "describe", "sys", [{"role": "user", "content": "q"}])


async def test_episodes_ask_each_ai_once(monkeypatch):
    """With both AIs the second question goes to the other one: two AIs agreeing."""
    from app.modules.library import ai_episodes
    cat = {(1, 1): {"cs": "", "en": "Pilot", "runtime": 40}, (1, 2): {"cs": "", "en": "Two", "runtime": 40}}
    files = [{"path": "/a.mkv", "name": "a.mkv", "own": "Pilot", "season": 1, "episode": 1, "duration": 2400}]
    calls = fake_http(monkeypatch, lambda url, body: gemini_ok("[[0, 1, 1, 90]]") if "generativelanguage" in url
                      else groq_ok("[[0, 1, 2, 80]]"))
    out = await ai_episodes.suggest(BOTH, files, cat, 1)
    assert len(calls) == 2 and "generativelanguage" in calls[0][0] and "groq" in calls[1][0]
    assert out[0]["by"] == ["Gemini", "Groq"] and "AI se neshodly (Gemini S01E01, Groq S01E02)" == out[0]["warning"]


async def test_describe_gemini_first_groq_when_gemini_fails(monkeypatch):
    from app.modules.search import describe

    async def gemini_fails(cfg, talk, deadline=None):
        raise ai.LimitError("Gemini: překročený limit")

    async def groq(cfg, talk, deadline=None):
        return [{"title": "Phenomenon", "year": 1996, "type": "movie", "why": "x"}], ""
    monkeypatch.setattr(describe, "ask_gemini", gemini_fails)
    monkeypatch.setattr(describe, "ask_groq", groq)
    got = await describe.ask(BOTH, [{"role": "user", "content": "q"}])
    assert got["by"] == "Groq" and got["guesses"][0]["title"] == "Phenomenon"


async def test_google_search_not_allowed_asks_without_it_for_the_day(monkeypatch):
    def answer(url, body):
        if body.get("tools"):
            return Resp(429, text='{"error": {"message": "You exceeded your current quota"}}')
        return gemini_ok("from memory")
    calls = fake_http(monkeypatch, answer)
    got = await ai.chat(BOTH, "describe", "sys", [{"role": "user", "content": "q"}], search=True)
    assert (got.text, got.provider, got.searched) == ("from memory", "gemini", False) and len(calls) == 2
    await ai.chat(BOTH, "describe", "sys", [{"role": "user", "content": "q"}], search=True)
    assert len(calls) == 3 and "tools" not in calls[2][1]          # not tried again today
    assert ai.quotas(BOTH)["gemini"]["no_search"]


async def test_a_bad_question_does_not_cross_out_the_models(monkeypatch):
    fake_http(monkeypatch, lambda url, body: Resp(400, text='{"error": {"message": "contents is not specified"}}'))
    with pytest.raises(ai.AIError):
        await ai._gemini_ask(BOTH, "sys", [{"role": "user", "content": "q"}], 100, 0.3, False, "gemini-3.7-flash")
    assert ai.gemini_usage()["out"] == []
    fake_http(monkeypatch, lambda url, body: Resp(404, text='{"error": {"message": "models/x is not found"}}'))
    with pytest.raises(ai.AIError):
        await ai._gemini_ask(BOTH, "sys", [{"role": "user", "content": "q"}], 100, 0.3, False, "gemini-x")
    assert ai.gemini_usage()["out"] == ["gemini-x"]
    with pytest.raises(ai.AIError):
        await ai._gemini_ask(BOTH, "sys", [], 100, 0.3, False, "gemini-3.7-flash")


async def test_a_hanging_gemini_model_gives_way_in_time(monkeypatch):
    """A model that does not answer (a timeout) — the next one; past the caller's deadline nothing more is asked."""
    import time as t
    import httpx

    calls = []

    def answer(url, body):
        calls.append(url)
        if "3.7" in url:
            raise httpx.ReadTimeout("")
        return gemini_ok("ok")
    fake_http(monkeypatch, answer)
    monkeypatch.setattr(ai, "_models", {"at": 9e12, "key": hash("k"), "names": ["gemini-3.7-flash", "gemini-3.6-flash"]})
    got = await ai.ask({**BOTH, "gemini_model": "gemini-3.7-flash"}, "gemini", "sys",
                       [{"role": "user", "content": "q"}], deadline=t.monotonic() + 30)
    assert got.model == "gemini-3.6-flash" and len(calls) == 2
    with pytest.raises(ai.LimitError):
        await ai.ask(BOTH, "groq", "sys", [{"role": "user", "content": "q"}], deadline=t.monotonic() + 2)
