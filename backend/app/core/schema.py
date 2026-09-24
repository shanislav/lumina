"""Tables shared by all modules.

- settings:    key/value app configuration
- sources:     configured source plugins (WebShare, FastShare, Jackett, ...)
- automations: on/off switch + JSON config of post-processing modules
               (each such module seeds its own row in its migrations)
"""

from app.core.module import Module

CORE_V1 = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    config TEXT NOT NULL DEFAULT '{}'
);
"""

CORE = Module(name="core", title="Core", order=0, required=True, migrations=[CORE_V1])


def seed_automation(type_name: str, name: str) -> str:
    """Migration that registers an automation row (disabled by default)."""
    type_sql = type_name.replace("'", "''")
    name_sql = name.replace("'", "''")
    return (
        "INSERT OR IGNORE INTO automations (type, name, enabled) "
        f"VALUES ('{type_sql}', '{name_sql}', 0);"
    )
