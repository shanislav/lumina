"""What is left of the Groq key's limits — Groq sends it with every answer (``x-ratelimit-*`` headers):
requests per day and tokens per minute. Every Groq call of Lumina notes it here; kept in a small file next to
the database so a restart does not forget it."""

import json
import logging
import re
import time

from app.db import DB_PATH

logger = logging.getLogger(__name__)

FILE = DB_PATH.parent / "groq_quota.json"
_last: dict = {}


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _seconds(value) -> float:
    """Groq's reset times: "2m59.56s", "7.66s", "1h2m3s", "120ms"."""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", str(value or "")):
        total += float(num) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total


def note(resp) -> None:
    """Remember the limits from a Groq response (any status — a 429 carries them too)."""
    h = getattr(resp, "headers", None) or {}
    if "x-ratelimit-limit-requests" not in h:
        return
    now = time.time()
    _last.clear()
    _last.update({
        "requests_limit": _int(h.get("x-ratelimit-limit-requests")),
        "requests_left": _int(h.get("x-ratelimit-remaining-requests")),
        "requests_reset_at": now + _seconds(h.get("x-ratelimit-reset-requests")),
        "tokens_limit": _int(h.get("x-ratelimit-limit-tokens")),
        "tokens_left": _int(h.get("x-ratelimit-remaining-tokens")),
        "tokens_reset_at": now + _seconds(h.get("x-ratelimit-reset-tokens")),
        "at": now,
    })
    try:
        FILE.write_text(json.dumps(_last))
    except OSError as e:
        logger.debug("Groq quota not saved: %s", e)


def get() -> dict | None:
    """The limits as last seen; what has reset since is shown full again. None before the first call."""
    if not _last:
        try:
            _last.update(json.loads(FILE.read_text()))
        except (OSError, ValueError):
            return None
    now = time.time()
    q = dict(_last)
    if q.get("requests_limit") is not None and now >= q.get("requests_reset_at", 0):
        q["requests_left"] = q["requests_limit"]
    if q.get("tokens_limit") is not None and now >= q.get("tokens_reset_at", 0):
        q["tokens_left"] = q["tokens_limit"]
    return q
