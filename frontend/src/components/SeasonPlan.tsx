"use client";

import { useEffect, useMemo, useState } from "react";
import { ScoredFile, SeasonOffers, SeasonSet, downloadSeason, getSeasonOffers } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

/**
 * A whole season at once (backend core/offers/season): the releases found (files of one uploader, one
 * quality), torrent packs, and the plan — each wanted episode from the chosen release, gaps (or episodes
 * the release has only without CZ/SK) from the others. Language first, then one release.
 */

const L = (code: string) => (code === "cs" ? "CZ" : code.toUpperCase());
const gb = (b: number) => `${(b / 1e9).toFixed(1)} GB`;
const SRC: Record<string, string> = { webshare: "WS", fastshare: "FS", prowlarr: "T", jackett: "T" };

function plan(sets: SeasonSet[], primary: string, wanted: number[]) {
  const first = sets.find((s) => s.key === primary) ?? sets[0];
  const local = (f: ScoredFile) => (f.lang_tier ?? 0) >= 2;
  return wanted.flatMap((ep) => {
    const own = first?.episodes[String(ep)];
    if (own && (local(own) || !first.local)) return [{ episode: ep, set: first.key, row: own }];
    const options = sets.filter((s) => s.episodes[String(ep)])
      .map((s) => ({ set: s, row: s.episodes[String(ep)] }))
      .sort((a, b) => Number(local(b.row)) - Number(local(a.row)) || b.set.coverage - a.set.coverage
        || b.row.quality_score - a.row.quality_score);
    return options.length ? [{ episode: ep, set: options[0].set.key, row: options[0].row }] : [];
  });
}

export default function SeasonPlan({ tmdbId, season, ownedEpisodes, busyEpisodes = [], onStarted }: {
  tmdbId: number; season: number; ownedEpisodes: number[]; busyEpisodes?: number[]; onStarted: () => void;
}) {
  const { can } = useAuth();
  const [offers, setOffers] = useState<SeasonOffers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [primary, setPrimary] = useState("");
  const [skip, setSkip] = useState<Set<number>>(new Set());
  const [state, setState] = useState<"" | "busy" | string>("");

  useEffect(() => {
    let live = true;
    getSeasonOffers(tmdbId, season)
      .then((o) => { if (live) { setOffers(o); setPrimary(o.sets[0]?.key ?? ""); } })
      .catch((e) => live && setError(e instanceof Error ? e.message : "Chyba"));
    return () => { live = false; };
  }, [tmdbId, season]);

  const items = useMemo(() => offers ? plan(offers.sets, primary, offers.wanted) : [], [offers, primary]);
  if (error) return <p className="px-4 py-3 text-xs text-red-400">{error}</p>;
  if (!offers) return <p className="px-4 py-3 text-xs text-zinc-400 animate-pulse">Hledám celou sérii na všech zdrojích…</p>;
  if (!offers.wanted.length) return <p className="px-4 py-3 text-xs text-zinc-500">V této sérii nic nechybí.</p>;

  const busy = new Set(busyEpisodes);
  const chosen = items.filter((i) => !skip.has(i.episode) && !busy.has(i.episode));
  const missing = offers.wanted.filter((e) => !items.some((i) => i.episode === e));
  const setOf = (key: string) => offers.sets.find((s) => s.key === key);

  async function start(list: { episode: number; row: ScoredFile }[]) {
    setState("busy");
    try {
      const r = await downloadSeason(tmdbId, season, list);
      setState(r.errors.length ? `Spuštěno ${r.started}, chyby: ${r.errors.join("; ")}` : `Spuštěno ${r.started} stahování — průběh je u dílů níže`);
      setSkip(new Set(list.map((i) => i.episode)));      // not offered again by a second click
      onStarted();
    } catch (e) {
      setState(e instanceof Error ? e.message : "Chyba");
    }
  }

  return (
    <div className="space-y-3 border-y border-violet-900/40 bg-zinc-950/60 px-4 py-3 text-xs">
      <div>
        <p className="mb-1.5 text-zinc-400">
          Vydání ({offers.sets.length}) — chybí díly {offers.wanted.map((e) => `E${String(e).padStart(2, "0")}`).join(", ")}.
          Vyber, ze kterého se má stahovat; díry doplní ostatní (CZ/SK má přednost).
        </p>
        <div className="space-y-1">
          {offers.sets.map((s) => (
            <label key={s.key} className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 ${primary === s.key ? "bg-violet-950/50" : "hover:bg-zinc-900"}`}>
              <input type="radio" checked={primary === s.key} onChange={() => setPrimary(s.key)} />
              <span className={`w-12 text-right ${s.coverage === offers.wanted.length ? "text-emerald-300" : "text-zinc-300"}`}>
                {s.coverage}/{offers.wanted.length}
              </span>
              <span className="w-8 rounded bg-zinc-800 text-center text-zinc-200">{s.score}</span>
              <span className="w-12 text-zinc-400">{s.resolution}</span>
              <span className={`w-20 ${s.local ? "text-emerald-300" : "text-amber-300"}`}>{s.langs.map(L).join("+") || "?"}</span>
              <span className="w-14 text-zinc-500">{gb(s.size)}</span>
              <span className="w-10 text-zinc-500">{s.sources.map((x) => SRC[x] ?? x).join("/")}</span>
              <span className="min-w-0 flex-1 truncate text-zinc-300" title={s.label}>{s.label}</span>
            </label>
          ))}
        </div>
      </div>

      {items.length > 0 && (
        <div>
          <p className="mb-1 text-zinc-400">Plán stahování</p>
          <div className="space-y-0.5">
            {items.map((i) => {
              const other = i.set !== primary;
              return (
                <label key={i.episode} className="flex items-center gap-2 px-2">
                  <input type="checkbox" checked={!skip.has(i.episode) && !busy.has(i.episode)} disabled={busy.has(i.episode)}
                    onChange={(e) => setSkip((prev) => { const n = new Set(prev); if (e.target.checked) n.delete(i.episode); else n.add(i.episode); return n; })} />
                  <span className="w-10 font-mono text-zinc-400">E{String(i.episode).padStart(2, "0")}</span>
                  <span className="w-14 text-zinc-500">{gb(i.row.size)}</span>
                  <span className="min-w-0 flex-1 truncate text-zinc-300" title={i.row.name}>{i.row.name}</span>
                  {other && <span className="text-amber-300" title={setOf(i.set)?.label}>z jiného vydání</span>}
                  {busy.has(i.episode) ? <span className="text-violet-300">už se stahuje</span>
                    : ownedEpisodes.includes(i.episode) && <span className="text-zinc-500">nahradí stažený</span>}
                </label>
              );
            })}
          </div>
          {missing.length > 0 && <p className="mt-1 text-red-300">Nenalezeno: {missing.map((e) => `E${String(e).padStart(2, "0")}`).join(", ")}</p>}
          {can("download") && (
            <div className="mt-2 flex items-center gap-3">
              <button disabled={!chosen.length || state === "busy"} onClick={() => start(chosen)}
                className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
                Stáhnout {chosen.length} {chosen.length === 1 ? "díl" : chosen.length < 5 ? "díly" : "dílů"} ({gb(chosen.reduce((a, i) => a + i.row.size, 0))})
              </button>
              {state && state !== "busy" && <span className="text-zinc-400">{state}</span>}
            </div>
          )}
        </div>
      )}

      {offers.packs.length > 0 && (
        <div>
          <p className="mb-1 text-zinc-400">Torrent balíky (celá série najednou — přinese i díly, které už máš)</p>
          {offers.packs.map((p) => (
            <div key={p.ident} className="flex items-center gap-2 px-2 py-0.5">
              <span className="w-14 text-zinc-500">{gb(p.size)}</span>
              <span className="w-10 text-zinc-500">{p.seeders ?? 0} s</span>
              <span className="min-w-0 flex-1 truncate text-zinc-300" title={`${p.name}\n${(p.film_reasons ?? []).join(", ")}`}>{p.name}</span>
              {can("download") && (
                <button onClick={() => start([{ episode: offers.wanted[0], row: p }])} disabled={state === "busy"}
                  className="text-violet-300 hover:text-violet-200 disabled:opacity-40">Stáhnout balík</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
