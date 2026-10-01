"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { BackgroundTask, formatSize, getTasks } from "@/lib/api";

const SEEN_KEY = "lumina.tasksSeen";

function readSeen(): string[] {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
  } catch {
    return [];
  }
}

/** What runs in the background (downloads, scans, audio work …): a button in the navigation that
 *  spins while something runs; a click shows the list. Finished work with a result stays until seen. */
export default function TasksButton() {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<string[]>([]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => setSeen(readSeen()), []);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const t = await getTasks();
        if (!alive) return;
        setTasks(t);
        timer = setTimeout(tick, t.some((x) => x.running) || open ? 3000 : 15000);
      } catch {
        timer = setTimeout(tick, 30000);
      }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const running = tasks.filter((t) => t.running);
  const finished = tasks.filter((t) => !t.running && !seen.includes(t.id));
  const failed = finished.some((t) => t.error);

  const dismiss = (ids: string[]) => {
    const next = [...seen, ...ids].slice(-50);
    setSeen(next);
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(next)); } catch { /* private mode */ }
  };

  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen(!open)} title={running.length ? `Běží na pozadí: ${running.length}` : "Úlohy na pozadí"}
        className={`relative flex h-8 w-8 items-center justify-center rounded-full border transition-colors ${
          running.length ? "border-violet-600 text-violet-300" : finished.length ? (failed ? "border-amber-600 text-amber-300" : "border-emerald-700 text-emerald-300")
            : "border-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
        {running.length > 0 && <span className="absolute inset-0 rounded-full border-2 border-transparent border-t-violet-400 animate-spin" />}
        {/* activity: a pulse line — "something is going on", not settings */}
        <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round">
          <polyline points="3 12 7 12 10 5 14 19 17 12 21 12" />
        </svg>
        {(running.length > 0 || finished.length > 0) && (
          <span className={`absolute -right-1 -top-1 min-w-[1rem] rounded-full px-1 text-[10px] leading-4 text-white ${
            running.length ? "bg-violet-600" : failed ? "bg-amber-600" : "bg-emerald-700"}`}>
            {running.length || finished.length}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-xl space-y-3 text-xs">
          <p className="uppercase tracking-wide text-zinc-500">Na pozadí</p>
          {!running.length && !finished.length && <p className="text-zinc-500">Nic neběží.</p>}
          {running.map((t) => <TaskRow key={t.id} t={t} onNavigate={() => setOpen(false)} />)}
          {finished.length > 0 && (
            <div className="space-y-2 border-t border-zinc-800 pt-2">
              <div className="flex items-center">
                <span className="text-zinc-500">Dokončeno</span>
                <button onClick={() => dismiss(finished.map((t) => t.id))} className="ml-auto text-zinc-500 hover:text-zinc-300">Skrýt vše</button>
              </div>
              {finished.map((t) => <TaskRow key={t.id} t={t} onNavigate={() => setOpen(false)} onDismiss={() => dismiss([t.id])} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TaskRow({ t, onNavigate, onDismiss }: { t: BackgroundTask; onNavigate: () => void; onDismiss?: () => void }) {
  const pct = t.total ? Math.min(100, Math.round(((t.done ?? 0) / t.total) * 100)) : null;
  const count = t.total ? (t.unit === "bytes" ? `${formatSize(t.done ?? 0)} / ${formatSize(t.total)}` : `${t.done ?? 0}/${t.total}`) : "";
  const title = <span className={t.error ? "text-amber-200" : t.running ? "text-zinc-100" : "text-emerald-200"}>
    {!t.running && (t.error ? "⚠ " : "✓ ")}{t.title}
  </span>;
  return (
    <div className="space-y-1">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 break-words">
          {t.link ? <Link href={t.link} onClick={onNavigate} className="hover:underline">{title}</Link> : title}
        </div>
        {onDismiss && <button onClick={onDismiss} title="Skrýt" className="text-zinc-500 hover:text-zinc-300">✕</button>}
      </div>
      {(t.detail || t.error || count) && (
        <p className="text-zinc-400 break-words">{t.error || t.detail}{count && <span className="text-zinc-500"> · {count}</span>}</p>
      )}
      {t.running && (
        <div className="h-1.5 rounded bg-zinc-800 overflow-hidden">
          <div className={`h-full bg-violet-500 ${pct === null ? "w-1/3 animate-pulse" : ""}`} style={pct === null ? undefined : { width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
