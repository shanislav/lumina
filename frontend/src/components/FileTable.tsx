"use client";

import { useState } from "react";
import { ScoredFile, startDownload, formatSize } from "@/lib/api";

interface Props {
  files: ScoredFile[];
  loading: boolean;
  onDownloadStarted?: () => void;
}

const BADGE_STYLES: Record<string, { bg: string; label: string }> = {
  webshare: { bg: "bg-violet-900/60 text-violet-300", label: "WS" },
  fastshare: { bg: "bg-cyan-900/60 text-cyan-300", label: "FS" },
  jackett: { bg: "bg-orange-900/60 text-orange-300", label: "T" },
};

function SourceBadge({ source, seeders }: { source: string; seeders: number | null }) {
  const style = BADGE_STYLES[source] || { bg: "bg-zinc-800 text-zinc-300", label: source.slice(0, 2).toUpperCase() };
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${style.bg}`}>
      {style.label}
      {seeders != null && source === "jackett" && (
        <span className="ml-1 opacity-70">{seeders}</span>
      )}
    </span>
  );
}

export default function FileTable({ files, loading, onDownloadStarted }: Props) {
  const [downloading, setDownloading] = useState<Record<string, string>>({});

  async function handleDownload(file: ScoredFile) {
    setDownloading((prev) => ({ ...prev, [file.ident]: "starting" }));
    try {
      const result = await startDownload(file);
      const id = result.gid || result.hash || "ok";
      setDownloading((prev) => ({ ...prev, [file.ident]: id }));
      onDownloadStarted?.();
    } catch {
      setDownloading((prev) => ({ ...prev, [file.ident]: "error" }));
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-zinc-400 py-8">
        <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        AI analyzuje soubory...
      </div>
    );
  }

  if (files.length === 0) return null;

  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-400 text-left">
            <th className="py-2 px-3 font-medium w-16">Zdroj</th>
            <th className="py-2 px-3 font-medium">Název</th>
            <th className="py-2 px-3 font-medium w-20">Kvalita</th>
            <th className="py-2 px-3 font-medium w-16">Lang</th>
            <th className="py-2 px-3 font-medium w-20">Velikost</th>
            <th className="py-2 px-3 font-medium w-16">Skóre</th>
            <th className="py-2 px-3 font-medium w-28"></th>
          </tr>
        </thead>
        <tbody>
          {files.map((file) => {
            const dlState = downloading[file.ident];
            return (
              <tr
                key={`${file.source}-${file.source_id}-${file.ident}`}
                className="border-b border-zinc-800/50 hover:bg-zinc-900/50"
              >
                <td className="py-2 px-3">
                  <SourceBadge source={file.source} seeders={file.seeders} />
                </td>
                <td className="py-2 px-3 text-zinc-200 max-w-md truncate">
                  {file.name}
                </td>
                <td className="py-2 px-3">
                  <span className="inline-block rounded bg-zinc-800 px-2 py-0.5 text-xs font-mono">
                    {file.quality}
                  </span>
                </td>
                <td className="py-2 px-3">
                  {file.is_dubbed ? (
                    <span className="text-green-400 font-bold text-xs">DUB</span>
                  ) : (
                    <span className="text-zinc-600">-</span>
                  )}
                </td>
                <td className="py-2 px-3 text-zinc-400 font-mono text-xs">
                  {formatSize(file.size)}
                </td>
                <td className="py-2 px-3">
                  <span
                    className={`font-mono text-xs ${
                      file.relevance_score >= 70
                        ? "text-green-400"
                        : file.relevance_score >= 40
                        ? "text-yellow-400"
                        : "text-red-400"
                    }`}
                  >
                    {file.relevance_score}
                  </span>
                </td>
                <td className="py-2 px-3">
                  {!dlState ? (
                    <button
                      onClick={() => handleDownload(file)}
                      className="rounded bg-violet-600 px-3 py-1 text-xs font-medium text-white hover:bg-violet-500 transition-colors"
                    >
                      Download
                    </button>
                  ) : dlState === "starting" ? (
                    <span className="text-xs text-zinc-500">Odesílám...</span>
                  ) : dlState === "error" ? (
                    <span className="text-xs text-red-400">Chyba</span>
                  ) : (
                    <span className="text-xs text-green-400">
                      {dlState.slice(0, 8)}...
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
