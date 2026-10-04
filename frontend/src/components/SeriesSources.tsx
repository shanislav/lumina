"use client";

import { useCallback, useEffect, useState } from "react";
import { OverviewPack, OverviewSet, SeriesOverview, getSeriesOverview, startSeriesOverview } from "@/lib/api";

/** Přehled zdrojů (backend series/overview.py): season by season where the show can be downloaded from — the best
 *  releases on WebShare / FastShare (one sample file of each verified), the season's torrent packs, the packs of
 *  the whole show — before deciding. ★ = what the automation would take (one uploader for the whole show). */

const gb = (b: number) => (b >= 1e12 ? `${(b / 1e12).toFixed(1)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.round(b / 1e6)} MB`);
const LANG: Record<string, string> = { cs: "CZ", sk: "SK", en: "EN" };
const langs = (l: string[]) => l.map((x) => LANG[x] ?? x.toUpperCase()).join("+") || "zvuk ?";
const SRC: Record<string, string> = { webshare: "WS", fastshare: "FS", jackett: "torrent", prowlarr: "torrent" };

function SetCell({ st, picked }: { st: OverviewSet; picked: boolean }) {
  return (
    <div className={`min-w-0 rounded px-2 py-1 ${picked ? "bg-violet-950/50 ring-1 ring-violet-700" : ""}`}>
      <p className="truncate text-zinc-200" title={st.label}>{picked && "★ "}{st.label}</p>
      <p className="text-zinc-400">
        <span className={st.coverage >= st.aired ? "text-emerald-300" : "text-amber-300"}>{st.coverage}/{st.aired} dílů</span>
        {" · "}{st.resolution}{st.codec && ` ${st.codec}`}
        {" · "}<span className={st.local ? "text-emerald-300" : st.local_subs ? "text-sky-300" : ""}>{langs(st.langs)}{!st.local && st.local_subs ? " + tit." : ""}</span>
        {" · "}⌀ {gb(st.episode_size)} · {st.sources.map((s) => SRC[s] ?? s).join("+")}
        {st.verified ? <span className="text-emerald-400" title="Vzorový soubor ověřen u zdroje"> ✓</span> : <span className="text-zinc-600" title="Jen podle názvu"> ?</span>}
        {st.fits < st.coverage && <span className="text-orange-300" title="Díly, které nesplní profil"> · mimo profil {st.coverage - st.fits}</span>}
      </p>
    </div>
  );
}

function PackCell({ p }: { p: OverviewPack }) {
  return (
    <div className="min-w-0 px-2 py-1">
      <p className="truncate text-zinc-200" title={p.name}>{p.fits ? "✓ " : ""}{p.name}</p>
      <p className="text-zinc-400">
        {p.resolution || "?"}{p.codec && ` ${p.codec}`} · <span className={p.lang_tier >= 2 ? "text-emerald-300" : ""}>{langs(p.langs)}</span>
        {" · "}{gb(p.size)} · {p.seeders} seedů{p.verified ? <span className="text-emerald-400"> ✓</span> : ""}
        {!p.fits && <span className="text-orange-300"> · nesplní profil / zvuk</span>}
      </p>
    </div>
  );
}

export default function SeriesSources({ tmdbId, canStart }: { tmdbId: number; canStart: boolean }) {
  const [data, setData] = useState<SeriesOverview | null>(null);
  const [open, setOpen] = useState(false);
  const load = useCallback(() => { getSeriesOverview(tmdbId).then(setData).catch(() => {}); }, [tmdbId]);
  useEffect(() => { load(); }, [load]);
  const running = !!data?.job.mine;
  useEffect(() => {
    if (!running) return;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [data, running, load]);
  useEffect(() => { if (running) setOpen(true); }, [running]);
  if (!data) return null;
  const o = data.data;

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
        <button onClick={() => setOpen(!open)} className="text-sm text-zinc-200">🔎 Přehled zdrojů po sériích {open ? "▲" : "▼"}</button>
        <span className="text-zinc-500">
          {running ? <span className="text-violet-300 animate-pulse">zjišťuji {data.job.done}/{data.job.total || "…"}{data.job.current && ` · ${data.job.current}`}</span>
            : o ? `z ${o.created_at} · profil ${o.profile}` : "odkud a v jaké kvalitě se dá stáhnout — každá série, pár vzorků"}
        </span>
        {o?.estimate && !running && <SpaceLine e={o.estimate} />}
        {canStart && !running && (
          <button onClick={async () => { await startSeriesOverview(tmdbId); setOpen(true); load(); }}
            className="ml-auto rounded bg-zinc-800 px-2.5 py-1 text-zinc-200 hover:bg-zinc-700">{o ? "Zjistit znovu" : "Zjistit"}</button>
        )}
      </div>
      {open && o && (
        <div className="space-y-3 px-4 pb-4">
          {o.torrent && (
            <div>
              <p className="mb-1 text-zinc-300">Celý seriál z torrentu</p>
              {o.whole.length ? <div className="divide-y divide-zinc-800/70 rounded border border-zinc-800">
                {o.whole.map((p) => <PackCell key={p.ident} p={{ ...p, name: `${p.name}${p.covers ? ` (${p.covers} sérií)` : ""}` }} />)}
              </div> : <p className="text-zinc-500">nenalezen</p>}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] table-fixed border-collapse">
              <thead>
                <tr className="text-left text-zinc-500">
                  <th className="w-16 py-1 font-normal">Série</th>
                  <th className="w-20 font-normal">mám</th>
                  <th className="font-normal">WebShare / FastShare — nejlepší vydání (★ = bere automatika)</th>
                  {o.torrent && <th className="w-[34%] font-normal">Torrent — balík série</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70 align-top">
                {o.seasons.map((r) => (
                  <tr key={r.season}>
                    <td className="py-1.5 font-mono text-zinc-300">S{String(r.season).padStart(2, "0")}</td>
                    <td className="py-1.5 text-zinc-400">{r.owned}/{r.aired}</td>
                    <td className="py-1 space-y-0.5">
                      {r.error ? <p className="text-red-400">hledání selhalo</p>
                        : r.sets.length ? r.sets.map((st) => <SetCell key={st.key} st={st} picked={st.key === r.pick} />)
                          : <p className="px-2 text-zinc-500">nic</p>}
                    </td>
                    {o.torrent && (
                      <td className="py-1">
                        {r.packs.length ? r.packs.map((p) => <PackCell key={p.ident} p={p} />) : <p className="px-2 text-zinc-500">—</p>}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-zinc-600">
            Z každého vydání se u zdroje ověří jeden vzorový soubor (✓), ostatní díly vydání jsou kódované stejně. ★ = vydání, které
            by vzala automatika: od jednoho uploadera pro celý seriál, kde to jde. „Chci“ stáhne balík série, kde ✓ sedí na profil a zvuk.
          </p>
        </div>
      )}
      {open && !o && !running && <p className="px-4 pb-3 text-zinc-500">Zatím nezjištěno.</p>}
    </section>
  );
}

/** "≈ 230 GB místa" — what the missing episodes take. */
export function SpaceLine({ e, big = false }: { e: NonNullable<NonNullable<SeriesOverview["data"]>["estimate"]>; big?: boolean }) {
  return (
    <span className={big ? "text-sm text-zinc-300" : "text-zinc-300"}
      title={`Chci: ${e.way === "whole" ? "balík celého seriálu" : "balíky sérií, kde sedí, zbytek po dílech"}. Po dílech celkem ${gb(e.episodes)}.`
        + (e.unknown ? ` U ${e.unknown} sérií nic nenalezeno — nezapočítáno.` : "")}>
      💾 ≈ {gb(e.chci)} místa{e.unknown ? <span className="text-zinc-500"> (+ {e.unknown} sérií neznámo)</span> : ""}
    </span>
  );
}
