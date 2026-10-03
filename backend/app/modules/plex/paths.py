"""Map a folder as Lumina sees it to the folder as Plex sees it."""


def _parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


def _join(parts: list[str]) -> str:
    return "/" + "/".join(parts)


def parse_rule(rule: str) -> tuple[list[str], list[str]] | None:
    """Manual rule "/data=/data/Share" (Lumina prefix = Plex prefix)."""
    if "=" not in (rule or ""):
        return None
    src, dst = rule.split("=", 1)
    return (_parts(src), _parts(dst)) if src.strip() else None


def _inside(path: list[str], folder: list[str]) -> bool:
    return path[:len(folder)] == folder


def to_plex(folder: str, library_root: str, locations: list[str], rule: str = "") -> str | None:
    """Plex path of ``folder``, or None when it cannot be mapped: by the manual rule, or as it is
    when a Plex location contains it. Never guessed — a guess can point at another library with
    the same layout (a test copy next to the real one), see ``suggest_rule``."""
    f = _parts(folder)
    parsed = parse_rule(rule)
    if parsed:
        src, dst = parsed
        return _join(dst + f[len(src):]) if _inside(f, src) else None
    for loc in locations:
        if _inside(f, _parts(loc)):
            return _join(f)
    return None


def from_plex(path: str, rule: str = "") -> str:
    """A file as Plex sees it → as Lumina sees it (the manual rule backwards; without one the same path)."""
    parsed = parse_rule(rule)
    p = _parts(path)
    if parsed:
        src, dst = parsed
        if _inside(p, dst):
            return _join(src + p[len(dst):])
    return _join(p)


def suggest_rule(library_root: str, locations: list[str]) -> str | None:
    """A rule for the user to confirm: the one location sharing the longest tail with the library
    root (/data/Video/Movies ↔ /mnt/share/Video/Movies → "/data=/mnt/share"); None on a tie."""
    root = _parts(library_root)
    scored = []
    for loc in locations:
        lp = _parts(loc)
        n = 0
        while n < min(len(lp), len(root)) and lp[-1 - n] == root[-1 - n]:
            n += 1
        if n:
            scored.append((n, lp))
    scored.sort(key=lambda x: -x[0])
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    n, lp = scored[0]
    return f"{_join(root[:len(root) - n])}={_join(lp[:len(lp) - n])}"


def section_for(plex_path: str, sections: list[dict]) -> dict | None:
    p = _parts(plex_path)
    for s in sections:
        if any(_inside(p, _parts(loc)) for loc in s["locations"]):
            return s
    return None
