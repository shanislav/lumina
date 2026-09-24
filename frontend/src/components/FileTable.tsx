"use client";

import { useState } from "react";
import { ScoredFile, startDownload, formatSize, OwnedVersion, LibraryAction, versionLabel } from "@/lib/api";

interface Props {
  files: ScoredFile[];
  loading: boolean;
  tmdb_id?: number;
  title?: string;
  year?: number;
  mediaType?: "movie" | "tv";
  onDownloadStarted?: () => void;
  owned?: OwnedVersion[];
}

const BADGE_STYLES: Record<string, { bg: string; label: string }> = {
  webshare: { bg: "bg-violet-900/60 text-violet-300", label: "WS" },
  fastshare: { bg: "bg-cyan-900/60 text-cyan-300", label: "FS" },
  jackett: { bg: "bg-orange-900/60 text-orange-300", label: "T" },
};

function SourceBadge({ source, seeders }: { source: string; seeders: number | null }) {
  const style = BADGE_STYLES[source] || { bg: "bg-zinc-800 text-zinc-300", label: source.slice(0, 2).toUpperCase() };
  const sourceUrl = source === "fastshare" ? "https://www.fastshare.cz" :
                    source === "webshare" ? "https://webshare.cz" : null;
  const badge = (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${style.bg} ${sourceUrl ? "cursor-pointer hover:opacity-80" : ""}`}>
      {style.label}
      {seeders != null && source === "jackett" && (
        <span className="ml-1 opacity-70">{seeders}</span>
      )}
    </span>
  );
  return badge;
}

function sourceLink(file: ScoredFile): string | null {
  if (file.source === "fastshare") {
    const ext = file.name.match(/\.[^.]+$/)?.[0] || "";
    const noExt = file.name.replace(/\.[^.]+$/, "");
    const noDiacritics = noExt.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
    // FastShare keeps a trailing "-" before the extension ("Film (2009).mkv" -> "film-2009-.mkv");
    // trimming it leads to a page without file details. Only the leading "-" is trimmed.
    const slug = noDiacritics.toLowerCase().replace(/[^a-z0-9.]+/g, "-").replace(/^-/, "");
    return `https://fastshare.cloud/${file.ident}/${slug}${ext.toLowerCase()}`;
  }
  if (file.source === "webshare") return `https://webshare.cz/file/${file.ident}/`;
  return null;
}

export default function FileTable({ files, loading, onDownloadStarted, tmdb_id, title, year, mediaType, owned = [] }: Props) {
  const [downloading, setDownloading] = useState<Record<string, string>>({});
  const [choosing, setChoosing] = useState<ScoredFile | null>(null);
  // same size to the byte = almost certainly the very file already in the library
  const ownedSizes = new Set(owned.map((v) => v.file_size));

  function handleDownload(file: ScoredFile) {
    // Movie already in the library → ask: another version, or replace one?
    if (owned.length > 0 && (mediaType || "movie") === "movie") {
      setChoosing(file);
      return;
    }
    runDownload(file);
  }

  async function runDownload(file: ScoredFile, action?: LibraryAction) {
    setChoosing(null);
    setDownloading((prev) => ({ ...prev, [file.ident]: "starting" }));
    try {
      const result = await startDownload(file, undefined, tmdb_id, title, year, mediaType || "movie", action);
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
      {choosing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setChoosing(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-xl w-full mx-4 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-zinc-100">Tento film už máš</h3>
            <div className="rounded-lg bg-zinc-950 border border-zinc-800 p-3 text-sm">
              <p className="text-xs uppercase tracking-wide text-zinc-500 mb-1">Stahuješ</p>
              <p className="text-zinc-100 break-all">{choosing.name}</p>
              <p className="text-zinc-400 text-xs mt-1">
                {choosing.quality} · {formatSize(choosing.size)}{choosing.is_dubbed ? " · dabing" : ""}
              </p>
            </div>
            <button onClick={() => runDownload(choosing, { mode: "version" })}
              className="w-full text-left rounded-lg border border-violet-700 bg-violet-950/30 hover:bg-violet-900/40 p-3">
              <p className="text-violet-200 font-medium">Stáhnout jako další verzi</p>
              <p className="text-xs text-zinc-400">Stávající zůstane, nová se uloží vedle ní (Plex je spojí do jednoho filmu).</p>
            </button>
            {owned.map((v) => (
              <button key={v.id} onClick={() => runDownload(choosing, { mode: "replace", file_id: v.id })}
                className="w-full text-left rounded-lg border border-zinc-700 hover:border-orange-600 hover:bg-orange-950/20 p-3">
                <p className="text-zinc-100 font-medium">Nahradit: {versionLabel(v)} · {formatSize(v.file_size)}</p>
                <p className="text-xs text-zinc-500 break-all">{v.filename}</p>
                <p className="text-[11px] text-orange-300/80 mt-1">
                  Stará verze se smaže až po úspěšném stažení a jen když délka filmu sedí.
                </p>
              </button>
            ))}
            <button onClick={() => setChoosing(null)} className="text-sm text-zinc-500 hover:text-zinc-300">Zrušit</button>
          </div>
        </div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-400 text-left">
            <th className="py-2 px-3 font-medium">Název</th>
            <th className="py-2 px-3 font-medium w-16">Zdroj</th>
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
            const link = sourceLink(file);

            return (
              <tr
                key={`${file.source}-${file.source_id}-${file.ident}`}
                className="border-b border-zinc-800/50 hover:bg-zinc-900/50"
              >
                <td className="py-2 px-3 text-zinc-200 max-w-md truncate">
                  {ownedSizes.has(file.size) && (
                    <span title="Soubor se stejnou velikostí už je v knihovně" className="mr-2 rounded bg-emerald-900/70 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
                      ✓ tento soubor už máš
                    </span>
                  )}
                  {file.name}
                </td>
                <td className="py-2 px-3">
                  {link ? (
                    <a href={link} target="_blank" rel="noopener noreferrer" title="Otevřít na zdroji">
                      <SourceBadge source={file.source} seeders={file.seeders} />
                    </a>
                  ) : (
                    <SourceBadge source={file.source} seeders={file.seeders} />
                  )}
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
