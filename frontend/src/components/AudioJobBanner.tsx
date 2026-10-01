"use client";

import { useEffect, useState } from "react";
import { AudioSyncJob, getAudioSyncJob } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

const KIND: Record<string, string> = { map: "Měření zvukových stop", apply: "Úprava zvuku filmu" };

const PHASE: Record<string, string> = {
  start: "začínám", target: "kontroluji stopy cíle", align: "srovnávám verze", tracks: "měřím jednotlivé stopy",
  track: "měřím stopu zvlášť", compare: "porovnávám dabingy", speed: "zjišťuji rychlost", windows: "porovnávám úseky filmu",
  cuts: "hledám místa střihu", prepare: "připravuji stopy", mux: "skládám soubor", verify: "kontroluji výsledek",
  import: "předávám knihovně",
};

const SEEN_KEY = "lumina.audioJobSeen";

/** Work with audio runs in the background — show it on every page. */
export default function AudioJobBanner() {
  const { can } = useAuth();
  const allowed = can("audiosync");   // a boolean: "can" itself is a new function on every render
  const [job, setJob] = useState<AudioSyncJob | null>(null);
  const [seen, setSeen] = useState(0);

  useEffect(() => {
    try { setSeen(Number(localStorage.getItem(SEEN_KEY) || 0)); } catch { /* private mode */ }
  }, []);

  useEffect(() => {
    if (!allowed) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const j = await getAudioSyncJob();
        if (alive) setJob(j);
        timer = setTimeout(tick, j.running ? 3000 : 15000);
      } catch {
        timer = setTimeout(tick, 30000);
      }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [allowed]);

  if (!job || !job.kind) return null;
  const title = `${KIND[job.kind] ?? "Práce se zvukem"}${job.title ? ` — ${job.title}` : ""}`;

  if (job.running) {
    const pct = job.total ? Math.round(((job.done ?? 0) / job.total) * 100) : null;
    const phase = PHASE[job.phase ?? ""] ?? job.phase ?? "";
    return (
      <div className="border-b border-violet-900/50 bg-violet-950/30 px-4 py-2 text-xs">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center gap-3">
          <span className="text-violet-200">{title}</span>
          <span className="text-zinc-400">{phase}{job.total ? ` ${job.done ?? 0}/${job.total}` : ""}</span>
          <div className="h-1.5 flex-1 min-w-[6rem] rounded bg-zinc-800 overflow-hidden">
            <div className={`h-full bg-violet-500 ${pct === null ? "w-1/3 animate-pulse" : ""}`} style={pct === null ? undefined : { width: `${pct}%` }} />
          </div>
        </div>
      </div>
    );
  }

  // the result stays visible until dismissed (once per finished job)
  if (!job.finished_at || job.finished_at <= seen || Date.now() / 1000 - job.finished_at > 24 * 3600) return null;
  const added = job.report?.added?.length ? ` Přidáno: ${job.report.added.join(", ")}.` : "";
  const ok = !job.error;
  return (
    <div className={`border-b px-4 py-2 text-xs ${ok ? "border-emerald-900/50 bg-emerald-950/20" : "border-amber-900/50 bg-amber-950/20"}`}>
      <div className="max-w-7xl mx-auto flex flex-wrap items-center gap-3">
        <span className={ok ? "text-emerald-200" : "text-amber-200"}>
          {ok ? "✓" : "⚠"} {title}: {ok ? `hotovo.${added}` : job.error}
        </span>
        <button className="ml-auto text-zinc-500 hover:text-zinc-300"
          onClick={() => {
            setSeen(job.finished_at!);
            try { localStorage.setItem(SEEN_KEY, String(job.finished_at)); } catch { /* private mode */ }
          }}>
          Zavřít
        </button>
      </div>
    </div>
  );
}
