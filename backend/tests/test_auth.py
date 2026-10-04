"""Sign-in, sessions, permissions — and a guard that no module endpoint is left unprotected."""

import asyncio
import importlib

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core import auth, registry
from app.core.auth import ADMIN_ONLY, LoginThrottle, hash_password, verify_password
from app.db import init_db


def test_password_hash_roundtrip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("correct hors", stored)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")   # salted


def test_throttle_grows_after_five_failures():
    t = LoginThrottle()
    for i in range(5):
        assert t.wait_s("ip:1", now=100.0 + i) == 0
        t.failed("ip:1", now=100.0 + i)
    assert t.wait_s("ip:1", now=105.0) == 30 - 1          # 5 fails → 30 s after the last one
    t.failed("ip:1", now=140.0)
    assert t.wait_s("ip:1", now=140.0) == 60              # 6th → 1 min
    assert t.wait_s("ip:2", now=140.0) == 0               # other keys are free
    assert t.wait_s("ip:1", now=140.0 + 16 * 60) == 0     # window over
    t.succeeded("ip:1")
    assert t.wait_s("ip:1", now=141.0) == 0


def _app():
    from app.main import app
    return app


def test_every_module_endpoint_is_guarded():
    """Every module route needs a signed-in user (router-level), every changing route names its
    permission explicitly, and every permission used is declared by some module."""
    app = _app()
    declared = {p.name for m in registry.discover() for p in m.permissions} | {ADMIN_ONLY}
    public = {"/api/health", "/api/auth/status", "/api/auth/setup", "/api/auth/login"}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        deps = [d.call for d in route.dependant.dependencies]
        guards = [d for d in deps if hasattr(d, "permission")]
        if route.path in public:
            continue
        assert guards, f"{route.methods} {route.path} is not guarded"
        for g in guards:
            assert g.permission is None or g.permission in declared, (route.path, g.permission)
        if route.methods - {"GET", "HEAD"} and not route.path.startswith("/api/auth/"):
            assert any(g.permission for g in guards), f"{route.methods} {route.path} needs a permission"


@pytest.fixture
def client():
    auth_router = importlib.import_module("app.modules.auth.router")
    asyncio.run(init_db(registry.discover()))
    asyncio.run(auth_router.prepare_setup())
    auth.THROTTLE._fails.clear()
    return TestClient(_app())


def _setup_admin(client):
    auth_router = importlib.import_module("app.modules.auth.router")
    r = client.post("/api/auth/setup", json={"code": auth_router._setup_token, "username": "shano",
                                              "password": "admin-pass-1", "remember": True})
    assert r.status_code == 200, r.text
    return r


def test_first_admin_needs_the_code_from_the_log(client):
    assert client.get("/api/auth/status").json() == {"setup_required": True, "user": None}
    r = client.post("/api/auth/setup", json={"code": "wrong", "username": "shano", "password": "admin-pass-1"})
    assert r.status_code == 403
    r = _setup_admin(client)
    assert "max-age=2592000" in r.headers["set-cookie"].lower() and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["is_admin"] is True
    # only once
    r = client.post("/api/auth/setup", json={"code": "x", "username": "other", "password": "admin-pass-1"})
    assert r.status_code == 409


def test_nothing_works_without_signing_in(client):
    _setup_admin(client)
    anonymous = TestClient(_app())
    assert anonymous.get("/api/library/movies").status_code == 401
    assert anonymous.get("/api/modules").status_code == 401
    assert anonymous.post("/api/download", json={}).status_code == 401
    assert client.get("/api/modules").status_code == 200


def test_user_permissions_are_checked_on_the_backend(client):
    _setup_admin(client)
    r = client.post("/api/auth/users", json={"username": "bracho", "password": "user-pass-1"})
    assert r.status_code == 200, r.text
    assert set(r.json()["permissions"]) == {"search", "download", "wanted", "library.view", "player"}   # defaults

    bro = TestClient(_app())
    assert bro.post("/api/auth/login", json={"username": "bracho", "password": "nope-nope"}).status_code == 401
    r = bro.post("/api/auth/login", json={"username": "BRACHO", "password": "user-pass-1"})
    assert r.status_code == 200 and "max-age" not in r.headers["set-cookie"].lower()   # not remembered
    assert bro.get("/api/library/movies").status_code == 200
    assert bro.delete("/api/library/movies/1/file").status_code == 403
    assert bro.put("/api/settings", json={}).status_code == 403
    assert bro.get("/api/auth/users").status_code == 403
    r = bro.post("/api/download", json={"file_ident": "x", "source": "webshare", "tmdb_id": 1, "title": "M",
                                        "library_action": {"mode": "replace", "file_id": 1}})
    assert r.status_code == 403 and "Nahradit" in r.json()["detail"]
    r = bro.post("/api/download", json={"file_ident": "x", "source": "webshare", "tmdb_id": 1, "title": "M",
                                        "target_folder": "/etc"})
    assert r.status_code == 403

    # the admin allows deleting → takes effect at once
    uid = [u for u in client.get("/api/auth/users").json() if u["username"] == "bracho"][0]["id"]
    client.patch(f"/api/auth/users/{uid}", json={"permissions": ["library.view", "library.delete", "bogus"]})
    assert bro.get("/api/auth/me").json()["permissions"] == ["library.delete", "library.view"]
    assert bro.delete("/api/library/movies/999/file").status_code != 403
    # blocked user is signed out
    client.patch(f"/api/auth/users/{uid}", json={"disabled": True})
    assert bro.get("/api/auth/me").status_code == 401


def test_cross_site_requests_are_refused(client):
    _setup_admin(client)
    r = client.post("/api/wanted/check", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/auth/login", json={"username": "shano", "password": "admin-pass-1"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_last_admin_stays_and_password_change_signs_out_elsewhere(client):
    _setup_admin(client)
    me = client.get("/api/auth/me").json()
    assert client.patch(f"/api/auth/users/{me['id']}", json={"role": "user"}).status_code == 400
    assert client.delete(f"/api/auth/users/{me['id']}").status_code == 400

    other = TestClient(_app())
    other.post("/api/auth/login", json={"username": "shano", "password": "admin-pass-1"})
    assert len(client.get("/api/auth/sessions").json()) == 2
    r = client.post("/api/auth/password", json={"current": "admin-pass-1", "new": "admin-pass-2"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200      # this browser got a new session
    assert other.get("/api/auth/me").status_code == 401       # the other one is out
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_login_brake(client):
    _setup_admin(client)
    c = TestClient(_app())
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "shano", "password": "bad-bad-bad"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "shano", "password": "admin-pass-1"})
    assert r.status_code == 429


def test_password_of_seven_characters_is_enough():
    from app.core.auth import password_problem
    assert password_problem("abcdefg") is None
    assert password_problem("abcdef") == "Heslo musí mít aspoň 7 znaků"
