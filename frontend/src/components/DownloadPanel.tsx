"use client";

import Link from "next/link";
import { useState, useEffect, useCallback } from "react";
import { DownloadItem, getDownloads, removeDownload, formatSize, startQueuedNow, stopAllDownloads } from "@/lib/api";

function speedText(bytesPerSec: number): string {
  if (bytesPerSec < 1024) return `${bytesPerSec} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(0)} KB/s`;
  return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
}

function statusIcon(status: string): { icon: string; color: string } {
  switch (status) {
    case "active":
    case "downloading":
    case "stalledDL":
    case "forcedDL":
      return { icon: "\u25BC", color: "text-blue-400" };       // ▼
    case "complete":
    case "uploading":
    case "stalledUP":
    case "pausedUP":
      return { icon: "\u2713", color: "text-green-400" };      // ✓
    case "paused":
    case "pausedDL":
      return { icon: "\u275A\u275A", color: "text-yellow-400" }; // ❚❚
    case "error":
      return { icon: "\u2717", color: "text-red-400" };         // ✗
    case "waiting":
    case "queued":
    case "queuedDL":
      return { icon: "\u23F3", color: "text-zinc-400" };        // ⏳
    case "removed":
    case "cancelled":
    case "not_found":
      return { icon: "\u2212", color: "text-zinc-600" };        // −
    default:
      return { icon: "\u2022", color: "text-zinc-500" };        // •
  }
}

function isActive(status: string): boolean {
  return ["active", "downloading", "stalledDL", "forcedDL", "waiting", "queuedDL", "queued"].includes(status);
}

const HISTORY_PAGE = 10;

export default function DownloadPanel() {
  const [running, setRunning] = useState<DownloadItem[]>([]);
  const [history, setHistory] = useState<DownloadItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE);
  const [expanded, setExpanded] = useState(true);
  const [loading, setLoading] = useState(true);
  // running first, the queue under it, then the finished ones (newest on top)
  const downloads = [...running, ...history];

  const refresh = useCallback(async () => {
    try {
      const list = await getDownloads(historyLimit);
      setRunning(list.downloads);
      setHistory(list.history);
      setHistoryTotal(list.history_total);
    } catch {
      // silent fail
    } finally {
      setLoading(false);
    }
  }, [historyLimit]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [refresh]);

  const activeCount = running.filter((d) => d.status !== "queued").length;
  const queuedCount = running.filter((d) => d.status === "queued").length;

  async function stopAll(cancelRunning: boolean) {
    const q = cancelRunning
      ? `Zrušit ${activeCount} běžících stahování (rozstažené soubory se smažou), vyprázdnit frontu (${queuedCount}) a zastavit hledání lepších verzí?`
      : `Vyprázdnit frontu (${queuedCount}) a zastavit hledání lepších verzí? Běžící stahování doběhnou.`;
    if (!confirm(q)) return;
    await stopAllDownloads(cancelRunning).catch(() => {});
    refresh();
  }

  // Don't show at all if no downloads ever
  if (!loading && downloads.length === 0) return null;

  return (
    <div className="w-full max-w-7xl mx-auto">
      {/* Header bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between rounded-t-lg bg-zinc-900 border border-zinc-800 px-4 py-2.5 hover:bg-zinc-800/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <svg className="w-4 h-4 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          <span className="text-sm font-medium text-zinc-200">Downloads</span>
          {activeCount > 0 && (
            <span className="rounded-full bg-blue-600 px-2 py-0.5 text-xs font-bold text-white">
              {activeCount}
            </span>
          )}
          {queuedCount > 0 && (
            <span className="text-xs text-zinc-400" title="Čekají na volné místo (max. souběžných stahování v Nastavení)">
              + {queuedCount} ve frontě
            </span>
          )}
          {activeCount === 0 && queuedCount === 0 && downloads.length > 0 && (
            <span className="text-xs text-zinc-500">{historyTotal} dokončených</span>
          )}
        </div>
        <svg
          className={`w-4 h-4 text-zinc-500 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Download list */}
      {expanded && (
        <div className="border border-t-0 border-zinc-800 rounded-b-lg divide-y divide-zinc-800/50 bg-zinc-950/50">
          {(queuedCount > 0 || activeCount > 0) && (
            <div className="flex flex-wrap items-center justify-end gap-3 px-4 py-1.5 text-xs">
              {queuedCount > 0 && (
                <button onClick={() => stopAll(false)} className="text-zinc-400 hover:text-zinc-200"
                  title="Vyprázdní frontu a zastaví hledání lepších verzí / Chci; běžící stahování doběhnou">
                  Vyprázdnit frontu
                </button>
              )}
              <button onClick={() => stopAll(true)} className="text-red-400/80 hover:text-red-300"
                title="Zruší i běžící stahování Lumina">
                Zastavit vše
              </button>
            </div>
          )}
          {loading && downloads.length === 0 && (
            <div className="px-4 py-3 text-sm text-zinc-500 animate-pulse">Loading...</div>
          )}
          {downloads.map((dl, i) => {
            const id = dl.history ? `h${dl.id}` : dl.gid || dl.hash || (dl.queue_id ? `q${dl.queue_id}` : String(i));
            const queued = dl.status === "queued";
            const firstDone = dl.history && i === running.length;
            const total = dl.total_length || 0;
            const done = dl.completed_length || 0;
            const pct =
              dl.progress != null
                ? Math.round(dl.progress * 100)
                : total > 0
                ? Math.round((done / total) * 100)
                : 0;
            const { icon, color } = statusIcon(dl.status);
            const active = isActive(dl.status);
            const name = dl.filename || id;

            return (
              <div key={id} className="px-4 py-2.5 space-y-1.5">
                {firstDone && <div className="-mt-1 pb-1 text-[11px] uppercase tracking-wide text-zinc-600">Dokončené</div>}
                <div className="flex items-center gap-3">
                  <span className={`text-sm ${color}`}>{icon}</span>
                  {dl.tmdb_id && dl.content_type !== "tv" ? (
                    <Link href={`/library?tmdb=${dl.tmdb_id}`} title={`${name}\nOtevřít v knihovně`}
                      className="text-sm text-zinc-200 truncate flex-1 hover:text-violet-300">
                      {name}
                    </Link>
                  ) : (
                    <span className="text-sm text-zinc-200 truncate flex-1" title={name}>{name}</span>
                  )}
                  {(dl.requested_by || dl.created_at) && (
                    <span className="cursor-default text-xs text-zinc-500 hover:text-zinc-300"
                      title={[dl.requested_by && `Přidal: ${dl.requested_by}`, dl.created_at && `Kdy: ${dl.created_at}`,
                        dl.mode === "replace" ? "Po stažení nahradí verzi v knihovně" : dl.mode === "version" ? "Přidá se jako další verze" : "",
                        dl.film && `Film: ${dl.film}`].filter(Boolean).join("\n")}>
                      ⓘ
                    </span>
                  )}
                  {dl.source_label ? (
                    <span className={`text-xs font-medium ${
                      dl.source_label === "FastShare" ? "text-cyan-400" :
                      dl.source_label === "WebShare" ? "text-violet-400" :
                      "text-orange-400"
                    }`}>
                      {dl.source_label}
                    </span>
                  ) : (
                    <span className="text-xs text-zinc-500 uppercase">
                      {dl.backend === "qbittorrent" ? "Torrent" : "DDL"}
                    </span>
                  )}
                  {queued && dl.queue_id && (
                    <button onClick={async () => {
                        try { await startQueuedNow(dl.queue_id!); } catch (e) { alert(e instanceof Error ? e.message : "Chyba"); }
                        refresh();
                      }}
                      className="text-xs text-violet-300 hover:text-violet-200" title="Přeskočit frontu — spustit hned">
                      ▶ hned
                    </button>
                  )}
                  {(dl.gid || dl.hash || dl.queue_id || dl.history) && (
                    <button
                      onClick={async () => {
                        if (dl.history) {
                          await removeDownload(dl.id!, "history");
                        } else if (dl.queue_id) {
                          await removeDownload(String(dl.queue_id), "queue");
                        } else if (dl.hash && dl.backend === "qbittorrent") {
                          await removeDownload(dl.hash, "qbittorrent", active);
                        } else if (dl.gid) {
                          await removeDownload(dl.gid, "aria2", active);
                        }
                        refresh();
                      }}
                      className={`transition-colors ${
                        active
                          ? "text-zinc-600 hover:text-red-400"
                          : "text-zinc-600 hover:text-zinc-400"
                      }`}
                      title={dl.history ? "Odebrat ze seznamu (soubor v knihovně zůstane)" : queued ? "Odebrat z fronty" : active ? "Zrušit stahování" : "Odstranit"}
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
                {queued ? (
                  <div className="text-xs text-zinc-500">ve frontě · {dl.queue_pos}. na řadě</div>
                ) : dl.history ? (
                  <div className="text-xs text-zinc-500">
                    {{ complete: "staženo", cancelled: "zrušeno", error: "selhalo", removed: "zrušeno", not_found: "zmizelo z klienta" }[dl.status] ?? dl.status}
                    {(dl.finished_at || dl.created_at) && ` · ${(dl.finished_at || dl.created_at)!.slice(0, 16)}`}
                    {dl.total_length > 0 && ` · ${formatSize(dl.total_length)}`}
                  </div>
                ) : (
                <div className="flex items-center gap-3">
                  {/* Progress bar */}
                  <div className="flex-1 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        pct >= 100
                          ? "bg-green-500"
                          : active
                          ? "bg-blue-500"
                          : "bg-zinc-600"
                      }`}
                      style={{ width: `${Math.min(pct, 100)}%` }}
                    />
                  </div>
                  {/* Stats */}
                  <div className="flex items-center gap-2 text-xs text-zinc-500 shrink-0">
                    <span className="font-mono">{pct}%</span>
                    {total > 0 && (
                      <span>
                        {formatSize(done)} / {formatSize(total)}
                      </span>
                    )}
                    {active && dl.download_speed > 0 && (
                      <span className="text-blue-400 font-medium">
                        {speedText(dl.download_speed)}
                      </span>
                    )}
                  </div>
                </div>
                )}
              </div>
            );
          })}
          {history.length < historyTotal && (
            <button onClick={() => setHistoryLimit((n) => n + HISTORY_PAGE)}
              className="w-full px-4 py-2 text-xs text-violet-300 hover:text-violet-200">
              Zobrazit dalších {Math.min(HISTORY_PAGE, historyTotal - history.length)} (celkem {historyTotal})
            </button>
          )}
        </div>
      )}
    </div>
  );
}
