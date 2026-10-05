from fastapi import FastAPI
from starlette.routing import Match

from app.modules.series.router import router


def test_fixed_paths_not_taken_by_show_id():
    """'/{tmdb_id}/overview' must not swallow '/automation/overview' (422 hid the library's quality overview)."""
    app = FastAPI()
    app.include_router(router)
    for path in ("/api/series/automation/overview",):
        scope = {"type": "http", "path": path, "method": "GET"}
        hit = next(r for r in app.routes if r.matches(scope)[0] == Match.FULL)
        assert hit.endpoint.__name__ == "automation_overview"
