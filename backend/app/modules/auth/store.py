"""Users table access (the table itself is core, app/core/auth.py)."""

import asyncio
import json
import re

from app.core.auth import ADMIN, USER, User, end_user_sessions, hash_password, user_from_row
from app.db import get_db

USERNAME = re.compile(r"^[\w.@-]{2,40}$")


def username_problem(name: str) -> str | None:
    if not USERNAME.match(name):
        return "Jméno: 2–40 znaků, písmena, číslice, . _ - @"
    return None


def _row_dict(row) -> dict:
    user = user_from_row(row)
    return {**user.public(), "disabled": bool(row["disabled"]), "created_at": row["created_at"],
            "last_login": row["last_login"]}


async def count_users() -> int:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        return (await cursor.fetchone())[0]
    finally:
        await db.close()


async def get_row(*, user_id: int | None = None, username: str | None = None):
    db = await get_db()
    try:
        if user_id is not None:
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        else:
            cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        return await cursor.fetchone()
    finally:
        await db.close()


async def list_users() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM users ORDER BY role = 'admin' DESC, username COLLATE NOCASE")
        return [_row_dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_user(user_id: int) -> dict | None:
    row = await get_row(user_id=user_id)
    return _row_dict(row) if row else None


async def create_user(username: str, password: str, role: str = USER, permissions: list[str] | None = None) -> int:
    password_hash = await asyncio.to_thread(hash_password, password)
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO users (username, password_hash, role, permissions) VALUES (?, ?, ?, ?)",
            (username, password_hash, role if role in (ADMIN, USER) else USER, json.dumps(permissions or [])))
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def active_admins() -> list[int]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM users WHERE role = 'admin' AND disabled = 0")
        return [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()


async def update_user(user_id: int, *, role: str | None = None, permissions: list[str] | None = None,
                      disabled: bool | None = None, password: str | None = None) -> None:
    sets, args = [], []
    if role is not None:
        sets.append("role = ?")
        args.append(role)
    if permissions is not None:
        sets.append("permissions = ?")
        args.append(json.dumps(permissions))
    if disabled is not None:
        sets.append("disabled = ?")
        args.append(int(disabled))
    if password is not None:
        sets.append("password_hash = ?")
        args.append(await asyncio.to_thread(hash_password, password))
    if not sets:
        return
    db = await get_db()
    try:
        await db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*args, user_id))
        await db.commit()
    finally:
        await db.close()
    if disabled or password is not None:
        await end_user_sessions(user_id)


async def delete_user(user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


async def list_sessions(user: User) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT token_hash, remember, created_at, last_seen, expires_at, ip, user_agent FROM sessions "
            "WHERE user_id = ? AND expires_at > datetime('now') ORDER BY last_seen DESC", (user.id,))
        return [{"id": r["token_hash"][:16], "current": r["token_hash"] == user.session,
                 "remember": bool(r["remember"]), "created_at": r["created_at"], "last_seen": r["last_seen"],
                 "expires_at": r["expires_at"], "ip": r["ip"], "user_agent": r["user_agent"]}
                for r in await cursor.fetchall()]
    finally:
        await db.close()


async def end_session_by_id(user_id: int, short_id: str) -> bool:
    if len(short_id) != 16:
        return False
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM sessions WHERE user_id = ? AND substr(token_hash, 1, 16) = ?",
                                  (user_id, short_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
