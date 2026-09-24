"""Polite access to external services that do not want bursts (WebShare file_info,
FastShare file pages): limited concurrency, a minimum gap between request starts,
and a cool-down when the service answers 403/429.

    throttle = Throttle("webshare", concurrency=2, interval=0.3)
    if throttle.cooling_down: return None
    async with throttle.slot():
        resp = await client.get(...)
    if resp.status_code in (403, 429): throttle.trip()
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class Throttle:
    def __init__(self, name: str, concurrency: int, interval: float, cooldown: float = 300.0) -> None:
        self.name = name
        self.interval = interval
        self.cooldown = cooldown
        self._concurrency = concurrency
        self._slots: asyncio.Semaphore | None = None
        self._schedule: asyncio.Lock | None = None
        self._last_start = 0.0
        self._cooling_until = 0.0

    def _init(self) -> None:
        # created lazily inside the running loop (module import happens before it exists)
        if self._slots is None:
            self._slots = asyncio.Semaphore(self._concurrency)
            self._schedule = asyncio.Lock()

    @property
    def cooling_down(self) -> bool:
        return time.monotonic() < self._cooling_until

    def trip(self, seconds: float | None = None) -> None:
        """The service refused us — leave it alone for a while."""
        self._cooling_until = time.monotonic() + (seconds or self.cooldown)
        logger.warning("%s refused requests — pausing detail requests for %.0f s", self.name, seconds or self.cooldown)

    @asynccontextmanager
    async def slot(self):
        self._init()
        async with self._slots:
            # starts are scheduled one at a time, so two waiting requests never fire together
            async with self._schedule:
                wait = self._last_start + self.interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_start = time.monotonic()
            yield
