import json
import logging

from app.db import get_db
from app.sources.base import BaseSource, SourceType

logger = logging.getLogger(__name__)

SOURCE_CLASSES: dict[SourceType, type[BaseSource]] = {}


def _ensure_classes() -> None:
    if SOURCE_CLASSES:
        return
    from app.sources.webshare import WebShareSource
    from app.sources.fastshare import FastShareSource
    from app.sources.jackett import JackettSource

    SOURCE_CLASSES[SourceType.WEBSHARE] = WebShareSource
    SOURCE_CLASSES[SourceType.FASTSHARE] = FastShareSource
    SOURCE_CLASSES[SourceType.JACKETT] = JackettSource


class SourceRegistry:
    _instance: "SourceRegistry | None" = None

    def __init__(self) -> None:
        if SourceRegistry._instance is not None:
            raise RuntimeError(
                "Use SourceRegistry.get() instead of creating a new instance"
            )
        self._sources: list[BaseSource] = []

    @classmethod
    def get(cls) -> "SourceRegistry":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def reload(self) -> None:
        """(Re)load all enabled sources from DB."""
        _ensure_classes()
        await self.close_all()

        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT id, type, name, config FROM sources WHERE enabled = 1"
            )
            rows = await cursor.fetchall()
        finally:
            await db.close()

        self._sources = []
        for row in rows:
            try:
                source_type = SourceType(row["type"])
                cls = SOURCE_CLASSES[source_type]
                config = json.loads(row["config"])
                self._sources.append(cls(source_id=row["id"], config=config))
                logger.info(
                    "Loaded source: %s (id=%d, type=%s)",
                    row["name"],
                    row["id"],
                    row["type"],
                )
            except Exception as e:
                logger.error(
                    "Failed to load source id=%d type=%s: %s",
                    row["id"],
                    row["type"],
                    e,
                )

    @property
    def sources(self) -> list[BaseSource]:
        return self._sources

    def get_source_by_id(self, source_id: int) -> BaseSource | None:
        return next((s for s in self._sources if s.source_id == source_id), None)

    async def close_all(self) -> None:
        for s in self._sources:
            try:
                await s.close()
            except Exception as e:
                logger.debug("Closing source %s failed: %s", s.source_id, e)
        self._sources = []
