"use client";

import DescribeSearch, { looksLikeDescription } from "@/components/DescribeSearch";
import ClearInput from "@/components/ClearInput";
import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { Suggestion, TMDBMovie, searchMovies, suggest } from "@/lib/api";
import { Spinner } from "@/components/mobile/ui";
import { titleHref } from "@/lib/mobile";

/** Phone: "Hledat" — type a name, tap the film or show. Suggestions while typing, the full search on Enter. */
const LAST_KEY = "lumina.m.lastSearch";
const BACK_KEY = "lumina.m.openedTitle";      // set when a title opens: coming back restores the results
const forget = () => { try { sessionStorage.removeItem(LAST_KEY); } catch { /* private */ } };

export default function MobileSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<(TMDBMovie & Partial<Suggestion>)[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const shownFor = useRef("");
  const [ask, setAsk] = useState<{ text: string; n: number } | null>(null);          // the text the shown list is for — no suggestions over it

  // a film handed over by another page (Objevit, the library) — "/?movie=<base64 JSON>" → its page
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("movie");
    if (!raw) return;
    try { router.replace(titleHref(JSON.parse(decodeURIComponent(atob(raw))))); } catch { /* bad data: stay */ }
  }, [router]);

  // back from a title: the last results again — anything else (a reload, the tab "Hledat") is a fresh start
  useEffect(() => {
    let back = false;
    try { back = !!sessionStorage.getItem(BACK_KEY); sessionStorage.removeItem(BACK_KEY); } catch { /* private */ }
    if (!back) { forget(); return; }
    try {
      const last = JSON.parse(sessionStorage.getItem(LAST_KEY) || "null");
      if (last?.q) { shownFor.current = last.q; setQ(last.q); setItems(last.items || []); setSearched(true); }
    } catch { /* nothing */ }
  }, []);

  // suggestions while typing
  useEffect(() => {
    if (q.trim().length < 2 || q.trim() === shownFor.current) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      const asked = q.trim();
      suggest(asked, ctl.signal).then((s) => {
        if (shownFor.current === asked) return;      // searched meanwhile (Enter): the full results stay
        setItems(s); setSearched(false);
      }).catch(() => {});
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
      shownFor.current = q.trim();
      setItems(res);
      setSearched(true);
      try { sessionStorage.setItem(LAST_KEY, JSON.stringify({ q: q.trim(), items: res })); } catch { /* full */ }
    } finally { setLoading(false); }
  }

  function open(m: TMDBMovie) {
    try {
      sessionStorage.setItem(LAST_KEY, JSON.stringify({ q: q.trim(), items }));
      sessionStorage.setItem(BACK_KEY, "1");
    } catch { /* full */ }
    router.push(titleHref(m));
  }

  return (
    <main className="space-y-4 px-4 py-4">
      <form onSubmit={submit} className="flex gap-2">
        <ClearInput ref={input} value={q} onChange={(e) => setQ(e.target.value)} type="search" enterKeyHint="search"
          onClear={() => { setQ(""); setItems([]); setSearched(false); forget(); input.current?.focus(); }}
          placeholder="Film nebo seriál…" autoComplete="off" wrapperClassName="flex-1 min-w-0"
          className="min-h-12 w-full rounded-xl border border-zinc-700 bg-zinc-900 px-4 text-base text-zinc-100 placeholder-zinc-500 outline-none focus:border-violet-500" />
        <button type="submit" className="min-h-12 rounded-xl bg-violet-600 px-4 text-base font-medium text-white active:bg-violet-700">Hledat</button>
      </form>

      {loading && <Spinner text="Hledám…" />}
      {!loading && searched && looksLikeDescription(q) && (
        <div className="space-y-2 rounded-2xl border border-violet-800/60 bg-violet-950/20 p-4">
          <p className="text-zinc-200">Hledáš podle toho, o čem to bylo?</p>
          <button onClick={() => { setAsk({ text: q.trim(), n: Date.now() }); setItems([]); setSearched(false); }}
            className="min-h-12 w-full rounded-xl bg-violet-600 px-4 text-base font-medium text-white active:bg-violet-700">
            ✨ Zeptat se AI
          </button>
        </div>
      )}
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

      <div className="pt-6">
        <DescribeSearch onPick={open} big ask={ask} />
      </div>

      {!q && !items.length && (
        <p className="pt-6 text-center text-sm text-zinc-500">Napiš název filmu nebo seriálu. Lumina najde soubory a doporučí ten nejlepší.</p>
      )}
    </main>
  );
}
