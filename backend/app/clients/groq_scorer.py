import json
import logging

import httpx

from app.models.schemas import ScorableFile, ScoredFile

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

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
- "video_codec": detected video codec from filename (e.g. "HEVC", "x265", "H.264", "x264", "AVC", "VP9", "AV1", "XviD", ""). Empty string if not found.
- "audio_codec": detected audio codec from filename (e.g. "DTS", "DTS-HD", "AC3", "EAC3", "AAC", "FLAC", "TrueHD", "Atmos", ""). Empty string if not found.
- "release_type": detected release source (e.g. "BluRay", "WEB-DL", "WEBRip", "HDRip", "BDRip", "DVDRip", "HDTV", "Remux", ""). Empty string if not found.
- "languages": array of language codes detected in the filename (e.g. ["CZ", "EN"], ["CZ"], []). Use uppercase 2-letter codes.

Return ONLY a valid JSON array, no markdown fences, no explanation.\
"""


async def score_results(
    movie_title: str,
    files: list[ScorableFile],
    api_key: str,
    languages: list[str] | None = None,
) -> list[ScoredFile]:
    if not files:
        return []

    system_prompt = _build_system_prompt(languages or ["cs"])

    lines: list[str] = []
    for f in files:
        prefix_map = {"webshare": "[WS]", "fastshare": "[FS]", "jackett": "[T]"}
        prefix = prefix_map.get(f.source, f"[{f.source[:2].upper()}]")
        extra = f" ({f.seeders} seeders)" if f.seeders is not None else ""
        lines.append(f"{f.index}. {prefix} {f.name} ({f.size} bytes){extra}")

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
                "model": "llama-3.3-70b-versatile",
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
        return _fallback_scoring(files, languages)

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
                    video_codec=entry.get("video_codec", ""),
                    audio_codec=entry.get("audio_codec", ""),
                    release_type=entry.get("release_type", ""),
                    languages=entry.get("languages", []),
                )
            )

    results.sort(key=lambda r: (-r.relevance_score, -r.size))
    return results


def _fallback_scoring(
    files: list[ScorableFile],
    languages: list[str] | None = None,
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
    for f in files:
        name_lower = f.name.lower()
        ext = "." + name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
        is_video = ext in video_exts or f.source == "jackett"
        is_lang = any(tag in name_lower for tag in all_tags)

        quality = "unknown"
        for q in ["2160p", "4k", "1080p", "720p", "480p"]:
            if q in name_lower:
                quality = q.upper() if q == "4k" else q
                break

        score = 70 if is_video else 10
        if f.seeders is not None and f.seeders > 5:
            score = min(100, score + 10)

        # Basic codec detection for fallback
        vcodec = ""
        for vc in ["hevc", "x265", "h.264", "x264", "avc", "xvid"]:
            if vc in name_lower:
                vcodec = vc.upper()
                break
        acodec = ""
        for ac in ["dts-hd", "dts", "truehd", "atmos", "eac3", "ac3", "aac", "flac"]:
            if ac in name_lower:
                acodec = ac.upper()
                break
        rtype = ""
        for rt in ["remux", "bluray", "blu-ray", "web-dl", "webrip", "hdrip", "bdrip", "dvdrip", "hdtv"]:
            if rt in name_lower:
                rtype = rt
                break

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
                video_codec=vcodec,
                audio_codec=acodec,
                release_type=rtype,
            )
        )
    results.sort(key=lambda r: (-r.relevance_score, -r.size))
    return results
