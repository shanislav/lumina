"use client";

import { useEffect, useState } from "react";
import { QualityProfile, getProfiles, saveProfile, deleteProfile } from "@/lib/api";

/**
 * Quality profiles (backend core/profiles.py): conditions are a hard filter, the score orders
 * what passes, "cíl" = an owned version this good is done (no better one is looked for).
 */

const RES = [["", "—"], ["SD", "SD"], ["720p", "720p"], ["1080p", "1080p"], ["2160p", "4K"]] as const;
const CODECS = ["H.265", "AV1", "H.264", "VC-1", "MPEG-2", "XviD"];
const EMPTY: QualityProfile = {
  id: 0, name: "Nový profil", is_default: false, min_resolution: "", max_resolution: "",
  require_local_audio: true, codecs: [], hdr: "any", max_size_gb: 0, min_video_mbps: 0,
  max_video_mbps: 0, min_score: 0, cutoff: 0,
};

export default function ProfilesEditor() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [editing, setEditing] = useState<QualityProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = () => getProfiles().then(setProfiles).catch(() => {});
  useEffect(() => { load(); }, []);

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
    p.require_local_audio ? "CZ/SK zvuk" : "",
    p.codecs.length ? p.codecs.join("/") : "",
    p.hdr === "require" ? "jen HDR" : p.hdr === "forbid" ? "bez HDR" : "",
    p.max_size_gb ? `max ${p.max_size_gb} GB` : "",
    p.min_video_mbps || p.max_video_mbps ? `video ${p.min_video_mbps || 0}–${p.max_video_mbps || "∞"} Mb/s` : "",
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
            </div>
          ))}
          <button onClick={() => setEditing({ ...EMPTY })}
            className="rounded bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700">+ Nový profil</button>
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

              <label className="text-zinc-400">Zvuk</label>
              <label className="flex items-center gap-2 text-zinc-300">
                <input type="checkbox" checked={editing.require_local_audio} onChange={(e) => set({ require_local_audio: e.target.checked })} />
                musí mít CZ/SK zvuk
              </label>

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

              <label className="text-zinc-400">Bitrate videa (Mb/s)</label>
              <div className="flex items-center gap-2">od {num("min_video_mbps", 0.5)} do {num("max_video_mbps", 0.5)}</div>

              <label className="text-zinc-400">Min. skóre</label>
              {num("min_score")}

              <label className="text-zinc-400">Cíl (skóre)</label>
              <div className="flex items-center gap-2">{num("cutoff")} <span className="text-xs text-zinc-600">0 = hledat pořád</span></div>

              <label className="text-zinc-400">Výchozí</label>
              <label className="flex items-center gap-2 text-zinc-300">
                <input type="checkbox" checked={editing.is_default} onChange={(e) => set({ is_default: e.target.checked })} />
                použít, když film nemá vlastní profil
              </label>
            </div>
            {error && <p className="text-xs text-red-400">{error}</p>}
            <div className="flex items-center gap-3 pt-2">
              {editing.id !== 0 && !editing.is_default && (
                <button onClick={() => remove(editing)} className="text-xs text-red-400 hover:text-red-300">Smazat profil</button>
              )}
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
