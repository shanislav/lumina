"use client";

import { useEffect, useMemo, useState } from "react";
import {
  TvAiSuggestion, TvCatalogEpisode, TvFolderDetail, TvFolderFile, getScanStatus, getTvFolder, scanLibrary,
  setEpisodeOverride, suggestEpisodesAi,
} from "@/lib/api";

/**
 * „Upravit díly“ — which TMDB episode each file of a show folder is, for what the name can not tell (another
 * language, an order of the uploader's own). The user picks the episode, or accepts an AI (Gemini / Groq) suggestion;
 * either becomes the user's word (backend tv_episode_overrides) — the scan and the renamer follow it.
 */

const STATUS: Record<string, { label: string; cls: string }> = {
  ok: { label: "v pořádku", cls: "text-zinc-500" },
  tmdb_other: { label: "jiné pořadí", cls: "text-amber-300" },
  not_in_tmdb: { label: "TMDB nezná", cls: "text-amber-300" },
  numbers: { label: "jiné číslo než Plex", cls: "text-amber-300" },
  not_in_plex: { label: "Plex nezná", cls: "text-zinc-400" },
  unknown: { label: "neznámý díl", cls: "text-red-300" },
  show: { label: "sporný seriál", cls: "text-red-300" },
};

const se = (s: number | null, e: number | null) =>
  s == null || e == null ? "—" : `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
const key = (s: number, e: number) => `${s}:${e}`;
const minutes = (sec: number) => (sec ? `${Math.round(sec / 60)} min` : "");

function epLabel(ep: TvCatalogEpisode): string {
  const names = [ep.cs, ep.en].filter((n, i, all) => n && all.indexOf(n) === i);
  return `${se(ep.season, ep.episode)} · ${names.join(" / ") || "bez názvu"}${ep.runtime ? ` (${ep.runtime} min)` : ""}`;
}

export default function EpisodeMapper({ folder, title, onClose, onSaved }: {
  folder: string; title?: string; onClose: () => void; onSaved?: () => void;
}) {
  const [data, setData] = useState<TvFolderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [season, setSeason] = useState<number | "all">("all");
  const [onlyProblems, setOnlyProblems] = useState(true);
  const [choice, setChoice] = useState<Record<string, string>>({});     // file → "s:e" | "" (no word)
  const [ai, setAi] = useState<Record<string, TvAiSuggestion>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<number | null>(null);

  async function load() {
    try {
      const d = await getTvFolder(folder);
      setData(d);
      setChoice({});
      const problems = d.files.some((f) => f.status !== "ok" || f.manual);
      setOnlyProblems(problems);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [folder]);

  const seasons = useMemo(() => {
    const count: Record<number, number> = {};
    for (const f of data?.files ?? []) if (f.season != null) count[f.season] = (count[f.season] ?? 0) + 1;
    return Object.entries(count).map(([s, n]) => [Number(s), n] as const).sort((a, b) => a[0] - b[0]);
  }, [data]);

  const bySeason = useMemo(() => {
    const out: Record<number, TvCatalogEpisode[]> = {};
    for (const ep of data?.episodes ?? []) (out[ep.season] ??= []).push(ep);
    return out;
  }, [data]);

  const known = useMemo(() => new Set((data?.episodes ?? []).map((ep) => key(ep.season, ep.episode))), [data]);

  const files = (data?.files ?? []).filter((f) => (season === "all" || f.season === season)
    && (!onlyProblems || f.status !== "ok" || f.manual || choice[f.file] !== undefined || ai[f.file]));

  const current = (f: TvFolderFile) => (f.season != null && f.episode != null ? key(f.season, f.episode) : "");
  const changes = Object.entries(choice).filter(([file, v]) => {
    const f = data?.files.find((x) => x.file === file);
    return f && (v !== current(f) || (v === "" && f.manual) || (v !== "" && !f.manual));
  });

  async function askAi() {
    if (!data) return;
    setBusy("ai");
    setError(null);
    try {
      const r = await suggestEpisodesAi(folder, season === "all" ? null : season, files.map((f) => f.file));
      const next: Record<string, TvAiSuggestion> = {};
      for (const s of r.suggestions) next[s.file] = s;
      setAi(next);
      if (!r.suggestions.length) setError("AI nic nenavrhla.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(null);
    }
  }

  function acceptAi(only?: string) {
    const next = { ...choice };
    for (const [file, s] of Object.entries(ai)) {
      if (only ? file === only : s.confidence >= 70 && !s.same && !s.warning) next[file] = key(s.season, s.episode);
    }
    setChoice(next);
  }

  async function save() {
    setBusy("save");
    setError(null);
    try {
      for (const [file, v] of changes) {
        if (v) {
          const [s, e] = v.split(":").map(Number);
          const a = ai[file];
          await setEpisodeOverride(file, s, e, a && a.season === s && a.episode === e ? `AI návrh (${a.confidence} %)` : "ručně");
        } else {
          await setEpisodeOverride(file, null, null);
        }
      }
      setSaved(changes.length);
      await load();
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(null);
    }
  }

  async function rescan() {
    setBusy("scan");
    try {
      await scanLibrary(false);
      for (;;) {
        await new Promise((r) => setTimeout(r, 2000));
        if (!(await getScanStatus()).running) break;
      }
      setSaved(null);
      await load();
      onSaved?.();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => !busy && onClose()}>
      <div className="mx-2 flex max-h-[94vh] w-full max-w-6xl flex-col gap-3 rounded-xl border border-zinc-700 bg-zinc-900 p-4 sm:mx-4 sm:p-5"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-lg font-semibold text-zinc-100">Díly ve složce „{folder}“</h3>
            <p className="text-xs text-zinc-500">
              {title ? `${title} · ` : ""}Když název souboru neřekne, který díl to je (jiný jazyk, vlastní pořadí), urči ho tady
              — nebo nech AI navrhnout. Uložené určení má přednost; projeví se po skenu a „Opravit názvy seriálů“.
            </p>
          </div>
          <button onClick={onClose} disabled={!!busy} className="text-sm text-zinc-500 hover:text-zinc-300">Zavřít</button>
        </div>

        {data && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <button onClick={() => setSeason("all")}
              className={`rounded px-2 py-1 ${season === "all" ? "bg-violet-700 text-white" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"}`}>
              vše ({data.files.length})
            </button>
            {seasons.map(([s, n]) => (
              <button key={s} onClick={() => setSeason(s)}
                className={`rounded px-2 py-1 ${season === s ? "bg-violet-700 text-white" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"}`}>
                {s === 0 ? "Speciály" : `S${String(s).padStart(2, "0")}`} ({n})
              </button>
            ))}
            <label className="ml-auto flex items-center gap-1 text-zinc-400">
              <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} /> jen k řešení
            </label>
          </div>
        )}

        {data && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <button onClick={askAi} disabled={!!busy || !data.groq || !files.length}
              title={data.groq ? "AI (Gemini a Groq) porovná názvy souborů (i přeložené), pořadí a délky s díly v TMDB" : "AI není nastavená (Nastavení → AI)"}
              className="rounded border border-violet-700 px-2.5 py-1 text-violet-200 hover:bg-violet-950 disabled:opacity-40">
              {busy === "ai" ? "AI přemýšlí…" : `✨ Navrhnout pomocí AI (${files.length} souborů)`}
            </button>
            {Object.keys(ai).length > 0 && (
              <button onClick={() => acceptAi()} className="rounded border border-zinc-700 px-2.5 py-1 text-zinc-300 hover:border-violet-500">
                Převzít jisté návrhy (≥ 70 %)
              </button>
            )}
            {!data.groq && <span className="text-zinc-500">AI návrh potřebuje klíč Groq nebo Gemini v Nastavení.</span>}
            {Object.keys(ai).length > 0 && (() => {
              const all = Object.values(ai);
              const same = all.filter((x) => x.same && !x.warning).length;
              const other = all.filter((x) => !x.same && !x.warning).length;
              const doubt = all.filter((x) => x.warning).length;
              return (
                <span className="text-zinc-400">
                  AI: <span className="text-emerald-300">{same} souhlasí</span>
                  {other > 0 && <> · <span className="text-violet-300">{other} jiný díl</span></>}
                  {doubt > 0 && <> · <span className="text-amber-300">{doubt} nejisté</span></>}
                  {files.length - all.length > 0 && <> · {files.length - all.length} bez odpovědi</>}
                </span>
              );
            })()}
            {error && <span className="text-red-400">{error}</span>}
          </div>
        )}

        {saved !== null && (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-emerald-800 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-200">
            Uloženo {saved} změn. Projeví se po skenu knihovny, na disku pak přes „Opravit názvy seriálů“.
            <button onClick={rescan} disabled={!!busy} className="rounded bg-emerald-700 px-2 py-0.5 text-white hover:bg-emerald-600 disabled:opacity-50">
              {busy === "scan" ? "Skenuji…" : "Skenovat teď"}
            </button>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-zinc-800">
          {!data && !error && <p className="animate-pulse p-4 text-sm text-zinc-500">Načítám…</p>}
          {data && !files.length && <p className="p-4 text-sm text-emerald-300">Tady je všechno v pořádku.</p>}
          {data && files.map((f) => {
            const v = choice[f.file] ?? current(f);
            const changed = choice[f.file] !== undefined && changes.some(([x]) => x === f.file);
            const s = ai[f.file];
            const st = f.manual ? { label: "určeno ručně", cls: "text-violet-300" } : STATUS[f.status] ?? { label: f.status, cls: "text-zinc-400" };
            return (
              <div key={f.file} className={`grid grid-cols-[minmax(0,1fr)] gap-2 border-b border-zinc-800/70 px-3 py-2 text-xs md:grid-cols-[minmax(0,1.3fr)_7rem_minmax(0,1.4fr)_minmax(0,1fr)] md:items-center ${changed ? "bg-violet-950/30" : ""}`}>
                <div className="min-w-0">
                  <p className="truncate text-zinc-200" title={f.file}>{f.own || f.name}</p>
                  <p className="truncate text-[11px] text-zinc-500" title={f.note || f.name}>
                    {[f.own ? f.name : "", minutes(f.duration), f.plex && f.plex[2] ? `Plex: ${f.plex[2]}` : ""].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div>
                  <span className="font-mono text-zinc-300">{se(f.season, f.episode)}</span>
                  <span className={`ml-2 md:ml-0 md:block ${st.cls}`}>{st.label}</span>
                </div>
                <div className="flex min-w-0 items-center gap-1">
                  <select value={v} onChange={(e) => setChoice({ ...choice, [f.file]: e.target.value })}
                    className="w-full min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-950 px-1.5 py-1 text-zinc-200 outline-none focus:border-violet-500">
                    {!v && <option value="">— vyber díl —</option>}
                    {v && !known.has(v) && <option value={v}>{se(...(v.split(":").map(Number) as [number, number]))} · tohle číslo TMDB nezná</option>}
                    {Object.entries(bySeason).map(([sn, eps]) => (
                      <optgroup key={sn} label={Number(sn) === 0 ? "Speciály" : `Série ${sn}`}>
                        {eps.map((ep) => <option key={key(ep.season, ep.episode)} value={key(ep.season, ep.episode)}>{epLabel(ep)}</option>)}
                      </optgroup>
                    ))}
                  </select>
                  {f.manual && (
                    <button title="Zrušit ruční určení — rozhodne zase sken" onClick={() => setChoice({ ...choice, [f.file]: "" })}
                      className="rounded px-1.5 py-1 text-zinc-500 hover:bg-zinc-800 hover:text-red-300">✕</button>
                  )}
                </div>
                <div className="min-w-0">
                  {s && s.same && !s.warning ? (
                    <p className="text-[11px] text-emerald-400/80">✓ AI souhlasí ({s.confidence} %)</p>
                  ) : s ? (
                    <div className="flex items-center gap-1.5">
                      <button onClick={() => acceptAi(f.file)} disabled={s.same}
                        title={s.same ? "AI souhlasí s tím, co soubor má" : "Převzít tento návrh"}
                        className={`min-w-0 flex-1 truncate rounded border px-1.5 py-1 text-left ${s.same ? "border-zinc-800 text-zinc-500" : "border-violet-800 text-violet-200 hover:bg-violet-950"}`}>
                        ✨ {se(s.season, s.episode)} · {s.title}
                      </button>
                      <span className={`w-10 text-right ${s.confidence >= 70 ? "text-emerald-300" : s.confidence >= 40 ? "text-amber-300" : "text-red-300"}`}>{s.confidence} %</span>
                    </div>
                  ) : null}
                  {s?.heard && <p className="text-[11px] text-sky-300" title="Soubor nemá název dílu — AI porovnala jeho titulky s popisy dílů">podle titulků</p>}
                  {s?.agrees && !s.same && <p className="text-[11px] text-emerald-400">souhlasí s pravidly Luminy</p>}
                  {s?.rules && <p className="text-[11px] text-amber-400">pravidla Luminy: {s.rules}</p>}
                  {s?.warning && <p className="text-[11px] text-red-400">{s.warning}</p>}
                </div>
              </div>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-3">
          {changes.length > 0 && <span className="text-xs text-zinc-400">{changes.length} změn k uložení</span>}
          <button onClick={() => setChoice({})} disabled={!changes.length || !!busy} className="text-sm text-zinc-500 hover:text-zinc-300 disabled:opacity-40">Vrátit</button>
          <button onClick={save} disabled={!changes.length || !!busy}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:bg-zinc-700">
            {busy === "save" ? "Ukládám…" : "Uložit určení"}
          </button>
        </div>
      </div>
    </div>
  );
}
