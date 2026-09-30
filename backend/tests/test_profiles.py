"""Quality profiles: hard filter, cutoff, default profiles, CRUD."""

import importlib

import pytest
from fastapi import HTTPException

from app.core import registry
from app.core.profiles import Profile, block, get_profile, load_profiles, reached_cutoff
from app.db import init_db

FULLHD_H265_CZ = {"resolution": "1080p", "codec": "H.265", "hdr": "", "size": 3_000_000_000,
                  "video_bitrate": 3_500_000, "lang_tier": 3, "quality_score": 76}


def test_hard_filter():
    fullhd = Profile(min_resolution="1080p", max_resolution="1080p", require_local_audio=True)
    assert block(FULLHD_H265_CZ, fullhd) is None
    assert block({**FULLHD_H265_CZ, "resolution": "2160p"}, fullhd) == "rozlišení 2160p > 1080p"
    assert block({**FULLHD_H265_CZ, "resolution": "720p"}, fullhd) == "rozlišení 720p < 1080p"
    assert block({**FULLHD_H265_CZ, "lang_tier": 1}, fullhd) == "bez CZ/SK zvuku"
    assert block({**FULLHD_H265_CZ, "resolution": ""}, fullhd).startswith("rozlišení ?")   # unknown does not pass
    assert block(FULLHD_H265_CZ, Profile(codecs=["H.265", "AV1"])) is None
    assert block({**FULLHD_H265_CZ, "codec": "H.264"}, Profile(codecs=["H.265"])) == "kodek H.264"
    assert block(FULLHD_H265_CZ, Profile(max_size_gb=2.5)) == "větší než 2.5 GB"
    assert block(FULLHD_H265_CZ, Profile(min_video_mbps=4)) == "video 3.5 Mb/s < 4"
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
        name="Pro děti", is_default=True, config={"require_local_audio": True, "max_size_gb": 4}))
    assert (await get_profile(None)).name == "Pro děti"                     # the new default
    assert (await get_profile(9999)).name == "Pro děti"                     # missing → default
    with pytest.raises(HTTPException):
        await settings.delete_profile(kids["id"])                           # the default cannot go
    await settings.update_profile(profiles[0].id, settings.ProfileBody(name="Standard", is_default=True))
    await settings.delete_profile(kids["id"])
    assert [p.name for p in await load_profiles()] == ["Standard", "Full HD", "4K"]
