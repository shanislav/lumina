"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  saveSeriesSettings,
  SeriesAutoFrom, SeriesAutoMode, SeriesAutoOverview, SeriesAutoRecord, SeriesAutoShow, SeriesSettingValues,
  dismissAutoFound, downloadAutoFound, getSeriesAutomation, getShowAutomation, runSeriesAutomation,
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
      {found.length > 1 && canDownload && (
        <button disabled={!!busy} onClick={() => act(() => downloadAutoFound(tmdbId, found.map(key)), "…")}
          className="rounded bg-violet-700 px-2.5 py-1 text-xs text-white hover:bg-violet-600 disabled:opacity-50">
          Stáhnout vše ({found.length})
        </button>
      )}
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

/** The show page of a show not in the library: "I want it" = the automation looks for every aired episode. */
export function WantShow({ tmdbId, aired, onDone }: { tmdbId: number; aired: number; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  async function want(mode: SeriesAutoMode) {
    setBusy(true);
    try {
      await saveSeriesSettings(tmdbId, { auto_new: mode, auto_from: "all" });
      await runSeriesAutomation([tmdbId]);          // checked right away, as "+ Chci" of a film
      onDone();
    } finally { setBusy(false); }
  }
  return (
    <section className="rounded-xl border border-violet-800/50 bg-violet-950/20 px-4 py-3 space-y-2 text-sm">
      <p className="text-zinc-200">Tenhle seriál zatím nemáš.</p>
      <p className="text-xs text-zinc-400">
        Chci = automatika najde všechny vydané díly ({aired}) podle profilu a jazyka seriálu, hned teď a pak každou noc
        i ty nové. Celé série najednou z torrentů jsou níž („Celý seriál na torrentech“).
      </p>
      <div className="flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => want("download")}
          className="rounded bg-violet-600 px-3 py-1.5 text-white hover:bg-violet-500 disabled:opacity-50">Chci — stahovat díly</button>
        <button disabled={busy} onClick={() => want("notify")}
          className="rounded bg-zinc-800 px-3 py-1.5 text-zinc-200 hover:bg-zinc-700 disabled:opacity-50">Jen najít a ukázat</button>
      </div>
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

type Filter = "all" | "on" | "found" | "foreign";

/** Library → Seriály: every show's automation in one list, bulk settings, what waits for a click. */
export default function SeriesAutomation({ canEdit, canDownload, query = "" }: { canEdit: boolean; canDownload: boolean; query?: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<SeriesAutoOverview | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [saving, setSaving] = useState(false);
  const load = useCallback(() => { getSeriesAutomation().then(setData).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!data?.job.running) return;
    const t = setTimeout(load, 4000);
    return () => clearTimeout(t);
  }, [data, load]);

  const isOn = (s: SeriesAutoShow) => s.effective.auto_new !== "off" || s.effective.auto_dub !== "off" || s.effective.auto_upgrade !== "off";
  const shows = useMemo(() => {
    const q = query.trim().toLocaleLowerCase("cs");
    return (data?.shows ?? []).filter((s) => (!q || s.title.toLocaleLowerCase("cs").includes(q)) && (
      filter === "on" ? isOn(s) : filter === "found" ? s.found.some((r) => r.status === "found")
        : filter === "foreign" ? s.foreign > 0 : true));
  }, [data, filter, query]);
  if (!data) return null;
  const onCount = data.shows.filter(isOn).length;
  const waiting = data.shows.filter((s) => s.found.some((r) => r.status === "found"));
  const waitingCount = waiting.reduce((n, s) => n + s.found.filter((r) => r.status === "found").length, 0);

  async function save(ids: number[], values: Partial<SeriesSettingValues>) {
    setSaving(true);
    try { await saveSeriesAutomationBulk(ids, values); load(); } finally { setSaving(false); }
  }
  const toggle = (id: number) => setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const sched = data.scheduler;
  const d = data.defaults;

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm">
        <span className="text-zinc-200">🤖 Automatika seriálů</span>
        <span className="text-xs text-zinc-500">
          {onCount ? `zapnutá u ${onCount}` : "u žádného seriálu"}
          {waitingCount > 0 && <span className="text-amber-300"> · čeká {waitingCount}</span>}
          {data.job.running && <span className="text-violet-300"> · kontroluje se {data.job.done}/{data.job.total}</span>}
          {" "}{open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div className="space-y-3 px-4 pb-4 text-xs">
          {/* when it runs */}
          <div className="rounded-lg bg-zinc-950 border border-zinc-800/60 p-3 space-y-1 text-zinc-400">
            {!sched.enabled ? (
              <p className="text-amber-300">Plánovač je vypnutý — automatika poběží jen přes „Zkontrolovat teď“. Zapneš ho v Nastavení → Integrace → Plánovač.</p>
            ) : !sched.series ? (
              <p className="text-amber-300">V plánovači je vypnutá volba „Automatika seriálů“ (Nastavení → Integrace → Plánovač).</p>
            ) : (
              <p>Kontroluje se každou noc v {sched.time}{sched.last_run && <> · naposledy {sched.last_run.slice(0, 16)}</>}.</p>
            )}
            <p className="text-zinc-500">
              Výchozí pro všechny seriály: nové díly — {label(AUTO_NEW, d.auto_new)}{d.auto_new !== "off" && ` (${label(AUTO_FROM, d.auto_from)})`},
              dabing — {label(AUTO_DUB, d.auto_dub)}, lepší kvalita — {label(AUTO_UPGRADE, d.auto_upgrade)}. Mění se v Nastavení → Seriály; seriál s vlastní volbou ho nepřebírá.
            </p>
            <p className="text-zinc-500">Bere jen soubory, které jsou jistě daný díl a projdou profilem kvality seriálu; dabing jen s CZ/SK zvukem;
              lepší kvalitu jen s vyšším skóre bez ztráty CZ/SK zvuku. AI nevolá.</p>
            {canEdit && (
              <button disabled={data.job.running || !onCount} onClick={async () => { await runSeriesAutomation(); load(); }}
                className="mt-1 rounded bg-zinc-700 px-3 py-1 text-zinc-200 hover:bg-zinc-600 disabled:opacity-50">
                {data.job.running ? `Kontroluje se ${data.job.done}/${data.job.total} — ${data.job.current}` : `Zkontrolovat teď (${onCount})`}
              </button>
            )}
          </div>

          {/* waiting for a click */}
          {waiting.length > 0 && (
            <div className="space-y-2">
              <p className="text-zinc-300">Nalezeno, čeká na tebe</p>
              {waiting.map((s) => (
                <div key={s.tmdb_id} className="rounded-lg border border-zinc-800 p-2 space-y-1">
                  <Link href={`/series?tmdb=${s.tmdb_id}`} className="text-zinc-200 hover:text-violet-300">{s.title} {s.year && `(${s.year})`}</Link>
                  <AutoFound tmdbId={s.tmdb_id} records={s.found} onChanged={load} canDownload={canDownload} />
                </div>
              ))}
            </div>
          )}

          {/* every show */}
          <div className="flex flex-wrap items-center gap-1.5">
            {([["all", `všechny (${data.shows.length})`], ["on", `zapnuté (${onCount})`], ["found", `s nálezy (${waiting.length})`],
               ["foreign", `mají EN díly (${data.shows.filter((s) => s.foreign > 0).length})`]] as [Filter, string][]).map(([f, l]) => (
              <button key={f} onClick={() => setFilter(f)}
                className={`rounded-full px-2.5 py-0.5 ${filter === f ? "bg-violet-600 text-white" : "bg-zinc-800 text-zinc-400 hover:text-zinc-200"}`}>{l}</button>
            ))}
          </div>

          {canEdit && picked.size > 0 && (
            <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-violet-800/60 bg-zinc-900 p-2">
              <span className="text-zinc-300">Vybráno {picked.size}:</span>
              <span className="text-zinc-500">nové díly</span>
              <BulkSelect options={AUTO_NEW} disabled={saving} onPick={(v) => save([...picked], { auto_new: v })} />
              <span className="text-zinc-500">které</span>
              <BulkSelect options={AUTO_FROM} disabled={saving} onPick={(v) => save([...picked], { auto_from: v })} />
              <span className="text-zinc-500">dabing</span>
              <BulkSelect options={AUTO_DUB} disabled={saving} onPick={(v) => save([...picked], { auto_dub: v })} />
              <span className="text-zinc-500">kvalita</span>
              <BulkSelect options={AUTO_UPGRADE} disabled={saving} onPick={(v) => save([...picked], { auto_upgrade: v })} />
              <button onClick={() => setPicked(new Set())} className="ml-auto text-zinc-500 hover:text-zinc-300">zrušit výběr</button>
            </div>
          )}

          <div className="divide-y divide-zinc-800/70 rounded-lg border border-zinc-800">
            {canEdit && shows.length > 0 && (
              <label className="flex items-center gap-2 px-2 py-1.5 text-zinc-500">
                <input type="checkbox" checked={shows.every((s) => picked.has(s.tmdb_id))}
                  onChange={(e) => setPicked(e.target.checked ? new Set(shows.map((s) => s.tmdb_id)) : new Set())} />
                vybrat všechny zobrazené
              </label>
            )}
            {shows.map((s) => (
              <div key={s.tmdb_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-2 py-2">
                {canEdit && <input type="checkbox" checked={picked.has(s.tmdb_id)} onChange={() => toggle(s.tmdb_id)} />}
                <div className="min-w-[10rem] flex-1">
                  <Link href={`/series?tmdb=${s.tmdb_id}`} className="text-zinc-200 hover:text-violet-300">{s.title || `TMDB ${s.tmdb_id}`}</Link>
                  {s.year && <span className="text-zinc-600"> ({s.year})</span>}
                  <p className="text-[11px] text-zinc-500">
                    {s.owned} dílů{s.foreign > 0 && <span className="text-amber-300/80"> · {s.foreign} bez CZ/SK</span>}
                    {s.checked && <> · kontrola {s.checked.checked_at.slice(5, 16)}{s.checked.note ? ` — ${s.checked.note}` : s.checked.found || s.checked.downloading ? ` — nalezeno ${s.checked.found}, stahuje ${s.checked.downloading}` : ""}</>}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-zinc-500">nové</span>
                  <AutoSelect value={s.own.auto_new} options={AUTO_NEW} fallback={d.auto_new} disabled={!canEdit || saving}
                    onChange={(v) => save([s.tmdb_id], { auto_new: v })} />
                  {s.effective.auto_new !== "off" && (
                    <AutoSelect value={s.own.auto_from} options={AUTO_FROM} fallback={d.auto_from} disabled={!canEdit || saving}
                      onChange={(v) => save([s.tmdb_id], { auto_from: v })} />
                  )}
                  <span className="text-zinc-500">dabing</span>
                  <AutoSelect value={s.own.auto_dub} options={AUTO_DUB} fallback={d.auto_dub} disabled={!canEdit || saving}
                    onChange={(v) => save([s.tmdb_id], { auto_dub: v })} />
                  <span className="text-zinc-500">kvalita</span>
                  <AutoSelect value={s.own.auto_upgrade} options={AUTO_UPGRADE} fallback={d.auto_upgrade} disabled={!canEdit || saving}
                    onChange={(v) => save([s.tmdb_id], { auto_upgrade: v })} />
                </div>
              </div>
            ))}
            {!shows.length && <p className="px-2 py-3 text-zinc-500">Nic.</p>}
          </div>
        </div>
      )}
    </section>
  );
}

function BulkSelect<T extends string>({ options, disabled, onPick }: { options: [T, string][]; disabled: boolean; onPick: (v: T | null) => void }) {
  return (
    <select disabled={disabled} value="__" className={field}
      onChange={(e) => { if (e.target.value !== "__") onPick((e.target.value || null) as T | null); }}>
      <option value="__">—</option>
      <option value="">výchozí</option>
      {options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );
}
