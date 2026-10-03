"""„Neznám název" — the user describes a film or a show in their own words ("cestování časem, kluk s
DeLoreanem"), Groq guesses the titles and TMDB finds them; the user can say more ("ne, novější, seriál") and
the guesses follow the whole talk.

Only with a Groq key. Groq's free tier has daily limits that the other AI features share — a user has
``DAILY_PER_USER`` questions a day, an admin is not limited (and sees what is left of the key's own limits).
"""

import asyncio
import json
import logging
import re
from datetime import date

import httpx

from app.clients import groq_quota
from app.clients.groq_scorer import GROQ_API_URL, _REASONING_PARAMS
from app.clients.tmdb import TMDBClient

logger = logging.getLogger(__name__)

DAILY_PER_USER = 10
MAX_TURNS = 8                  # messages of the talk sent (the newest)
MAX_CHARS = 600                # of one message
MAX_GUESSES = 8

AI_USAGE = """
CREATE TABLE IF NOT EXISTS ai_usage (
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    feature TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, feature)
);
"""

SYSTEM = (
    "You identify films and TV shows from a vague description by a viewer (often Czech or Slovak): the plot, "
    "a scene, actors, when they saw it. Think of every real title that fits, most likely first. Answer only JSON: "
    '{"guesses": [{"title": "original or English title", "year": 1985, "type": "movie" or "tv", '
    '"why": "one short sentence in Czech why it fits"}], "ask": "one short question in Czech that would tell the '
    'guesses apart, or empty"}. At most 8 guesses, only titles that really exist. When the viewer rejects a title, '
    "do not offer it again."
)


async def usage(db, user_id: int) -> int:
    """This user's questions today."""
    row = await (await db.execute("SELECT COALESCE(SUM(count), 0) FROM ai_usage WHERE day = ? AND user_id = ? "
                                  "AND feature = 'describe'", (date.today().isoformat(), user_id))).fetchone()
    return row[0]


def left(mine: int, admin: bool = False) -> int | None:
    """Questions left today; None = not limited (an admin)."""
    return None if admin else max(0, DAILY_PER_USER - mine)


async def count(db, user_id: int) -> None:
    await db.execute("INSERT INTO ai_usage (day, user_id, feature, count) VALUES (?, ?, 'describe', 1) "
                     "ON CONFLICT(day, user_id, feature) DO UPDATE SET count = count + 1",
                     (date.today().isoformat(), user_id))
    await db.commit()


def _messages(talk: list[dict]) -> list[dict]:
    out = []
    for m in talk[-MAX_TURNS:]:
        text = str(m.get("content") or "").strip()[:MAX_CHARS]
        if text:
            out.append({"role": "assistant" if m.get("role") == "assistant" else "user", "content": text})
    return out


def _parse(content: str) -> tuple[list[dict], str]:
    start, end = content.find("{"), content.rfind("}")
    data = json.loads(content[start:end + 1])
    guesses = []
    for g in data.get("guesses") or []:
        if not isinstance(g, dict) or not str(g.get("title") or "").strip():
            continue
        m = re.search(r"\d{4}", str(g.get("year") or ""))
        guesses.append({"title": str(g["title"]).strip(), "year": int(m.group(0)) if m else None,
                        "type": "tv" if str(g.get("type") or "").lower() in ("tv", "series", "show") else "movie",
                        "why": str(g.get("why") or "").strip()[:200]})
    return guesses[:MAX_GUESSES], str(data.get("ask") or "").strip()[:300]


async def ask_groq(cfg: dict, talk: list[dict]) -> tuple[list[dict], str]:
    model = cfg.get("groq_model") or ""
    body = {"model": model, "temperature": 0.3, "max_tokens": 1500,
            "messages": [{"role": "system", "content": SYSTEM}, *_messages(talk)]}
    for prefix, params in _REASONING_PARAMS.items():
        if model.startswith(prefix):
            body.update(params)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(GROQ_API_URL, json=body, headers={
            "Authorization": f"Bearer {cfg['groq_api_key']}", "Content-Type": "application/json"})
    groq_quota.note(resp)
    if resp.status_code == 429:
        raise ValueError("Groq má teď plno (limit) — zkus to za chvíli")
    resp.raise_for_status()
    payload = resp.json()
    logger.info("Groq %s describe, tokens=%s", model, (payload.get("usage") or {}).get("total_tokens"))
    try:
        return _parse(payload["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        raise ValueError("AI odpověděla nesrozumitelně — zkus to popsat jinak") from e


def _best(found: list, year: int | None):
    """The search result of the guessed year (±1), else the first one when no year was guessed."""
    if year:
        for m in found:
            if m.year.isdigit() and abs(int(m.year) - year) <= 1:
                return m
        return None
    return found[0] if found else None


async def resolve(cfg: dict, locale: str, guesses: list[dict]) -> list[dict]:
    """Each guess as a TMDB title (in the user's language, with a poster); guesses TMDB does not know drop out."""
    client = TMDBClient(cfg["tmdb_api_key"])

    async def one(g: dict):
        try:
            search = client.search_tv if g["type"] == "tv" else client.search_movie
            hit = _best(await search(g["title"], language=locale), g["year"])
            if not hit:            # the model mixes films and shows up now and then
                other = client.search_movie if g["type"] == "tv" else client.search_tv
                hit = _best(await other(g["title"], language=locale), g["year"])
        except Exception as e:
            logger.info("Describe: TMDB '%s' failed: %s", g["title"], e)
            return None
        return {**hit.model_dump(), "why": g["why"]} if hit else None

    try:
        found = await asyncio.gather(*(one(g) for g in guesses))
    finally:
        await client.close()
    out, seen = [], set()
    for m in found:
        if m and (m["media_type"], m["tmdb_id"]) not in seen:
            seen.add((m["media_type"], m["tmdb_id"]))
            out.append(m)
    return out
