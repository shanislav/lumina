"""Quality profiles: hard filter, cutoff, default profiles, CRUD."""

import importlib

import pytest
from fastapi import HTTPException

from app.core import registry
from app.core.profiles import Profile, block, get_profile, load_profiles, reached_cutoff
from app.db import init_db

FULLHD_H265_CZ = {"resolution": "1080p", "codec": "H.265", "hdr": "", "size": 3_000_000_000,
                  "bitrate": 3_900_000, "video_bitrate": 3_500_000, "lang_tier": 3, "audio_langs": ["cs"], "quality_score": 76}


def test_hard_filter():
    fullhd = Profile(min_resolution="1080p", max_resolution="1080p", audio_langs=["cs", "sk"])
    assert block(FULLHD_H265_CZ, fullhd) is None
    assert block({**FULLHD_H265_CZ, "resolution": "2160p"}, fullhd) == "rozlišení 2160p > 1080p"
    assert block({**FULLHD_H265_CZ, "resolution": "720p"}, fullhd) == "rozlišení 720p < 1080p"
    assert block({**FULLHD_H265_CZ, "audio_langs": ["en"]}, fullhd) == "bez zvuku CZ/SK"
    assert block({**FULLHD_H265_CZ, "resolution": ""}, fullhd).startswith("rozlišení ?")   # unknown does not pass
    assert block(FULLHD_H265_CZ, Profile(codecs=["H.265", "AV1"])) is None
    assert block({**FULLHD_H265_CZ, "codec": "H.264"}, Profile(codecs=["H.265"])) == "kodek H.264"
    assert block(FULLHD_H265_CZ, Profile(max_size_gb=2.5)) == "větší než 2.5 GB"
    assert block(FULLHD_H265_CZ, Profile(min_mbps=4)) == "bitrate 3.9 Mb/s < 4"
    assert block(FULLHD_H265_CZ, Profile(hdr="require")) == "bez HDR"
    assert block({**FULLHD_H265_CZ, "hdr": "DV"}, Profile(hdr="forbid")) == "DV nechceš"


def test_cutoff():
    assert reached_cutoff(FULLHD_H265_CZ, Profile(min_resolution="1080p", cutoff=75))
    assert not reached_cutoff(FULLHD_H265_CZ, Profile(min_resolution="1080p", cutoff=80))
    assert not reached_cutoff(FULLHD_H265_CZ, Profile(min_resolution="2160p", cutoff=50))   # does not pass → not done
    assert not reached_cutoff(FULLHD_H265_CZ, Profile())                                    # no cutoff → never done


@pytest.fixture
async def db():
    await init_db(registry.discover())


async def test_default_profiles_and_crud(db):
    profiles = await load_profiles()
    assert [p.name for p in profiles] == ["Standard", "Full HD", "4K"] and profiles[0].is_default
    settings = importlib.import_module("app.modules.settings.router")
    kids = await settings.create_profile(settings.ProfileBody(
        name="Pro děti", is_default=True, config={"audio_langs": ["cs"], "max_size_gb": 4}))
    assert (await get_profile(None)).name == "Pro děti"                     # the new default
    assert (await get_profile(9999)).name == "Pro děti"                     # missing → default
    with pytest.raises(HTTPException):
        await settings.delete_profile(kids["id"])                           # the default cannot go
    await settings.update_profile(profiles[0].id, settings.ProfileBody(name="Standard", is_default=True))
    await settings.delete_profile(kids["id"])
    assert [p.name for p in await load_profiles()] == ["Standard", "Full HD", "4K"]


def test_audio_languages():
    cz_en = {**FULLHD_H265_CZ, "audio_langs": ["cs", "en"]}
    assert block(cz_en, Profile(audio_langs=["cs", "en"], audio_mode="all")) is None
    assert block(FULLHD_H265_CZ, Profile(audio_langs=["cs", "en"], audio_mode="all")) == "chybí zvuk EN"
    assert block({**FULLHD_H265_CZ, "audio_langs": ["sk"]}, Profile(audio_langs=["cs", "sk"])) is None     # one is enough
    assert block({**FULLHD_H265_CZ, "audio_langs": []}, Profile(audio_langs=["cs"])) == "bez zvuku CZ"    # unknown fails
    assert block({**FULLHD_H265_CZ, "audio_langs": []}, Profile()) is None                                # no wish


async def test_old_profiles_keep_their_czech_requirement(db):
    import json
    import sqlite3
    from app.db import DB_PATH
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE quality_profiles SET config = ? WHERE name = 'Standard'",
                     (json.dumps({"min_resolution": "720p", "require_local_audio": True, "cutoff": 70}),))
    standard = next(p for p in await load_profiles() if p.name == "Standard")
    assert standard.audio_langs == ["cs", "sk"] and standard.audio_mode == "any"



def test_suitable_offers_verified_first_then_score():
    from app.core.profiles import Profile, suitable
    p = Profile(min_resolution="1080p", audio_langs=["cs"])
    rows = [
        {"ident": "a", "film": "yes", "resolution": "2160p", "audio_langs": ["cs"], "quality_score": 90, "verified": False},
        {"ident": "b", "film": "yes", "resolution": "1080p", "audio_langs": ["cs"], "quality_score": 70, "verified": True},
        {"ident": "c", "film": "yes", "resolution": "720p", "audio_langs": ["cs"], "quality_score": 99, "verified": True},
        {"ident": "d", "film": "no", "resolution": "2160p", "audio_langs": ["cs"], "quality_score": 99, "verified": True},
        {"ident": "e", "film": "yes", "resolution": "2160p", "audio_langs": ["en"], "quality_score": 95, "verified": True},
    ]
    assert [r["ident"] for r in suitable(rows, p)] == ["b", "a"]
