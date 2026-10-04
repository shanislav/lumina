"use client";

import Link from "next/link";
import { gb as packSize } from "@/components/ShowPacks";
import { useCallback, useEffect, useState } from "react";
import {
  SeriesAutoFrom, SeriesAutoMode, SeriesAutoOverview, SeriesAutoRecord, SeriesAutoShow, SeriesSettingValues,
  QualityProfile, SeriesLangMode, WantShowResult, getProfiles, wantShow,
  cancelAutoAll, dismissAutoFound, downloadAutoFound, getSeriesAutomation, getShowAutomation, runSeriesAutomation,
  saveSeriesAutomationBulk,
} from "@/lib/api";

/** TV show automation (backend modules/series/auto.py): new episodes and the CZ/SK dub, each off / show / download,
 *  per show on top of the defaults; the scheduler's nightly run checks them. */

export const AUTO_NEW: [SeriesAutoMode, string][] = [
  ["off", "nehlídat"], ["notify", "hledat, jen ukázat"], ["download", "hledat a stáhnout"],
];
export const AUTO_FROM: [SeriesAutoFrom, string][] = [
  ["next", "jen díly po posledním, který mám"], ["all", "všechny chybějící díly"],
];
export const AUTO_DUB: [SeriesAutoMode, string][] = [
  ["off", "nehledat"], ["notify", "hledat, jen ukázat"], ["download", "hledat a nahradit EN díl"],
];
export const AUTO_UPGRADE: [SeriesAutoMode, string][] = [
  ["off", "nehledat"], ["notify", "hledat, jen ukázat"], ["download", "hledat a nahradit"],
];
const KIND: Record<string, [string, string]> = {
  new: ["nový díl", "bg-emerald-900/60 text-emerald-200"],
  dub: ["dabing", "bg-sky-900/60 text-sky-200"],
  upgrade: ["lepší kvalita", "bg-violet-900/60 text-violet-200"],
};

const label = (list: [string, string][], v: string | null | undefined) => list.find(([k]) => k === v)?.[1] ?? "";
const field = "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs text-zinc-200 disabled:opacity-50";

/** A select of one setting; with ``fallback`` the first option is "výchozí (…)" = null. */
export function AutoSelect<T extends string>({ value, options, fallback, disabled, onChange, className }: {
  value: T | null; options: [T, string][]; fallback?: T; disabled?: boolean; onChange: (v: T | null) => void; className?: string;
}) {
  return (
    <select disabled={disabled} value={value ?? ""} className={className ?? field}
      onChange={(e) => onChange((e.target.value || null) as T | null)}>
      {fallback !== undefined && <option value="">výchozí ({label(options, fallback)})</option>}
      {options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );
}

/** The three automation settings as rows of a settings grid (show page, defaults). */
export function AutoFields({ own, defaults, langMode, disabled, onChange }: {
  own: Pick<SeriesSettingValues, "auto_new" | "auto_from" | "auto_dub" | "auto_upgrade">;
  defaults?: Pick<SeriesSettingValues, "auto_new" | "auto_from" | "auto_dub" | "auto_upgrade">;  // none: the defaults themselves
  langMode: string; disabled?: boolean; onChange: (values: Partial<SeriesSettingValues>) => void;
}) {
  const effNew = own.auto_new ?? defaults?.auto_new ?? "off";
  return (
    <>
      <label className="text-zinc-400">Nové díly</label>
      <AutoSelect value={own.auto_new} options={AUTO_NEW} fallback={defaults ? (defaults.auto_new ?? "off") : undefined}
        disabled={disabled} onChange={(v) => onChange({ auto_new: v ?? (defaults ? null : "off") })} />
      {effNew !== "off" && <>
        <label className="text-zinc-400">Které díly</label>
        <AutoSelect value={own.auto_from} options={AUTO_FROM} fallback={defaults ? (defaults.auto_from ?? "next") : undefined}
          disabled={disabled} onChange={(v) => onChange({ auto_from: v ?? (defaults ? null : "next") })} />
      </>}
      <label className="text-zinc-400 self-start pt-1">CZ/SK dabing</label>
      <div className="space-y-1">
        <AutoSelect value={own.auto_dub} options={AUTO_DUB} fallback={defaults ? (defaults.auto_dub ?? "off") : undefined}
          disabled={disabled} onChange={(v) => onChange({ auto_dub: v ?? (defaults ? null : "off") })} />
        <p className="text-[11px] text-zinc-500">
          {langMode === "local_or_temp"
            ? "U dílů, které máš jen anglicky, hledá český nebo slovenský zvuk."
            : "Platí jen pro jazyk „CZ/SK, jinak hned EN“."}
        </p>
      </div>
      <label className="text-zinc-400 self-start pt-1">Lepší kvalita</label>
      <div className="space-y-1">
        <AutoSelect value={own.auto_upgrade} options={AUTO_UPGRADE} fallback={defaults ? (defaults.auto_upgrade ?? "off") : undefined}
          disabled={disabled} onChange={(v) => onChange({ auto_upgrade: v ?? (defaults ? null : "off") })} />
        <p className="text-[11px] text-zinc-500">U dílů, které nesplní profil kvality seriálu (třeba 480p, když chce aspoň 720p, nebo pod cílovým skóre).
          Jen s vyšším skóre a bez ztráty CZ/SK zvuku.</p>
      </div>
    </>
  );
}

const se = (r: { season: number; episode: number }) =>
  `S${String(r.season).padStart(2, "0")}E${String(r.episode).padStart(2, "0")}`;
const gb = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.round(b / 1e6)} MB`);

/** What the automation found for one show — "show" mode waits here for the click. */
export function AutoFound({ tmdbId, records, onChanged, canDownload }: {
  tmdbId: number; records: SeriesAutoRecord[]; onChanged: () => void; canDownload: boolean;
}) {
  const [busy, setBusy] = useState("");
  const found = records.filter((r) => r.status === "found");
  const going = records.filter((r) => r.status === "downloading");
  if (!found.length && !going.length) return null;
  const key = (r: SeriesAutoRecord): [number, number, string] => [r.season, r.episode, r.kind];

  async function act(fn: () => Promise<unknown>, label: string) {
    setBusy(label);
    try { await fn(); onChanged(); } catch (e) { setBusy(e instanceof Error ? e.message : "Chyba"); return; }
    setBusy("");
  }
  return (
    <div className="space-y-1.5">
      {found.map((r) => (
        <div key={`${r.season}-${r.episode}-${r.kind}`} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="font-mono text-zinc-300">{se(r)}</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] ${(KIND[r.kind] ?? KIND.new)[1]}`}>{(KIND[r.kind] ?? KIND.new)[0]}</span>
          <span className="order-last min-w-0 basis-full truncate text-zinc-400 sm:order-none sm:basis-0 sm:flex-1" title={r.row.name}>{r.row.name}</span>
          <span className="text-zinc-500">{(r.row.audio_langs || []).join("+").toUpperCase()} · {r.row.resolution || ""} · {gb(r.row.size || 0)}</span>
          {canDownload && <button disabled={!!busy} onClick={() => act(() => downloadAutoFound(tmdbId, [key(r)]), "…")}
            className="rounded bg-violet-600 px-2 py-0.5 text-white hover:bg-violet-500 disabled:opacity-50">Stáhnout</button>}
          <button disabled={!!busy} onClick={() => act(() => dismissAutoFound(tmdbId, [key(r)]), "…")} title="Tenhle soubor ne — příště nabídne jiný"
            className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-400 hover:text-zinc-200 disabled:opacity-50">Zahodit</button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        {found.length > 1 && canDownload && (
          <button disabled={!!busy} onClick={() => act(() => downloadAutoFound(tmdbId, found.map(key)), "…")}
            className="rounded bg-violet-700 px-2.5 py-1 text-xs text-white hover:bg-violet-600 disabled:opacity-50">
            Stáhnout vše ({found.length})
          </button>
        )}
        {found.length > 1 && (
          <button disabled={!!busy} onClick={() => act(() => dismissAutoFound(tmdbId, found.map(key)), "…")}
            title="Tyhle soubory ne — příště nabídne jiné" className="rounded bg-zinc-800 px-2.5 py-1 text-xs text-zinc-300 hover:text-zinc-100 disabled:opacity-50">
            Zahodit vše ({found.length})
          </button>
        )}
        {canDownload && found.length + going.length > 1 && (
          <button disabled={!!busy} onClick={() => {
            if (!confirm(`Zrušit vše, co automatika u tohoto seriálu našla nebo stahuje (${found.length + going.length}), `
              + "a vypnout ji?\n\nStahování, která běží nebo čekají ve frontě, se zruší (nedokončené soubory se smažou). "
              + "Co už je v knihovně, zůstane.")) return;
            act(() => cancelAutoAll(tmdbId, true), "…");
          }} className="ml-auto rounded border border-red-900/70 px-2.5 py-1 text-xs text-red-300 hover:bg-red-950/40 disabled:opacity-50">
            Zrušit vše a vypnout automatiku
          </button>
        )}
      </div>
      {going.length > 0 && (
        <p className="text-[11px] text-zinc-500">Automatika stahuje: {going.map((r) => `${se(r)}${r.kind !== "new" ? ` (${KIND[r.kind]?.[0]})` : ""}`).join(", ")}</p>
      )}
      {busy && busy !== "…" && <p className="text-[11px] text-red-400">{busy}</p>}
    </div>
  );
}

/** The show page: what the automation found + "check now". */
export function ShowAutomation({ tmdbId, on, canDownload, canEdit }: { tmdbId: number; on: boolean; canDownload: boolean; canEdit: boolean }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getShowAutomation>> | null>(null);
  const [started, setStarted] = useState(false);
  const load = useCallback(() => { getShowAutomation(tmdbId).then(setData).catch(() => {}); }, [tmdbId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {          // while it runs, follow it
    if (!data?.job.running && !started) return;
    const t = setTimeout(() => { load(); if (!data?.job.running) setStarted(false); }, 4000);
    return () => clearTimeout(t);
  }, [data, started, load]);
  if (!data || (!on && !data.records.length)) return null;
  const c = data.checked;
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 px-4 py-3 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="text-sm text-zinc-200">🤖 Automatika</span>
        <span className="text-zinc-500">
          {data.job.running && data.job.current ? `kontroluje se… (${data.job.current})`
            : c ? `kontrola ${c.checked_at.slice(0, 16)} — ${c.note || (c.wanted ? `hledáno ${c.wanted}, nalezeno ${c.found}, stahuje ${c.downloading}` : "nic nechybí")}`
              : "zatím nekontrolováno (v noci s plánovačem)"}
        </span>
        {canEdit && on && (
          <button disabled={started || data.job.running} onClick={async () => { await runSeriesAutomation([tmdbId]); setStarted(true); load(); }}
            className="ml-auto rounded bg-zinc-800 px-2 py-0.5 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50">
            {started || data.job.running ? "kontroluje se…" : "Zkontrolovat teď"}
          </button>
        )}
      </div>
      <AutoFound tmdbId={tmdbId} records={data.records} onChanged={load} canDownload={canDownload} />
    </section>
  );
}

const WANT_LANG: [SeriesLangMode, string][] = [
  ["local_or_temp", "CZ/SK, jinak zatím EN"], ["local_only", "jen CZ/SK"], ["original", "původní (EN…)"],
];

/** The show page of a show not owned: "Chci" — the quality (profile) and the sound, nothing else; Lumina decides
 *  where from (backend series/router.want_show): a fitting pack of the whole show from a torrent, or episode by
 *  episode. The manual search (packs, seasons) is further down the page. ``big``: the phone. */
export function WantShow({ tmdbId, aired, onDone, langDefault, big = false }: {
  tmdbId: number; aired: number; onDone: () => void;
  langDefault?: SeriesLangMode;
  big?: boolean;
}) {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [profile, setProfile] = useState<number | "">("");
  const [lang, setLang] = useState<SeriesLangMode | "">("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WantShowResult | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getProfiles("tv").then(setProfiles).catch(() => {}); }, []);
  const def = profiles.find((p) => p.is_default);

  async function want(mode: "download" | "notify") {
    setBusy(true);
    setError("");
    try {
      setResult(await wantShow(tmdbId, { profile_id: profile === "" ? null : profile, lang_mode: lang || null, mode }));
      setTimeout(onDone, 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se");
    } finally { setBusy(false); }
  }

  const sel = `rounded-lg border border-zinc-700 bg-zinc-900 text-zinc-200 ${big ? "min-h-12 w-full px-3 text-base" : "px-2 py-1 text-sm"}`;
  const btn = big ? "min-h-12 w-full rounded-xl px-4 text-base font-medium" : "rounded px-4 py-1.5 font-medium";
  return (
    <section className={`space-y-3 border border-violet-800/50 bg-violet-950/20 ${big ? "rounded-2xl p-4" : "rounded-xl px-4 py-3 text-sm"}`}>
      <p className={big ? "text-base text-zinc-200" : "text-zinc-200"}>Tenhle seriál zatím nemáš.</p>
      {result ? (
        result.way === "seasons" ? (
          <p className="text-emerald-300">
            ✓ Celý seriál najednou na torrentu není{result.why ? ` (${result.why})` : ""}. Lumina teď na pozadí projde série:
            kde je balík série v téhle kvalitě a zvuku, stáhne ho, zbytek díl po dílu. Průběh je v „Přehledu zdrojů po sériích“.
          </p>
        ) : result.way === "pack" && result.pack ? (
          <p className="text-emerald-300">
            ✓ Stahuje se celý seriál z torrentu: <span className="break-all text-zinc-200">{result.pack.name}</span>
            {" "}({packSize(result.pack.size)}{result.pack.is_dubbed ? ", dabing" : ""}). Díly se zařadí do sérií, nové pak hlídá automatika.
          </p>
        ) : (
          <p className="text-emerald-300">
            ✓ Automatika hledá díly po jednom (WebShare / FastShare{result.why ? "" : ", torrenty"}) — hned teď a pak každou noc.
            {result.why && <span className="block text-xs text-zinc-400">Celý seriál najednou ne: {result.why}.</span>}
          </p>
        )
      ) : (
        <>
          <div className={big ? "space-y-2" : "flex flex-wrap items-center gap-x-4 gap-y-2"}>
            <label className={big ? "block space-y-1 text-sm text-zinc-400" : "flex items-center gap-2 text-zinc-400"}>
              <span>Kvalita</span>
              <select value={profile} onChange={(e) => setProfile(e.target.value === "" ? "" : Number(e.target.value))} className={sel}>
                <option value="">výchozí ({def?.name ?? "—"})</option>
                {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label className={big ? "block space-y-1 text-sm text-zinc-400" : "flex items-center gap-2 text-zinc-400"}>
              <span>Zvuk</span>
              <select value={lang} onChange={(e) => setLang(e.target.value as SeriesLangMode | "")} className={sel}>
                <option value="">výchozí ({WANT_LANG.find(([k]) => k === langDefault)?.[1] ?? "—"})</option>
                {WANT_LANG.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
          </div>
          <div className={big ? "space-y-2" : "flex flex-wrap items-center gap-2"}>
            <button disabled={busy} onClick={() => want("download")}
              className={`${btn} bg-violet-600 text-white hover:bg-violet-500 active:bg-violet-700 disabled:opacity-50`}>
              {busy ? "Hledám, odkud nejlépe…" : "Chci"}
            </button>
            <button disabled={busy} onClick={() => want("notify")}
              className={`${btn} bg-zinc-800 text-zinc-200 hover:bg-zinc-700 disabled:opacity-50`}>Jen najít a ukázat</button>
          </div>
          <p className="text-xs text-zinc-500">
            Lumina sama vybere, odkud: když je na torrentech celý seriál ({aired} dílů) v téhle kvalitě a zvuku, stáhne ho
            najednou od jednoho uploadera; jinak díl po dílu z WebShare / FastShare. Nové díly pak hlídá každou noc.
            Ruční hledání je níž.
          </p>
          {error && <p className="text-red-400">{error}</p>}
        </>
      )}
    </section>
  );
}

/** The wanted page: shows the automation looks for that are not in the library yet. */
export function WantedShows() {
  const [shows, setShows] = useState<SeriesAutoShow[] | null>(null);
  useEffect(() => {
    getSeriesAutomation().then((d) => setShows(d.shows.filter((s) => !s.in_library &&
      s.effective.auto_new !== "off"))).catch(() => setShows([]));
  }, []);
  if (!shows?.length) return null;
  return (
    <div className="space-y-2">
      <h2 className="text-lg font-semibold text-zinc-100">Seriály</h2>
      {shows.map((s) => (
        <div key={s.tmdb_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-sm">
          <Link href={`/series?tmdb=${s.tmdb_id}`} className="text-zinc-100 hover:text-violet-300">{s.title || `TMDB ${s.tmdb_id}`}</Link>
          {s.year && <span className="text-zinc-500">({s.year})</span>}
          <span className="text-xs text-zinc-400">{label(AUTO_NEW, s.effective.auto_new)}</span>
          <span className="ml-auto text-xs text-zinc-500">
            {s.checked ? `kontrola ${s.checked.checked_at.slice(5, 16)} — ${s.checked.note || `nalezeno ${s.checked.found}, stahuje ${s.checked.downloading}`}` : "zatím nekontrolováno"}
          </span>
        </div>
      ))}
      <p className="text-xs text-zinc-500">Až bude díl v knihovně, seriál se přesune do Knihovna → Seriály (automatika běží dál).</p>
    </div>
  );
}

const isOn = (s: SeriesAutoShow) =>
  s.effective.auto_new !== "off" || s.effective.auto_dub !== "off" || s.effective.auto_upgrade !== "off";

/** The wanted page → "Hlídám lepší verzi": the library's shows the automation watches — where it is set, what it
 *  found. Each show is set on its own page (Automatika); here only "check" and "switch all off". */
export function WatchedShows({ canEdit, canDownload }: { canEdit: boolean; canDownload: boolean }) {
  const [data, setData] = useState<SeriesAutoOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { getSeriesAutomation().then(setData).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!data?.job.running) return;
    const t = setTimeout(load, 4000);
    return () => clearTimeout(t);
  }, [data, load]);
  if (!data) return <p className="text-zinc-500 animate-pulse">Načítám…</p>;
  const shows = data.shows.filter((s) => s.in_library && isOn(s));
  const sched = data.scheduler;

  async function allOff() {
    if (!confirm(`Vypnout automatiku u všech ${shows.length} seriálů (nové díly, dabing i lepší kvalitu)?\n\n`
      + "Seriály v knihovně zůstanou, jen se u nich přestane hledat. Zapnout jde znovu na stránce seriálu.")) return;
    setBusy(true);
    try {
      await saveSeriesAutomationBulk(shows.map((s) => s.tmdb_id), { auto_new: "off", auto_dub: "off", auto_upgrade: "off" });
      load();
    } finally { setBusy(false); }
  }
  // "hledat a stáhnout (výchozí)" — the own choice, or the default it takes over
  const setting = (s: SeriesAutoShow, key: "auto_new" | "auto_dub" | "auto_upgrade", list: [string, string][]) =>
    `${label(list, s.effective[key])}${s.own[key] == null ? " (výchozí)" : ""}`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-zinc-100">Seriály <span className="text-sm font-normal text-zinc-500">({shows.length})</span></h2>
        <div className="flex-1" />
        {canEdit && shows.length > 0 && (data.job.running ? (
          <span className="text-sm text-violet-300 animate-pulse">Kontroluji {data.job.done}/{data.job.total}{data.job.current && ` · ${data.job.current}`}</span>
        ) : (
          <button onClick={async () => { await runSeriesAutomation(shows.map((s) => s.tmdb_id)); load(); }}
            className="rounded bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-500">Zkontrolovat vše</button>
        ))}
        {canEdit && shows.length > 0 && (
          <button disabled={busy} onClick={allOff} className="text-xs text-zinc-500 hover:text-red-400 disabled:opacity-40">Odstranit vše</button>
        )}
      </div>
      <p className="text-xs text-zinc-500 -mt-1">
        Seriály z knihovny se zapnutou automatikou — hledá nové díly, CZ/SK dabing u dílů jen v EN a lepší kvalitu dílů pod profilem.
        Nastavuje se u každého seriálu zvlášť (stránka seriálu → Automatika); „výchozí“ = převzato z Nastavení → Seriály.
        {!sched.enabled ? <span className="text-amber-300"> Plánovač je vypnutý — kontroluje se jen ručně.</span>
          : !sched.series ? <span className="text-amber-300"> V plánovači je vypnutá „Automatika seriálů“.</span>
            : <> Kontrola každou noc v {sched.time}.</>}
      </p>
      {shows.length === 0 ? <p className="text-zinc-500">Žádný seriál se nehlídá.</p> : shows.map((s) => (
        <div key={s.tmdb_id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 space-y-1.5 text-sm">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Link href={`/series?tmdb=${s.tmdb_id}`} className="text-zinc-100 font-medium hover:text-violet-300">{s.title || `TMDB ${s.tmdb_id}`}</Link>
            {s.year && <span className="text-zinc-500">({s.year})</span>}
            <span className="text-xs text-zinc-500">{s.owned} dílů{s.foreign > 0 && <span className="text-amber-300/80"> · {s.foreign} bez CZ/SK</span>}</span>
            <Link href={`/series?tmdb=${s.tmdb_id}`} className="ml-auto text-xs text-violet-300 hover:text-violet-200">Nastavit ›</Link>
          </div>
          <p className="text-xs text-zinc-400">
            nové díly: <span className="text-zinc-300">{setting(s, "auto_new", AUTO_NEW)}</span>
            {s.effective.auto_new !== "off" && <span className="text-zinc-500"> ({label(AUTO_FROM, s.effective.auto_from)})</span>}
            {" · "}dabing: <span className="text-zinc-300">{setting(s, "auto_dub", AUTO_DUB)}</span>
            {" · "}kvalita: <span className="text-zinc-300">{setting(s, "auto_upgrade", AUTO_UPGRADE)}</span>
          </p>
          <p className="text-xs text-zinc-500">
            {s.checked ? `kontrola ${s.checked.checked_at.slice(0, 16)} — ${s.checked.note
              || (s.checked.found || s.checked.downloading ? `nalezeno ${s.checked.found}, stahuje ${s.checked.downloading}` : "nic nechybí")}`
              : "zatím nekontrolováno"}
          </p>
          <AutoFound tmdbId={s.tmdb_id} records={s.found} onChanged={load} canDownload={canDownload} />
        </div>
      ))}
    </div>
  );
}
