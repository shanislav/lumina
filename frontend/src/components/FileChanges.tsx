"use client";

/**
 * Úpravy souborů (backend modules/convert): library files that want a change — now Dolby Vision in an MKV, which
 * Android TV players do not play — and the queue. A button in the library's header; the list opens over the page.
 */
import { useCallback, useEffect, useState } from "react";
import ShrinkTab, { OPEN_EVENT, ShrinkScope, runningText } from "@/components/ShrinkTab";
import WorkersTab from "@/components/WorkersTab";
import { ConvertCandidate, ConvertJob, getConvertCandidates, getConvertJobs, startConvertDv } from "@/lib/api";

const STATUS: Record<ConvertJob["status"], { label: string; cls: string }> = {
  queued: { label: "ve frontě", cls: "text-zinc-400" },
  running: { label: "běží", cls: "text-sky-300" },
  done: { label: "hotovo", cls: "text-emerald-400" },
  error: { label: "chyba", cls: "text-red-400" },
};

const gb = (b: number) => `${(b / 1024 ** 3).toFixed(1)} GB`;
const base = (p: string) => p.split("/").pop() ?? p;

export default function FileChanges() {
  const [open, setOpen] = useState(false);
  const [dv, setDv] = useState<ConvertCandidate[]>([]);
  const [jobs, setJobs] = useState<ConvertJob[]>([]);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"dv" | "shrink" | "workers">("shrink");
  const [phases, setPhases] = useState<Record<string, string>>({});
  const [scope, setScope] = useState<ShrinkScope | null>(null);
  const [serverEncoders, setServerEncoders] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const [c, j] = await Promise.all([getConvertCandidates(), getConvertJobs()]);
      setDv(c.dv);
      setJobs(j.jobs);
      const v = j.current.value;
      setPhases(j.phases);
      setServerEncoders(j.server_encoders ?? []);
      const pct = j.current.phase === "encode" || j.current.phase === "parts";
      setPhase(j.current.phase ? (j.phases[j.current.phase] ?? j.current.phase)
        + (v != null ? (pct ? ` ${Math.round(v)} %` : ` ${v}`) : "") + (j.current.detail ? ` (${j.current.detail})` : "") : "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    // opened from a notification / the task list (?convert=1|shrink|dv), or from a show's season (+ cshow, cseason,
    // ctitle) — also when the library is open already (the link then only says so by "lumina:url")
    const fromUrl = () => {
      const p = new URLSearchParams(window.location.search);
      const which = p.get("convert");
      if (which) {
        setOpen(true);
        if (which === "dv" || which === "shrink" || which === "workers") setTab(which);
        load();
      }
      if (p.get("cshow") || p.get("cmovie")) {
        setScope({ show: Number(p.get("cshow")) || undefined, season: p.get("cseason") ? Number(p.get("cseason")) : undefined,
          movie: Number(p.get("cmovie")) || undefined, label: p.get("ctitle") || "" });
        setTab("shrink");
      }
    };
    fromUrl();
    window.addEventListener("lumina:url", fromUrl);
    // … or from a film's window on this page
    const fromFilm = (e: Event) => {
      setScope((e as CustomEvent<ShrinkScope>).detail);
      setTab("shrink");
      setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, fromFilm);
    return () => {
      window.removeEventListener(OPEN_EVENT, fromFilm);
      window.removeEventListener("lumina:url", fromUrl);
    };
  }, [load]);

  const active = jobs.some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!open || !active) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [open, active, load]);

  async function start(paths: string[]) {
    setBusy(true);
    setError("");
    try {
      await startConvertDv(paths);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const waiting = dv.filter((c) => !c.queued);
  return (
    <>
      <button onClick={() => setOpen(true)}
        title="Soubory, které potřebují upravit — Dolby Vision v MKV (Android TV ho nepřehraje) — a fronta úprav"
        className="px-3 py-2 rounded-lg border border-zinc-700 hover:border-zinc-500 text-zinc-300 text-sm transition-colors">
        Úpravy souborů{(waiting.length > 0 || active) && (
          <span className={`ml-1.5 rounded-full px-1.5 text-xs ${active ? "bg-sky-900/70 text-sky-200" : "bg-amber-900/70 text-amber-200"}`}>
            {active ? "…" : waiting.length}
          </span>
        )}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4" onClick={() => setOpen(false)}>
          <div className="my-8 w-full max-w-4xl space-y-4 rounded-xl border border-zinc-700 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold text-zinc-100">Úpravy souborů</h3>
              <button onClick={() => setOpen(false)} className="text-xl leading-none text-zinc-500 hover:text-zinc-200" aria-label="Zavřít">✕</button>
            </div>
            {error && <p className="rounded-lg bg-red-950/60 px-3 py-2 text-red-300">{error}</p>}

            <div className="flex gap-1 rounded-lg bg-zinc-800/60 p-1 text-xs">
              {([["shrink", "Zmenšit soubory"], ["dv", `Dolby Vision v MKV (${dv.length})`], ["workers", "Workeři"]] as const).map(([k, label]) => (
                <button key={k} onClick={() => setTab(k)}
                  className={`flex-1 rounded-md px-3 py-1.5 ${tab === k ? "bg-violet-600 text-white" : "text-zinc-400 hover:text-zinc-200"}`}>
                  {label}
                </button>
              ))}
            </div>

            {tab === "shrink" && <ShrinkTab jobs={jobs} phase={phase} phases={phases} onChanged={load} scope={scope} onClearScope={() => setScope(null)}
              serverGpu={serverEncoders.includes("hevc_nvenc")} />}

            {tab === "workers" && <WorkersTab phases={phases} />}

            {tab === "dv" && <section className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="font-medium text-zinc-200">Dolby Vision v MKV <span className="text-zinc-500">({dv.length})</span></h4>
                {waiting.length > 1 && (
                  <button disabled={busy} onClick={() => start(waiting.map((c) => c.path))}
                    className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
                    Přebalit vše ({waiting.length})
                  </button>
                )}
              </div>
              <p className="text-xs text-zinc-500">
                Android TV přehrávače (Google TV Streamer) Dolby Vision v MKV nepřehrají. Přebalení do MP4 je bez ztráty —
                stejný obraz i zvuk, titulky se uloží vedle souboru. Originál se smaže až po kontrole (délka, Dolby Vision,
                zvukové stopy). Nově stažené se přebalí samy (Nastavení → Knihovna).
              </p>
              {dv.length === 0 ? (
                <p className="text-xs text-zinc-400">V knihovně není žádný soubor s Dolby Vision v MKV.</p>
              ) : (
                <ul className="divide-y divide-zinc-800 rounded-lg border border-zinc-800">
                  {dv.map((c) => (
                    <li key={c.path} className="flex items-center gap-3 px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-zinc-200">{c.title}</div>
                        <div className="truncate text-xs text-zinc-500" title={c.path}>{base(c.path)} · {gb(c.size)}</div>
                      </div>
                      {c.queued ? <span className="text-xs text-sky-300">ve frontě</span> : (
                        <button disabled={busy} onClick={() => start([c.path])}
                          className="rounded-lg border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40">
                          Přebalit
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>}

            {jobs.some((j) => j.kind !== "plan") && (
              <section className="space-y-2">
                <h4 className="font-medium text-zinc-200">Fronta a historie</h4>
                <ul className="divide-y divide-zinc-800 rounded-lg border border-zinc-800 text-xs">
                  {jobs.filter((j) => j.kind !== "plan").map((j) => (
                    <li key={j.id} className="flex items-start gap-3 px-3 py-2">
                      <span className={`w-28 shrink-0 ${STATUS[j.status].cls}`}>
                        {j.status === "running" ? runningText(j, phase, phases) : STATUS[j.status].label}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-zinc-300" title={j.path}>{base(j.result_path || j.path)}</div>
                        {j.note && <div className={j.status === "error" ? "text-red-300" : "text-zinc-500"}>{j.note}</div>}
                      </div>
                      <span className="shrink-0 text-zinc-500">{j.auto ? "po stažení · " : ""}{(j.finished_at || j.created_at).slice(5, 16)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </div>
      )}
    </>
  );
}
