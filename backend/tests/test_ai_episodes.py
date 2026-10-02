"""AI suggestion of episodes (library/ai_episodes): every answer is checked — made-up episodes dropped, two files
on one episode flagged, agreement with the rules shown."""

import json

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
    answer = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json=None, headers=None):
            answer["prompt"] = json["messages"][1]["content"]
            return FakeResponse(answer["content"])
    monkeypatch.setattr(ai_episodes.httpx, "AsyncClient", FakeClient)
    return answer


async def test_answers_are_checked(groq):
    groq["content"] = json.dumps([[0, 8, 9, 90], [1, 8, 10, 85], [2, 8, 55, 90], [2, 0, 2, 70]])
    out = await ai_episodes.suggest({"groq_api_key": "k", "groq_model": "m"}, FILES, CAT, 8)
    got = {s["path"]: (s["season"], s["episode"], s["title"]) for s in out}
    assert got == {"/s/a.mkv": (8, 9, "Teeth of the Tegha"), "/s/b.mkv": (8, 10, "Headhunters Revenge"),
                   "/s/c.mkv": (0, 2, "The Rhomphaia")}                      # S08E55 does not exist: dropped
    assert "Zuby teghy" in groq["prompt"] and "Headhunters Revenge" in groq["prompt"]


async def test_two_files_on_one_episode_are_flagged(groq):
    groq["content"] = json.dumps([[0, 8, 9, 90], [1, 8, 9, 60]])
    out = await ai_episodes.suggest({"groq_api_key": "k", "groq_model": "m"}, FILES[:2], CAT, 8)
    assert all(s.get("warning") for s in out)


async def test_no_key():
    with pytest.raises(ValueError):
        await ai_episodes.suggest({}, FILES, CAT, 8)
