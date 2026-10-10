"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AudioSyncJob, DubEpisode, getAudioSyncJob, getSeasonDubPlan, startSeasonDub } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

/** A season's dub moved from the old versions of its episodes into the new ones (backend audiosync/episodes.py):
 *  which episode gets it from which file, then a background job — each episode measured (speed, offset, cuts,
 *  the picture where the sound is not enough), built, checked; the old version goes when asked. */

const LANGS = ["sk", "cs", "en"];
const STATUS: Record<DubEpisode["status"], [string, string]> = {
  ready: ["připraveno", "text-violet-300"], done: ["už má", "text-emerald-400"], skip: ["nejde", "text-zinc-500"],
  ok: ["přeneseno", "text-emerald-300"], unsure: ["nejisté — ručně", "text-amber-300"], error: ["chyba", "text-red-300"],
};
const PHASE: Record<string, string> = {
  start: "začínám", speed: "zjišťuji rychlost", windows: "porovnávám úseky", cuts: "hledám střihy",
  picture: "kontroluji obraz", prepare: "připravuji stopu", mux: "skládám soubor", verify: "kontroluji výsledek",
};

export default function SeasonDub({ tmdbId, season, onDone }: { tmdbId: number; season: number; onDone: () => void }) {
  const { can } = useAuth();
  const [lang, setLang] = useState("sk");
  const [plan, setPlan] = useState<DubEpisode[] | null>(null);
  const [error, setError] = useState("");
  const [del, setDel] = useState(true);
  const [job, setJob] = useState<(AudioSyncJob & { report?: DubEpisode[] }) | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  const running = !!job?.running;

  const load = useCallback(() => {
    setPlan(null);
    getSeasonDubPlan(tmdbId, season, lang).then((r) => setPlan(r.episodes)).catch((e) => setError(e.message));
  }, [tmdbId, season, lang]);
  useEffect(load, [load]);

  // a running job of this season (also after reopening the page)
  useEffect(() => {
    let live = true;
    const tick = async () => {
      const j = await getAudioSyncJob().catch(() => null) as (AudioSyncJob & { report?: DubEpisode[] }) | null;
      if (!live || !j || j.kind !== "dub" || j.tmdb_id !== tmdbId || j.season !== season) return;
      setJob(j);
      if (j.running) setTimeout(tick, 3000);
      else if (running) { load(); done.current(); }
    };
    tick();
    return () => { live = false; };
  }, [tmdbId, season, load, running]);

  const ready = (plan ?? []).filter((p) => p.status === "ready");
  const report = new Map((job?.report ?? []).map((r) => [r.episode, r]));

  async function start() {
    setError("");
    try {
      setJob(await startSeasonDub(tmdbId, season, lang, del));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    }
  }

  return (
    <div className="space-y-2 border-y border-sky-900/40 bg-zinc-950/60 px-4 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-zinc-300">Přenést dabing z jiných verzí dílů:</span>
        <select value={lang} onChange={(e) => setLang(e.target.value)} disabled={!!job?.running}
          className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-zinc-200">
          {LANGS.map((l) => <option key={l} value={l}>{l === "cs" ? "CZ" : l.toUpperCase()}</option>)}
        </select>
        {can("library.delete") && (
          <label className="flex items-center gap-1 text-zinc-400" title="Stará verze se smaže až po úspěšném přenosu a kontrole">
            <input type="checkbox" checked={del} onChange={(e) => setDel(e.target.checked)} disabled={!!job?.running} />
            starou verzi pak smazat
          </label>
        )}
        {job?.running ? (
          <span className="text-sky-300 animate-pulse">
            {job.done ?? 0}/{job.total} · {job.current} · {PHASE[job.phase ?? ""] ?? job.phase}
          </span>
        ) : (
          <button disabled={!ready.length} onClick={start}
            className="rounded bg-sky-700 px-3 py-1 font-medium text-white hover:bg-sky-600 disabled:opacity-40">
            Přenést ({ready.length} {ready.length === 1 ? "díl" : ready.length < 5 ? "díly" : "dílů"})
          </button>
        )}
        {error && <span className="text-red-400">{error}</span>}
      </div>
      <p className="text-zinc-500">
        Každý díl: změří rychlost (PAL), posun a střihy, kde zvuk nestačí, pomůže obraz; výsledek zkontroluje. Díl, kde si
        Lumina není jistá, nechá být. Trvá to asi 3–4 minuty na díl.
      </p>
      {!plan ? <p className="text-zinc-500 animate-pulse">Načítám…</p> : (
        <div className="space-y-0.5">
          {plan.map((p) => {
            const r = report.get(p.episode);
            const st = r?.status ?? p.status;
            return (
              <div key={p.episode} className="flex items-center gap-2">
                <span className="w-10 font-mono text-zinc-400">E{String(p.episode).padStart(2, "0")}</span>
                <span className={`w-28 ${STATUS[st][1]}`}>{STATUS[st][0]}</span>
                <span className="min-w-0 flex-1 truncate text-zinc-400" title={`${p.target}\n← ${p.source}`}>
                  {p.source ? `${p.source} → ${p.target}` : p.target}
                </span>
                <span className="max-w-[45%] truncate text-zinc-500" title={(r as { picture?: string[] } | undefined)?.picture?.join("\n") ?? ""}>
                  {r?.note || p.note}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
