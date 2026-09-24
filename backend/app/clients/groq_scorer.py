import json
import logging

import httpx

from app.models.schemas import ScorableFile, ScoredFile

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
# Groq retires models regularly — the UI lists the live ones via list_models().
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
# Models Lumina used to offer that Groq has retired (stored settings get migrated).
RETIRED_GROQ_MODELS = ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it", "mixtral-8x7b-32768")
# Non-chat models (speech, TTS, safety classifiers) are not usable for scoring.
_NON_CHAT = ("whisper", "guard", "orpheus", "tts", "safeguard", "allam")

# Language config for dubbing detection
LANGUAGE_CONFIG: dict[str, dict] = {
    "cs": {
        "name": "Czech",
        "tags": ["cz", "czech", "český", "česky", "dabing", "dubbing", "czdab", "cze"],
        "label": "CZ",
    },
    "sk": {
        "name": "Slovak",
        "tags": ["sk", "slovak", "slovenský", "slovensky", "skdab", "svk"],
        "label": "SK",
    },
    "en": {
        "name": "English",
        "tags": ["en", "eng", "english"],
        "label": "EN",
    },
    "de": {
        "name": "German",
        "tags": ["de", "ger", "german", "deutsch", "deutsche"],
        "label": "DE",
    },
    "pl": {
        "name": "Polish",
        "tags": ["pl", "pol", "polish", "polski", "lektor"],
        "label": "PL",
    },
    "hu": {
        "name": "Hungarian",
        "tags": ["hu", "hun", "hungarian", "magyar"],
        "label": "HU",
    },
    "fr": {
        "name": "French",
        "tags": ["fr", "fre", "french", "français", "vf", "vff", "truefrench"],
        "label": "FR",
    },
    "es": {
        "name": "Spanish",
        "tags": ["es", "spa", "spanish", "español", "castellano", "latino"],
        "label": "ES",
    },
    "it": {
        "name": "Italian",
        "tags": ["it", "ita", "italian", "italiano"],
        "label": "IT",
    },
    "pt": {
        "name": "Portuguese",
        "tags": ["pt", "por", "portuguese", "português", "legendado"],
        "label": "PT",
    },
    "ru": {
        "name": "Russian",
        "tags": ["ru", "rus", "russian", "русский"],
        "label": "RU",
    },
    "ja": {
        "name": "Japanese",
        "tags": ["ja", "jpn", "japanese", "日本語"],
        "label": "JA",
    },
    "ko": {
        "name": "Korean",
        "tags": ["ko", "kor", "korean", "한국어"],
        "label": "KO",
    },
    "zh": {
        "name": "Chinese",
        "tags": ["zh", "chi", "chinese", "中文"],
        "label": "ZH",
    },
    "nl": {
        "name": "Dutch",
        "tags": ["nl", "dut", "dutch", "nederlands"],
        "label": "NL",
    },
    "sv": {
        "name": "Swedish",
        "tags": ["sv", "swe", "swedish", "svenska"],
        "label": "SV",
    },
    "da": {
        "name": "Danish",
        "tags": ["da", "dan", "danish", "dansk"],
        "label": "DA",
    },
    "no": {
        "name": "Norwegian",
        "tags": ["no", "nor", "norwegian", "norsk"],
        "label": "NO",
    },
    "fi": {
        "name": "Finnish",
        "tags": ["fi", "fin", "finnish", "suomi"],
        "label": "FI",
    },
    "ro": {
        "name": "Romanian",
        "tags": ["ro", "rum", "romanian", "română"],
        "label": "RO",
    },
    "tr": {
        "name": "Turkish",
        "tags": ["tr", "tur", "turkish", "türkçe"],
        "label": "TR",
    },
    "el": {
        "name": "Greek",
        "tags": ["el", "gre", "greek", "ελληνικά"],
        "label": "EL",
    },
    "uk": {
        "name": "Ukrainian",
        "tags": ["uk", "ukr", "ukrainian", "українська"],
        "label": "UK",
    },
    "hr": {
        "name": "Croatian",
        "tags": ["hr", "cro", "croatian", "hrvatski"],
        "label": "HR",
    },
    "bg": {
        "name": "Bulgarian",
        "tags": ["bg", "bul", "bulgarian", "български"],
        "label": "BG",
    },
}


def _build_system_prompt(languages: list[str]) -> str:
    """Build Groq system prompt dynamically based on selected languages."""
    lang_names = []
    dubbing_hints = []
    for code in languages:
        cfg = LANGUAGE_CONFIG.get(code)
        if cfg:
            lang_names.append(cfg["name"])
            tags_str = ", ".join(cfg["tags"])
            dubbing_hints.append(f"  - {cfg['name']}: look for tags: {tags_str}")

    if not lang_names:
        lang_names = ["Czech"]
        dubbing_hints = ["  - Czech: look for tags: cz, czech, český, dabing, dubbing, czdab, cze"]

    lang_list = ", ".join(lang_names)
    dubbing_block = "\n".join(dubbing_hints)

    return f"""\
You are a file-name analyzer for a movie/TV download manager.
Given a movie title and a list of file/torrent names from multiple sources,
analyze each entry and return a JSON array.

Each entry is prefixed with [WS] (WebShare direct download), [FS] (FastShare direct download), or [T] (torrent).
Torrent entries may include seeders count.

The user is interested in these languages: {lang_list}

For EACH entry extract:
- "index": the 0-based index of the entry in the input list
- "quality": detected video quality (e.g. "4K", "2160p", "1080p", "720p", "480p", "SD", "unknown")
- "is_dubbed": true if the file name suggests dubbing/audio in any of the user's preferred languages.
  Language detection hints:
{dubbing_block}
  Also consider "multi" or "dual audio" tags that may include preferred languages.
- "relevance_score": integer 0-100 rating how likely this entry is the actual movie content.
  Score LOW (0-20) for: subtitles (.srt, .sub), samples, soundtracks, NFO files, screenshots, RAR parts that are not the main file.
  Score HIGH (70-100) for: full movie files (.mkv, .avi, .mp4) or torrents whose name closely matches the query.
  For torrents, higher seeders count is a positive quality signal.
Return ONLY a valid JSON array, no markdown fences, no explanation.\
"""


async def score_results(
    movie_title: str,
    files: list[ScorableFile],
    api_key: str,
    languages: list[str] | None = None,
    model: str = DEFAULT_GROQ_MODEL,
) -> list[ScoredFile]:
    if not files:
        return []

    # Pre-filter: drop torrents with less than 10 seeders
    files = [f for f in files if not (f.source == "jackett" and (f.seeders is None or f.seeders < 10))]
    if not files:
        return []

    system_prompt = _build_system_prompt(languages or ["cs"])

    lines: list[str] = []
    # Use enumerate because ScorableFile model does not have an index attribute
    for i, f in enumerate(files):
        prefix_map = {"webshare": "[WS]", "fastshare": "[FS]", "jackett": "[T]"}
        prefix = prefix_map.get(f.source, f"[{f.source[:2].upper()}]")
        extra = f" ({f.seeders} seeders)" if f.seeders is not None else ""
        lines.append(f"{i}. {prefix} {f.name} ({f.size} bytes){extra}")

    file_list_text = "\n".join(lines)
    user_prompt = f'Movie title: "{movie_title}"\n\nFiles:\n{file_list_text}'

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
            },
        )
        resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[: content.rfind("```")]
    content = content.strip()

    try:
        scored_data = json.loads(content)
    except json.JSONDecodeError:
        logger.error("Groq returned invalid JSON: %s", content[:500])
        return _fallback_scoring(files, languages, query=movie_title)

    results: list[ScoredFile] = []
    for entry in scored_data:
        idx = entry.get("index", -1)
        if 0 <= idx < len(files):
            f = files[idx]
            results.append(
                ScoredFile(
                    ident=f.ident,
                    name=f.name,
                    size=f.size,
                    quality=entry.get("quality", "unknown"),
                    is_dubbed=bool(entry.get("is_dubbed", False)),
                    relevance_score=int(entry.get("relevance_score", 50)),
                    source=f.source,
                    source_id=f.source_id,
                    magnet_url=f.magnet_url,
                    seeders=f.seeders,
                )
            )

    results.sort(key=lambda r: (-r.relevance_score, -r.size))
    return results


def _normalize(text: str) -> str:
    """Normalize text for comparison: strip diacritics, lowercase, remove special chars."""
    import unicodedata, re
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^\w\s]", " ", ascii_text.lower()).strip()


def _title_match_score(query: str, filename: str) -> int:
    """Score how well filename matches the search query (0-100)."""
    q_norm = _normalize(query)
    f_norm = _normalize(filename)
    q_words = set(q_norm.split())
    f_words = set(f_norm.split())

    if not q_words:
        return 50

    # How many query words appear in filename
    matched = q_words & f_words
    ratio = len(matched) / len(q_words)

    # Strip year from query for title-only matching
    import re
    q_no_year = re.sub(r"\b(19|20)\d{2}\b", "", q_norm).strip()
    q_title_words = set(q_no_year.split())

    if q_title_words:
        title_matched = q_title_words & f_words
        title_ratio = len(title_matched) / len(q_title_words)
    else:
        title_ratio = ratio

    # Full title match bonus
    if title_ratio >= 0.9:
        return 95
    elif title_ratio >= 0.7:
        return 85
    elif title_ratio >= 0.5:
        return 70
    elif ratio >= 0.3:
        return 50
    else:
        return 20


def _fallback_scoring(
    files: list[ScorableFile],
    languages: list[str] | None = None,
    query: str = "",
) -> list[ScoredFile]:
    """Basic heuristic scoring when AI is unavailable."""
    video_exts = {".mkv", ".avi", ".mp4", ".m4v", ".ts"}

    # Build all tags from selected languages
    all_tags: set[str] = set()
    for code in (languages or ["cs"]):
        cfg = LANGUAGE_CONFIG.get(code)
        if cfg:
            all_tags.update(cfg["tags"])
    if not all_tags:
        all_tags = {"cz", "czech", "český", "dabing"}

    results: list[ScoredFile] = []
    for i, f in enumerate(files):
        name_lower = f.name.lower()
        ext = "." + name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
        is_video = ext in video_exts or f.source in ("jackett", "webshare", "fastshare")
        is_lang = any(tag in name_lower for tag in all_tags)

        quality = "unknown"
        for q in ["2160p", "4k", "1080p", "720p", "480p"]:
            if q in name_lower:
                quality = q.upper() if q == "4k" else q
                break

        # Filter out torrents with low seeders
        if f.source == "jackett" and (f.seeders is None or f.seeders < 10):
            continue

        # Title relevance (main scoring factor)
        if query:
            score = _title_match_score(query, f.name)
        else:
            score = 75 if is_video else 10

        # Language bonus
        if is_lang:
            score = min(100, score + 5)

        # Non-video penalty
        if not is_video:
            score = min(score, 20)

        results.append(
            ScoredFile(
                ident=f.ident,
                name=f.name,
                size=f.size,
                quality=quality,
                is_dubbed=is_lang,
                relevance_score=score,
                source=f.source,
                source_id=f.source_id,
                magnet_url=f.magnet_url,
                seeders=f.seeders,
            )
        )
    results.sort(key=lambda r: (-r.relevance_score, -r.size))
    return results


async def list_models(api_key: str) -> list[str]:
    """Chat models currently available for the given Groq API key."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(GROQ_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
    ids = [m["id"] for m in resp.json().get("data", []) if m.get("active", True)]
    return sorted(i for i in ids if not any(tag in i.lower() for tag in _NON_CHAT))
