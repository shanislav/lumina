"""„Neznám název" — the user describes a film or a show in their own words ("cestování časem, kluk s
DeLoreanem"), an AI guesses the titles and TMDB finds them; the user can say more ("ne, novější, seriál") and
the guesses follow the whole talk.

Groq's model alone remembers famous titles only (asked for "a man struck by a light becomes a genius and moves
things by thought" it never says Phenomenon). So it first turns the description into plot keywords, TMDB gives
the best-known titles tagged with them, and the model picks from those (with their plot) and its own memory.

Gemini (when it has a key) answers first — from its memory, which knows far more films than Groq's model (it
searches Google where the key allows it); one model out of its daily limit, the next Gemini model answers. Groq
answers only when no Gemini model can. An answer that only asks back (no guesses) is an answer too — Groq is not
asked to guess instead.
Only with an AI key. Groq's free tier has daily limits that the other AI features share — a user has
``DAILY_PER_USER`` questions a day, an admin is not limited (and sees what is left of the key's own limits).
"""

import asyncio
import json
import logging
import re
import time
from datetime import date

from app.clients import ai
from app.clients.tmdb import TMDBClient

logger = logging.getLogger(__name__)

DAILY_PER_USER = 10
MAX_TURNS = 8                  # messages of the talk sent (the newest)
MAX_CHARS = 600                # of one message
MAX_GUESSES = 8
MAX_KEYWORDS = 8
PER_KEYWORD = 20               # best-known titles of one keyword (films and shows each)
MAX_CANDIDATES = 35
OVERVIEW_CHARS = 160

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

SEARCH = (
    " Before answering, search the web for the scene or plot detail the viewer remembers (search in English too, "
    "e.g. 'film where a man learns a language in minutes'); trust what you find over your memory."
)

KEYWORDS = (
    "Turn a viewer's vague description of a film or TV show (any language) into up to 8 TMDB-style plot keywords "
    "in English: short tags such as 'telekinesis', 'time travel', 'genius', 'brain tumor', 'heist', 'small town'. "
    "Only what the description says, no title guesses. Answer only a JSON array of strings."
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


# the whole answer: the user rather waits for a good guess (Gemini's newest flash thinks ~45 s); the web server
# lets this one request take 180 s (nginx location /api/search/describe), everything else 60 s
DEADLINE_S = 150
GROQ_RESERVE_S = 25      # Gemini stops trying models while Groq still has this much time to answer
GEMINI_MODEL_S = 90      # one Gemini model may think this long here


async def _groq(cfg: dict, messages: list[dict], max_tokens: int, temperature: float = 0.3,
                deadline: float | None = None) -> str:
    """One question to Groq (the first message is the system one)."""
    answer = await ai.ask(cfg, "groq", messages[0]["content"], messages[1:], max_tokens=max_tokens,
                          temperature=temperature, deadline=deadline)
    return answer.text


async def plot_keywords(cfg: dict, talk: list[dict], deadline: float | None = None) -> list[str]:
    """TMDB-style plot keywords of everything the user said."""
    said = "\n".join(m["content"] for m in _messages(talk) if m["role"] == "user")
    content = await _groq(cfg, [{"role": "system", "content": KEYWORDS}, {"role": "user", "content": said}], 400, 0.2,
                          deadline=deadline)
    data = json.loads(content[content.find("["):content.rfind("]") + 1])
    return [str(k).strip() for k in data if str(k).strip()][:MAX_KEYWORDS]


async def candidates(cfg: dict, keywords: list[str]) -> list[str]:
    """Films and shows TMDB tags with those keywords — the best-known of each, the most keywords in common
    first: "Phenomenon (1996, film): An ordinary man sees a bright light…" for the model to choose from (it
    does not remember lesser-known titles by itself)."""
    client = TMDBClient(cfg["tmdb_api_key"])
    try:
        found = [k for k in await asyncio.gather(*(client.keyword(k) for k in keywords), return_exceptions=True)
                 if isinstance(k, dict)]
        lists = await asyncio.gather(*(client.by_keyword(k["id"], kind, "en-US") for k in found
                                       for kind in ("movie", "tv")), return_exceptions=True)
    finally:
        await client.close()
    hits: dict[tuple[str, int], int] = {}
    info: dict[tuple[str, int], str] = {}
    for i, items in enumerate(lists):
        if not isinstance(items, list):
            continue
        kind = "movie" if i % 2 == 0 else "tv"
        for item in items[:PER_KEYWORD]:
            key = (kind, item["id"])
            hits[key] = hits.get(key, 0) + 1
            year = (item.get("release_date") or item.get("first_air_date") or "")[:4]
            info[key] = (f"{item.get('title') or item.get('name')} ({year}, {'show' if kind == 'tv' else 'film'}): "
                         f"{(item.get('overview') or '')[:OVERVIEW_CHARS]}")
    best = sorted(hits, key=lambda k: -hits[k])[:MAX_CANDIDATES]
    logger.info("Describe: keywords %s → %d candidates", [k["name"] for k in found], len(best))
    return [info[k] for k in best]


async def ask_groq(cfg: dict, talk: list[dict], deadline: float | None = None) -> tuple[list[dict], str]:
    """Groq: plot keywords → TMDB's titles with them → the model picks from those and its own memory."""
    messages = _messages(talk)
    try:
        cands = await candidates(cfg, await plot_keywords(cfg, talk, deadline))
    except Exception as e:                  # the guesses go on without them
        logger.info("Describe: no TMDB candidates: %s", e)
        cands = []
    if cands and messages:
        last = messages[-1]
        messages[-1] = {**last, "content": last["content"] + "\n\nTMDB titles tagged with plot keywords of the "
                        "description (the right one may be among them, or not — then use your own):\n"
                        + "\n".join(f"- {c}" for c in cands)}
    content = await _groq(cfg, [{"role": "system", "content": SYSTEM}, *messages], 2000, deadline=deadline)
    try:
        return _parse(content)
    except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        raise ValueError("AI odpověděla nesrozumitelně — zkus to popsat jinak") from e


async def ask_gemini(cfg: dict, talk: list[dict], deadline: float | None = None) -> tuple[list[dict], str, bool, str]:
    """Gemini searches Google for the scene described (forums, lists "films where…") — finds titles a model
    does not remember. (guesses, question, did it search)"""
    answer = await ai.ask(cfg, "gemini", SYSTEM + SEARCH, _messages(talk), max_tokens=2000, search=True,
                          deadline=deadline, model_timeout=GEMINI_MODEL_S)
    try:
        guesses, question = _parse(answer.text)
    except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        raise ValueError("AI odpověděla nesrozumitelně — zkus to popsat jinak") from e
    return guesses, question, answer.searched, answer.label


async def ask(cfg: dict, talk: list[dict]) -> dict:
    """The AIs in the feature's order (Gemini first: it searches Google), the next one when one fails or
    guesses nothing. {guesses, ask, by, searched}"""
    who = ai.order(cfg, "describe")
    if not who:
        raise ValueError("AI není nastavená (Nastavení → AI: Groq nebo Gemini)")
    last: Exception | None = None
    deadline = time.monotonic() + DEADLINE_S
    for i, provider in enumerate(who):
        try:
            if provider == "gemini":
                # Groq after it: Gemini gives up in time for Groq to answer
                mine = deadline - (GROQ_RESERVE_S if "groq" in who[i + 1:] else 0)
                guesses, question, searched, by = await ask_gemini(cfg, talk, mine)
            else:
                (guesses, question), searched, by = await ask_groq(cfg, talk, deadline), False, ai.LABELS[provider]
        except Exception as e:                       # the other AI answers
            logger.warning("Describe: %s failed: %s", ai.LABELS[provider], str(e) or type(e).__name__)
            last = e
            continue
        if guesses or question or provider == who[-1]:
            return {"guesses": guesses, "ask": question, "by": by, "searched": searched}
    raise last if isinstance(last, ValueError) else ValueError(f"AI neodpověděla — zkus to za chvíli ({last})")


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
