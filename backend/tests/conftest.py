import pytest

from app.core import events


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test runs in its own temp dir, so the relative data/lumina.db is fresh."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    events.clear()
    yield tmp_path
    events.clear()


@pytest.fixture(autouse=True)
def offline_episode_catalog(monkeypatch):
    """No TMDB in tests: the episode catalog of a search / an import is what tmdb_episodes holds."""
    from app.modules.library import episode_names

    class Offline:
        async def get_tv_full(self, *a, **kw):
            raise RuntimeError("offline")

        async def close(self):
            pass
    monkeypatch.setattr(episode_names, "_client", lambda key: Offline())
