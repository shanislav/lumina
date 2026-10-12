"use client";

/**
 * Zmenšení souborů (backend modules/convert/shrink): the user picks the quality (a VMAF target), the speed and a few
 * more choices, then files; Lumina makes a preview of each (samples encoded, measured) — the estimate and frames of
 * the original and the new one to compare — and encodes only what the user confirms (or, with "rovnou", what saves
 * enough).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  ConvertJob, ShrinkFile, ShrinkOptions, ShrinkPlan, convertPreviewUrl, dropConvertJob, getShrinkCandidates,
  startShrink, startShrinkPlan,
} from "@/lib/api";

const DEFAULTS: ShrinkOptions = { vmaf: 95, speed: "balanced", max_height: 0, audio: "copy", min_saving: 25, go: false };
const STORE = "lumina.shrink.options";

const QUALITY = [
  { v: 95, label: "Nerozeznatelné (95)", hint: "Většina lidí rozdíl neuvidí ani při porovnání vedle sebe" },
  { v: 93, label: "Velmi dobrá (93)", hint: "Rozdíl postřehneš jen při přímém porovnání" },
  { v: 90, label: "Úsporná (90)", hint: "Při pozorném sledování je rozdíl vidět" },
];
const SPEED = [
  { v: "small", label: "Nejmenší", hint: "CPU, x265 slow — nejmenší soubor, nejpomalejší" },
  { v: "balanced", label: "Vyvážený", hint: "CPU, x265 medium" },
  { v: "fast", label: "Rychlejší", hint: "CPU, x265 fast — větší soubor, rychleji (GPU přijde s pracovníkem na PC)" },
] as const;

// short explanations of the words on this tab (hover / tap); the long ones are in "Jak to funguje?"
const TERMS = {
  vmaf: "VMAF = skóre 0–100, jak moc se nový obraz podobá originálu očima diváka (měřítko od Netflixu). 95 a víc: rozdíl neuvidíš ani vedle sebe. 93: jen při přímém porovnání. 90: při pozorném sledování. Pod 85: rozdíl je zřejmý.",
  crf: "CRF = nastavení kodéru (0–51): menší číslo = lepší obraz a větší soubor, větší číslo = menší soubor a horší obraz. Nevybíráš ho ty — Lumina ho najde tak, aby vyšla zvolená kvalita (VMAF).",
  weight: "× = kolikrát víc dat má soubor, než obraz v tomto rozlišení a kodeku běžně potřebuje. ×1 = akorát, ×2 = dvakrát víc. Hrubý odhad podle datového toku (počítá i zvuk) — kolik se dá doopravdy ušetřit, změří až náhled.",
  saving: "O kolik bude soubor menší. Odhad z krátkých vzorků — skutečnost bývá o pár procent jiná.",
  samples: "Pokusy na vzorcích: každý řádek je jedno CRF a VMAF, které s ním vyšlo. Hledá se nejvyšší CRF (nejmenší soubor), které ještě dosáhne cíle.",
};

function Term({ k, children }: { k: keyof typeof TERMS; children: React.ReactNode }) {
  return <abbr title={TERMS[k]} className="cursor-help underline decoration-zinc-500 decoration-dotted underline-offset-2">{children}</abbr>;
}

function Help() {
  return (
    <details className="rounded-lg border border-zinc-800 bg-zinc-800/30 px-3 py-2 text-xs text-zinc-400">
      <summary className="cursor-pointer select-none font-medium text-zinc-300">❓ Jak to funguje a co znamenají ty zkratky</summary>
      <div className="mt-2 space-y-2 leading-relaxed">
        <p><b className="text-zinc-200">Co se děje.</b> Video se znovu zakóduje úspornějším kodekem (H.265) tak, aby bylo menší, ale vypadalo
          (skoro) stejně. Zvuk a titulky zůstanou beze změny. Každé překódování trochu ubere — cílem je ubrat tak málo, aby to nebylo vidět.</p>
        <p><b className="text-zinc-200">1. Náhled</b> (pár minut, soubor se nemění): Lumina vystřihne 4 krátké kousky filmu, zkusí je
          zakódovat s různým nastavením a změří, jak moc se podobají originálu. Najde nastavení, které dá nejmenší soubor při tvé
          zvolené kvalitě, a ukáže odhad velikosti, času a 3 snímky — originál a nový (přepínáš mezerníkem).</p>
        <p><b className="text-zinc-200">2. Překódování</b> (dlouho — na serveru zhruba 2–3× délka filmu): celý soubor se zakóduje,
          zkontroluje (délka, zvukové stopy, kvalita na dvou místech) a teprve pak nahradí originál. Když kontrola neprojde nebo by
          úspora byla moc malá, originál zůstane.</p>
        <p><b className="text-zinc-200">VMAF</b> — {TERMS.vmaf}</p>
        <p><b className="text-zinc-200">CRF</b> — {TERMS.crf}</p>
        <p><b className="text-zinc-200">×</b> — {TERMS.weight}</p>
        <p><b className="text-zinc-200">Rychlost</b> — kodér může hledat úsporu důkladněji (pomaleji) nebo rychleji. Kvalita je pořád
          stejná (drží ji VMAF); pomalejší jen dá menší soubor.</p>
        <p><b className="text-zinc-200">Dolby Vision</b> se překódováním ztratí, proto takové soubory v seznamu nejsou.</p>
        <p><b className="text-zinc-200">Jak začít:</b> nech „Nerozeznatelné (95)“ a „Vyvážený“, udělej náhled 2–3 souborů a prohlédni
          si snímky na velké obrazovce. Když rozdíl nevidíš, můžeš zkusit 93.</p>
      </div>
    </details>
  );
}

const gb = (b: number) => `${(b / 1024 ** 3).toFixed(2)} GB`;
const dur = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.max(1, Math.round(s / 60))} min`);
const base = (p: string) => p.split("/").pop() ?? p;

function loadOptions(): ShrinkOptions {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORE) || "{}") };
  } catch {
    return DEFAULTS;
  }
}

export default function ShrinkTab({ jobs, phase, onChanged }: { jobs: ConvertJob[]; phase: string; onChanged: () => void }) {
  const [opts, setOpts] = useState<ShrinkOptions>(DEFAULTS);
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  const [byWeight, setByWeight] = useState(false);
  const [files, setFiles] = useState<ShrinkFile[]>([]);
  const [total, setTotal] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [compare, setCompare] = useState<{ job: number; shots: { a: string; b: string }[]; i: number; side: "a" | "b" } | null>(null);

  useEffect(() => setOpts(loadOptions()), []);
  const set = (patch: Partial<ShrinkOptions>) => {
    const next = { ...opts, ...patch };
    setOpts(next);
    try { localStorage.setItem(STORE, JSON.stringify(next)); } catch { /* */ }
  };

  const load = useCallback(async () => {
    try {
      const r = await getShrinkCandidates(q, all, byWeight);
      setFiles(r.files);
      setTotal(r.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [q, all, byWeight]);
  useEffect(() => {
    const t = setTimeout(load, 300);
    return () => clearTimeout(t);
  }, [load]);

  const groups = useMemo(() => {
    if (byWeight) return [["Nejvíc nadbytečných dat nahoře", [...files].sort((a, b) => b.weight - a.weight)]] as [string, ShrinkFile[]][];
    const m = new Map<string, ShrinkFile[]>();
    for (const f of files) m.set(f.group, [...(m.get(f.group) ?? []), f]);
    return [...m.entries()];
  }, [files, byWeight]);

  const toggle = (paths: string[], on: boolean) => {
    const next = new Set(picked);
    for (const p of paths) on ? next.add(p) : next.delete(p);
    setPicked(next);
  };

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const plans = jobs.filter((j) => j.kind === "plan");

  // keys in the comparison: ←/→ another frame, space = original / new
  useEffect(() => {
    if (!compare) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === " ") { e.preventDefault(); setCompare((c) => c && { ...c, side: c.side === "a" ? "b" : "a" }); }
      if (e.key === "ArrowRight") setCompare((c) => c && { ...c, i: (c.i + 1) % c.shots.length });
      if (e.key === "ArrowLeft") setCompare((c) => c && { ...c, i: (c.i + c.shots.length - 1) % c.shots.length });
      if (e.key === "Escape") setCompare(null);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [compare]);

  return (
    <div className="space-y-4">
      <Help />
      {error && <p className="rounded-lg bg-red-950/60 px-3 py-2 text-red-300">{error}</p>}

      <section className="grid gap-3 rounded-lg border border-zinc-800 p-3 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="text-xs text-zinc-400">Kvalita (<Term k="vmaf">VMAF</Term> — jak blízko originálu)</span>
          <select value={QUALITY.some((x) => x.v === opts.vmaf) ? opts.vmaf : "custom"}
            onChange={(e) => e.target.value !== "custom" && set({ vmaf: Number(e.target.value) })}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-zinc-200">
            {QUALITY.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
            <option value="custom">vlastní…</option>
          </select>
          <span className="flex items-center gap-2 text-xs text-zinc-500">
            {QUALITY.find((x) => x.v === opts.vmaf)?.hint ?? "Vlastní cíl"}
            <input type="number" min={80} max={99} step={0.5} value={opts.vmaf}
              onChange={(e) => set({ vmaf: Number(e.target.value) || 95 })}
              className="ml-auto w-16 rounded border border-zinc-700 bg-zinc-800 px-1 text-right text-zinc-200" />
          </span>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-zinc-400">Rychlost (mění čas a velikost, ne kvalitu)</span>
          <select value={opts.speed} onChange={(e) => set({ speed: e.target.value as ShrinkOptions["speed"] })}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-zinc-200">
            {SPEED.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
          </select>
          <span className="text-xs text-zinc-500">{SPEED.find((x) => x.v === opts.speed)?.hint}</span>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-zinc-400">Rozlišení</span>
          <select value={opts.max_height} onChange={(e) => set({ max_height: Number(e.target.value) })}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-zinc-200">
            <option value={0}>ponechat</option>
            <option value={1080}>nejvýš 1080p (4K → 1080p)</option>
            <option value={720}>nejvýš 720p</option>
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-zinc-400">Zvuk</span>
          <select value={opts.audio} onChange={(e) => set({ audio: e.target.value as ShrinkOptions["audio"] })}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-zinc-200">
            <option value="copy">kopírovat beze změny</option>
            <option value="shrink">zmenšit velké stopy (DTS, TrueHD, PCM → E-AC-3)</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          Překódovat jen při úspoře aspoň
          <input type="number" min={0} max={80} value={opts.min_saving} onChange={(e) => set({ min_saving: Number(e.target.value) || 0 })}
            className="w-14 rounded border border-zinc-700 bg-zinc-800 px-1 text-right text-zinc-200" /> %
        </label>
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          <input type="checkbox" checked={opts.go} onChange={(e) => set({ go: e.target.checked })} />
          po náhledu rovnou kódovat, když se to vyplatí (bez potvrzení)
        </label>
      </section>

      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Hledat film nebo seriál…"
            className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-zinc-200" />
          <label className="flex items-center gap-1 text-xs text-zinc-400">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> i soubory s rozumnou velikostí
          </label>
          <button onClick={() => setByWeight(!byWeight)}
            title="×: kolikrát víc dat, než obraz v tomto rozlišení a kodeku běžně potřebuje (hrubý odhad — přesně to změří náhled)"
            className={`rounded-lg border px-2.5 py-1.5 text-xs ${byWeight ? "border-violet-500 text-violet-300" : "border-zinc-700 text-zinc-400 hover:border-zinc-500"}`}>
            {byWeight ? "řazeno podle ×" : "seřadit podle ×"}
          </button>
          <button disabled={busy || picked.size === 0} onClick={() => run(async () => { await startShrinkPlan([...picked], opts); setPicked(new Set()); })}
            className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
            Udělat náhled ({picked.size})
          </button>
        </div>
        <p className="text-xs text-zinc-500">
          {all ? "Všechny soubory knihovny" : "Soubory s datovým tokem výrazně nad tím, co obraz potřebuje"} — {total}.
          Náhled zakóduje pár krátkých vzorků, změří kvalitu a ukáže odhad velikosti, času a snímky k porovnání. Soubor zatím nemění.
        </p>
        <div className="max-h-80 overflow-y-auto rounded-lg border border-zinc-800">
          {groups.length === 0 && <p className="p-3 text-xs text-zinc-500">Nic tu není.</p>}
          {groups.map(([group, list]) => {
            const on = list.every((f) => picked.has(f.path));
            return (
              <div key={group} className="border-b border-zinc-800 last:border-0">
                <label className="flex items-center gap-2 bg-zinc-800/40 px-3 py-1.5 text-xs font-medium text-zinc-300">
                  <input type="checkbox" checked={on} onChange={(e) => toggle(list.map((f) => f.path), e.target.checked)} />
                  {group} <span className="text-zinc-500">({list.length}, {gb(list.reduce((a, f) => a + f.size, 0))})</span>
                </label>
                {list.map((f) => (
                  <label key={f.path} className="flex items-center gap-2 px-3 py-1 text-xs hover:bg-zinc-800/30" title={f.path}>
                    <input type="checkbox" checked={picked.has(f.path)} onChange={(e) => toggle([f.path], e.target.checked)} />
                    <span className={`min-w-0 flex-1 truncate text-zinc-300 ${byWeight ? "" : "sm:w-24 sm:flex-none"}`}>
                      {byWeight && f.kind === "episode" ? `${f.group} ${f.title}` : f.title}
                    </span>
                    <span className="hidden min-w-0 flex-1 truncate text-zinc-500 sm:block">{base(f.path)}</span>
                    <span className="shrink-0 text-zinc-400">{f.height}p {f.codec} {f.hdr && f.hdr !== "SDR" ? f.hdr : ""} · {f.mbps} Mb/s · {gb(f.size)}</span>
                    <span className={`w-10 shrink-0 text-right ${f.weight >= 2 ? "text-amber-300" : f.weight >= 1.3 ? "text-amber-200/70" : "text-zinc-500"}`}
                      title={TERMS.weight}>×{f.weight}</span>
                  </label>
                ))}
              </div>
            );
          })}
        </div>
      </section>

      {plans.length > 0 && (
        <section className="space-y-2">
          <h4 className="font-medium text-zinc-200">Náhledy</h4>
          {plans.map((j) => {
            const r = j.result as unknown as ShrinkPlan;
            const ok = j.status === "done" && r?.crf;
            const encoding = jobs.some((x) => x.kind === "shrink" && x.path === j.path && (x.status === "queued" || x.status === "running"));
            return (
              <div key={j.id} className="space-y-2 rounded-lg border border-zinc-800 p-3 text-xs">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-zinc-200" title={j.path}>{base(j.path)}</div>
                    <div className={j.status === "error" || (r?.decision && r.decision !== "ok") ? "text-amber-300" : "text-zinc-400"}>
                      {j.status === "queued" ? "čeká ve frontě" : j.status === "running" ? `probíhá — ${phase || "…"}` : j.note}
                    </div>
                  </div>
                  {j.status !== "running" && j.status !== "queued" && (
                    <button onClick={() => run(() => dropConvertJob(j.id))} className="text-zinc-500 hover:text-zinc-200" title="Zahodit náhled">✕</button>
                  )}
                </div>
                {ok && (
                  <>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-zinc-300">
                      <span>{gb(r.src_size)} → <b className="text-emerald-300">~{gb(r.est_size ?? 0)}</b> (<Term k="saving">−{r.saving} %</Term>)</span>
                      <span><Term k="vmaf">VMAF</Term> {r.vmaf} (cíl {r.options.vmaf})</span>
                      <span><Term k="crf">CRF</Term> {r.crf}</span>
                      <span>kódování ~{dur(r.est_seconds ?? 0)}</span>
                    </div>
                    {r.trials && (
                      <div className="text-zinc-500">
                        <Term k="samples">Vzorky</Term>: {r.trials.map((t) => `CRF ${t.crf} → VMAF ${t.vmaf}`).join(" · ")}
                      </div>
                    )}
                    {r.stills && (
                      <div className="flex gap-2">
                        {r.stills.map((s, i) => (
                          <button key={i} onClick={() => setCompare({ job: j.id, shots: r.stills!, i, side: "a" })}
                            className="overflow-hidden rounded border border-zinc-700 hover:border-violet-500" title="Porovnat originál a nový">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={convertPreviewUrl(j.id, s.b)} alt="" className="h-16 w-28 object-cover" />
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      {encoding ? <span className="text-sky-300">kóduje se / ve frontě</span> : (
                        <button disabled={busy} onClick={() => run(() => startShrink(j.id, undefined, r.decision === "low_saving"))}
                          className="rounded-lg bg-violet-600 px-3 py-1.5 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
                          {r.decision === "low_saving" ? "Překódovat i tak" : "Překódovat"}
                        </button>
                      )}
                      {r.decision === "low_saving" && <span className="text-zinc-500">úspora je pod tvým limitem — soubor se nahradí i tak</span>}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </section>
      )}

      {compare && createPortal(
        <div className="fixed inset-0 z-[60] flex flex-col items-center justify-center gap-3 bg-black/95 p-4" onClick={() => setCompare(null)}>
          <div className="flex items-center gap-3 text-sm" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => setCompare({ ...compare, side: "a" })}
              className={`rounded-lg px-3 py-1.5 ${compare.side === "a" ? "bg-zinc-100 text-zinc-900" : "border border-zinc-600 text-zinc-300"}`}>Originál</button>
            <button onClick={() => setCompare({ ...compare, side: "b" })}
              className={`rounded-lg px-3 py-1.5 ${compare.side === "b" ? "bg-violet-500 text-white" : "border border-zinc-600 text-zinc-300"}`}>Nový</button>
            <span className="text-zinc-500">snímek {compare.i + 1}/{compare.shots.length} · mezerník přepíná, ←/→ další snímek</span>
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={convertPreviewUrl(compare.job, compare.shots[compare.i][compare.side])} alt=""
            className="max-h-[85vh] max-w-full cursor-pointer object-contain"
            onClick={(e) => { e.stopPropagation(); setCompare({ ...compare, side: compare.side === "a" ? "b" : "a" }); }} />
        </div>,
        document.body,   // over the whole page — the panel's blurred backdrop would hold a fixed element inside it
      )}
    </div>
  );
}
