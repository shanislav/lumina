"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { Suggestion, TMDBMovie, searchMovies, suggest } from "@/lib/api";
import { Spinner } from "@/components/mobile/ui";
import { titleHref } from "@/lib/mobile";

/** Phone: "Hledat" — type a name, tap the film or show. Suggestions while typing, the full search on Enter. */
const LAST_KEY = "lumina.m.lastSearch";

export default function MobileSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<(TMDBMovie & Partial<Suggestion>)[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // back from a title: the last results again
  useEffect(() => {
    try {
      const last = JSON.parse(sessionStorage.getItem(LAST_KEY) || "null");
      if (last?.q) { setQ(last.q); setItems(last.items || []); setSearched(true); }
    } catch { /* nothing */ }
  }, []);

  // suggestions while typing
  useEffect(() => {
    if (q.trim().length < 2) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      suggest(q.trim(), ctl.signal).then((s) => { setItems(s); setSearched(false); }).catch(() => {});
    }, 250);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    input.current?.blur();
    setLoading(true);
    try {
      const res = await searchMovies(q.trim());
      setItems(res);
      setSearched(true);
      try { sessionStorage.setItem(LAST_KEY, JSON.stringify({ q: q.trim(), items: res })); } catch { /* full */ }
    } finally { setLoading(false); }
  }

  function open(m: TMDBMovie) {
    try { sessionStorage.setItem(LAST_KEY, JSON.stringify({ q: q.trim(), items })); } catch { /* full */ }
    router.push(titleHref(m));
  }

  return (
    <main className="space-y-4 px-4 py-4">
      <form onSubmit={submit} className="flex gap-2">
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} type="search" enterKeyHint="search"
          placeholder="Film nebo seriál…" autoComplete="off"
          className="min-h-12 w-full rounded-xl border border-zinc-700 bg-zinc-900 px-4 text-base text-zinc-100 placeholder-zinc-500 outline-none focus:border-violet-500" />
        <button type="submit" className="min-h-12 rounded-xl bg-violet-600 px-4 text-base font-medium text-white active:bg-violet-700">Hledat</button>
      </form>

      {loading && <Spinner text="Hledám…" />}
      {!loading && searched && !items.length && <p className="py-6 text-center text-zinc-500">Nic nenalezeno.</p>}

      <div className="space-y-2">
        {items.map((m) => (
          <button key={`${m.media_type}-${m.tmdb_id}-${m.title}`} onClick={() => open(m)}
            className="flex w-full items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-2 text-left active:bg-zinc-800">
            <div className="relative h-[72px] w-12 flex-none overflow-hidden rounded-md bg-zinc-800">
              {m.poster_url && <Image src={m.poster_url} alt="" fill sizes="48px" className="object-cover" unoptimized />}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-base font-medium text-zinc-100">{m.title}</p>
              <p className="text-sm text-zinc-400">
                {[m.year, m.media_type === "tv" ? "seriál" : "film", m.person ? `s ${m.person}` : ""].filter(Boolean).join(" · ")}
              </p>
              {m.in_library && <p className="text-sm text-emerald-300">V knihovně</p>}
            </div>
            <span className="px-1 text-xl text-zinc-600">›</span>
          </button>
        ))}
      </div>

      {!q && !items.length && (
        <p className="pt-6 text-center text-sm text-zinc-500">Napiš název filmu nebo seriálu. Lumina najde soubory a doporučí ten nejlepší.</p>
      )}
    </main>
  );
}
