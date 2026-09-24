"use client";

import { useEffect, useMemo, useState } from "react";
import { ScoredFile, startDownload, formatSize, OwnedVersion, LibraryAction, versionLabel, FileDetails, getFileDetails } from "@/lib/api";

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
  // Real tracks from the sources, loaded for the top rows only (each file is asked once, then cached server-side).
  const [details, setDetails] = useState<Record<string, FileDetails | null>>({});
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

  useEffect(() => setFilters(loadFilters()), []);
  function updateFilters(patch: Partial<Filters>) {
    const next = { ...filters, ...patch };
    setFilters(next);
    try { localStorage.setItem(FILTERS_KEY, JSON.stringify(next)); } catch { /* private mode */ }
  }

  // Details are asked in small batches only for rows that need them (the server caches them per file
  // and throttles WebShare/FastShare), never for all results at once.
  async function loadDetails(rows: ScoredFile[]) {
    const batch = rows.filter((f) => DETAIL_SOURCES.has(f.source) && !(keyOf(f) in details)).slice(0, DETAILS_ROWS);
    if (!batch.length) return;
    setDetailsLoading(true);
    try {
      const d = await getFileDetails(batch.map((f) => ({ source_id: f.source_id, ident: f.ident, name: f.name })));
      setDetails((prev) => ({ ...prev, ...d }));
    } finally {
      setDetailsLoading(false);
    }
  }

  useEffect(() => {
    setDetails({});
    loadDetails(files);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files]);

  const { view, hiddenUnverified } = useMemo(() => {
    const facts = (f: ScoredFile) => rowFacts(f, details[keyOf(f)]);
    const pass = (f: ScoredFile) => {
      const x = facts(f);
      if (filters.sources.length && !filters.sources.includes(f.source)) return false;
      if (filters.qualities.length && !filters.qualities.includes(x.quality)) return false;
      if (filters.audio === "local" && !x.localAudio) return false;
      if (filters.audio === "local_or_subs" && !x.localAudio && !x.localSubs) return false;
      return true;
    };
    const shown = files.filter(pass);
    // hidden only because we do not know their languages yet → worth verifying
    const hidden = files.filter((f) => !pass(f) && !facts(f).verified && DETAIL_SOURCES.has(f.source));
    const sorted = [...shown].sort((a, b) => {
      const fa = facts(a), fb = facts(b);
      if (filters.sort === "size") return b.size - a.size;
      if (filters.sort === "quality") return (QUALITY_ORDER[fb.quality] ?? 0) - (QUALITY_ORDER[fa.quality] ?? 0) || b.size - a.size;
      // recommended: verified CZ/SK audio first, then CZ/SK from the name, then AI relevance
      const rank = (x: ReturnType<typeof facts>) => (x.localAudio ? (x.verified ? 2 : 1) : 0);
      return rank(fb) - rank(fa) || b.relevance_score - a.relevance_score || b.size - a.size;
    });
    return { view: sorted, hiddenUnverified: hidden };
  }, [files, details, filters]);

  const unverifiedInView = view.filter((f) => DETAIL_SOURCES.has(f.source) && !(keyOf(f) in details));

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
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mb-3 text-xs">
        <FilterGroup label="Zvuk" value={filters.audio} onChange={(v) => updateFilters({ audio: v as AudioFilter })}
          options={[["all", "Vše"], ["local", "CZ/SK zvuk"], ["local_or_subs", "CZ/SK zvuk nebo titulky"]]} />
        <MultiGroup label="Kvalita" values={filters.qualities} onChange={(v) => updateFilters({ qualities: v })}
          options={[["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"], ["SD", "SD"]]} />
        <MultiGroup label="Zdroj" values={filters.sources} onChange={(v) => updateFilters({ sources: v })}
          options={[["webshare", "WS"], ["fastshare", "FS"], ["jackett", "Torrent"]]} />
        <FilterGroup label="Řadit" value={filters.sort} onChange={(v) => updateFilters({ sort: v as SortMode })}
          options={[["recommended", "Doporučené"], ["quality", "Kvalita"], ["size", "Velikost"]]} />
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-400 text-left">
            <th className="py-2 px-3 font-medium">Název</th>
            <th className="py-2 px-3 font-medium w-16">Zdroj</th>
            <th className="py-2 px-3 font-medium w-20">Kvalita</th>
            <th className="py-2 px-3 font-medium w-40">
              Zvuk / titulky{detailsLoading && <span className="ml-1 text-zinc-600 animate-pulse">…</span>}
            </th>
            <th className="py-2 px-3 font-medium w-20">Velikost</th>
            <th className="py-2 px-3 font-medium w-16">Skóre</th>
            <th className="py-2 px-3 font-medium w-28"></th>
          </tr>
        </thead>
        <tbody>
          {view.map((file) => {
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
                    {resolutionLabel(details[`${file.source_id}:${file.ident}`]) || file.quality}
                  </span>
                </td>
                <td className="py-2 px-3">
                  <LanguageCell file={file} details={details[`${file.source_id}:${file.ident}`]} />
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
      <div className="flex flex-wrap items-center gap-3 mt-3 text-xs text-zinc-500">
        <span>Zobrazeno {view.length} z {files.length}</span>
        {hiddenUnverified.length > 0 && (
          <span>· {hiddenUnverified.length} skrytých zatím neověřených (jazyk jen podle názvu)</span>
        )}
        {(unverifiedInView.length > 0 || hiddenUnverified.length > 0) && (
          <button
            onClick={() => loadDetails([...unverifiedInView, ...hiddenUnverified])}
            disabled={detailsLoading}
            className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:border-zinc-500 disabled:opacity-50"
          >
            {detailsLoading ? "Ověřuji…" : `Ověřit další (${Math.min(DETAILS_ROWS, unverifiedInView.length + hiddenUnverified.length)})`}
          </button>
        )}
      </div>
    </div>
  );
}

const DETAILS_ROWS = 15;
// Czech/Slovak audio is what this library is about — highlight it.
const LOCAL = new Set(["cs", "sk"]);

function resolutionLabel(d?: FileDetails | null): string {
  if (!d || !d.width) return "";
  if (d.width >= 3200 || d.height >= 1600) return "2160p";
  if (d.width >= 1800 || d.height >= 900) return "1080p";
  if (d.width >= 1200 || d.height >= 650) return "720p";
  return "SD";
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

function LanguageCell({ file, details }: { file: ScoredFile; details?: FileDetails | null }) {
  const verified = !!details && details.audio.some((a) => a.lang);
  const audio = Array.from(new Set(verified ? details!.audio.map((a) => a.lang).filter(Boolean) : file.audio_langs ?? []));
  // WebShare does not report subtitles → keep the ones from the name (shown as unverified)
  const subsVerified = !!details && details.subtitles.length > 0;
  const subs = Array.from(new Set(subsVerified ? details!.subtitles : file.subtitle_langs ?? []));
  if (!audio.length && !subs.length) return <span className="text-zinc-600">-</span>;
  const tip = verified
    ? [
        details!.audio.map((a) => [a.lang.toUpperCase(), a.codec, a.channels ? `${a.channels}ch` : ""].filter(Boolean).join(" ")).join(", "),
        details!.video_codec, details!.bitrate ? `${Math.round(details!.bitrate / 1000)} kb/s` : "",
      ].filter(Boolean).join(" · ")
    : "Podle názvu souboru (neověřeno)";
  return (
    <div title={tip} className="leading-tight">
      {verified ? <span className="text-green-500 text-[10px] mr-1">✓</span> : <span className="text-zinc-600 text-[10px] mr-1">?</span>}
      {audio.map((l, i) => <Lang key={`a${i}${l}`} code={l} verified={verified} />)}
      {subs.length > 0 && (
        <span className="text-[10px] text-zinc-500 ml-0.5">
          tit: {subs.map((l) => <Lang key={`s${l}`} code={l} verified={subsVerified} sub />)}
        </span>
      )}
    </div>
  );
}

// ── filters ──

type AudioFilter = "all" | "local" | "local_or_subs";
type SortMode = "recommended" | "quality" | "size";
interface Filters {
  audio: AudioFilter;
  qualities: string[]; // empty = all
  sources: string[];   // empty = all
  sort: SortMode;
}
const DEFAULT_FILTERS: Filters = { audio: "all", qualities: [], sources: [], sort: "recommended" };
const FILTERS_KEY = "lumina.fileFilters";
const DETAIL_SOURCES = new Set(["webshare", "fastshare"]);
const QUALITY_ORDER: Record<string, number> = { "2160p": 4, "1080p": 3, "720p": 2, SD: 1 };

function loadFilters(): Filters {
  try {
    return { ...DEFAULT_FILTERS, ...JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}") };
  } catch {
    return DEFAULT_FILTERS;
  }
}

function keyOf(f: ScoredFile): string {
  return `${f.source_id}:${f.ident}`;
}

function normalizeQuality(q: string): string {
  const v = (q || "").toLowerCase();
  if (v.includes("2160") || v.includes("4k") || v.includes("uhd")) return "2160p";
  if (v.includes("1080")) return "1080p";
  if (v.includes("720")) return "720p";
  if (v.includes("576") || v.includes("480") || v === "sd") return "SD";
  return "";
}

/** What we know about a row: verified from the source when loaded, otherwise from the name. */
function rowFacts(f: ScoredFile, d?: FileDetails | null) {
  const verified = !!d && d.audio.some((a) => a.lang);
  const audio = verified ? d!.audio.map((a) => a.lang) : f.audio_langs ?? [];
  const subs = d && d.subtitles.length ? d.subtitles : f.subtitle_langs ?? [];
  return {
    verified,
    quality: resolutionLabel(d) || normalizeQuality(f.quality),
    localAudio: audio.some((l) => LOCAL.has(l)),
    localSubs: subs.some((l) => LOCAL.has(l)),
  };
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
