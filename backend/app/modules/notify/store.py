"""Notifications: what happened while the user was away — a download landed or failed, the wanted list or the
nightly check found something, the TV automation found or started episodes.

Other modules know nothing about it: it listens to their events (decisions/0005: results as events, a
notification module subscribes). Each notification names the permission a user needs to see it. A user's
"seen" is the id of the newest one they opened the list at. The same news again (the nightly check finding the
same better version) is not repeated for ``REPEAT_DAYS``; downloads of one show close together merge into one.
Kept ``KEEP_DAYS``. Later a channel (Telegram, e-mail, ntfy) can send them out — the user picks one.
"""

import json
from datetime import datetime, timedelta

from app.db import get_db

NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- download | download_failed | wanted | upgrade | series
    level TEXT NOT NULL DEFAULT 'info', -- info | ok | warn | error
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    permission TEXT NOT NULL DEFAULT '',
    dedup TEXT NOT NULL DEFAULT '',     -- the same news: not again for a while
    group_key TEXT NOT NULL DEFAULT '', -- downloads of one show: merged into one
    items TEXT NOT NULL DEFAULT '[]'    -- the merged ones ("S02E06", …)
);
CREATE INDEX IF NOT EXISTS notifications_dedup ON notifications (dedup);
CREATE TABLE IF NOT EXISTS notify_seen (
    user_id INTEGER PRIMARY KEY,
    last_id INTEGER NOT NULL DEFAULT 0
);
"""

REPEAT_DAYS = 14
MERGE_MINUTES = 60
KEEP_DAYS = 60
MAX_ITEMS_SHOWN = 8


def _now() -> datetime:
    return datetime.now()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _body(items: list[str], body: str) -> str:
    if not items:
        return body
    shown = ", ".join(items[:MAX_ITEMS_SHOWN]) + (f" a {len(items) - MAX_ITEMS_SHOWN} další" if len(items) > MAX_ITEMS_SHOWN else "")
    return f"{shown}{' · ' + body if body else ''}"


async def add(kind: str, title: str, body: str = "", *, level: str = "info", link: str = "", permission: str = "",
              dedup: str = "", group: str = "", item: str = "") -> int | None:
    """A notification; None when it is a repeat. ``group`` + ``item``: merged with the group's last one from
    the last hour (its items grow, it becomes unread again)."""
    now = _now()
    db = await get_db()
    try:
        if dedup:
            since = _fmt(now - timedelta(days=REPEAT_DAYS))
            if await (await db.execute("SELECT 1 FROM notifications WHERE dedup = ? AND created_at > ? LIMIT 1",
                                       (dedup, since))).fetchone():
                return None
        items = [item] if item else []
        if group:
            since = _fmt(now - timedelta(minutes=MERGE_MINUTES))
            old = await (await db.execute(
                "SELECT id, items FROM notifications WHERE group_key = ? AND created_at > ? AND hidden = 0 "
                "ORDER BY id DESC LIMIT 1",
                (group, since))).fetchone()
            if old:
                items = [*json.loads(old["items"] or "[]"), *items]
                await db.execute("DELETE FROM notifications WHERE id = ?", (old["id"],))
        cur = await db.execute(
            "INSERT INTO notifications (created_at, kind, level, title, body, link, permission, dedup, group_key, items) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_fmt(now), kind, level, title, _body(items, body), link, permission, dedup, group, json.dumps(items)))
        await db.execute("DELETE FROM notifications WHERE created_at < ?", (_fmt(now - timedelta(days=KEEP_DAYS)),))
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def listing(user, limit: int = 30) -> dict:
    """The newest notifications the user may see, and how many of them are new to them."""
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM notifications WHERE hidden = 0 ORDER BY id DESC LIMIT 300")).fetchall()
        seen = await (await db.execute("SELECT last_id FROM notify_seen WHERE user_id = ?", (user.id,))).fetchone()
    finally:
        await db.close()
    last = seen[0] if seen else 0
    mine = [r for r in rows if not r["permission"] or user.can(r["permission"])]
    items = [{"id": r["id"], "created_at": r["created_at"], "kind": r["kind"], "level": r["level"], "title": r["title"],
              "body": r["body"], "link": r["link"], "new": r["id"] > last} for r in mine[:limit]]
    return {"items": items, "unread": sum(1 for r in mine if r["id"] > last)}


async def clear() -> int:
    """The admin clears the history: hidden for everyone, kept for the repeat check (the same news is not told
    again), gone after ``KEEP_DAYS`` as every other one. Returns how many were hidden."""
    db = await get_db()
    try:
        cur = await db.execute("UPDATE notifications SET hidden = 1 WHERE hidden = 0")
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def mark_seen(user_id: int, last_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("INSERT INTO notify_seen (user_id, last_id) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                         "last_id = MAX(notify_seen.last_id, excluded.last_id)", (user_id, last_id))
        await db.commit()
    finally:
        await db.close()
