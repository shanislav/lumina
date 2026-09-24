"""Per-module database migrations.

Applied versions are stored in ``schema_migrations(module, version)``. Each module
lists its migrations in order; only the ones not applied yet are run.

Migrations must be safe on databases created before this mechanism existed, so
table creation uses ``CREATE TABLE IF NOT EXISTS`` and columns are added with
:func:`add_column`, which is a no-op when the column already exists.
"""

import logging

import aiosqlite

from app.core.module import Migration, Module

logger = logging.getLogger(__name__)


def add_column(table: str, column: str, declaration: str) -> Migration:
    async def _migrate(db: aiosqlite.Connection) -> None:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        if column not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    return _migrate


async def run_migrations(db: aiosqlite.Connection, modules: list[Module]) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            module TEXT NOT NULL,
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (module, version)
        )
        """
    )
    await db.commit()

    for module in modules:
        cursor = await db.execute(
            "SELECT version FROM schema_migrations WHERE module = ?", (module.name,)
        )
        applied = {row[0] for row in await cursor.fetchall()}

        for version, migration in enumerate(module.migrations, start=1):
            if version in applied:
                continue
            if isinstance(migration, str):
                await db.executescript(migration)
            else:
                await migration(db)
            await db.execute(
                "INSERT INTO schema_migrations (module, version) VALUES (?, ?)",
                (module.name, version),
            )
            await db.commit()
            logger.info("Applied migration %s v%d", module.name, version)
