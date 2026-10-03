"""AI suggestion of which TMDB episode each file of a show folder is — for what the rules can not tell: the
file's name in another language than TMDB's names ("Pomsta lovce hlav" = "Headhunters Revenge"), an order of
the uploader's own (Znalec psí duše: the dogs' names in a Czech description).

An AI (Gemini or Groq, app/clients/ai) gets the files of one season (own name, length — in order, without their
numbers) and TMDB's episodes of it and the seasons next to it plus the specials (Czech and English names, runtime) and answers file → episode with a confidence. Lumina
checks every answer (the episode exists, no two files on one, does it agree with its own rules) — a suggestion
only: the user accepts it in the UI (it becomes the user's word, ``tv_episode_overrides``).
"""

import asyncio
import json
import logging
import re

from app.clients import ai
from app.core import naming
from app.modules.library import episode_names

logger = logging.getLogger(__name__)

MAX_FILES = 60
MAX_EPISODES = 160
DIALOGUE_CHARS = 320           # a file without a name: this much of its subtitles' text
PLOT_CHARS = 170               # TMDB's plot of an episode (when files come with dialogue)

SYSTEM = (
    "You match video files of one TV show to TMDB episodes. File names are the user's (often Czech, a translation "
    "of the episode's name, an order of the uploader's own); TMDB names are given in Czech and English. Decide by "
    "the meaning of the names (translate), people or places named in them, the order, and the length in minutes. "
    "Two files may be copies of one episode. Some files come with a few lines of their dialogue (subtitles) and "
    "some episodes with a short plot: match who and what the dialogue is about to the plot. "
    "Answer only JSON: an array of [file_index, season, episode, confidence] "
    "with confidence 0-100; use null for season and episode when you do not know. No explanation."
)


def _lines(files: list[dict], cat: dict, seasons: set[int]) -> tuple[str, list[tuple[int, int]]]:
    eps = sorted(k for k in cat if k[0] in seasons)[:MAX_EPISODES]
    # the files' numbers are left out: they are what is in doubt (the model would copy them); the order stays
    out = ["Files (in the user's order):"]
    for i, f in enumerate(files[:MAX_FILES]):
        out.append(f"{i}. {f.get('own') or f['name']} ({round((f.get('duration') or 0) / 60)} min)")
        if f.get("dialogue"):
            out.append(f"   dialogue: {f['dialogue']}")
    out.append("TMDB episodes:")
    for s, e in eps:
        v = cat[(s, e)]
        names = " / ".join(dict.fromkeys(t for t in (v.get("cs"), v.get("en")) if t))
        plot = (v.get("plot") or "")[:PLOT_CHARS]
        out.append(f"S{s:02d}E{e:02d} {names} ({v.get('runtime') or '?'} min)" + (f" — {plot}" if plot else ""))
    return "\n".join(out), eps


def _num(value) -> int | None:
    """8, "8", "S08", "E09", 8.0 → the number; None for none."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else None


def _parse(content: str) -> list[list]:
    start, end = content.find("["), content.rfind("]")
    data = json.loads(content[start:end + 1])
    return [row for row in data if isinstance(row, list) and len(row) >= 4]


async def _ask(cfg: dict, files: list[dict], cat: dict, seasons: set[int],
               providers: list[str] | None = None) -> tuple[dict[int, tuple[tuple[int, int], int]], str]:
    """One question to an AI (``providers`` in order, the next one when one fails): file index → (episode,
    confidence) for the episodes TMDB has; and who answered."""
    user, _eps = _lines(files, cat, seasons)
    try:
        answer = await ai.chat(cfg, "episodes", SYSTEM, [{"role": "user", "content": user}], max_tokens=3000,
                               temperature=0.1, providers=providers)
    except ai.LimitError as e:
        raise ValueError(f"{e}") from e
    logger.info("%s mapped %d files", ai.LABELS[answer.provider], len(files))
    try:
        rows = _parse(answer.text)
    except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        raise ValueError(f"AI odpověděla nesrozumitelně: {e}") from e
    out = {}
    for row in rows:
        try:
            i, s, e, conf = _num(row[0]), _num(row[1]), _num(row[2]), _num(row[3]) or 0
        except (TypeError, ValueError):
            continue
        if i is None or not 0 <= i < min(len(files), MAX_FILES) or s is None or e is None or (s, e) not in cat:
            continue                                         # no answer, or an episode TMDB does not have: made up
        out[i] = ((s, e), max(0, min(100, conf)))
    return out, answer.provider


async def suggest(cfg: dict, files: list[dict], cat: dict, season: int | None) -> list[dict]:
    """files: [{path, name, own, season, episode, duration}]. Returns per file {path, season, episode, confidence,
    title, agrees, rules, same, warning} for every file the model placed.

    The model is asked twice, the files in the user's order and reversed: its confidence alone says little (a blind
    test: 7 of 31 wrong at 95 %) — an answer both times the same is a suggestion, two different ones are a doubt."""
    who = ai.order(cfg, "episodes")
    if not who:
        raise ValueError("AI není nastavená (Nastavení → AI: Groq nebo Gemini)")
    files = files[:MAX_FILES]
    seasons = {season} if season is not None else {f["season"] for f in files if f.get("season") is not None}
    seasons |= {s + d for s in list(seasons) for d in (-1, 1) if s + d > 0} | {0}
    # with both AIs each one answers once (the second question goes to the other one first): two AIs agreeing
    # is worth more than one agreeing with itself
    first, by_first = await _ask(cfg, files, cat, seasons, who)
    n = len(files)
    again, by_second = await _ask(cfg, list(reversed(files)), cat, seasons, who[::-1])
    second = {n - 1 - i: v for i, v in again.items()}
    both = by_first != by_second

    out: list[dict] = []
    taken: dict[tuple[int, int], int] = {}
    for i, f in enumerate(files):
        a, b = first.get(i), second.get(i)
        if not a and not b:
            continue
        key, conf = a or b
        warning = ""
        if a and b and a[0] != b[0]:
            conf = min(a[1], b[1], 40)
            warning = (f"AI se neshodly ({ai.LABELS[by_first]} {_label(a[0])}, {ai.LABELS[by_second]} {_label(b[0])})"
                       if both else f"AI si není jistá (jednou {_label(a[0])}, podruhé {_label(b[0])})")
        elif not (a and b):
            conf = min(conf, 50)
            warning = (f"odpověděl jen {ai.LABELS[by_first if a else by_second]}" if both
                       else "AI odpověděla jen jednou z dvou")
        else:
            conf = min(a[1], b[1])
        rule, sure = episode_names.best(f.get("own") or "", cat, f.get("season"), f.get("duration") or 0)
        cs = cat[key].get("cs") or ""
        title = cs if naming.episode_title(cs) else cat[key].get("en") or cs
        out.append({"path": f["path"], "season": key[0], "episode": key[1], "confidence": conf, "title": title or "",
                    "agrees": bool(rule) and rule == key,
                    "rules": _label(rule) if rule and sure and rule != key else "",
                    "same": key == (f.get("season"), f.get("episode")), "warning": warning,
                    "heard": bool(f.get("dialogue")),            # decided by the file's subtitles
                    "by": [ai.LABELS[p] for p in dict.fromkeys((by_first, by_second))]})
        taken[key] = taken.get(key, 0) + 1
    for s in out:
        if taken[(s["season"], s["episode"])] > 1 and not s["warning"]:
            s["warning"] = "na stejný díl míří víc souborů (kopie, nebo omyl)"
    return out


def _label(key: tuple[int, int]) -> str:
    return f"S{key[0]:02d}E{key[1]:02d}"


_SUB_SIDE = (".srt", ".vtt", ".ass", ".ssa")


def _clean_subtitles(text: str) -> str:
    """The spoken lines of a subtitle file, without numbers, times, tags and the first credits."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"<[^>]+>|\{[^}]*\}", "", line).strip()
        if not line or line.isdigit() or "-->" in line or line.upper().startswith(("WEBVTT", "[", "DIALOGUE:", "STYLE:")):
            if line.startswith("Dialogue:"):
                line = line.split(",", 9)[-1]
            else:
                continue
        if re.search(r"(?i)překlad|titulky|subtitles|časování|www\.|\.(cz|sk|com|net)", line):
            continue                                     # the translator's credits
        lines.append(line)
    return " / ".join(lines)


async def dialogue(path: str) -> str:
    """A few lines of what is said in a video — its subtitles next to it, else the first text subtitle track
    in it (ffmpeg) — for files whose name says nothing."""
    import asyncio
    import os

    stem = os.path.splitext(path)[0]
    folder = os.path.dirname(path)
    try:
        side = sorted(f for f in os.listdir(folder)
                      if f.startswith(os.path.basename(stem) + ".") and f.lower().endswith(_SUB_SIDE))
    except OSError:
        side = []
    text = ""
    if side:
        try:
            with open(os.path.join(folder, side[0]), encoding="utf-8", errors="replace") as fh:
                text = fh.read(60000)
        except OSError:
            text = ""
    else:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-v", "error", "-i", path, "-map", "0:s:0", "-t", "900", "-f", "srt", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
            text = out.decode("utf-8", errors="replace")
        except (OSError, asyncio.TimeoutError):
            text = ""
    spoken = _clean_subtitles(text)
    # past the opening (recaps, the intro song): from the middle of the first quarter
    start = len(spoken) // 10
    return spoken[start:start + DIALOGUE_CHARS].strip(" /")
