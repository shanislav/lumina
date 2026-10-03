"use client";

import { useEffect, useState } from "react";
import { SeriesLangMode, SeriesSettingValues, getSeriesDefaults, saveSeriesDefaults } from "@/lib/api";
import { AutoFields } from "@/components/SeriesAuto";

/** Defaults every TV show uses unless it has its own value (backend modules/series, setting "series_defaults").
 *  The quality profile of shows is the default profile of kind "Seriály" (profiles above). */
export default function SeriesDefaults() {
  const [values, setValues] = useState<SeriesSettingValues | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { getSeriesDefaults().then(setValues).catch(() => {}); }, []);
  if (!values) return null;

  async function save(patch: Partial<SeriesSettingValues>) {
    setValues(await saveSeriesDefaults(patch));
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }
  const field = "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-sm text-zinc-100";
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 px-5 py-4 space-y-3 text-sm">
      <p className="text-zinc-200 font-medium">📺 Seriály — výchozí nastavení {saved && <span className="text-xs text-emerald-400">uloženo</span>}</p>
      <p className="text-xs text-zinc-500">Platí pro každý seriál, který nemá vlastní nastavení (na stránce seriálu). Profil kvality seriálů = výchozí profil v sekci Seriály výše. Automatika se hledá v noci s plánovačem; přehled všech seriálů je v Knihovna → Seriály → Automatika.</p>
      <div className="grid grid-cols-[9rem_1fr] items-center gap-x-3 gap-y-2.5">
        <label className="text-zinc-400">Jazyk</label>
        <select value={values.lang_mode ?? "local_or_temp"} className={field}
          onChange={(e) => save({ lang_mode: e.target.value as SeriesLangMode })}>
          <option value="local_or_temp">CZ/SK, jinak hned EN (CZ/SK nahradí, až vyjde)</option>
          <option value="local_only">Jen CZ/SK</option>
          <option value="original">Originál (dabing nehledat)</option>
        </select>
        <label className="text-zinc-400">Torrenty</label>
        <select value={values.torrent ? "1" : "0"} className={field} onChange={(e) => save({ torrent: e.target.value === "1" })}>
          <option value="1">hledat i na torrentech</option>
          <option value="0">jen WebShare / FastShare</option>
        </select>
        <AutoFields own={values} langMode={values.lang_mode ?? "local_or_temp"} onChange={save} />
      </div>
    </div>
  );
}
