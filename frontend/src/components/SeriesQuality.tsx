"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AUTO_DUB, AUTO_NEW, AUTO_UPGRADE } from "@/components/SeriesAuto";
import {
  QualityProfile, SeriesAutoOverview, SeriesAutoShow, SeriesSettingValues, formatSize, getProfiles, getSeriesAutomation,
  runSeriesAutomation, saveSeriesAutomationBulk,
} from "@/lib/api";

/** Library → Seriály: the quality of the shows' episodes (backend series/router._quality) — flags to filter the
 *  grid by, a sort, and the same settings for every shown show at once (as the films' "Přehled kvality"). */

export const WEAK_SCORE = 50;
export type SeriesSort = "name" | "quality_asc" | "below_desc" | "size_desc";

const isOn = (s: SeriesAutoShow) => s.effective.auto_new !== "off" || s.effective.auto_dub !== "off" || s.effective.auto_upgrade !== "off";
const main = (counts: Record<string, number> | undefined) =>
  Object.entries(counts ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";

type Flag = { key: string; label: string; title: string; warn?: boolean; test: (s: SeriesAutoShow) => boolean };
const FLAGS: Flag[] = [
  { key: "below", label: "Díly pod profilem", title: "Některé díly nesplní profil kvality seriálu — to, co „Lepší kvalita“ automatiky nahrazuje",
    warn: true, test: (s) => (s.quality?.below ?? 0) > 0 },
  { key: "weak", label: `Slabé díly (< ${WEAK_SCORE})`, title: "Některé díly mají skóre kvality pod 50", warn: true, test: (s) => (s.quality?.weak ?? 0) > 0 },
  { key: "2160p", label: "4K", title: "Většina dílů je 4K", test: (s) => main(s.quality?.res) === "2160p" },
  { key: "1080p", label: "1080p", title: "Většina dílů je 1080p", test: (s) => main(s.quality?.res) === "1080p" },
  { key: "720p", label: "720p", title: "Většina dílů je 720p", test: (s) => main(s.quality?.res) === "720p" },
  { key: "sd", label: "SD díly", title: "Aspoň jeden díl pod 720p", warn: true, test: (s) => (s.quality?.res.SD ?? 0) > 0 },
  { key: "h265", label: "H.265 / AV1", title: "Většina dílů v úsporném kodeku", test: (s) => ["H.265", "AV1"].includes(main(s.quality?.codec)) },
  { key: "h264", label: "H.264", title: "Většina dílů v H.264", test: (s) => main(s.quality?.codec) === "H.264" },
  { key: "old_codec", label: "XviD / MPEG-2 / VC-1", title: "Aspoň jeden díl v zastaralém kodeku", warn: true,
    test: (s) => ["XviD", "MPEG-2", "VC-1"].some((c) => (s.quality?.codec[c] ?? 0) > 0) },
  { key: "hdr", label: "HDR / DV", title: "Aspoň jeden díl s HDR", test: (s) => (s.quality?.hdr ?? 0) > 0 },
  { key: "foreign", label: "Díly bez CZ/SK", title: "Díly jen s cizím zvukem (neznámý jazyk se nepočítá)", warn: true, test: (s) => s.foreign > 0 },
  { key: "auto_on", label: "Automatika zapnutá", title: "Hlídá nové díly, dabing nebo lepší kvalitu", test: isOn },
  { key: "auto_off", label: "Automatika vypnutá", title: "Nic se nehlídá", test: (s) => !isOn(s) },
  { key: "found", label: "Nalezeno, čeká", title: "Automatika něco našla a čeká na tvůj klik (stránka seriálu)",
    test: (s) => s.found.some((r) => r.status === "found") },
];

export default function SeriesQuality({ canEdit, query, onView }: {
  canEdit: boolean; query: string;
  /** the shows to show (tmdb ids in order; null = all by name) and each show's data for the grid's tiles */
  onView: (ids: number[] | null, byId: Record<number, SeriesAutoShow>) => void;
}) {
  const [data, setData] = useState<SeriesAutoOverview | null>(null);
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [flag, setFlag] = useState<string | null>(null);
  const [sort, setSort] = useState<SeriesSort>("name");
  const [saving, setSaving] = useState("");
  const load = useCallback(() => { getSeriesAutomation().then(setData).catch(() => {}); }, []);
  useEffect(() => { load(); getProfiles("tv").then(setProfiles).catch(() => {}); }, [load]);
  useEffect(() => {
    if (!data?.job.running) return;
    const t = setTimeout(load, 4000);
    return () => clearTimeout(t);
  }, [data, load]);

  const library = useMemo(() => (data?.shows ?? []).filter((s) => s.in_library), [data]);
  const counts = useMemo(() => Object.fromEntries(FLAGS.map((f) => [f.key, library.filter(f.test).length])), [library]);
  const visible = useMemo(() => {
    const q = query.trim().toLocaleLowerCase("cs");
    const test = FLAGS.find((f) => f.key === flag)?.test;
    const list = library.filter((s) => (!test || test(s)) && (!q || s.title.toLocaleLowerCase("cs").includes(q)));
    const by: Record<SeriesSort, (a: SeriesAutoShow, b: SeriesAutoShow) => number> = {
      name: (a, b) => a.title.localeCompare(b.title, "cs", { sensitivity: "base", numeric: true }),
      quality_asc: (a, b) => (a.quality?.min_score ?? 999) - (b.quality?.min_score ?? 999),
      below_desc: (a, b) => (b.quality?.below ?? 0) - (a.quality?.below ?? 0),
      size_desc: (a, b) => (b.quality?.size ?? 0) - (a.quality?.size ?? 0),
    };
    return [...list].sort(by[sort]);
  }, [library, flag, sort, query]);

  useEffect(() => {
    if (!data) return;
    onView(flag || sort !== "name" ? visible.map((s) => s.tmdb_id) : null, Object.fromEntries(library.map((s) => [s.tmdb_id, s])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, visible, flag, sort]);

  if (!data || !library.length) return null;
  const episodes = library.reduce((n, s) => n + s.owned, 0);
  const size = library.reduce((n, s) => n + (s.quality?.size ?? 0), 0);
  const ids = visible.map((s) => s.tmdb_id);

  async function bulk(values: Partial<SeriesSettingValues>, what: string) {
    if (!confirm(`${what} — u ${ids.length} zobrazených seriálů?`)) return;
    setSaving("…");
    try {
      await saveSeriesAutomationBulk(ids, values);
      setSaving(`Uloženo ${ids.length} seriálům`);
      load();
    } catch (e) { setSaving(e instanceof Error ? e.message : "Chyba"); }
  }
  const def = profiles.find((p) => p.is_default);

  return (
    <details className="rounded-lg border border-zinc-800 bg-zinc-900/40" open={flag !== null}>
      <summary className="cursor-pointer px-4 py-2 text-sm text-zinc-300 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-medium">Přehled kvality</span>
        <span className="text-xs text-zinc-500">
          {library.length} seriálů · {episodes} dílů · {formatSize(size)}
          {counts.below > 0 && <> · <span className="text-orange-300">{counts.below} s díly pod profilem</span></>}
          {counts.found > 0 && <> · <span className="text-amber-300">{counts.found} s nálezy</span></>}
        </span>
      </summary>
      <div className="px-4 pb-3 pt-1 space-y-3 text-xs">
        <div className="flex flex-wrap gap-2">
          {FLAGS.map((f) => (
            <button key={f.key} title={f.title} disabled={!counts[f.key]} onClick={() => setFlag(flag === f.key ? null : f.key)}
              className={`px-2.5 py-1 rounded-full border transition-colors disabled:opacity-30 ${
                flag === f.key ? "border-violet-500 bg-violet-600/20 text-violet-200"
                  : f.warn ? "border-orange-900/70 text-orange-300/90 hover:text-orange-200"
                    : "border-zinc-800 text-zinc-400 hover:text-zinc-200"}`}>
              {f.label} <span className="font-mono">{counts[f.key] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-zinc-500">
          Řadit:
          <select value={sort} onChange={(e) => setSort(e.target.value as SeriesSort)}
            className="rounded bg-zinc-900 border border-zinc-800 px-2 py-1 text-zinc-300">
            <option value="name">Podle názvu</option>
            <option value="quality_asc">Kvalita — nejhorší díl první</option>
            <option value="below_desc">Nejvíc dílů pod profilem</option>
            <option value="size_desc">Velikost</option>
          </select>
          {flag && <button onClick={() => setFlag(null)} className="ml-2 text-violet-300 hover:text-violet-200">× {FLAGS.find((f) => f.key === flag)?.label}</button>}
        </div>
        {canEdit && ids.length > 0 && (
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-2.5 space-y-2">
            <p className="text-zinc-400">Hromadně pro zobrazené seriály ({ids.length}):</p>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <Bulk label="profil" disabled={!!saving && saving === "…"}
                options={[["", `výchozí (${def?.name ?? "—"})`], ...profiles.map((p) => [String(p.id), p.name] as [string, string])]}
                onPick={(v, l) => bulk({ profile_id: v === "" ? null : Number(v) }, `Profil „${l}“`)} />
              <Bulk label="nové díly" disabled={saving === "…"} options={[["", "výchozí"], ...AUTO_NEW]}
                onPick={(v, l) => bulk({ auto_new: (v || null) as SeriesSettingValues["auto_new"] }, `Nové díly: ${l}`)} />
              <Bulk label="dabing" disabled={saving === "…"} options={[["", "výchozí"], ...AUTO_DUB]}
                onPick={(v, l) => bulk({ auto_dub: (v || null) as SeriesSettingValues["auto_dub"] }, `CZ/SK dabing: ${l}`)} />
              <Bulk label="lepší kvalita" disabled={saving === "…"} options={[["", "výchozí"], ...AUTO_UPGRADE]}
                onPick={(v, l) => bulk({ auto_upgrade: (v || null) as SeriesSettingValues["auto_upgrade"] }, `Lepší kvalita: ${l}`)} />
              {data.job.running ? (
                <span className="text-violet-300 animate-pulse">Kontroluji {data.job.done}/{data.job.total}{data.job.current && ` · ${data.job.current}`}</span>
              ) : (
                <button disabled={!visible.some(isOn)}
                  title="Automatika hned teď projde zobrazené seriály, které mají něco zapnuté (postupně, šetrně k WS/FS)"
                  onClick={async () => { await runSeriesAutomation(visible.filter(isOn).map((s) => s.tmdb_id)); load(); }}
                  className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
                  Zkontrolovat teď ({visible.filter(isOn).length})
                </button>
              )}
              {saving && saving !== "…" && <span className="text-emerald-300">{saving}</span>}
            </div>
            <p className="text-[11px] text-zinc-600">
              „výchozí“ = převzít z Nastavení → Seriály. Jednotlivý seriál nastavíš na jeho stránce; co se hlídá, vidíš v Chci → Hlídám lepší verzi.
            </p>
          </div>
        )}
        <p className="text-[11px] text-zinc-600">
          Podle MediaInfo dílů z posledního skenu (díly bez něj se nepočítají). „Pod profilem“ = díl nesplní profil kvality seriálu.
        </p>
      </div>
    </details>
  );
}

function Bulk({ label, options, disabled, onPick }: {
  label: string; options: [string, string][]; disabled: boolean; onPick: (v: string, label: string) => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-zinc-500">{label}
      <select disabled={disabled} value="__" className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200 disabled:opacity-50"
        onChange={(e) => { const v = e.target.value; if (v !== "__") onPick(v, options.find(([k]) => k === v)?.[1] ?? v); }}>
        <option value="__">—</option>
        {options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
    </label>
  );
}
