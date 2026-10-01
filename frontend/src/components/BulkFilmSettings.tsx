"use client";

import { useState } from "react";
import { QualityProfile, UpgradeJob, setFilmSettingsBulk } from "@/lib/api";
import { ON_BETTER } from "@/components/WatchedList";

/** The same film settings for every film shown (e.g. all SD films): profile, watching, what to do with a
 *  better version, once only — and optionally look for the better versions right away. */
export default function BulkFilmSettings({ tmdbIds, profiles, onSaved }: {
  tmdbIds: number[];
  profiles: QualityProfile[];
  onSaved: (job: UpgradeJob | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState("keep");          // keep | "" (default) | id
  const [watch, setWatch] = useState(true);
  const [onBetter, setOnBetter] = useState("replace");
  const [once, setOnce] = useState(true);
  const [checkNow, setCheckNow] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const def = profiles.find((p) => p.is_default);

  async function save() {
    const what = watch
      ? `${ON_BETTER.find(([v]) => v === onBetter)?.[1]}${once ? ", jen jednou" : ""}`
      : "nehlídat";
    if (!confirm(`Nastavit ${tmdbIds.length} filmům: ${what}?${watch && checkNow ? "\nLepší verze se začnou hledat hned." : ""}`)) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await setFilmSettingsBulk({
        tmdb_ids: tmdbIds, keep_profile: profile === "keep", profile_id: profile === "keep" || profile === "" ? null : Number(profile),
        watch_upgrades: watch, on_better: onBetter, upgrade_once: once, check_now: watch && checkNow,
      });
      setResult(`Uloženo ${r.saved} filmům${r.job ? " · hledám lepší verze" : ""}`);
      onSaved(r.job);
    } catch (e) {
      setResult(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button disabled={!tmdbIds.length} onClick={() => setOpen(true)}
        title="Stejné nastavení filmu (profil, hlídání lepší verze) všem zobrazeným filmům"
        className="rounded border border-violet-700 px-3 py-1 text-violet-200 hover:bg-violet-900/40 disabled:opacity-40">
        Hromadně nastavit ({tmdbIds.length})
      </button>
    );
  }
  const sel = "rounded bg-zinc-800 border border-zinc-700 px-1.5 py-0.5 text-zinc-300";
  return (
    <div className="w-full rounded-lg border border-violet-900/60 bg-violet-950/20 p-3 space-y-2 text-xs text-zinc-400">
      <div className="text-zinc-200 font-medium">Hromadně nastavit {tmdbIds.length} zobrazeným filmům</div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-1">profil
          <select value={profile} onChange={(e) => setProfile(e.target.value)} className={sel}>
            <option value="keep">— neměnit —</option>
            <option value="">výchozí ({def?.name ?? "—"})</option>
            {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={watch} onChange={(e) => setWatch(e.target.checked)} />
          Hlídat lepší verzi
        </label>
        {watch && <>
          <label className="flex items-center gap-1">když najde lepší
            <select value={onBetter} onChange={(e) => setOnBetter(e.target.value)} className={sel}>
              {ON_BETTER.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1.5" title="Jakmile je lepší verze v knihovně, hlídání se vypne">
            <input type="checkbox" checked={once} onChange={(e) => setOnce(e.target.checked)} />
            jen jednou (po stažení dál nehledat)
          </label>
          <label className="flex items-center gap-1.5" title="Jinak až při nočním běhu plánovače">
            <input type="checkbox" checked={checkNow} onChange={(e) => setCheckNow(e.target.checked)} />
            hledat hned
          </label>
        </>}
      </div>
      <p className="text-[11px] text-zinc-500">
        Stahování jde přes frontu (max. souběžných stahování v Nastavení → Stahování). „Stáhnout a nahradit“ smaže
        starou verzi až po úspěšném importu nové. Preferovaná (oblíbená) verze se nikdy nenahradí — lepší přibude jako další verze.
      </p>
      <div className="flex items-center gap-3">
        <button onClick={save} disabled={busy || !tmdbIds.length}
          className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
          {busy ? "Ukládám…" : "Uložit"}
        </button>
        <button onClick={() => { setOpen(false); setResult(null); }} className="text-zinc-500 hover:text-zinc-300">Zavřít</button>
        {result && <span className="text-violet-300">{result}</span>}
      </div>
    </div>
  );
}
