"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ScoredFile,
  MovieContext,
  startDownload,
  formatSize,
  OwnedVersion,
  LibraryAction,
  versionLabel,
  getFileDetails,
} from "@/lib/api";

interface Props {
  files: ScoredFile[];
  loading: boolean;
  tmdb_id?: number;
  title?: string;
  year?: number;
  mediaType?: "movie" | "tv";
  onDownloadStarted?: () => void;
  owned?: OwnedVersion[];
  movie?: MovieContext | null;
  preferLocalAudio?: boolean;
}

const BADGE_STYLES: Record<string, { bg: string; label: string }> = {
  webshare: { bg: "bg-violet-900/60 text-violet-300", label: "WS" },
  fastshare: { bg: "bg-cyan-900/60 text-cyan-300", label: "FS" },
  jackett: { bg: "bg-orange-900/60 text-orange-300", label: "T" },
};

function SourceBadge({ file }: { file: ScoredFile }) {
  const style = BADGE_STYLES[file.source] || { bg: "bg-zinc-800 text-zinc-300", label: file.source.slice(0, 2).toUpperCase() };
  const link = sourceLink(file);
  const badge = (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${style.bg} ${link ? "cursor-pointer hover:opacity-80" : ""}`}>
      {style.label}
      {file.seeders != null && file.source === "jackett" && <span className="ml-1 opacity-70">{file.seeders}</span>}
    </span>
  );
  return link ? (
    <a href={link} target="_blank" rel="noopener noreferrer" title="Otevřít na zdroji">{badge}</a>
  ) : badge;
}

function sourceLink(file: ScoredFile): string | null {
  if (file.source === "fastshare") {
    const ext = file.name.match(/\.[^.]+$/)?.[0] || "";
    const noExt = file.name.replace(/\.[^.]+$/, "");
    const noDiacritics = noExt.normalize("NFKD").replace(/[̀-ͯ]/g, "");
    // FastShare keeps a trailing "-" before the extension ("Film (2009).mkv" -> "film-2009-.mkv");
    // trimming it leads to a page without file details. Only the leading "-" is trimmed.
    const slug = noDiacritics.toLowerCase().replace(/[^a-z0-9.]+/g, "-").replace(/^-/, "");
    return `https://fastshare.cloud/${file.ident}/${slug}${ext.toLowerCase()}`;
  }
  if (file.source === "webshare") return `https://webshare.cz/file/${file.ident}/`;
  return null;
}

/** One line in the table: the same file (same size to the byte) found on several places. */
interface Row {
  key: string;
  file: ScoredFile;      // the representative (verified one if any)
  copies: ScoredFile[];  // all places where the file is
}

export default function FileTable({
  files, loading, onDownloadStarted, tmdb_id, title, year, mediaType, owned = [], movie, preferLocalAudio = true,
}: Props) {
  const [downloading, setDownloading] = useState<Record<string, string>>({});
  const [choosing, setChoosing] = useState<ScoredFile | null>(null);
  // same size to the byte = almost certainly the very file already in the library
  const ownedSizes = new Set(owned.map((v) => v.file_size));
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [showJunk, setShowJunk] = useState(false);
  // Re-evaluations from verified details, by "<source_id>:<ident>"
  const [updates, setUpdates] = useState<Record<string, Partial<ScoredFile>>>({});
  const [verify, setVerify] = useState({ done: 0, total: 0, running: false });
  const generation = useRef(0);

  useEffect(() => setFilters(loadFilters()), []);
  function updateFilters(patch: Partial<Filters>) {
    // functional update: quick consecutive clicks must not overwrite each other
    setFilters((prev) => {
      const next = { ...prev, ...patch };
      try { localStorage.setItem(FILTERS_KEY, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  }

  // Verify WebShare/FastShare files in the background, in small batches — only those that may be
  // the film: junk ("no" by name, year, episode, other part) is not worth a request. Likely matches
  // go first. The server throttles both sources and caches every file, so a repeated search is free.
  useEffect(() => {
    const gen = ++generation.current;
    setUpdates({});
    const todo = files
      .filter((f) => DETAIL_SOURCES.has(f.source) && !f.verified && f.film !== "no")
      .sort((a, b) => Number(b.film === "yes") - Number(a.film === "yes"));
    setVerify({ done: 0, total: todo.length, running: todo.length > 0 });
    (async () => {
      for (let i = 0; i < todo.length; i += BATCH) {
        const batch = todo.slice(i, i + BATCH);
        const res = await getFileDetails(
          batch.map((f) => ({ source_id: f.source_id, ident: f.ident, name: f.name, size: f.size })),
          movie ?? null,
        ).catch(() => ({} as Record<string, Partial<ScoredFile> | null>));
        if (gen !== generation.current) return; // a new search started
        const got: Record<string, Partial<ScoredFile>> = {};
        for (const [k, v] of Object.entries(res)) if (v) got[k] = v;
        setUpdates((prev) => ({ ...prev, ...got }));
        setVerify({ done: Math.min(i + BATCH, todo.length), total: todo.length, running: i + BATCH < todo.length });
      }
    })();
  }, [files, movie]);

  const rows = useMemo(() => {
    const merged = files.map((f) => ({ ...f, ...(updates[keyOf(f)] ?? {}) }) as ScoredFile);
    // same file on several sources → one row
    const bySize = new Map<string, ScoredFile[]>();
    for (const f of merged) {
      const k = f.source === "jackett" ? `t:${keyOf(f)}` : `s:${f.size}`;
      bySize.set(k, [...(bySize.get(k) ?? []), f]);
    }
    return Array.from(bySize, ([key, copies]): Row => ({
      key,
      copies,
      file: copies.find((c) => c.verified) ?? copies[0],
    }));
  }, [files, updates]);

  const { view, junk } = useMemo(() => {
    const junkRows = rows.filter((r) => r.file.film === "no");
    const pass = (r: Row) => {
      const f = r.file;
      if (f.film === "no" && !showJunk) return false;
      if (filters.sources.length && !r.copies.some((c) => filters.sources.includes(c.source))) return false;
      if (filters.qualities.length && !filters.qualities.includes(f.resolution || "")) return false;
      if (filters.audio === "local" && f.lang_tier < 2) return false;
      if (filters.audio === "local_or_subs" && f.lang_tier < 1) return false;
      return true;
    };
    const sorted = rows.filter(pass).sort((ra, rb) => {
      const a = ra.file, b = rb.file;
      if (filters.sort === "size") return b.size - a.size;
      if (filters.sort === "bitrate") return b.bitrate - a.bitrate;
      if (filters.sort === "quality") return b.quality_score - a.quality_score || a.size - b.size;
      // recommended: the right film → wanted language → quality → smaller file (same as the server)
      return (FILM_ORDER[a.film] ?? 1) - (FILM_ORDER[b.film] ?? 1)
        || (preferLocalAudio ? b.lang_tier - a.lang_tier : 0)
        || b.quality_score - a.quality_score
        || a.size - b.size;
    });
    return { view: sorted, junk: junkRows };
  }, [rows, filters, showJunk, preferLocalAudio]);

  const best = view.find((r) => r.file.film === "yes");

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
        Hledám soubory…
      </div>
    );
  }

  if (files.length === 0) return <p className="text-zinc-500 text-sm py-6">Nic nenalezeno.</p>;

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
                <QualityBadge file={choosing} /> {choosing.quality_summary} · {formatSize(choosing.size)}
              </p>
            </div>
            <button onClick={() => runDownload(choosing, { mode: "version" })}
              className="w-full text-left rounded-lg border border-violet-700 bg-violet-950/30 hover:bg-violet-900/40 p-3">
              <p className="text-violet-200 font-medium">Stáhnout jako další verzi</p>
              <p className="text-xs text-zinc-400">Stávající zůstane, nová se uloží vedle ní (Plex je spojí do jednoho filmu).</p>
            </button>
            {owned.map((v) => {
              const delta = v.quality_score != null ? choosing.quality_score - v.quality_score : null;
              return (
                <button key={v.id} onClick={() => runDownload(choosing, { mode: "replace", file_id: v.id })}
                  className="w-full text-left rounded-lg border border-zinc-700 hover:border-orange-600 hover:bg-orange-950/20 p-3">
                  <p className="text-zinc-100 font-medium">
                    Nahradit: {versionLabel(v)} · {formatSize(v.file_size)}
                    {delta != null && (
                      <span className={`ml-2 text-xs ${delta > 0 ? "text-green-400" : delta < 0 ? "text-red-400" : "text-zinc-400"}`}>
                        kvalita {v.quality_score} → {choosing.quality_score} ({delta > 0 ? "+" : ""}{delta})
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-zinc-500 break-all">{v.filename}</p>
                  <p className="text-[11px] text-orange-300/80 mt-1">
                    Stará verze se smaže až po úspěšném stažení a jen když délka filmu sedí.
                  </p>
                </button>
              );
            })}
            <button onClick={() => setChoosing(null)} className="text-sm text-zinc-500 hover:text-zinc-300">Zrušit</button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mb-3 text-xs">
        <FilterGroup label="Zvuk" value={filters.audio} onChange={(v) => updateFilters({ audio: v as AudioFilter })}
          options={[["all", "Vše"], ["local", "CZ/SK zvuk"], ["local_or_subs", "CZ/SK zvuk nebo titulky"]]} />
        <MultiGroup label="Rozlišení" values={filters.qualities} onChange={(v) => updateFilters({ qualities: v })}
          options={[["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"], ["SD", "SD"]]} />
        <MultiGroup label="Zdroj" values={filters.sources} onChange={(v) => updateFilters({ sources: v })}
          options={[["webshare", "WS"], ["fastshare", "FS"], ["jackett", "Torrent"]]} />
        <FilterGroup label="Řadit" value={filters.sort} onChange={(v) => updateFilters({ sort: v as SortMode })}
          options={[["recommended", "Doporučené"], ["quality", "Kvalita"], ["bitrate", "Bitrate"], ["size", "Velikost"]]} />
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-400 text-left">
            <th className="py-2 px-3 font-medium">Název</th>
            <th className="py-2 px-3 font-medium w-20">Zdroj</th>
            <th className="py-2 px-3 font-medium w-64">Kvalita</th>
            <th className="py-2 px-3 font-medium w-40">Zvuk / titulky</th>
            <th className="py-2 px-3 font-medium w-20">Velikost</th>
            <th className="py-2 px-3 font-medium w-12" title="Je to hledaný film?">Film</th>
            <th className="py-2 px-3 font-medium w-28"></th>
          </tr>
        </thead>
        <tbody>
          {view.map((row) => {
            const file = row.file;
            const dlState = downloading[file.ident];
            const dim = file.film === "no" || file.film === "length";
            return (
              <tr key={row.key} className={`border-b border-zinc-800/50 hover:bg-zinc-900/50 ${dim ? "opacity-50" : ""}`}>
                <td className="py-2 px-3 text-zinc-200 max-w-md truncate" title={file.name}>
                  {row === best && <span title="Doporučená volba" className="mr-1 text-yellow-400">★</span>}
                  {ownedSizes.has(file.size) && (
                    <span title="Soubor se stejnou velikostí už je v knihovně" className="mr-2 rounded bg-emerald-900/70 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
                      ✓ tento soubor už máš
                    </span>
                  )}
                  {file.name}
                </td>
                <td className="py-2 px-3 whitespace-nowrap space-x-1">
                  {row.copies.map((c) => <SourceBadge key={keyOf(c)} file={c} />)}
                </td>
                <td className="py-2 px-3">
                  <div className="flex items-center gap-2" title={qualityTooltip(file)}>
                    <QualityBadge file={file} />
                    <span className={`text-xs ${file.verified ? "text-zinc-300" : "text-zinc-500 italic"}`}>
                      {file.quality_summary || "?"}
                    </span>
                  </div>
                </td>
                <td className="py-2 px-3"><LanguageCell file={file} /></td>
                <td className="py-2 px-3 text-zinc-400 font-mono text-xs">{formatSize(file.size)}</td>
                <td className="py-2 px-3"><FilmCell file={file} /></td>
                <td className="py-2 px-3">
                  {!dlState ? (
                    <button onClick={() => handleDownload(file)}
                      className="rounded bg-violet-600 px-3 py-1 text-xs font-medium text-white hover:bg-violet-500 transition-colors">
                      Download
                    </button>
                  ) : dlState === "starting" ? (
                    <span className="text-xs text-zinc-500">Odesílám...</span>
                  ) : dlState === "error" ? (
                    <span className="text-xs text-red-400">Chyba</span>
                  ) : (
                    <span className="text-xs text-green-400">{dlState.slice(0, 8)}...</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center gap-3 mt-3 text-xs text-zinc-500">
        <span>Zobrazeno {view.length} z {rows.length}{rows.length < files.length ? ` (${files.length} souborů, stejné sloučeny)` : ""}</span>
        {verify.running && <span className="animate-pulse">· ověřuji u zdrojů {verify.done}/{verify.total}…</span>}
        {junk.length > 0 && (
          <button onClick={() => setShowJunk(!showJunk)} className="rounded border border-zinc-700 px-2 py-0.5 hover:border-zinc-500">
            {showJunk ? "Skrýt" : "Zobrazit"} jiné filmy / odpad ({junk.length})
          </button>
        )}
      </div>
    </div>
  );
}

// ── cells ──

const BATCH = 15;
const DETAIL_SOURCES = new Set(["webshare", "fastshare"]);
const FILM_ORDER: Record<string, number> = { yes: 0, unsure: 1, length: 2, no: 3 };
// Czech/Slovak audio is what this library is about — highlight it.
const LOCAL = new Set(["cs", "sk"]);

function keyOf(f: ScoredFile): string {
  return `${f.source_id}:${f.ident}`;
}

function qualityTooltip(f: ScoredFile): string {
  const parts = (f.quality_parts ?? []).map(([label, pts]) => `${label} ${pts >= 0 && f.quality_parts[0][0] !== label ? "+" : ""}${pts}`);
  return [parts.join(" · "), f.verified ? "ověřeno u zdroje" : "odhad podle názvu"].filter(Boolean).join("\n");
}

function QualityBadge({ file }: { file: ScoredFile }) {
  const s = file.quality_score;
  const cls = !file.resolution ? "bg-zinc-800 text-zinc-500"
    : s >= 80 ? "bg-green-900/70 text-green-300"
    : s >= 60 ? "bg-lime-900/60 text-lime-300"
    : s >= 40 ? "bg-yellow-900/60 text-yellow-300"
    : "bg-red-900/50 text-red-300";
  return <span className={`inline-block min-w-[2rem] text-center rounded px-1.5 py-0.5 text-xs font-bold font-mono ${cls}`}>{file.resolution ? s : "?"}</span>;
}

function FilmCell({ file }: { file: ScoredFile }) {
  const map: Record<string, [string, string]> = {
    yes: ["✓", "text-green-400"], unsure: ["?", "text-yellow-400"], length: ["⏱", "text-orange-400"], no: ["✗", "text-red-400"],
  };
  const [icon, cls] = map[file.film] ?? map.unsure;
  return <span className={`font-bold ${cls}`} title={(file.film_reasons ?? []).join("\n")}>{icon}</span>;
}

function Lang({ code, verified, sub }: { code: string; verified: boolean; sub?: boolean }) {
  const local = LOCAL.has(code);
  const base = "inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase mr-1 mb-0.5";
  const style = sub
    ? "bg-transparent text-zinc-400 border border-zinc-700"
    : verified
      ? local ? "bg-green-900/70 text-green-300" : "bg-zinc-700 text-zinc-200"
      : local ? "border border-green-800 text-green-400/80" : "border border-zinc-700 text-zinc-400";
  return <span className={`${base} ${style}`}>{code || "?"}</span>;
}

function LanguageCell({ file }: { file: ScoredFile }) {
  const verified = file.verified && (file.audio ?? []).some((a) => a.lang);
  const audio = Array.from(new Set(file.audio_langs ?? []));
  const subs = Array.from(new Set(file.subtitle_langs ?? []));
  if (!audio.length && !subs.length) return <span className="text-zinc-600">-</span>;
  const tip = verified
    ? (file.audio ?? []).map((a) => [(a.lang || "?").toUpperCase(), a.codec, a.channels ? `${a.channels}ch` : ""].filter(Boolean).join(" ")).join(", ")
    : "Podle názvu souboru (neověřeno)";
  return (
    <div title={tip} className="leading-tight">
      {verified ? <span className="text-green-500 text-[10px] mr-1">✓</span> : <span className="text-zinc-600 text-[10px] mr-1">?</span>}
      {audio.map((l) => <Lang key={`a${l}`} code={l} verified={verified} />)}
      {subs.length > 0 && (
        <span className="text-[10px] text-zinc-500 ml-0.5">
          tit: {subs.map((l) => <Lang key={`s${l}`} code={l} verified={verified} sub />)}
        </span>
      )}
    </div>
  );
}

// ── filters ──

type AudioFilter = "all" | "local" | "local_or_subs";
type SortMode = "recommended" | "quality" | "bitrate" | "size";
interface Filters {
  audio: AudioFilter;
  qualities: string[]; // empty = all
  sources: string[];   // empty = all
  sort: SortMode;
}
const DEFAULT_FILTERS: Filters = { audio: "all", qualities: [], sources: [], sort: "recommended" };
const FILTERS_KEY = "lumina.fileFilters";

function loadFilters(): Filters {
  try {
    return { ...DEFAULT_FILTERS, ...JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}") };
  } catch {
    return DEFAULT_FILTERS;
  }
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`px-2 py-0.5 rounded-full border transition-colors ${active ? "border-violet-500 bg-violet-600/20 text-violet-200" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"}`}>
      {children}
    </button>
  );
}

function FilterGroup({ label, value, options, onChange }: { label: string; value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-zinc-500 mr-1">{label}:</span>
      {options.map(([v, l]) => <Chip key={v} active={value === v} onClick={() => onChange(v)}>{l}</Chip>)}
    </div>
  );
}

function MultiGroup({ label, values, options, onChange }: { label: string; values: string[]; options: [string, string][]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-zinc-500 mr-1">{label}:</span>
      <Chip active={values.length === 0} onClick={() => onChange([])}>Vše</Chip>
      {options.map(([v, l]) => (
        <Chip key={v} active={values.includes(v)}
          onClick={() => onChange(values.includes(v) ? values.filter((x) => x !== v) : [...values, v])}>{l}</Chip>
      ))}
    </div>
  );
}
