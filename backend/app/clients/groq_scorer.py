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
    """Compact system prompt — Groq free tier allows ~8k tokens/min, so every token counts."""
    hints = []
    for code in languages:
        cfg = LANGUAGE_CONFIG.get(code)
        if cfg:
            hints.append(f"{cfg['name']} ({', '.join(cfg['tags'][:5])})")
    if not hints:
        hints = ["Czech (cz, cze, czech, dabing)"]
    return (
        "Rate files for a movie download manager.\n"
        "Entries are prefixed [WS]/[FS] (direct download) or [T] (torrent, with seeders).\n"
        "Reply ONLY with a JSON array, one item per file: [index, quality, relevance]\n"
        '- quality: "2160p"|"1080p"|"720p"|"SD"|"unknown"\n'
        "- relevance 0-100 answers ONLY \"is this file the full movie?\" — NOT its quality or size: "
        "any copy of the right film scores 80-100 even if small, old or low resolution (quality is "
        "filtered separately). Another part of the same series (\"Part Two\" when the film is Part One, "
        "\"2\", a remake with another year), TV series or episodes of the same universe, samples, extras, "
        "soundtracks, subtitles 0-20. Unsure whether it is the same film 40-60.\n"
        "No explanation."
    )


# Extra request parameters that cut hidden reasoning tokens (they count against the rate limit).
_REASONING_PARAMS = {
    "openai/gpt-oss": {"reasoning_effort": "low"},
    "qwen/": {"reasoning_format": "hidden"},
}
MAX_AI_FILES = 30  # ≈2.6k tokens → ~3 searches per minute on the free tier
_NON_VIDEO_EXTS = {".srt", ".sub", ".idx", ".ass", ".ssa", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".sfv", ".md5", ".url"}


def _is_obviously_irrelevant(f: ScorableFile) -> bool:
    """Files that never need AI: subtitles/metadata/images and small samples."""
    name = f.name.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in _NON_VIDEO_EXTS:
        return True
    return "sample" in name and 0 < f.size < 300 * 1024 * 1024


def _parse_scores(content: str) -> list[tuple[int, str, bool, int]]:
    """Accept [index, quality, relevance], [index, quality, dubbed, relevance] and the old object format.
    "dubbed" is ignored by callers — languages come from the name parser / the source itself."""
    content = content.strip()
    start, end = content.find("["), content.rfind("]")
    data = json.loads(content[start:end + 1])
    parsed = []
    for entry in data:
        if isinstance(entry, list) and len(entry) >= 4:
            parsed.append((int(entry[0]), str(entry[1]), bool(entry[2]), int(entry[3])))
        elif isinstance(entry, list) and len(entry) == 3:
            parsed.append((int(entry[0]), str(entry[1]), False, int(entry[2])))
        elif isinstance(entry, dict):
            parsed.append((int(entry.get("index", -1)), str(entry.get("quality", "unknown")),
                           bool(entry.get("is_dubbed", False)), int(entry.get("relevance_score", 50))))
    return parsed


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

    # Keep the AI request within the free-tier token budget: only the most promising files go
    # to the model, the rest get the local name-based score.
    overflow: list[ScorableFile] = []
    if len(files) > MAX_AI_FILES:
        files = sorted(files, key=lambda f: (-_title_match_score(movie_title, f.name), -f.size))
        files, overflow = files[:MAX_AI_FILES], files[MAX_AI_FILES:]

    # Obvious non-movies are scored locally — no need to spend tokens on them.
    local = [f for f in files if _is_obviously_irrelevant(f)]
    files = [f for f in files if not _is_obviously_irrelevant(f)]
    local_scored = _fallback_scoring(local, languages, query=movie_title)
    overflow_scored = _fallback_scoring(overflow, languages, query=movie_title)
    for s in local_scored:
        s.relevance_score = min(s.relevance_score, 10)
    if not files:
        return local_scored + overflow_scored

    prefix_map = {"webshare": "[WS]", "fastshare": "[FS]", "jackett": "[T]"}
    lines = []
    for i, f in enumerate(files):
        prefix = prefix_map.get(f.source, f"[{f.source[:2].upper()}]")
        extra = f" {f.seeders}s" if f.seeders is not None else ""
        lines.append(f"{i}. {prefix} {f.name} {f.size / 1e9:.1f}GB{extra}")
    names = [n.strip() for n in movie_title.split(" / ") if n.strip()]
    heading = (f'Movie: "{names[0]}"' if len(names) == 1
               else "Movie (one film, known under any of these names): " + " / ".join(f'"{n}"' for n in names))
    user_prompt = heading + "\n" + "\n".join(lines)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _build_system_prompt(languages or ["cs"])},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    for prefix, params in _REASONING_PARAMS.items():
        if model.startswith(prefix):
            body.update(params)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()

    payload = resp.json()
    usage = payload.get("usage", {})
    logger.info("Groq %s scored %d files, tokens=%s", model, len(files), usage.get("total_tokens"))
    content = payload["choices"][0]["message"]["content"]

    try:
        scored_data = _parse_scores(content)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.error("Groq returned invalid JSON: %s", content[:500])
        return _fallback_scoring(files, languages, query=movie_title) + local_scored + overflow_scored

    results: list[ScoredFile] = []
    for idx, quality, dubbed, relevance in scored_data:
        if 0 <= idx < len(files):
            f = files[idx]
            results.append(
                ScoredFile(
                    ident=f.ident,
                    name=f.name,
                    size=f.size,
                    quality=quality,
                    is_dubbed=dubbed,
                    relevance_score=relevance,
                    source=f.source,
                    source_id=f.source_id,
                    magnet_url=f.magnet_url,
                    seeders=f.seeders,
                )
            )

    results.extend(local_scored)
    results.extend(overflow_scored)
    results.sort(key=lambda r: (-r.relevance_score, -r.size))
    return results


def _normalize(text: str) -> str:
    """Normalize text for comparison: strip diacritics, lowercase, remove special chars."""
    import unicodedata, re
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^\w\s]", " ", ascii_text.lower()).strip()


def _title_match_score(query: str, filename: str) -> int:
    """Score how well filename matches the search query (0-100).
    Several names separated by " / " (local / original / English) → the best match counts."""
    if " / " in query:
        return max(_title_match_score(part, filename) for part in query.split(" / ") if part.strip())
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
