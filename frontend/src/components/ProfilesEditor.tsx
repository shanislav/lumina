"use client";

import { useEffect, useState } from "react";
import { QualityProfile, ScoreRange, getProfiles, getScoreRange, saveProfile, deleteProfile } from "@/lib/api";

/**
 * Quality profiles (backend core/profiles.py): conditions are a hard filter, the score orders
 * what passes, "cíl" = an owned version this good is done (no better one is looked for).
 */

const RES = [["", "—"], ["SD", "SD"], ["720p", "720p"], ["1080p", "1080p"], ["2160p", "4K"]] as const;
const CODECS = ["H.265", "AV1", "H.264", "VC-1", "MPEG-2", "XviD"];
const LANGS: [string, string][] = [["cs", "CZ"], ["sk", "SK"], ["en", "EN"], ["de", "DE"], ["pl", "PL"], ["hu", "HU"]];
const langLabel = (code: string) => LANGS.find(([c]) => c === code)?.[1] ?? code.toUpperCase();
const EMPTY: QualityProfile = {
  id: 0, name: "Nový profil", is_default: false, min_resolution: "", max_resolution: "",
  audio_langs: ["cs", "sk"], audio_mode: "any", codecs: [], hdr: "any", max_size_gb: 0, min_mbps: 0,
  max_mbps: 0, min_score: 0, cutoff: 0,
};

export default function ProfilesEditor() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [editing, setEditing] = useState<QualityProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  const load = () => getProfiles().then(setProfiles).catch(() => {});
  useEffect(() => { load(); }, []);

  // hints while editing: the score a file can get with these conditions, the size of a 2-hour film
  const [range, setRange] = useState<ScoreRange | null>(null);
  const rangeKey = editing ? JSON.stringify({ ...editing, name: "", cutoff: 0, is_default: false }) : "";
  useEffect(() => {
    if (!editing) { setRange(null); return; }
    let live = true;
    const t = setTimeout(() => getScoreRange(editing).then((r) => live && setRange(r)).catch(() => {}), 300);
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey]);

  async function save() {
    if (!editing) return;
    setError(null);
    try {
      await saveProfile(editing);
      setEditing(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Uložení selhalo");
    }
  }

  async function remove(p: QualityProfile) {
    setError(null);
    try {
      await deleteProfile(p.id);
      setEditing(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Smazání selhalo");
    }
  }

  const summary = (p: QualityProfile) => [
    p.min_resolution || p.max_resolution
      ? `${p.min_resolution || "…"}${p.max_resolution && p.max_resolution !== p.min_resolution ? `–${p.max_resolution}` : p.max_resolution ? " jen" : "+"}`
      : "jakékoli rozlišení",
    p.audio_langs.length
      ? `zvuk ${p.audio_langs.map(langLabel).join(p.audio_mode === "all" ? " + " : " / ")}`
      : "",
    p.codecs.length ? p.codecs.join("/") : "",
    p.hdr === "require" ? "jen HDR" : p.hdr === "forbid" ? "bez HDR" : "",
    p.max_size_gb ? `max ${p.max_size_gb} GB` : "",
    p.min_mbps || p.max_mbps ? `${p.min_mbps || 0}–${p.max_mbps || "∞"} Mb/s` : "",
    p.cutoff ? `cíl ${p.cutoff}` : "bez cíle",
  ].filter(Boolean).join(" · ");

  const field = "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-sm text-zinc-100 outline-none focus:border-violet-500";
  const set = (patch: Partial<QualityProfile>) => editing && setEditing({ ...editing, ...patch });
  const num = (key: keyof QualityProfile, step = 1) => (
    <input type="number" step={step} min={0} value={Number(editing?.[key] ?? 0)}
      onChange={(e) => set({ [key]: Number(e.target.value) || 0 } as Partial<QualityProfile>)}
      className={`${field} w-24`} />
  );

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-5 py-3 text-left">
        <span className="text-zinc-200 font-medium">🎯 Profily kvality</span>
        <span className="text-xs text-zinc-500">{profiles.length} {open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-5 pb-5 space-y-3 text-sm">
          <p className="text-xs text-zinc-500">
            Profil říká, jakou verzi filmu chceš. Podmínky jsou tvrdý filtr (co nesplní, nenabídne se),
            skóre pak řadí zbytek. <b>Cíl</b>: když verze v knihovně splní profil a má aspoň toto skóre, lepší se už nehledá.
          </p>
          {profiles.map((p) => (
            <div key={p.id} className="flex items-center gap-3 rounded-lg border border-zinc-800 px-3 py-2">
              <div className="flex-1 min-w-0">
                <p className="text-zinc-100">{p.name} {p.is_default && <span className="text-[10px] text-violet-300 ml-1">výchozí</span>}</p>
                <p className="text-xs text-zinc-500 truncate">{summary(p)}</p>
              </div>
              <button onClick={() => setEditing({ ...p })} className="text-xs text-violet-300 hover:text-violet-200">Upravit</button>
              {p.is_default ? (
                <span className="text-xs text-zinc-600" title="Výchozí profil nejde smazat — nejdřív nastav jiný jako výchozí">Smazat</span>
              ) : confirmDelete === p.id ? (
                <span className="flex items-center gap-2 text-xs">
                  <button onClick={() => { setConfirmDelete(null); remove(p); }} className="text-red-400 hover:text-red-300">Opravdu smazat</button>
                  <button onClick={() => setConfirmDelete(null)} className="text-zinc-500 hover:text-zinc-300">Ne</button>
                </span>
              ) : (
                <button onClick={() => setConfirmDelete(p.id)} className="text-xs text-zinc-500 hover:text-red-400">Smazat</button>
              )}
            </div>
          ))}
          <button onClick={() => setEditing({ ...EMPTY, audio_langs: [...EMPTY.audio_langs] })}
            className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500">+ Nový profil</button>
          <p className="text-[11px] text-zinc-600">Filmy se smazaným profilem použijí výchozí profil.</p>
          {error && !editing && <p className="text-xs text-red-400">{error}</p>}
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 px-4 backdrop-blur-sm" onClick={() => setEditing(null)}>
          <div className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-900 p-6 space-y-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-zinc-100">{editing.id ? "Upravit profil" : "Nový profil"}</h3>
            <div className="grid grid-cols-[10rem_1fr] gap-x-3 gap-y-3 items-center text-sm">
              <label className="text-zinc-400">Název</label>
              <input value={editing.name} onChange={(e) => set({ name: e.target.value })} className={field} />

              <label className="text-zinc-400">Rozlišení</label>
              <div className="flex items-center gap-2">
                od <select value={editing.min_resolution} onChange={(e) => set({ min_resolution: e.target.value })} className={field}>
                  {RES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                do <select value={editing.max_resolution} onChange={(e) => set({ max_resolution: e.target.value })} className={field}>
                  {RES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>

              <label className="text-zinc-400 self-start pt-1">Zvuk</label>
              <div className="space-y-1.5">
                <div className="flex flex-wrap gap-3">
                  {LANGS.map(([code, label]) => (
                    <label key={code} className="flex items-center gap-1 text-zinc-300">
                      <input type="checkbox" checked={editing.audio_langs.includes(code)}
                        onChange={(e) => set({ audio_langs: e.target.checked
                          ? [...editing.audio_langs, code] : editing.audio_langs.filter((x) => x !== code) })} />
                      {label}
                    </label>
                  ))}
                </div>
                <select value={editing.audio_mode} onChange={(e) => set({ audio_mode: e.target.value as QualityProfile["audio_mode"] })}
                  disabled={editing.audio_langs.length < 2} className={`${field} disabled:opacity-50`}>
                  <option value="any">stačí jeden z vybraných</option>
                  <option value="all">musí mít všechny vybrané</option>
                </select>
                <p className="text-[11px] text-zinc-600">nic nezaškrtnuto = na zvuku nezáleží</p>
              </div>

              <label className="text-zinc-400">Kodeky</label>
              <div className="flex flex-wrap gap-3">
                {CODECS.map((c) => (
                  <label key={c} className="flex items-center gap-1 text-zinc-300">
                    <input type="checkbox" checked={editing.codecs.includes(c)}
                      onChange={(e) => set({ codecs: e.target.checked ? [...editing.codecs, c] : editing.codecs.filter((x) => x !== c) })} />
                    {c}
                  </label>
                ))}
                <span className="text-[11px] text-zinc-600 w-full">nic nezaškrtnuto = jakýkoli</span>
              </div>

              <label className="text-zinc-400">HDR / DV</label>
              <select value={editing.hdr} onChange={(e) => set({ hdr: e.target.value as QualityProfile["hdr"] })} className={field}>
                <option value="any">je mi to jedno</option>
                <option value="require">jen s HDR/DV</option>
                <option value="forbid">bez HDR (starší TV)</option>
              </select>

              <label className="text-zinc-400">Max. velikost (GB)</label>
              <div className="flex items-center gap-2">{num("max_size_gb", 0.5)} <span className="text-xs text-zinc-600">0 = bez limitu</span></div>

              <label className="text-zinc-400 self-start pt-1">Bitrate (Mb/s)</label>
              <div className="space-y-1">
                <div className="flex items-center gap-2">od {num("min_mbps", 0.5)} do {num("max_mbps", 0.5)}</div>
                <p className="text-[11px] text-zinc-500">
                  celkový (obraz + zvuk), 0 = bez limitu · 1 Mb/s ≈ 0,9 GB na 2 h
                  {range && (range.size_2h_gb[0] > 0 || range.size_2h_gb[1] != null) && (
                    <> · <span className="text-zinc-300">film 2 h: {range.size_2h_gb[0] > 0 ? `${range.size_2h_gb[0]} GB` : "0"}
                      {" – "}{range.size_2h_gb[1] != null ? `${range.size_2h_gb[1]} GB` : "bez limitu"}</span></>
                  )}
                </p>
              </div>

              <label className="text-zinc-400">Min. skóre</label>
              {num("min_score")}

              <label className="text-zinc-400 self-start pt-1">Cíl (skóre)</label>
              <div className="space-y-1">
                <div className="flex items-center gap-2">{num("cutoff")} <span className="text-xs text-zinc-600">0 = hledat pořád</span></div>
                {range && (range.possible ? (
                  <div className="text-[11px] text-zinc-500 space-y-0.5">
                    <p>S těmito podmínkami má soubor skóre <span className="text-zinc-200">{range.min}–{range.max}</span> (film 2 h, současné váhy skóre).</p>
                    <p title={range.max_example}>nejlepší: {range.max_example}</p>
                    <p title={range.min_example}>nejhorší, co projde: {range.min_example}</p>
                    {editing.cutoff > (range.max ?? 0) && (
                      <p className="text-orange-300">Cíl {editing.cutoff} je nad maximem {range.max} — žádný soubor ho nesplní, hledalo by se pořád.</p>
                    )}
                  </div>
                ) : (
                  <p className="text-[11px] text-orange-300">{range.reason}</p>
                ))}
              </div>

              <label className="text-zinc-400">Výchozí</label>
              <label className="flex items-center gap-2 text-zinc-300">
                <input type="checkbox" checked={editing.is_default} onChange={(e) => set({ is_default: e.target.checked })} />
                použít, když film nemá vlastní profil
              </label>
            </div>
            {error && <p className="text-xs text-red-400">{error}</p>}
            <div className="flex items-center gap-3 pt-2">
              <div className="flex-1" />
              <button onClick={() => setEditing(null)} className="text-sm text-zinc-400 hover:text-zinc-200">Zrušit</button>
              <button onClick={save} className="rounded bg-violet-600 px-5 py-1.5 text-sm font-bold text-white hover:bg-violet-500">Uložit</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
