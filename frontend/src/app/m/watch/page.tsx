"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { NowPlaying, getPlexPlaying } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import Subtitles from "@/components/mobile/Subtitles";
import { BigButton, Spinner } from "@/components/mobile/ui";

/** Phone: "Sleduji" — what plays in Plex right now, for everyone (you first, the others with their name — "send me
 *  subtitles"); asked when this tab opens. A tap on a card: its subtitles. */
const se = (s: number | null, e: number | null) => `S${String(s ?? 0).padStart(2, "0")}E${String(e ?? 0).padStart(2, "0")}`;

function Playing({ item, open, toggle }: { item: NowPlaying; open: boolean; toggle: () => void }) {
  const { can } = useAuth();
  const name = item.kind === "episode" ? item.show : item.title;
  const what = item.kind === "episode" ? `${se(item.season, item.episode)} · ${item.title}` : String(item.year ?? "");
  return (
    <div className={`rounded-2xl border ${item.mine ? "border-violet-800/60 bg-violet-950/20" : "border-zinc-800 bg-zinc-900/60"}`}>
      <button onClick={toggle} className="w-full space-y-1 p-4 text-left">
        <p className="text-sm text-zinc-400">
          <span className={item.mine ? "text-violet-300" : "font-medium text-zinc-200"}>{item.mine ? "Ty" : item.user || "?"}</span>
          {" · "}{item.state === "paused" ? "⏸ pozastaveno" : "▶ sleduje"}{item.player ? ` · ${item.player}` : ""}
        </p>
        <p className="text-xl font-semibold text-zinc-100">{name}</p>
        <p className="flex items-center justify-between text-zinc-400">
          <span>{what}</span>
          {!open && <span className="text-sm text-violet-300">Titulky ›</span>}
        </p>
        {item.progress != null && (
          <div className="mt-1 h-1.5 overflow-hidden rounded bg-zinc-800"><div className="h-full bg-violet-500" style={{ width: `${item.progress}%` }} /></div>
        )}
      </button>
      {open && (
        <div className="space-y-4 border-t border-zinc-800 p-4">
          {item.id && can("subtitles") ? (
            <Subtitles id={item.id} kind={item.kind} />
          ) : (
            <p className="text-sm text-zinc-500">Tenhle soubor Lumina v knihovně nezná — titulky přes ni nestáhneš.</p>
          )}
          {item.kind === "episode" && item.tmdb_id && (
            <Link href={`/m/episode?tmdb=${item.tmdb_id}&s=${item.season}&e=${item.episode}`}
              className="block rounded-xl border border-zinc-700 px-4 py-3 text-center text-base text-zinc-100 active:bg-zinc-800">
              Díl v Lumině (jiná verze, dabing)
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

export default function MobileWatch() {
  const [data, setData] = useState<Awaited<ReturnType<typeof getPlexPlaying>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<number | null>(null);    // the card with its subtitles out (yours at first)
  const load = () => {
    setLoading(true);
    getPlexPlaying().then((d) => { setData(d); setOpen(d.items.length && d.items[0].mine ? 0 : d.items.length === 1 ? 0 : null); }).catch((e) => setData({ configured: true, items: [], error: e instanceof Error ? e.message : "Chyba" }))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  return (
    <main className="space-y-4 px-4 py-4">
      <h1 className="text-xl font-semibold text-zinc-100">Sleduji</h1>
      {loading && !data && <Spinner text="Ptám se Plexu, co hraje…" />}
      {data && !data.configured && <p className="text-zinc-400">Plex není v Lumině nastavený.</p>}
      {data?.error && <p className="text-red-400">Plex neodpověděl: {data.error}</p>}
      {data?.configured && !data.error && !data.items.length && (
        <div className="space-y-3 py-6 text-center">
          <p className="text-zinc-400">V Plexu teď nikdo nic nesleduje.</p>
          <p className="text-sm text-zinc-500">Pusť film nebo díl a ťukni na Obnovit.</p>
        </div>
      )}
      {data?.items.map((item, i) => (
        <Playing key={`${item.user}-${item.title}-${i}`} item={item} open={open === i} toggle={() => setOpen(open === i ? null : i)} />
      ))}
      {data && <BigButton kind="secondary" onClick={load} disabled={loading}>{loading ? "Načítám…" : "Obnovit"}</BigButton>}
    </main>
  );
}
