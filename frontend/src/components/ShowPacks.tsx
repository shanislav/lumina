"use client";

import { useEffect, useState } from "react";
import { ShowPack, ShowPacks as Packs, downloadShowPack, getShowPacks } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

// Torrents of the whole show ("komplet", "1-26. série", "S01-S10") — beside the search by seasons.
export const gb = (b: number) => (b >= 1e12 ? `${(b / 1e12).toFixed(2)} TB` : `${(b / 1e9).toFixed(1)} GB`);

function span(seasons: number[]): string {
  if (!seasons.length) return "";
  const a = seasons[0], b = seasons[seasons.length - 1];
  return a === b ? `S${String(a).padStart(2, "0")}` : `S${String(a).padStart(2, "0")}–S${String(b).padStart(2, "0")}`;
}

export function coverage(p: ShowPack, all: number[]): { label: string; cls: string } {
  if (!p.seasons.length) return { label: "celý seriál", cls: "text-emerald-300" };
  const whole = all.length > 0 && p.covers >= all.length;
  return {
    label: `${span(p.seasons)}${all.length ? ` · ${p.covers} z ${all.length} sérií` : ""}`,
    cls: whole ? "text-emerald-300" : p.covers > 1 ? "text-lime-300" : "text-zinc-400",
  };
}

export default function ShowPacks({ tmdbId, onStarted, autoOpen = false, big = false, onClose }: {
  tmdbId: number; onStarted: () => void;
  autoOpen?: boolean;        // searched right away (the "Chci" card's "Celý seriál z torrentu")
  big?: boolean;             // the phone: big buttons
  onClose?: () => void;
}) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Packs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [replace, setReplace] = useState(false);
  const [started, setStarted] = useState<Record<string, string>>({});

  const search = () => {
    setOpen(true);
    if (data || loading) return;
    setLoading(true);
    setError(null);
    getShowPacks(tmdbId).then(setData).catch((e) => setError(e instanceof Error ? e.message : "Chyba"))
      .finally(() => setLoading(false));
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (autoOpen) search(); }, [autoOpen]);

  const start = async (p: ShowPack) => {
    setStarted((s) => ({ ...s, [p.ident]: "…" }));
    try {
      await downloadShowPack(tmdbId, p, replace);
      setStarted((s) => ({ ...s, [p.ident]: "stahuje se — díly se po dokončení zařadí do sérií" }));
      onStarted();
    } catch (e) {
      setStarted((s) => ({ ...s, [p.ident]: e instanceof Error ? e.message : "Chyba" }));
    }
  };

  if (!open) {
    return (
      <button onClick={search} className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:border-violet-500 hover:text-violet-200">
        Celý seriál na torrentech <span className="text-zinc-500">(komplet, víc sérií najednou)</span>
      </button>
    );
  }

  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-xs">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-medium text-zinc-200">Celý seriál na torrentech</h2>
        <label className="flex items-center gap-1.5 text-zinc-400" title="Bez zaškrtnutí zůstanou díly, které už máš, a jejich kopie z balíku se nepřesunou">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          nahradit díly, které už mám
        </label>
        <button onClick={() => { setOpen(false); onClose?.(); }} className="ml-auto text-zinc-500 hover:text-zinc-300">Zavřít</button>
      </div>
      {loading && <p className="text-zinc-500">Hledám na torrentech…</p>}
      {error && <p className="text-red-400">{error}</p>}
      {data && !data.packs.length && <p className="text-zinc-500">Žádný balík celého seriálu ani víc sérií nenalezen.</p>}
      {data && data.packs.length > 0 && (
        <div className="divide-y divide-zinc-800/70">
          {data.packs.map((p) => {
            const cov = coverage(p, data.seasons);
            return (
              <div key={p.ident} className="flex flex-col gap-1 py-1.5 sm:flex-row sm:items-center sm:gap-3">
                <div className="min-w-0 flex-1">
                  <p className={`text-zinc-200 ${big ? "break-all text-sm" : "truncate"}`} title={`${p.name}\n${(p.film_reasons ?? []).join(", ")}`}>{p.name}</p>
                  <p className="text-[11px]">
                    <span className={cov.cls}>{cov.label}</span>
                    <span className="text-zinc-500"> · {gb(p.size)} · {p.seeders ?? 0} seedů{p.resolution ? ` · ${p.resolution}` : ""}</span>
                    {p.is_dubbed && <span className="text-emerald-300"> · dabing</span>}
                    {!p.is_dubbed && p.lang_tier === 1 && <span className="text-sky-300"> · titulky</span>}
                    {p.film === "unsure" && <span className="text-amber-300" title={(p.film_reasons ?? []).join(", ")}> · název seriálu sedí jen zčásti</span>}
                  </p>
                </div>
                {can("download") && (
                  started[p.ident]
                    ? <span className="text-violet-300 sm:w-56">{started[p.ident]}</span>
                    : <button onClick={() => start(p)} className={`self-start rounded bg-violet-600 font-medium text-white hover:bg-violet-500 sm:self-auto ${
                      big ? "min-h-11 w-full rounded-xl px-4 text-base" : "px-2.5 py-1"}`}>Stáhnout</button>
                )}
              </div>
            );
          })}
        </div>
      )}
      <p className="mt-2 text-[11px] text-zinc-500">
        Díly se zařadí podle čísel a názvů; co není díl (film, bonusy), zůstane ve stažených.
      </p>
    </section>
  );
}
