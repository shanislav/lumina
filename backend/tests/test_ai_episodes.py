"""AI suggestion of episodes (library/ai_episodes): asked twice (the files in order and reversed); every answer is
checked — made-up episodes dropped, two different answers are a doubt, two files on one episode flagged."""

import json
import re

import pytest

from app.modules.library import ai_episodes

CAT = {(8, 9): {"cs": "9. epizoda", "en": "Teeth of the Tegha", "runtime": 40},
       (8, 10): {"cs": "10. epizoda", "en": "Headhunters Revenge", "runtime": 40},
       (0, 2): {"cs": "2. epizoda", "en": "The Rhomphaia", "runtime": 40}}
FILES = [{"path": "/s/a.mkv", "name": "a.mkv", "own": "Zuby teghy", "season": 8, "episode": 8, "duration": 2400},
         {"path": "/s/b.mkv", "name": "b.mkv", "own": "Pomsta lovce hlav", "season": 8, "episode": 9, "duration": 2400},
         {"path": "/s/c.mkv", "name": "c.mkv", "own": "Rhomphaia", "season": 8, "episode": 1, "duration": 2400}]


class FakeResponse:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}], "usage": {"total_tokens": 1}}


@pytest.fixture
def groq(monkeypatch):
    """answers: own name → list of answers, one per question ([season, episode, confidence])."""
    state = {"answers": {}, "asked": 0}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json=None, headers=None):
            prompt = json["messages"][1]["content"]
            out = []
            for i, name in re.findall(r"^(\d+)\. (.+?) \(\d+ min\)$", prompt, re.M):
                answer = state["answers"].get(name)
                if answer:
                    out.append([int(i), *answer[min(state["asked"], len(answer) - 1)]])
            state["asked"] += 1
            return FakeResponse(_json.dumps(out))
    _json = json
    monkeypatch.setattr(ai_episodes.httpx, "AsyncClient", FakeClient)
    return state


async def test_answers_both_times_the_same_are_suggestions(groq):
    groq["answers"] = {"Zuby teghy": [[8, 9, 90]], "Pomsta lovce hlav": [[8, 10, 85]], "Rhomphaia": [["S00", "E02", 70]],
                       }
    out = await ai_episodes.suggest({"groq_api_key": "k", "groq_model": "m"}, FILES, CAT, 8)
    got = {s["path"]: (s["season"], s["episode"], s["title"], s["warning"]) for s in out}
    assert got == {"/s/a.mkv": (8, 9, "Teeth of the Tegha", ""), "/s/b.mkv": (8, 10, "Headhunters Revenge", ""),
                   "/s/c.mkv": (0, 2, "The Rhomphaia", "")}
    assert groq["asked"] == 2


async def test_two_different_answers_are_a_doubt_and_made_up_episodes_dropped(groq):
    groq["answers"] = {"Zuby teghy": [[8, 9, 95], [8, 10, 95]], "Pomsta lovce hlav": [[8, 55, 99]]}
    out = await ai_episodes.suggest({"groq_api_key": "k", "groq_model": "m"}, FILES, CAT, 8)
    assert [(s["path"], s["confidence"]) for s in out] == [("/s/a.mkv", 40)]
    assert "není jistá" in out[0]["warning"]


async def test_two_files_on_one_episode_are_flagged(groq):
    groq["answers"] = {"Zuby teghy": [[8, 9, 90]], "Pomsta lovce hlav": [[8, 9, 60]]}
    out = await ai_episodes.suggest({"groq_api_key": "k", "groq_model": "m"}, FILES[:2], CAT, 8)
    assert all("stejný díl" in s["warning"] for s in out)


async def test_no_key():
    with pytest.raises(ValueError):
        await ai_episodes.suggest({}, FILES, CAT, 8)


async def test_rate_limit_waits_and_asks_again(monkeypatch):
    """Groq's free tier: the second question often hits tokens per minute — it waits as asked, then goes on."""
    calls, slept = [], []

    class Limited:
        status_code = 429
        headers = {}
        text = "Rate limit reached ... Please try again in 1.5s."

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json=None, headers=None):
            calls.append(1)
            return Limited() if len(calls) == 1 else FakeResponse("[[0, 8, 9, 90]]")

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(ai_episodes.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(ai_episodes.asyncio, "sleep", fake_sleep)
    out = await ai_episodes._ask({"groq_api_key": "x"}, FILES, CAT, {8})
    assert out == {0: ((8, 9), 90)} and slept == [1.5]
