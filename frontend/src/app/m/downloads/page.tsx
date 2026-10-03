"use client";

import { useEffect, useState } from "react";
import { DownloadItem, DownloadList, getDownloads, removeDownload } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import { size } from "@/lib/mobile";
import { Spinner } from "@/components/mobile/ui";

/** Phone: "Stahuje" — what downloads now (progress, speed, cancel), what waits, what finished lately. */
const se = (d: DownloadItem) => (d.season != null && d.episode != null
  ? ` S${String(d.season).padStart(2, "0")}E${String(d.episode).padStart(2, "0")}` : "");

function pct(d: DownloadItem): number {
  if (d.progress != null) return Math.round(d.progress * 100);
  return d.total_length ? Math.round((d.completed_length / d.total_length) * 100) : 0;
}

export default function MobileDownloads() {
  const { can } = useAuth();
  const [data, setData] = useState<DownloadList | null>(null);
  const [confirm, setConfirm] = useState<string>("");
  const refresh = () => getDownloads(15).then(setData).catch(() => {});
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);

  async function cancel(d: DownloadItem) {
    const key = d.gid || d.hash || String(d.queue_id);
    if (confirm !== key) { setConfirm(key); return; }
    if (d.queue_id) await removeDownload(String(d.queue_id), "queue");
    else if (d.hash && d.backend === "qbittorrent") await removeDownload(d.hash, "qbittorrent", true);
    else if (d.gid) await removeDownload(d.gid, "aria2", true);
    setConfirm("");
    refresh();
  }

  if (!data) return <main className="px-4"><Spinner text="Načítám…" /></main>;
  const running = data.downloads.filter((d) => d.status !== "queued");
  const queued = data.downloads.filter((d) => d.status === "queued");
  return (
    <main className="space-y-5 px-4 py-4">
      <h1 className="text-xl font-semibold text-zinc-100">Stahuje se</h1>
      {!data.downloads.length && <p className="text-zinc-400">Nic se nestahuje.</p>}
      {running.map((d) => {
        const key = d.gid || d.hash || "";
        const p = pct(d);
        return (
          <div key={key} className="space-y-2 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <p className="text-base font-medium text-zinc-100">{(d.film || d.filename) + se(d)}</p>
            <div className="h-2 overflow-hidden rounded bg-zinc-800"><div className="h-full bg-violet-500 transition-all" style={{ width: `${p}%` }} /></div>
            <p className="text-sm text-zinc-400">
              {p} % · {size(d.completed_length)} z {size(d.total_length)}{d.download_speed ? ` · ${size(d.download_speed)}/s` : ""}
              {d.source_label ? ` · ${d.source_label}` : ""}
            </p>
            {can("download") && (
              <button onClick={() => cancel(d)}
                className={`min-h-11 w-full rounded-xl border px-4 text-base ${confirm === key ? "border-red-700 bg-red-950 text-red-200" : "border-zinc-700 text-zinc-300"}`}>
                {confirm === key ? "Opravdu zrušit? Ťukni znovu" : "Zrušit"}
              </button>
            )}
          </div>
        );
      })}
      {queued.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-zinc-500">Ve frontě ({queued.length})</p>
          {queued.map((d) => (
            <div key={d.queue_id} className="flex items-center justify-between gap-3 rounded-xl border border-zinc-800 px-4 py-3">
              <span className="min-w-0 truncate text-zinc-200">{d.queue_pos}. {(d.film || d.filename) + se(d)}</span>
              {can("download") && (
                <button onClick={() => cancel(d)} className={`shrink-0 text-sm ${confirm === String(d.queue_id) ? "text-red-300" : "text-zinc-400"}`}>
                  {confirm === String(d.queue_id) ? "Opravdu?" : "Zrušit"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {data.history.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-zinc-500">Nedávno staženo</p>
          {data.history.map((d) => (
            <div key={d.id} className="rounded-xl border border-zinc-800/60 px-4 py-3">
              <p className="truncate text-zinc-200">{(d.film || d.filename) + se(d)}</p>
              <p className="text-sm text-zinc-500">
                {d.status === "complete" ? "✓ hotovo" : d.status === "error" ? "✕ chyba" : d.status}
                {d.finished_at ? ` · ${new Date(d.finished_at.replace(" ", "T")).toLocaleString("cs-CZ", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}` : ""}
              </p>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
