"""The user's word on an episode's sound language (library/audio_lang): into MKV in place, MP4 by a copy,
AVI only in Lumina; only tracks without a language unless one is named."""

import pytest

from app.modules.library import audio_lang


def test_which_tracks():
    audio = [{"lang": "en"}, {"lang": ""}, {"lang": "und"}]
    assert audio_lang._targets(audio, None) == [1, 2]
    assert audio_lang._targets(audio, 0) == [0]
    assert audio_lang._targets(audio, 5) == []


async def test_avi_keeps_it_in_lumina(tmp_path):
    f = tmp_path / "South Park - S28E03.avi"
    f.write_bytes(b"x")
    out = await audio_lang.set_language(str(f), "cs", media={"audio": [{"lang": ""}]})
    assert out["written"] is False and out["media"]["audio"][0]["lang"] == "cs" and out["tracks"] == 1


async def test_mkv_is_written_in_place(tmp_path, monkeypatch):
    f = tmp_path / "South Park - S28E03.mkv"
    f.write_bytes(b"x")
    calls = []

    async def fake_run(*args, timeout=0):
        calls.append(args)
        return 0, ""

    async def probe(path):
        return {"audio": [{"lang": "cs"}]}
    monkeypatch.setattr(audio_lang, "_run", fake_run)
    monkeypatch.setattr(audio_lang, "probe_async", probe)
    out = await audio_lang.set_language(str(f), "cs", media={"audio": [{"lang": ""}]})
    assert calls == [("mkvpropedit", str(f), "--edit", "track:a1", "--set", "language=cze")]
    assert out["written"] and out["media"]["audio"][0]["lang"] == "cs"


async def test_an_unknown_language_is_refused(tmp_path):
    with pytest.raises(ValueError):
        await audio_lang.set_language(str(tmp_path / "x.mkv"), "xx", media={"audio": [{"lang": ""}]})
