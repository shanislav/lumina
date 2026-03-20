"use client";

import { useState, useEffect, useCallback } from "react";
import { DownloadItem, getDownloads, removeDownload, formatSize } from "@/lib/api";

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
    case "queuedDL":
      return { icon: "\u23F3", color: "text-zinc-400" };        // ⏳
    case "removed":
      return { icon: "\u2212", color: "text-zinc-600" };        // −
    default:
      return { icon: "\u2022", color: "text-zinc-500" };        // •
  }
}

function isActive(status: string): boolean {
  return ["active", "downloading", "stalledDL", "forcedDL", "waiting", "queuedDL"].includes(status);
}

export default function DownloadPanel() {
  const [downloads, setDownloads] = useState<DownloadItem[]>([]);
  const [expanded, setExpanded] = useState(true);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const items = await getDownloads();
      setDownloads(items);
    } catch {
      // silent fail
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [refresh]);

  const activeCount = downloads.filter((d) => isActive(d.status)).length;

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
          {activeCount === 0 && downloads.length > 0 && (
            <span className="text-xs text-zinc-500">{downloads.length} total</span>
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
          {loading && downloads.length === 0 && (
            <div className="px-4 py-3 text-sm text-zinc-500 animate-pulse">Loading...</div>
          )}
          {downloads.map((dl, i) => {
            const id = dl.gid || dl.hash || String(i);
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
                <div className="flex items-center gap-3">
                  <span className={`text-sm ${color}`}>{icon}</span>
                  <span className="text-sm text-zinc-200 truncate flex-1" title={name}>
                    {name}
                  </span>
                  <span className="text-xs text-zinc-500 uppercase">
                    {dl.backend === "qbittorrent" ? "qBit" : "Aria2"}
                  </span>
                  {(dl.gid || dl.hash) && (
                    <button
                      onClick={async () => {
                        if (dl.hash && dl.backend === "qbittorrent") {
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
                      title={active ? "Zrušit stahování" : "Odstranit"}
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
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
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
