"""Module definition.

Every feature of Lumina lives in its own package under ``app/modules/<name>/``
and exposes a ``module = Module(...)`` object in its ``__init__.py``.

A module owns its API routers, its database tables (via ``migrations``) and its
background work. Modules never import each other; they share data through the
core (``app.db``, ``app.config``) and react to each other through events
(``app.core.events``).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import aiosqlite
from fastapi import APIRouter

EventHandler = Callable[[dict], Awaitable[None]]
# A migration is either an SQL script or an async callable that gets the open DB connection.
Migration = str | Callable[[aiosqlite.Connection], Awaitable[None]]
Hook = Callable[[], Awaitable[None] | None]


@dataclass
class Subscription:
    event: str
    handler: EventHandler
    # Lower runs first. Handlers of one event run sequentially, so a handler can
    # rely on changes made to the payload by handlers with a lower priority.
    priority: int = 100


@dataclass
class Module:
    name: str
    title: str
    # Load order: modules with lower order are migrated/started first.
    order: int = 100
    # Required modules cannot be disabled via the ``disabled_modules`` setting.
    required: bool = False
    routers: list[APIRouter] = field(default_factory=list)
    # Ordered list; the position (1-based) is the migration version. Never edit or
    # reorder an already released migration — append a new one instead.
    migrations: list[Migration] = field(default_factory=list)
    subscriptions: list[Subscription] = field(default_factory=list)
    on_startup: list[Hook] = field(default_factory=list)
    on_shutdown: list[Hook] = field(default_factory=list)
