"""Lumina's AIs: Groq and Google Gemini — either, or both working together.

Every AI feature asks through ``chat(cfg, feature, …)``: the AIs with a key, in the order the feature prefers
(the setting ``ai_<feature>``: auto | groq | gemini). When one fails (a limit, an error) the next one answers.

- Groq (OpenAI-like API): fast, a big daily limit, knows famous titles only.
- Gemini (Google AI Studio key, a free tier): remembers far more titles than Groq's model; can search Google
  while answering (``search=True``) where the key allows it (the free tier of newer models does not — then it
  answers from its memory, and Lumina does not try the search again that day). The free tier gives each model
  its own small daily limit (gemini-flash-latest = the newest flash: 20 a day), so when the chosen model is out
  the next Gemini model answers (older flash models, then the lite ones, then Gemma — measured 2026-10-03: all of
  them know "Phenomenon" from a vague description, Groq's model never), and Groq only when every one is out.
  Google sends no "what is left" with an answer: Lumina counts its own calls per Google's day (midnight Pacific
  time) and remembers the models Google says are out.
"""

import asyncio
import json
import logging
import re
import time
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
_GEMINI_NON_CHAT = ("embedding", "tts", "image", "live", "audio", "aqa", "imagen", "veo", "robotics", "computer-use",
                    "transcribe", "customtools", "omni", "lyria", "nano-banana", "antigravity", "deep-research")
MODELS_TTL_S = 24 * 3600
_models: dict = {}                     # the key's chat models: {"at": time, "key": hash, "names": [...]}

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
MODEL_TIMEOUT = 40                     # one Gemini model that hangs (it happens) gives way to the next one
MIN_LEFT = 6                           # less time left than this: no new question (the caller's deadline)

USAGE_FILE = DB_PATH.parent / "gemini_usage.json"
_gemini: dict = {}


class AIError(Exception):
    pass


class LimitError(AIError):
    pass


class SearchLimitError(LimitError):
    """Google search is not allowed to the key (the free tier of newer models has none) — ask without it."""


@dataclass
class Answer:
    text: str
    provider: str
    searched: bool = False             # Gemini searched Google for it
    model: str = ""                    # Gemini: which model answered

    @property
    def label(self) -> str:
        """"Gemini 3.7 flash", "Groq"."""
        if self.provider != "gemini" or not self.model:
            return LABELS[self.provider]
        return "Gemini " + self.model.removeprefix("gemini-").replace("-latest", "").replace("-it", "").replace("-", " ")


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
    return sorted(out, key=lambda p: p == "gemini" and gemini_out(cfg))


def _version(name: str) -> float:
    m = re.search(r"-(\d+(?:\.\d+)?)-", name + "-")
    return float(m.group(1)) if m else 0.0


def gemini_chain(cfg: dict, names: list[str] | None = None) -> list[str]:
    """The Gemini models to ask, in order: the chosen one, the other flash models (the newest first), the lite
    ones, Gemma — without those out of their daily limit today. ``names``: the key's chat models (else the
    last list read from Google; before any, the chosen model and the lite alias)."""
    chosen = cfg.get("gemini_model") or DEFAULT_GEMINI_MODEL
    if names is None:
        names = _models.get("names") or [chosen, "gemini-flash-lite-latest"]
    usable = [n for n in names if "pro" not in n]          # pro: 0 requests a day on the free tier

    def rank(n: str) -> tuple:
        if "gemma" in n:
            return (3, "a4b" in n, -_version(n), n)          # the dense model before the small MoE one
        return (1 + ("lite" in n), "preview" in n, -(99 if "latest" in n else _version(n)), n)

    out = [chosen] + sorted((n for n in usable if n != chosen), key=rank)
    gone = set(gemini_usage()["out"])
    return [n for n in dict.fromkeys(out) if n not in gone]


# ── Gemini's day ──

def _google_day() -> str:
    """Google's free tier resets at midnight Pacific time (UTC−8, −7 in summer — an hour does not matter)."""
    return (datetime.now(timezone.utc) - timedelta(hours=8)).date().isoformat()


def gemini_usage() -> dict:
    """{day, calls, searches, out: [models out of their daily limit], no_search} of Google's day now."""
    if not _gemini:
        try:
            _gemini.update(json.loads(USAGE_FILE.read_text()))
        except (OSError, ValueError):
            pass
    day = _google_day()
    if _gemini.get("day") != day:
        _gemini.clear()
        _gemini.update({"day": day, "calls": 0, "searches": 0, "out": []})
    _gemini.setdefault("out", [])
    return _gemini


def gemini_out(cfg: dict) -> bool:
    """Is every Gemini model out of its daily limit (Google counts each model on its own)?"""
    return not gemini_chain(cfg)


def _gemini_note(searched: bool = False, out: str = "") -> None:
    u = gemini_usage()
    if out:
        u["out"] = sorted({*u["out"], out})
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
        chain = gemini_chain(cfg)
        out["gemini"] = {"calls": u["calls"], "searches": u["searches"], "exhausted": not chain,
                         "no_search": bool(u.get("no_search")), "model": chain[0] if chain else "",
                         "out": u["out"]}
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


def _why(e: Exception) -> str:
    """An exception for the log — a timeout has an empty message."""
    return str(e) or type(e).__name__


def _left(deadline: float | None, cap: float) -> float:
    """Seconds a question may take: ``cap``, or less as the caller's deadline nears."""
    if deadline is None:
        return cap
    left = deadline - time.monotonic()
    if left < MIN_LEFT:
        raise LimitError("AI: vypršel čas na odpověď")
    return min(cap, left)


async def _groq(cfg: dict, system: str, messages: list[dict], max_tokens: int, temperature: float,
                timeout: float = 90) -> str:
    model = cfg.get("groq_model") or ""
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages]}
    for prefix, params in REASONING_PARAMS.items():
        if model.startswith(prefix):
            body.update(params)
    async with httpx.AsyncClient(timeout=timeout) as client:
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


def _quota_model(text: str) -> str:
    """The model Google counts a 429 against — "gemini-flash-latest" is counted as "gemini-3.8-flash"."""
    m = re.search(r'"model":\s*"([^"]+)"', text or "")
    return m.group(1) if m else ""


async def _gemini_ask(cfg: dict, system: str, messages: list[dict], max_tokens: int, temperature: float,
                      search: bool, model: str = "", timeout: float = 120) -> tuple[str, bool]:
    model = model or cfg.get("gemini_model") or DEFAULT_GEMINI_MODEL
    contents = _gemini_contents(messages)
    if not contents:
        raise AIError("Gemini: prázdná otázka")
    body: dict = {"contents": contents,
                  "systemInstruction": {"parts": [{"text": system}]},
                  # thinking models count their thoughts in the output: room for both
                  "generationConfig": {"temperature": temperature, "maxOutputTokens": max(max_tokens * 4, 4096)}}
    if "gemma" in model:                  # Gemma: no system instruction, no Google search
        del body["systemInstruction"]
        contents[0]["parts"][0]["text"] = system + "\n\n" + contents[0]["parts"][0]["text"]
        search = False
    if search:
        body["tools"] = [{"google_search": {}}]
    url = f"{GEMINI_API}/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            resp = await client.post(url, json=body, headers={"x-goog-api-key": cfg["gemini_api_key"],
                                                              "Content-Type": "application/json"})
            if resp.status_code == 503 and attempt == 0:      # "high demand" — usually gone in a moment
                await asyncio.sleep(2)
                continue
            if resp.status_code != 429:
                break
            if search and "PerDay" not in (resp.text or ""):
                raise SearchLimitError("Gemini: hledání na Googlu není s tímto klíčem dostupné")
            if "PerDay" in (resp.text or "") or attempt == 1 or retry_after(resp) > MAX_WAIT:
                if "PerDay" in (resp.text or ""):
                    _gemini_note(out=model)               # out for today: the next model answers until tomorrow
                    if (counted := _quota_model(resp.text)) and counted != model:
                        _gemini_note(out=counted)         # the alias and the model behind it share the limit
                raise LimitError(f"Gemini {model}: překročený limit")
            await asyncio.sleep(retry_after(resp))
    # the model is not for this key ("no longer available to new users", not found): skip it today —
    # never for a bad question (400 without that), it would cross out every model
    if resp.status_code == 404 or resp.status_code in (400, 403) and re.search(
            r"(?i)no longer available|not (?:found|supported|available)|permission", resp.text or ""):
        _gemini_note(out=model)
    if resp.status_code >= 400:
        raise AIError(f"Gemini {model} HTTP {resp.status_code}: {(resp.text or '')[:200]}")
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
              temperature: float = 0.3, search: bool = False, deadline: float | None = None) -> Answer:
    """One question to one AI. ``deadline`` (time.monotonic()): the answer must come before it — a model that
    hangs gives way to the next one in time (the web server cuts a request at 60 s)."""
    if provider == "gemini":
        if search and gemini_usage().get("no_search"):
            search = False                       # Google said no today: do not ask (and wait) again
        await _load_models(cfg)
        chain = gemini_chain(cfg)
        if not chain:
            raise LimitError("Gemini: všechny modely mají dnes vyčerpaný limit")
        last: Exception | None = None
        for model in chain:                      # a model out of its limit (or failing): the next one
            if model in gemini_usage()["out"]:  # the alias's 429 named the model behind it
                continue
            try:
                try:
                    text, searched = await _gemini_ask(cfg, system, messages, max_tokens, temperature, search, model,
                                                       _left(deadline, MODEL_TIMEOUT))
                except SearchLimitError as e:
                    logger.info("%s — asking without it until tomorrow", e)
                    gemini_usage()["no_search"] = True
                    search = False
                    text, searched = await _gemini_ask(cfg, system, messages, max_tokens, temperature, False, model,
                                                       _left(deadline, MODEL_TIMEOUT))
                return Answer(text, "gemini", searched, model)
            except (AIError, httpx.HTTPError) as e:
                logger.info("Gemini %s: %s — the next model", model, _why(e))
                last = e
                if deadline is not None and deadline - time.monotonic() < MIN_LEFT:
                    break                        # no time for another model: the caller's other AI answers
        raise last if isinstance(last, LimitError) else AIError(_why(last) if last else "Gemini neodpověděl")
    return Answer(await _groq(cfg, system, messages, max_tokens, temperature, _left(deadline, 90)), "groq")


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
            logger.warning("%s (%s) failed: %s", LABELS[provider], feature, _why(e))
            last = e
    if isinstance(last, LimitError):
        raise LimitError(f"{last} — zkus to za chvíli")
    raise AIError(str(last) or type(last).__name__)


async def _load_models(cfg: dict) -> None:
    """The key's chat models, read once a day (the order of the fallbacks comes from them)."""
    key = cfg.get("gemini_api_key") or ""
    if _models.get("names") and _models.get("key") == hash(key) and time.time() - _models["at"] < MODELS_TTL_S:
        return
    try:
        names = await gemini_models(key, gemma=True)
    except Exception as e:  # noqa: BLE001 — without the list: the chosen model and the lite alias
        logger.info("Gemini models not read: %s", e)
        return
    _models.update(at=time.time(), key=hash(key), names=names)


async def gemini_models(api_key: str, gemma: bool = False) -> list[str]:
    """Gemini chat models available for the key."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GEMINI_API}/models", params={"pageSize": 200}, headers={"x-goog-api-key": api_key})
        resp.raise_for_status()
    out = []
    for m in resp.json().get("models", []):
        name = m.get("name", "").removeprefix("models/")
        if "generateContent" in (m.get("supportedGenerationMethods") or []) \
                and (name.startswith("gemini") or gemma and name.startswith("gemma")) \
                and not any(t in name for t in _GEMINI_NON_CHAT):
            out.append(name)
    return sorted(out)

