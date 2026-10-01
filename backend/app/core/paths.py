"""One folder seen by two programs under different paths (Lumina in Docker sees /data, a program on
the host sees /data/Share). A rule "lumina prefix=other prefix" translates between them."""


def _parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


def map_path(path: str, rule: str, to_lumina: bool = False) -> str:
    """``path`` as the other program sees it (or, with ``to_lumina``, as Lumina sees it).
    Without a rule, or outside its prefix, the path stays as it is."""
    if not path or "=" not in (rule or ""):
        return path
    lumina, other = (_parts(x) for x in rule.split("=", 1))
    src, dst = (other, lumina) if to_lumina else (lumina, other)
    parts = _parts(path)
    if parts[:len(src)] != src:
        return path
    return "/" + "/".join(dst + parts[len(src):])
