"""Accounts, sign-in sessions and permissions (decisions/0006).

- Passwords are hashed with scrypt (stdlib, memory-hard); nothing readable is stored.
- A session is a random token in an HttpOnly cookie; the DB keeps only its SHA-256, so a leaked
  DB does not let anyone sign in. "Stay signed in" = 30 days, extended while used; otherwise a
  browser-session cookie that also expires after 12 hours without use.
- Modules declare permissions (``Module.permissions``) and guard endpoints with
  ``Depends(require("name"))``. Every module router already needs a signed-in user
  (main.py); ``require`` adds the permission check. Admins can do everything.
- Changing requests are only accepted from the app's own origin (CSRF), the cookie is SameSite=Lax.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

from app.core.module import Module, Permission
from app.db import get_db

COOKIE = "lumina_session"
REMEMBER = timedelta(days=30)
IDLE = timedelta(hours=12)
MIN_PASSWORD = 7
ADMIN, USER = "admin", "user"
# pseudo permission only admins have (user management)
ADMIN_ONLY = "admin"

USERS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    permissions TEXT NOT NULL DEFAULT '[]',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    remember INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


# ── passwords ──

_N, _R, _P = 2**15, 8, 1          # ~32 MB and ~50–100 ms per hash
_MAXMEM = 64 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(key)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


_DUMMY_HASH: str | None = None


async def check_password(password: str, stored: str | None) -> bool:
    """Verify off the event loop; an unknown user costs the same time as a wrong password."""
    global _DUMMY_HASH
    if stored is None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = await asyncio.to_thread(hash_password, secrets.token_hex(8))
        await asyncio.to_thread(verify_password, password, _DUMMY_HASH)
        return False
    return await asyncio.to_thread(verify_password, password, stored)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Heslo musí mít aspoň {MIN_PASSWORD} znaků"
    if len(password) > 256:
        return "Heslo je příliš dlouhé"
    return None


# ── permissions ──

_PERMISSIONS: dict[str, Permission] = {}


def register_permissions(modules: list[Module]) -> None:
    _PERMISSIONS.clear()
    for module in modules:
        for perm in module.permissions:
            _PERMISSIONS[perm.name] = perm


def all_permissions() -> list[Permission]:
    return list(_PERMISSIONS.values())


def default_permissions() -> list[str]:
    return [p.name for p in _PERMISSIONS.values() if p.default]


def clean_permissions(names) -> list[str]:
    """Only known permissions are stored (never the admin pseudo permission)."""
    return sorted({n for n in names if n in _PERMISSIONS})


@dataclass
class User:
    id: int
    username: str
    role: str
    permissions: frozenset[str]
    session: str = ""   # hash of the token of the current request's session
    remember: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    def can(self, permission: str) -> bool:
        return self.is_admin or (permission != ADMIN_ONLY and permission in self.permissions)

    def public(self) -> dict:
        perms = [p.name for p in all_permissions()] if self.is_admin else sorted(self.permissions)
        return {"id": self.id, "username": self.username, "role": self.role,
                "is_admin": self.is_admin, "permissions": perms}


def user_from_row(row) -> User:
    try:
        perms = frozenset(json.loads(row["permissions"] or "[]"))
    except ValueError:
        perms = frozenset()
    return User(id=row["id"], username=row["username"], role=row["role"], permissions=perms)


# ── sessions ──

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(user_id: int, remember: bool, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE expires_at < ?", (_fmt(now),))
        await db.execute(
            "INSERT INTO sessions (token_hash, user_id, remember, created_at, last_seen, expires_at, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (token_hash(token), user_id, int(remember), _fmt(now), _fmt(now),
             _fmt(now + (REMEMBER if remember else IDLE)), ip[:64], user_agent[:300]))
        await db.execute("UPDATE users SET last_login = ? WHERE id = ?", (_fmt(now), user_id))
        await db.commit()
    finally:
        await db.close()
    return token


async def lookup_session(token: str) -> tuple[User, bool] | None:
    """The signed-in user of a token, or None. Extends the session while it is used;
    the bool says the cookie should be sent again (a remembered session got a new expiry)."""
    key = token_hash(token)
    now = _now()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT s.remember, s.expires_at, s.last_seen, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?", (key,))
        row = await cursor.fetchone()
        if not row:
            return None
        if row["disabled"] or _parse(row["expires_at"]) < now:
            await db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
            await db.commit()
            return None
        refresh_cookie = False
        # write at most every 5 minutes — not on every request
        if now - _parse(row["last_seen"]) > timedelta(minutes=5):
            expires = now + (REMEMBER if row["remember"] else IDLE)
            refresh_cookie = bool(row["remember"])
            await db.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                             (_fmt(now), _fmt(expires), key))
            await db.commit()
        user = user_from_row(row)
        user.session = key
        user.remember = bool(row["remember"])
        return user, refresh_cookie
    finally:
        await db.close()


async def end_session(key: str) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
        await db.commit()
    finally:
        await db.close()


async def end_user_sessions(user_id: int, keep: str | None = None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep or ""))
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()


# ── requests ──

def _from_proxy(request: Request) -> bool:
    """Forwarded headers are trusted only from a local reverse proxy (nginx, docker network)."""
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("testclient", "localhost")
    return ip.is_private or ip.is_loopback


def client_ip(request: Request) -> str:
    if _from_proxy(request):
        real = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if real.strip():
            return real.strip()
    return request.client.host if request.client else ""


def _is_https(request: Request) -> bool:
    if _from_proxy(request) and request.headers.get("x-forwarded-proto") == "https":
        return True
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str, remember: bool) -> None:
    response.set_cookie(COOKIE, token, max_age=int(REMEMBER.total_seconds()) if remember else None,
                        httponly=True, secure=_is_https(request), samesite="lax", path="/")


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=_is_https(request), samesite="lax")


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def check_origin(request: Request) -> None:
    """A changing request must come from the app's own pages (CSRF). Browsers send Origin with
    every such fetch; clients without it (curl, scripts) carry no browser cookies by accident."""
    if request.method in _SAFE_METHODS:
        return
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return
    netloc = urlsplit(origin).netloc.lower()
    allowed = {request.headers.get("host", "").lower()}
    if _from_proxy(request) and request.headers.get("x-forwarded-host"):
        allowed.add(request.headers["x-forwarded-host"].lower())
    if netloc not in allowed:
        raise HTTPException(403, "Požadavek z cizí stránky byl odmítnut")


async def authenticate(request: Request, response: Response) -> User:
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    check_origin(request)
    token = request.cookies.get(COOKIE)
    found = await lookup_session(token) if token else None
    if not found:
        raise HTTPException(401, "Nejsi přihlášený")
    user, refresh = found
    if refresh:
        set_session_cookie(response, request, token, remember=True)
    request.state.user = user
    return user


def require(permission: str | None = None):
    """FastAPI dependency: a signed-in user, and with ``permission`` one who has it.
    Returns the user, so an endpoint can take it: ``user: User = Depends(require("download"))``."""
    async def dependency(request: Request, response: Response) -> User:
        user = await authenticate(request, response)
        if permission and not user.can(permission):
            perm = _PERMISSIONS.get(permission)
            what = perm.title if perm else "jen pro správce"
            raise HTTPException(403, f"Na tohle nemáš oprávnění ({what})")
        return user

    dependency.permission = permission  # type: ignore[attr-defined]  — read by the route guard test
    return dependency


def current_user(request: Request) -> User | None:
    """The user of this request (set by the login check) — e.g. to record who added something."""
    return getattr(request.state, "user", None)


# ── brute-force brake ──

class LoginThrottle:
    """After 5 failed sign-ins (per IP and per username) within 15 minutes each further attempt
    has to wait longer: 30 s, 1 min, 2 min … up to 15 min. In memory — a restart resets it."""

    WINDOW = 15 * 60
    FREE = 5
    MAX_WAIT = 15 * 60

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        fails = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        if fails:
            self._fails[key] = fails
        else:
            self._fails.pop(key, None)
        return fails

    def wait_s(self, *keys: str, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        wait = 0.0
        for key in keys:
            fails = self._recent(key, now)
            if len(fails) >= self.FREE:
                delay = min(self.MAX_WAIT, 30 * 2 ** (len(fails) - self.FREE))
                wait = max(wait, delay - (now - fails[-1]))
        return max(0, int(wait + 0.999))

    def failed(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            self._recent(key, now)
            self._fails.setdefault(key, []).append(now)

    def succeeded(self, *keys: str) -> None:
        for key in keys:
            self._fails.pop(key, None)


THROTTLE = LoginThrottle()
