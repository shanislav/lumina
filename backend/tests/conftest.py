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
