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
    """Plex path of ``folder``, or None when it cannot be mapped.

    Manual rule first; else a Plex location containing the folder as it is; else the
    location sharing the longest tail with the library root
    (/data/Video/Movies ↔ /mnt/share/Video/Movies).
    """
    f = _parts(folder)
    parsed = parse_rule(rule)
    if parsed:
        src, dst = parsed
        return _join(dst + f[len(src):]) if _inside(f, src) else None
    for loc in locations:
        if _inside(f, _parts(loc)):
            return _join(f)
    root = _parts(library_root)
    if not root or not _inside(f, root):
        return None
    best, best_n = None, 0
    for loc in locations:
        lp = _parts(loc)
        n = 0
        while n < min(len(lp), len(root)) and lp[-1 - n] == root[-1 - n]:
            n += 1
        if n > best_n:
            best, best_n = lp, n
    return _join(best + f[len(root):]) if best else None


def section_for(plex_path: str, sections: list[dict]) -> dict | None:
    p = _parts(plex_path)
    for s in sections:
        if any(_inside(p, _parts(loc)) for loc in s["locations"]):
            return s
    return None
