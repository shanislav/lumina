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

function hasDetail(file: ScoredFile): boolean {
  return !!(file.video_codec || file.audio_codec || file.release_type || (file.languages && file.languages.length > 0));
}

function DetailRow({ file }: { file: ScoredFile }) {
  const tags: { label: string; value: string }[] = [];
  if (file.video_codec) tags.push({ label: "Video", value: file.video_codec });
  if (file.audio_codec) tags.push({ label: "Audio", value: file.audio_codec });
  if (file.release_type) tags.push({ label: "Zdroj", value: file.release_type });

  return (
    <div className="px-3 py-2 bg-zinc-900/80 border-t border-zinc-800/50 flex items-center gap-4 flex-wrap">
      {tags.map((t) => (
        <span key={t.label} className="text-xs">
          <span className="text-zinc-500">{t.label}:</span>{" "}
          <span className="text-zinc-200 font-mono">{t.value}</span>
        </span>
      ))}
      {file.languages && file.languages.length > 0 && (
        <div className="flex gap-1">
          {file.languages.map((l) => (
            <span key={l} className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-300 font-medium">
              {l}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function FileTable({ files, loading, onDownloadStarted }: Props) {
  const [downloading, setDownloading] = useState<Record<string, string>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);

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
            const key = `${file.source}-${file.source_id}-${file.ident}`;
            const dlState = downloading[file.ident];
            const detail = hasDetail(file);
            const isExpanded = expandedId === key;

            return (
              <tr
                key={key}
                className="border-b border-zinc-800/50 hover:bg-zinc-900/50"
              >
                <td colSpan={7} className="p-0">
                  <div
                    className="grid grid-cols-[4rem_1fr_5rem_4rem_5rem_4rem_7rem] items-center cursor-pointer"
                    onClick={() => detail && setExpandedId(isExpanded ? null : key)}
                  >
                    <div className="py-2 px-3">
                      <SourceBadge source={file.source} seeders={file.seeders} />
                    </div>
                    <div className="py-2 px-3 text-zinc-200 truncate flex items-center gap-1.5">
                      <span className="truncate">{file.name}</span>
                      {detail && (
                        <svg className={`w-3.5 h-3.5 text-zinc-600 shrink-0 transition-transform ${isExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      )}
                    </div>
                    <div className="py-2 px-3">
                      <span className="inline-block rounded bg-zinc-800 px-2 py-0.5 text-xs font-mono">
                        {file.quality}
                      </span>
                    </div>
                    <div className="py-2 px-3">
                      {file.is_dubbed ? (
                        <span className="text-green-400 font-bold text-xs">DUB</span>
                      ) : (
                        <span className="text-zinc-600">-</span>
                      )}
                    </div>
                    <div className="py-2 px-3 text-zinc-400 font-mono text-xs">
                      {formatSize(file.size)}
                    </div>
                    <div className="py-2 px-3">
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
                    </div>
                    <div className="py-2 px-3" onClick={(e) => e.stopPropagation()}>
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
                    </div>
                  </div>
                  {isExpanded && <DetailRow file={file} />}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
