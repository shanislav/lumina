"""Audio and subtitle languages from a release/file name — deterministic, no AI.

Uploaders mark languages in names like "CZ dabing", "CZ-EN", "CZEN", "EN+CZ DABING",
"CZ titulky", "Cz.Sub", "EN, SK+CZ sub". A language next to a subtitle marker is a
subtitle, anything else is audio. Only unambiguous tokens are recognised
("de", "it", "es" are ordinary words too often).

Names are only a hint — WebShare file_info / the FastShare file page tell the truth.
"""

import re
import unicodedata

_LANG_TOKENS = {
    "cz": "cs", "cze": "cs", "ces": "cs", "czech": "cs", "cesky": "cs", "cestina": "cs", "cs": "cs",
    "sk": "sk", "svk": "sk", "slk": "sk", "slovak": "sk", "slovensky": "sk",
    "en": "en", "eng": "en", "english": "en", "anglicky": "en",
    "ger": "de", "deu": "de", "german": "de",
    "pol": "pl", "polish": "pl", "hun": "hu", "hungarian": "hu",
    "fre": "fr", "fra": "fr", "french": "fr", "rus": "ru", "russian": "ru",
    "ita": "it", "italian": "it", "spa": "es", "spanish": "es",
    "jpn": "ja", "japanese": "ja", "kor": "ko", "korean": "ko",
}
_SUB_MARKERS = {"tit", "titulky", "titl", "titule", "sub", "subs", "subtitles", "subtitle", "forced", "sdh"}
_AUDIO_MARKERS = {"dabing", "dab", "dub", "dubbed", "audio"}
# compound tokens: "czdab", "cztit", "czsub", "czen", "czeng", "encz"
_COMPOUND = re.compile(r"^(cz|sk|en|eng|cze)(dab|dabing|tit|titulky|sub|subs|cz|sk|en|eng)$")


# Separators that keep languages together in one group ("SK+CZ", "CZ-EN", "cz/en");
# anything else (comma, space, dot, brackets) starts a new group ("EN, SK+CZ sub").
_JOINERS = {"+", "-", "/", "&", ""}


def _tokens(name: str) -> list[tuple[str, str]]:
    """(token, separator before it) pairs."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    ascii_name = re.sub(r"\.(mkv|mp4|avi|m4v|ts|wmv|mov|mpg|mpeg|webm)$", "", ascii_name)
    out: list[tuple[str, str]] = []
    for sep, tok in re.findall(r"([^a-z0-9]*)([a-z0-9]+)", ascii_name):
        sep = sep.strip() if sep.strip() in _JOINERS else (sep[:1] if sep else "")
        m = _COMPOUND.match(tok)
        if m:
            out.extend([(m.group(1), sep), (m.group(2), "")])
        else:
            out.append((tok, sep))
    return out


def parse_languages(name: str) -> dict[str, list[str]]:
    """{"audio": [...], "subtitles": [...]} in order of appearance (ISO 639-1)."""
    tokens = _tokens(name)
    words = [t for t, _ in tokens]
    joined = [sep in _JOINERS for _, sep in tokens]  # joined[i]: token i glued to token i-1
    audio: list[str] = []
    subs: list[str] = []
    sub_positions = {i for i, t in enumerate(words) if t in _SUB_MARKERS}

    for i, word in enumerate(words):
        lang = _LANG_TOKENS.get(word)
        if not lang:
            continue
        # the group of languages this one belongs to
        start = i
        while start > 0 and joined[start] and _LANG_TOKENS.get(words[start - 1]):
            start -= 1
        end = i
        while end + 1 < len(words) and joined[end + 1] and _LANG_TOKENS.get(words[end + 1]):
            end += 1
        # A marker right before the group wins ("audio: CZ-EN tit: CZ-EN", "titulky CZ"),
        # then a subtitle marker right after it ("SK+CZ sub", "CZ tit"); otherwise audio.
        before = words[start - 1] if start > 0 else ""
        if before in _AUDIO_MARKERS:
            is_sub = False
        elif before in _SUB_MARKERS:
            is_sub = True
        else:
            is_sub = (end + 1) in sub_positions
        target = subs if is_sub else audio
        if lang not in target:
            target.append(lang)
    return {"audio": audio, "subtitles": subs}
