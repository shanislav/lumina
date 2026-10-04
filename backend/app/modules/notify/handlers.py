"""What becomes a notification — the other modules' events."""

import logging

from app.modules.notify import store

logger = logging.getLogger(__name__)


KIND_LABEL = {"dub": " dabing", "upgrade": " lepší kvalita"}


def _se(season, episode) -> str:
    return f"S{int(season):02d}E{int(episode):02d}" if season is not None and episode is not None else ""


def _name(p: dict) -> str:
    title = p.get("title") or "?"
    return f"{title} ({p['year']})" if p.get("year") and p.get("content_type") != "tv" else title


async def on_download_completed(p: dict) -> None:
    """After the library took it (priority behind the import): landed in the library, or only downloaded."""
    if p.get("held_by"):
        return                                   # another module works on it and emits the event again
    action = p.get("library_action") or {}
    tv = p.get("content_type") == "tv"
    link = f"/series?tmdb={p['tmdb_id']}" if tv and p.get("tmdb_id") else "/library" if p.get("imported") else "/downloads"
    if not p.get("imported"):
        await store.add("download", f"Staženo, ale není v knihovně: {_name(p)}", "soubor zůstal ve stažených",
                        level="warn", link="/downloads", permission="download")
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
                        level="warn", link="/library", permission="download")
        return
    if tv:
        item = _se(action.get("season"), action.get("episode")) or ("celý balík" if action.get("mode") == "pack" else "")
        await store.add("download", f"V knihovně: {_name(p)}", level="ok", link=link, permission="download",
                        group=f"tv:{p.get('tmdb_id')}", item=item)
    else:
        await store.add("download", f"V knihovně: {_name(p)}", "nahrazena starší verze" if action.get("mode") == "replace" else "",
                        level="ok", link=link, permission="download")


async def on_download_failed(p: dict) -> None:
    se = _se((p.get("library_action") or {}).get("season"), (p.get("library_action") or {}).get("episode"))
    await store.add("download_failed", f"Stahování selhalo: {_name(p)}{' ' + se if se else ''}",
                    p.get("reason") or "", level="error", link="/downloads", permission="download")


async def on_offers_found(p: dict) -> None:
    best = p.get("best") or {}
    what = " · ".join(x for x in (best.get("quality_summary") or best.get("resolution") or "",
                                  "+".join(best.get("audio_langs") or []).upper()) if x)
    if p.get("kind") == "upgrade":
        await store.add("upgrade", f"Lepší verze: {_name(p)}", what, link="/library", permission="library.view",
                        dedup=f"upgrade:{p.get('tmdb_id')}:{best.get('ident')}")
    else:
        await store.add("wanted", f"Chci — nalezeno: {_name(p)}", what, level="ok", link="/wanted", permission="wanted",
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
