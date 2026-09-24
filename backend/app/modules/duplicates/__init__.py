"""Duplicates: finds multiple files of the same movie in the library (simple or AI grouping)."""

from app.core.migrations import add_column
from app.core.module import Module
from app.modules.duplicates.router import router

SCANNED_FILES_V1 = """
CREATE TABLE IF NOT EXISTS scanned_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    year TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    quality TEXT NOT NULL DEFAULT 'unknown',
    language TEXT NOT NULL DEFAULT '-',
    modified_at TEXT NOT NULL DEFAULT '',
    ai_group TEXT NOT NULL DEFAULT '',
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scanned_norm_title ON scanned_files (normalized_title, year);
"""

module = Module(
    name="duplicates",
    title="Duplicity",
    order=30,
    routers=[router],
    migrations=[
        SCANNED_FILES_V1,
        add_column("scanned_files", "ai_group", "TEXT NOT NULL DEFAULT ''"),
    ],
)
