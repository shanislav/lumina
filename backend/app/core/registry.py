"""Discovers modules in ``app/modules`` and wires them into the app."""

import importlib
import logging
import pkgutil
import sqlite3

import app.modules
from app.core import events
from app.core.module import Module
from app.db import DB_PATH

logger = logging.getLogger(__name__)

# Setting with a comma-separated list of module names that should not be loaded.
DISABLED_SETTING = "disabled_modules"


def discover() -> list[Module]:
    """Import every package in app/modules and collect its ``module`` object."""
    found: list[Module] = []
    for info in pkgutil.iter_modules(app.modules.__path__):
        if not info.ispkg:
            continue
        package = importlib.import_module(f"app.modules.{info.name}")
        module = getattr(package, "module", None)
        if not isinstance(module, Module):
            logger.warning("Package app.modules.%s has no Module definition, skipped", info.name)
            continue
        found.append(module)

    names = [m.name for m in found]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise RuntimeError(f"Duplicate module names: {sorted(duplicates)}")
    return sorted(found, key=lambda m: (m.order, m.name))


def read_disabled() -> set[str]:
    """Read disabled module names synchronously (needed before the app starts)."""
    if not DB_PATH.exists():
        return set()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (DISABLED_SETTING,)
            ).fetchone()
    except sqlite3.Error:
        return set()
    if not row or not row[0]:
        return set()
    return {name.strip() for name in row[0].split(",") if name.strip()}


def active(modules: list[Module], disabled: set[str]) -> list[Module]:
    return [m for m in modules if m.required or m.name not in disabled]


def register_subscriptions(modules: list[Module]) -> None:
    events.clear()
    for module in modules:
        for sub in module.subscriptions:
            events.subscribe(sub.event, sub.handler, sub.priority, owner=module.name)
