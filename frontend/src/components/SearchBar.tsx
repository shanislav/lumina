"use client";

import { useState, useEffect, useRef, FormEvent, KeyboardEvent } from "react";
import { Suggestion, TMDBMovie, suggest } from "@/lib/api";

interface Props {
  onSearch: (query: string, language?: string) => void;
  /** a suggestion picked: go straight to its files */
  onPick?: (movie: TMDBMovie) => void;
  loading: boolean;
  initialQuery?: string;
}

/** The language of titles / overviews is a setting ("Jazyk názvů a popisů"); file search finds every language.
 *  As you type, films / shows (and the best-known films of a person) are suggested under the box. */
export default function SearchBar({ onSearch, onPick, loading, initialQuery }: Props) {
  const [query, setQuery] = useState(initialQuery || "");
  const [items, setItems] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const typed = useRef(false);        // only typing suggests (not a query set from outside)
  const box = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    if (initialQuery) setQuery(initialQuery);
  }, [initialQuery]);

  useEffect(() => {
    const q = query.trim();
    if (!onPick || !typed.current || q.length < 3) { setItems([]); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      suggest(q, ctrl.signal).then((s) => { setItems(s); setActive(-1); setOpen(true); }).catch(() => {});
    }, 250);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query, onPick]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function pick(s: Suggestion) {
    typed.current = false;
    setOpen(false);
    setQuery(s.title);
    onPick?.(s);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (open && active >= 0 && items[active]) { pick(items[active]); return; }
    const trimmed = query.trim();
    if (!trimmed) return;
    setOpen(false);
    onSearch(trimmed);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (!open || !items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(-1, a - 1)); }
    else if (e.key === "Escape") setOpen(false);
  }

  return (
    <form ref={box} onSubmit={handleSubmit} className="relative flex gap-2 sm:gap-3 w-full max-w-2xl items-center">
      <input
        type="text"
        value={query}
        onChange={(e) => { typed.current = true; setQuery(e.target.value); }}
        onFocus={() => items.length && setOpen(true)}
        onKeyDown={onKey}
        placeholder="Film, seriál nebo herec…"
        autoComplete="off"
        className="flex-1 min-w-0 rounded-lg bg-zinc-800 border border-zinc-700 px-4 py-3 text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent"
      />
      <button
        type="submit"
        disabled={loading || !query.trim()}
        className="flex-shrink-0 rounded-lg bg-violet-600 px-4 sm:px-6 py-3 font-medium text-white hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "..." : "Search"}
      </button>
      {open && items.length > 0 && (
        <ul className="absolute left-0 right-0 top-full z-40 mt-1 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-900 shadow-2xl">
          {items.map((s, i) => (
            <li key={`${s.media_type}-${s.tmdb_id}`}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(s)}
                onMouseEnter={() => setActive(i)}
                className={`flex w-full items-center gap-3 px-3 py-2 text-left ${i === active ? "bg-violet-900/40" : "hover:bg-zinc-800"}`}>
                {s.poster_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={s.poster_url.replace("/w500/", "/w92/")} alt="" className="h-12 w-8 flex-shrink-0 rounded object-cover" />
                ) : <div className="h-12 w-8 flex-shrink-0 rounded bg-zinc-800" />}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-zinc-100">
                    {s.title} {s.year && <span className="text-zinc-500">({s.year})</span>}
                  </p>
                  <p className="truncate text-xs text-zinc-500">
                    {s.media_type === "tv" ? "seriál" : "film"}
                    {s.original_title && s.original_title !== s.title && ` · ${s.original_title}`}
                    {s.person && ` · s ${s.person}`}
                  </p>
                </div>
                {s.in_library && <span className="flex-shrink-0 text-[10px] text-emerald-400">v knihovně</span>}
              </button>
            </li>
          ))}
          <li className="border-t border-zinc-800 px-3 py-1.5 text-[11px] text-zinc-600">
            ↑↓ vybrat · Enter otevřít · Enter bez výběru = všechny výsledky
          </li>
        </ul>
      )}
    </form>
  );
}
