"""What becomes a notification — the other modules' events."""

import os
import logging

from app.modules.notify import store

logger = logging.getLogger(__name__)


KIND_LABEL = {"dub": " dabing", "upgrade": " lepší kvalita"}


def _se(season, episode) -> str:
    return f"S{int(season):02d}E{int(episode):02d}" if season is not None and episode is not None else ""


def _name(p: dict) -> str:
    title = p.get("title") or "?"
    return f"{title} ({p['year']})" if p.get("year") and p.get("content_type") != "tv" else title


def _film(p: dict) -> str:
    """The film's card in the library (its versions when it has more)."""
    return f"/library?tmdb={p['tmdb_id']}" if p.get("tmdb_id") else "/library"


async def on_download_completed(p: dict) -> None:
    """After the library took it (priority behind the import): landed in the library, or only downloaded."""
    if p.get("held_by"):
        return                                   # another module works on it and emits the event again
    action = p.get("library_action") or {}
    tv = p.get("content_type") == "tv"
    link = f"/series?tmdb={p['tmdb_id']}" if tv and p.get("tmdb_id") else _film(p) if p.get("imported") else "/downloads"
    if not p.get("imported"):
        # which file and why — "Columbo: 18 - Sladká, leč smrtící.avi (S03E01 už máš)"
        left = p.get("left_files") or [[os.path.basename(p.get("path") or ""), ""]]
        body = "; ".join(f"{name}{f' — {why}' if why else ''}" for name, why in left[:3]) + (f" a {len(left) - 3} dalších" if len(left) > 3 else "")
        await store.add("download", f"Staženo, ale není v knihovně: {_name(p)}", f"{body} · zůstalo ve stažených",
                        level="warn", link="/downloads", permission="download",
                        group=f"left:{p.get('tmdb_id')}" if tv else "", item=left[0][0] if tv else "")
        return
    if p.get("unsorted"):
        n = len(p["unsorted"])
        await store.add("download", f"Nezařazené díly: {_name(p)}",
                        f"{n} {'soubor' if n == 1 else 'soubory' if n < 5 else 'souborů'} ve složce Nezařazeno — "
                        "Knihovna → Seriály → Kontrola knihovny → Upravit díly (i s návrhem AI)",
                        level="warn", link="/library?tab=serialy", permission="library.edit", group=f"unsorted:{p.get('tmdb_id')}",
                        item=", ".join(p["unsorted"][:3]))
        if not p.get("placed"):
            return                               # nothing of it went to its episode
    if p.get("review"):
        await store.add("download", f"Na kontrolu: {_name(p)}", f"stažený soubor sedí jen napůl — {p['review']}",
                        level="warn", link=_film(p), permission="download")
        return
    if tv:
        # the episodes that went in (a pack imports them one by one while it downloads), else the one asked for
        item = ", ".join(p.get("placed_episodes") or []) or _se(action.get("season"), action.get("episode")) \
            or ("celý balík" if action.get("mode") == "pack" else "")
        await store.add("download", f"V knihovně: {_name(p)}", level="ok", link=link, permission="download",
                        group=f"tv:{p.get('tmdb_id')}", item=item)
    else:
        await store.add("download", f"V knihovně: {_name(p)}", "nahrazena starší verze" if action.get("mode") == "replace" else "",
                        level="ok", link=link, permission="download")


async def on_download_planned(p: dict) -> None:
    """A show pack's plan: what it holds and where it goes — before it downloads ("256 souborů → 251 dílů · 5×
    dvojdíl · 1 bonus"); what Lumina can not place is named."""
    s = p.get("summary") or {}
    files = sum(v for k, v in s.items() if k in ("episode", "extra", "sample", "unknown", "owned"))
    bits = [f"{files} souborů → {s.get('episodes', 0)} dílů z {s.get('tmdb', 0)}"]
    if s.get("parts"):
        bits.append(f"{s['parts']}× dvojdíl (pt1/pt2)")
    if s.get("extra"):
        bits.append(f"{s['extra']} bonus{'y' if 1 < s['extra'] < 5 else 'ů' if s['extra'] >= 5 else ''} do Extra")
    if s.get("owned"):
        bits.append(f"{s['owned']} už máš (nestahuje)")
    if s.get("sample"):
        bits.append(f"{s['sample']} ukázka (nestahuje)")
    if s.get("unknown"):
        bits.append(f"{s['unknown']} nejasných → Nezařazeno: {', '.join(p.get('unknown') or [])[:200]}")
    await store.add("download", f"Balík {p.get('title') or ''}: plán", " · ".join(bits),
                    level="warn" if s.get("unknown") else "info", link=f"/series?tmdb={p.get('tmdb_id')}",
                    permission="download")


async def on_download_failed(p: dict) -> None:
    se = _se((p.get("library_action") or {}).get("season"), (p.get("library_action") or {}).get("episode"))
    await store.add("download_failed", f"Stahování selhalo: {_name(p)}{' ' + se if se else ''}",
                    p.get("reason") or "", level="error", link="/downloads", permission="download")


async def on_offers_found(p: dict) -> None:
    best = p.get("best") or {}
    what = " · ".join(x for x in (best.get("quality_summary") or best.get("resolution") or "",
                                  "+".join(best.get("audio_langs") or []).upper()) if x)
    if p.get("kind") == "upgrade":
        await store.add("upgrade", f"Lepší verze: {_name(p)}", what, link=_film(p), permission="library.view",
                        dedup=f"upgrade:{p.get('tmdb_id')}:{best.get('ident')}")
    elif not p.get("first"):
        # found right after adding: Chci shows it (its top bar mark) — the bell only for what turns up later;
        # who may not download does not get it (it leads to the offers)
        await store.add("wanted", f"Chci — nalezeno: {_name(p)}", what, level="ok", link="/wanted", permission="download",
                        dedup=f"wanted:{p.get('tmdb_id')}:{best.get('ident')}")


async def on_series_found(p: dict) -> None:
    """The TV automation: found (waiting for the user's click) or started downloads, one show at a time."""
    link = f"/series?tmdb={p['tmdb_id']}"
    found = [f"{_se(s, e)}{KIND_LABEL.get(k, '')}" for s, e, k in p.get("found") or []]
    started = [f"{_se(s, e)}{KIND_LABEL.get(k, '')}" for s, e, k in p.get("downloading") or []]
    if found:
        await store.add("series", f"Automatika našla: {p.get('title') or '?'}", store._body(found, "čeká na tebe"),
                        level="ok", link=link, permission="search",
                        dedup=f"series:{p.get('tmdb_id')}:{','.join(found)}")
    if started:
        await store.add("series", f"Automatika stahuje: {p.get('title') or '?'}", store._body(started, ""),
                        link=link, permission="download")
