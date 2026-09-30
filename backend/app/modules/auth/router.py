"""Sign-in, own account, and user management (admin)."""

import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.auth import (ADMIN, ADMIN_ONLY, COOKIE, THROTTLE, USER, User, all_permissions, check_origin,
                           check_password, clean_permissions, clear_session_cookie, client_ip, create_session,
                           default_permissions, end_session, end_user_sessions, lookup_session, password_problem,
                           require, set_session_cookie, user_from_row)
from app.modules.auth import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# One-time code for creating the first admin, printed to the backend log (a fresh install
# must not be claimable by whoever opens the page first).
_setup_token: str | None = None


async def prepare_setup() -> None:
    global _setup_token
    if await store.count_users() == 0:
        _setup_token = secrets.token_urlsafe(12)
        logger.warning("No user yet - create the admin in the web UI with this setup code: %s", _setup_token)
    else:
        _setup_token = None


def _bad(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, message)


# ── signing in ──

@router.get("/status")
async def status(request: Request) -> dict:
    """Public: is there any user yet, and who is signed in (without failing when nobody is)."""
    user = None
    token = request.cookies.get(COOKIE)
    if token:
        found = await lookup_session(token)
        user = found[0].public() if found else None
    return {"setup_required": await store.count_users() == 0, "user": user}


class SetupBody(BaseModel):
    code: str
    username: str
    password: str
    remember: bool = False


@router.post("/setup")
async def setup(body: SetupBody, request: Request, response: Response) -> dict:
    """Creates the first admin — only while there is no user, and only with the code from the log."""
    check_origin(request)
    if await store.count_users() > 0:
        raise _bad("Správce už existuje", 409)
    ip = client_ip(request)
    wait = THROTTLE.wait_s(f"ip:{ip}")
    if wait:
        raise _bad(f"Příliš mnoho pokusů, zkus to za {wait} s", 429)
    if not _setup_token or not hmac.compare_digest(body.code.strip(), _setup_token):
        THROTTLE.failed(f"ip:{ip}")
        raise _bad("Neplatný kód z logu backendu", 403)
    problem = store.username_problem(body.username.strip()) or password_problem(body.password)
    if problem:
        raise _bad(problem)
    user_id = await store.create_user(body.username.strip(), body.password, ADMIN)
    await prepare_setup()
    token = await create_session(user_id, body.remember, ip, request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token, body.remember)
    logger.info("Admin '%s' created", body.username.strip())
    return {"user": (await store.get_user(user_id))}


class LoginBody(BaseModel):
    username: str
    password: str
    remember: bool = False


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    check_origin(request)
    ip = client_ip(request)
    name = body.username.strip()
    keys = (f"ip:{ip}", f"user:{name.lower()}")
    wait = THROTTLE.wait_s(*keys)
    if wait:
        raise _bad(f"Příliš mnoho neúspěšných pokusů, zkus to za {wait} s", 429)
    row = await store.get_row(username=name) if name else None
    ok = await check_password(body.password, row["password_hash"] if row else None)
    if not ok or row["disabled"]:
        THROTTLE.failed(*keys)
        logger.warning("Failed sign-in for '%s' from %s", name[:40], ip)
        raise _bad("Špatné jméno nebo heslo", 401)
    THROTTLE.succeeded(*keys)
    token = await create_session(row["id"], body.remember, ip, request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token, body.remember)
    return {"user": user_from_row(row).public()}


@router.post("/logout")
async def logout(request: Request, response: Response, user: User = Depends(require())) -> dict:
    await end_session(user.session)
    clear_session_cookie(response, request)
    return {"ok": True}


# ── own account ──

@router.get("/me")
async def me(user: User = Depends(require())) -> dict:
    return user.public()


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/password")
async def change_password(body: PasswordBody, request: Request, response: Response,
                          user: User = Depends(require())) -> dict:
    """Signs out everywhere else; this browser gets a fresh session."""
    row = await store.get_row(user_id=user.id)
    keys = (f"user:{user.username.lower()}",)
    wait = THROTTLE.wait_s(*keys)
    if wait:
        raise _bad(f"Příliš mnoho pokusů, zkus to za {wait} s", 429)
    if not await check_password(body.current, row["password_hash"]):
        THROTTLE.failed(*keys)
        raise _bad("Současné heslo nesedí", 403)
    problem = password_problem(body.new)
    if problem:
        raise _bad(problem)
    THROTTLE.succeeded(*keys)
    await store.update_user(user.id, password=body.new)   # ends all sessions, this one too
    token = await create_session(user.id, user.remember, client_ip(request), request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token, user.remember)
    return {"ok": True}


@router.get("/sessions")
async def sessions(user: User = Depends(require())) -> list[dict]:
    return await store.list_sessions(user)


@router.delete("/sessions/{session_id}")
async def end_one_session(session_id: str, user: User = Depends(require())) -> dict:
    if not await store.end_session_by_id(user.id, session_id):
        raise _bad("Přihlášení nenalezeno", 404)
    return {"ok": True}


@router.post("/sessions/end-others")
async def end_other_sessions(user: User = Depends(require())) -> dict:
    return {"ended": await end_user_sessions(user.id, keep=user.session)}


# ── users (admin) ──

@router.get("/permissions")
async def permissions(user: User = Depends(require())) -> list[dict]:
    return [{"name": p.name, "title": p.title, "default": p.default} for p in all_permissions()]


@router.get("/users")
async def users(user: User = Depends(require(ADMIN_ONLY))) -> list[dict]:
    return await store.list_users()


class NewUser(BaseModel):
    username: str
    password: str
    role: str = USER
    permissions: list[str] | None = None   # None = the defaults


@router.post("/users")
async def add_user(body: NewUser, user: User = Depends(require(ADMIN_ONLY))) -> dict:
    name = body.username.strip()
    problem = store.username_problem(name) or password_problem(body.password)
    if problem:
        raise _bad(problem)
    if body.role not in (ADMIN, USER):
        raise _bad("Neznámá role")
    if await store.get_row(username=name):
        raise _bad("Uživatel s tímto jménem už existuje", 409)
    perms = clean_permissions(default_permissions() if body.permissions is None else body.permissions)
    user_id = await store.create_user(name, body.password, body.role, perms)
    logger.info("User '%s' (%s) created by '%s'", name, body.role, user.username)
    return await store.get_user(user_id)


class UserChange(BaseModel):
    role: str | None = None
    permissions: list[str] | None = None
    disabled: bool | None = None
    password: str | None = None


@router.patch("/users/{user_id}")
async def change_user(user_id: int, body: UserChange, user: User = Depends(require(ADMIN_ONLY))) -> dict:
    target = await store.get_row(user_id=user_id)
    if not target:
        raise _bad("Uživatel nenalezen", 404)
    if body.role is not None and body.role not in (ADMIN, USER):
        raise _bad("Neznámá role")
    losing_admin = target["role"] == ADMIN and not target["disabled"] and (
        (body.role is not None and body.role != ADMIN) or body.disabled)
    if losing_admin and await store.active_admins() == [user_id]:
        raise _bad("Musí zůstat aspoň jeden aktivní správce")
    if user_id == user.id and body.disabled:
        raise _bad("Sám sebe zablokovat nemůžeš")
    if body.password is not None:
        problem = password_problem(body.password)
        if problem:
            raise _bad(problem)
    await store.update_user(user_id, role=body.role,
                            permissions=None if body.permissions is None else clean_permissions(body.permissions),
                            disabled=body.disabled, password=body.password)
    return await store.get_user(user_id)


@router.delete("/users/{user_id}")
async def remove_user(user_id: int, user: User = Depends(require(ADMIN_ONLY))) -> dict:
    target = await store.get_row(user_id=user_id)
    if not target:
        raise _bad("Uživatel nenalezen", 404)
    if user_id == user.id:
        raise _bad("Sám sebe smazat nemůžeš")
    if target["role"] == ADMIN and await store.active_admins() == [user_id]:
        raise _bad("Musí zůstat aspoň jeden aktivní správce")
    await store.delete_user(user_id)
    logger.info("User '%s' deleted by '%s'", target["username"], user.username)
    return {"ok": True}
