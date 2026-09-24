"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { QualityWeights, QualitySample, getQualityWeights, previewQuality } from "@/lib/api";

/**
 * Editor of the quality score numbers (core/quality.py DEFAULT_WEIGHTS) with a live score of
 * typical files. Stores only the values that differ from the defaults in the setting
 * "quality_weights" — saved with the page's Save button like every other setting.
 */

const RESOLUTIONS: [string, string][] = [["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"], ["SD", "SD"]];
const CODECS = ["H.265", "AV1", "H.264", "VC-1", "MPEG-2", "XviD"];
const POINTS: [string, string][] = [
  ["bitrate_bonus_max", "Bonus za výborný bitrate (max)"],
  ["low_bitrate_max_pct", "Nízký bitrate: max. srážka (% bodů rozlišení)"],
  ["unknown_bitrate_pct", "Neznámý bitrate: srážka (% bodů rozlišení)"],
  ["efficient_codec", "H.265 / AV1"],
  ["xvid", "XviD"],
  ["hdr_neutral", "HDR/DV — „je mi to jedno“"],
  ["hdr_prefer", "HDR — „chci“"],
  ["dv_prefer", "Dolby Vision — „chci“"],
  ["hdr_avoid", "HDR/DV — „nechci“"],
  ["upscale", "Upscale do 4K"],
  ["surround_51", "Zvuk 5.1"],
  ["surround_71", "Zvuk 7.1"],
  ["lossless_audio", "Bezeztrátový zvuk (TrueHD, DTS-HD, FLAC)"],
  ["over_size_limit", "Nad limit velikosti"],
];

type Group = keyof QualityWeights;

function diff(current: QualityWeights, defaults: QualityWeights): Partial<QualityWeights> {
  const out: Record<string, Record<string, number>> = {};
  for (const group of Object.keys(defaults) as Group[]) {
    for (const [key, value] of Object.entries(current[group])) {
      if (value !== defaults[group][key]) (out[group] ??= {})[key] = value;
    }
  }
  return out as Partial<QualityWeights>;
}

export default function QualityWeightsEditor({ value, onChange }: { value: string; onChange: (json: string) => void }) {
  const [defaults, setDefaults] = useState<QualityWeights | null>(null);
  const [weights, setWeights] = useState<QualityWeights | null>(null);
  const [preview, setPreview] = useState<QualitySample[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    getQualityWeights().then((w) => { setDefaults(w.defaults); setWeights(w.current); }).catch(() => {});
  }, []);

  // live preview, debounced
  useEffect(() => {
    if (!weights || !open) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => { previewQuality(weights).then(setPreview).catch(() => {}); }, 300);
  }, [weights, open]);

  const changed = useMemo(() => (weights && defaults ? diff(weights, defaults) : {}), [weights, defaults]);
  const changedCount = Object.values(changed).reduce((n, g) => n + Object.keys(g ?? {}).length, 0);

  function set(group: Group, key: string, raw: string) {
    const num = Number(raw.replace(",", "."));
    if (!weights || !defaults || raw.trim() === "" || !Number.isFinite(num)) return;
    const next = { ...weights, [group]: { ...weights[group], [key]: num } } as QualityWeights;
    setWeights(next);
    const d = diff(next, defaults);
    onChange(Object.keys(d).length ? JSON.stringify(d) : "");
  }

  function reset() {
    if (!defaults) return;
    setWeights(defaults);
    onChange("");
  }

  if (!weights || !defaults) return null;
  void value;

  const num = (group: Group, key: string, step = 1) => {
    const isChanged = weights[group][key] !== defaults[group][key];
    return (
      <input
        key={`${group}.${key}.${weights[group][key]}`}
        type="number" step={step} defaultValue={weights[group][key]}
        onBlur={(e) => set(group, key, e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") set(group, key, (e.target as HTMLInputElement).value); }}
        title={`Výchozí: ${defaults[group][key]}`}
        className={`w-20 rounded bg-zinc-800 border px-2 py-1 text-sm text-zinc-100 outline-none focus:border-violet-500 ${
          isChanged ? "border-violet-600" : "border-zinc-700"}`}
      />
    );
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-5 py-3 text-left">
        <span className="text-zinc-200 font-medium">🧮 Váhy skóre kvality</span>
        <span className="text-xs text-zinc-500">
          {changedCount ? `${changedCount} změněno` : "výchozí"} {open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div className="px-5 pb-5 space-y-5 text-sm">
          <p className="text-xs text-zinc-500">
            Platí pro hledání i knihovnu. Bitrate se porovnává jako H.264 ekvivalent videa (bez zvuku):
            bitrate × násobek kodeku. Změny se projeví po uložení nastavení.
          </p>

          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead>
                <tr className="text-xs text-zinc-500 text-left">
                  <th className="pr-4 font-normal">Rozlišení</th>
                  <th className="pr-4 font-normal">Body</th>
                  <th className="pr-4 font-normal">Dobrý bitrate (Mb/s)</th>
                  <th className="font-normal">Výborný (Mb/s)</th>
                </tr>
              </thead>
              <tbody>
                {RESOLUTIONS.map(([key, label]) => (
                  <tr key={key}>
                    <td className="pr-4 py-1 text-zinc-300">{label}</td>
                    <td className="pr-4 py-1">{num("res_base", key)}</td>
                    <td className="pr-4 py-1">{num("good_mbps", key, 0.5)}</td>
                    <td className="py-1">{num("excellent_mbps", key, 0.5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <p className="text-xs text-zinc-500 mb-1">Násobek kodeku (kolik obrazu dá 1 bit oproti H.264)</p>
            <div className="flex flex-wrap gap-3">
              {CODECS.map((c) => (
                <label key={c} className="flex items-center gap-2 text-zinc-300">{c} {num("efficiency", c, 0.1)}</label>
              ))}
            </div>
          </div>

          <div className="grid sm:grid-cols-2 gap-x-6 gap-y-2">
            {POINTS.map(([key, label]) => (
              <label key={key} className="flex items-center justify-between gap-3 text-zinc-300">
                <span className="text-xs">{label}</span>{num("points", key)}
              </label>
            ))}
          </div>

          <div>
            <p className="text-xs uppercase tracking-wide text-zinc-500 mb-2">Náhled na typických souborech</p>
            <div className="space-y-1">
              {preview.map((s) => (
                <div key={s.label} className="flex items-center gap-3" title={s.parts.map(([l, p], i) => `${l} ${i && p >= 0 ? "+" : ""}${p}`).join(" · ")}>
                  <span className={`inline-block min-w-[2.2rem] text-center rounded px-1.5 py-0.5 text-xs font-bold font-mono ${
                    s.score >= 80 ? "bg-green-900/70 text-green-300" : s.score >= 60 ? "bg-lime-900/60 text-lime-300"
                    : s.score >= 40 ? "bg-yellow-900/60 text-yellow-300" : "bg-red-900/50 text-red-300"}`}>{s.score}</span>
                  <span className="text-zinc-300">{s.label}</span>
                  <span className="text-xs text-zinc-600 truncate">{s.parts.map(([l, p], i) => `${l} ${i && p >= 0 ? "+" : ""}${p}`).join(" · ")}</span>
                </div>
              ))}
            </div>
          </div>

          <button onClick={reset} disabled={!changedCount}
            className="rounded bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700 disabled:opacity-40">
            Obnovit výchozí
          </button>
        </div>
      )}
    </div>
  );
}
