"""Lumina's AIs: Groq and Google Gemini — either, or both working together.

Every AI feature asks through ``chat(cfg, feature, …)``: the AIs with a key, in the order the feature prefers
(the setting ``ai_<feature>``: auto | groq | gemini). When one fails (a limit, an error) the next one answers.

- Groq (OpenAI-like API): fast, a big daily limit, knows famous titles only.
- Gemini (Google AI Studio key, a free tier): can search Google while answering (``search=True``) — finds
  lesser-known films by a scene. Google sends no "what is left" with an answer: Lumina counts its own calls
  per Google's day (midnight Pacific time) and stops asking for the day after Google says the limit is out.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.clients import groq_quota
from app.db import DB_PATH

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
# Non-chat Gemini models (embeddings, speech, images, live) cannot answer a question.
_GEMINI_NON_CHAT = ("embedding", "tts", "image", "live", "audio", "aqa", "imagen", "veo", "robotics", "computer-use")

# extra request parameters that cut hidden reasoning tokens (they count against Groq's limit)
REASONING_PARAMS = {
    "openai/gpt-oss": {"reasoning_effort": "low"},
    "qwen/": {"reasoning_format": "hidden"},
}

PROVIDERS = ("groq", "gemini")
LABELS = {"groq": "Groq", "gemini": "Gemini"}
# auto: who answers first in each feature
ORDER = {
    "describe": ("gemini", "groq"),    # Gemini searches Google — finds what a model does not remember
    "episodes": ("gemini", "groq"),    # asked twice: once each, two AIs agreeing is worth more
    "scoring": ("groq", "gemini"),     # many short questions while searching: Groq is faster
}
MAX_WAIT = 20                          # seconds a per-minute limit may hold a question

USAGE_FILE = DB_PATH.parent / "gemini_usage.json"
_gemini: dict = {}


class AIError(Exception):
    pass


class LimitError(AIError):
    pass


@dataclass
class Answer:
    text: str
    provider: str
    searched: bool = False             # Gemini searched Google for it


# ── which AI ──

def keys(cfg: dict) -> dict[str, str]:
    return {"groq": cfg.get("groq_api_key") or "", "gemini": cfg.get("gemini_api_key") or ""}


def available(cfg: dict) -> list[str]:
    """The AIs with a key."""
    return [p for p, k in keys(cfg).items() if k]


def order(cfg: dict, feature: str) -> list[str]:
    """The AIs to ask for ``feature``, the first one first; one out of its daily limit goes last."""
    have = available(cfg)
    choice = (cfg.get(f"ai_{feature}") or "auto").strip().lower()
    base = ORDER.get(feature, PROVIDERS)
    if choice in PROVIDERS:
        base = (choice, *[p for p in base if p != choice])
    out = [p for p in base if p in have]
    return sorted(out, key=lambda p: p == "gemini" and gemini_usage()["exhausted"])


# ── Gemini's day ──

def _google_day() -> str:
    """Google's free tier resets at midnight Pacific time (UTC−8, −7 in summer — an hour does not matter)."""
    return (datetime.now(timezone.utc) - timedelta(hours=8)).date().isoformat()


def gemini_usage() -> dict:
    """{day, calls, searches, exhausted} of Google's day now."""
    if not _gemini:
        try:
            _gemini.update(json.loads(USAGE_FILE.read_text()))
        except (OSError, ValueError):
            pass
    day = _google_day()
    if _gemini.get("day") != day:
        _gemini.clear()
        _gemini.update({"day": day, "calls": 0, "searches": 0, "exhausted": False})
    return _gemini


def _gemini_note(searched: bool = False, exhausted: bool = False) -> None:
    u = gemini_usage()
    if exhausted:
        u["exhausted"] = True
    else:
        u["calls"] += 1
        u["searches"] += int(searched)
    try:
        USAGE_FILE.write_text(json.dumps(u))
    except OSError as e:
        logger.debug("Gemini usage not saved: %s", e)


def quotas(cfg: dict) -> dict:
    """What is left of each AI's limits, for the UI (admin)."""
    out: dict = {}
    if cfg.get("groq_api_key"):
        q = groq_quota.get()
        keep = ("requests_left", "requests_limit", "tokens_left", "tokens_limit")
        out["groq"] = {k: q.get(k) for k in keep} if q else {}
    if cfg.get("gemini_api_key"):
        u = gemini_usage()
        out["gemini"] = {"calls": u["calls"], "searches": u["searches"], "exhausted": u["exhausted"]}
    return out


# ── asking ──

def retry_after(resp) -> float:
    """Seconds an API asks to wait: retry-after, Groq's "try again in 12.5s" / "1m3s", Google's retryDelay
    "17s"; 10 when it does not say."""
    try:
        return float(resp.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        pass
    text = getattr(resp, "text", "") or ""
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", text)
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2))
    m = re.search(r'"retryDelay":\s*"([\d.]+)s"', text)
    return float(m.group(1)) if m else 10.0


async def _groq(cfg: dict, system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    model = cfg.get("groq_model") or ""
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages]}
    for prefix, params in REASONING_PARAMS.items():
        if model.startswith(prefix):
            body.update(params)
    async with httpx.AsyncClient(timeout=90) as client:
        for attempt in range(3):
            resp = await client.post(GROQ_API_URL, json=body, headers={
                "Authorization": f"Bearer {cfg['groq_api_key']}", "Content-Type": "application/json"})
            groq_quota.note(resp)
            if resp.status_code != 429:
                break
            wait = retry_after(resp)          # the free tier's tokens per minute: a second question waits
            if attempt == 2 or wait > MAX_WAIT:
                raise LimitError("Groq: překročený limit")
            logger.info("Groq rate limit, waiting %.0f s", wait)
            await asyncio.sleep(wait)
        resp.raise_for_status()
    payload = resp.json()
    logger.info("Groq %s answered, tokens=%s", model, (payload.get("usage") or {}).get("total_tokens"))
    return payload["choices"][0]["message"]["content"] or ""


def _gemini_contents(messages: list[dict]) -> list[dict]:
    """Gemini's turns: user / model, alternating, the first one the user's."""
    out: list[dict] = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["parts"][0]["text"] += "\n\n" + m["content"]
        else:
            out.append({"role": role, "parts": [{"text": m["content"]}]})
    if out and out[0]["role"] == "model":
        out.insert(0, {"role": "user", "parts": [{"text": "…"}]})
    return out


async def _gemini_ask(cfg: dict, system: str, messages: list[dict], max_tokens: int, temperature: float,
                      search: bool) -> tuple[str, bool]:
    model = cfg.get("gemini_model") or DEFAULT_GEMINI_MODEL
    body: dict = {"contents": _gemini_contents(messages),
                  "systemInstruction": {"parts": [{"text": system}]},
                  # thinking models count their thoughts in the output: room for both
                  "generationConfig": {"temperature": temperature, "maxOutputTokens": max(max_tokens * 4, 4096)}}
    if search:
        body["tools"] = [{"google_search": {}}]
    url = f"{GEMINI_API}/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(2):
            resp = await client.post(url, json=body, headers={"x-goog-api-key": cfg["gemini_api_key"],
                                                              "Content-Type": "application/json"})
            if resp.status_code != 429:
                break
            if "PerDay" in (resp.text or "") or attempt == 1 or retry_after(resp) > MAX_WAIT:
                if "PerDay" in (resp.text or ""):
                    _gemini_note(exhausted=True)          # out for today: the other AI answers until tomorrow
                raise LimitError("Gemini: překročený limit")
            await asyncio.sleep(retry_after(resp))
    if resp.status_code >= 400:
        raise AIError(f"Gemini HTTP {resp.status_code}: {(resp.text or '')[:200]}")
    payload = resp.json()
    cand = (payload.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    searched = bool((cand.get("groundingMetadata") or {}).get("webSearchQueries"))
    _gemini_note(searched=searched)
    logger.info("Gemini %s answered%s, tokens=%s", model, " (searched Google)" if searched else "",
                (payload.get("usageMetadata") or {}).get("totalTokenCount"))
    if not text:
        raise AIError(f"Gemini neodpověděl ({cand.get('finishReason') or 'prázdná odpověď'})")
    return text, searched


async def ask(cfg: dict, provider: str, system: str, messages: list[dict], *, max_tokens: int = 2000,
              temperature: float = 0.3, search: bool = False) -> Answer:
    """One question to one AI."""
    if provider == "gemini":
        text, searched = await _gemini_ask(cfg, system, messages, max_tokens, temperature, search)
        return Answer(text, "gemini", searched)
    return Answer(await _groq(cfg, system, messages, max_tokens, temperature), "groq")


async def chat(cfg: dict, feature: str, system: str, messages: list[dict], *, max_tokens: int = 2000,
               temperature: float = 0.3, search: bool = False, providers: list[str] | None = None) -> Answer:
    """Ask the feature's AIs in order until one answers."""
    todo = providers if providers is not None else order(cfg, feature)
    if not todo:
        raise AIError("AI není nastavená (Nastavení → AI: Groq nebo Gemini)")
    last: Exception | None = None
    for provider in todo:
        try:
            return await ask(cfg, provider, system, messages, max_tokens=max_tokens, temperature=temperature,
                             search=search)
        except Exception as e:                       # the next AI answers
            logger.warning("%s (%s) failed: %s", LABELS[provider], feature, e)
            last = e
    if isinstance(last, LimitError):
        raise LimitError(f"{last} — zkus to za chvíli")
    raise AIError(str(last) or type(last).__name__)


async def gemini_models(api_key: str) -> list[str]:
    """Gemini chat models available for the key."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GEMINI_API}/models", params={"pageSize": 200}, headers={"x-goog-api-key": api_key})
        resp.raise_for_status()
    out = []
    for m in resp.json().get("models", []):
        name = m.get("name", "").removeprefix("models/")
        if "generateContent" in (m.get("supportedGenerationMethods") or []) and name.startswith("gemini") \
                and not any(t in name for t in _GEMINI_NON_CHAT):
            out.append(name)
    return sorted(out)

