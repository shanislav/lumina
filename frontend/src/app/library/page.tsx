"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import {
  LibraryMovie,
  LibraryShow,
  LibraryShowDetail,
  TMDBSearchResult,
  ScanStatus,
  LibraryStatus,
  scanLibrary,
  getScanStatus,
  getLibrarySummary,
  OrganizePlan,
  OrganizeResult,
  getOrganizePlan,
  getOrganizePlanAll,
  applyOrganize,
  undoOrganize,
  getLibraryMovies,
  getLibraryShows,
  getShowDetail,
  searchTMDBForFix,
  fixMovieMatch,
  formatSize,
} from "@/lib/api";
import DownloadPanel from "@/components/DownloadPanel";

type Tab = "filmy" | "serialy";
type MovieFilter = "all" | "versions" | "review" | "unmatched";

/** One card in the grid: a movie with all its files (versions). Unidentified files stay alone. */
interface MovieGroup {
  key: string;
  main: LibraryMovie;
  versions: LibraryMovie[];
}

const QUALITY_RANK: Record<string, number> = { "2160p": 5, "1080p": 4, "720p": 3, "576p": 2, "480p": 1 };

function groupMovies(movies: LibraryMovie[]): MovieGroup[] {
  const groups = new Map<string, LibraryMovie[]>();
  for (const m of movies) {
    const identified = (m.status === "matched" || m.status === "manual") && m.tmdb_id;
    const key = identified ? `tmdb-${m.tmdb_id}` : `file-${m.id}`;
    groups.set(key, [...(groups.get(key) ?? []), m]);
  }
  return Array.from(groups, ([key, versions]) => {
    versions.sort((a, b) => (QUALITY_RANK[b.quality] ?? 0) - (QUALITY_RANK[a.quality] ?? 0) || b.file_size - a.file_size);
    return { key, main: versions[0], versions };
  });
}

const STATUS_BADGE: Record<LibraryStatus, { label: string; cls: string; title: string } | null> = {
  matched: null,
  manual: { label: "✓", cls: "bg-sky-900/80 text-sky-300", title: "Vybráno ručně" },
  review: { label: "?", cls: "bg-orange-900/80 text-orange-300", title: "Na kontrolu" },
  unmatched: { label: "!", cls: "bg-red-900/80 text-red-300", title: "Nespárováno" },
};

function formatDuration(seconds: number): string {
  if (!seconds) return "";
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

const QUALITY_COLORS: Record<string, string> = {
  "2160p": "bg-amber-900/60 text-amber-300",
  "1080p": "bg-green-900/60 text-green-300",
  "720p": "bg-blue-900/60 text-blue-300",
  "480p": "bg-zinc-700 text-zinc-300",
  unknown: "bg-zinc-800 text-zinc-500",
};

export default function LibraryPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("filmy");
  const [movies, setMovies] = useState<LibraryMovie[]>([]);
  const [shows, setShows] = useState<LibraryShow[]>([]);
  const [loading, setLoading] = useState(true);
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [scanResult, setScanResult] = useState<string | null>(null);
  const [movieFilter, setMovieFilter] = useState<MovieFilter>("all");
  const [summary, setSummary] = useState<Partial<Record<LibraryStatus, number>>>({});
  const scanning = !!scan?.running;
  // fix names on disk
  const [moviePlan, setMoviePlan] = useState<OrganizePlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkPlans, setBulkPlans] = useState<OrganizePlan[] | null>(null);
  const [bulkSelected, setBulkSelected] = useState<Set<number>>(new Set());
  const [organizeResult, setOrganizeResult] = useState<OrganizeResult | null>(null);
  const [selectedShow, setSelectedShow] = useState<LibraryShowDetail | null>(null);
  const [showLoading, setShowLoading] = useState(false);
  const [fixingMovie, setFixingMovie] = useState<LibraryMovie | null>(null);
  const [fixQuery, setFixQuery] = useState("");
  const [fixResults, setFixResults] = useState<TMDBSearchResult[]>([]);
  const [fixSearching, setFixSearching] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [m, s, sum] = await Promise.all([getLibraryMovies(), getLibraryShows(), getLibrarySummary()]);
      setMovies(m);
      setShows(s);
      setSummary(sum);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    getScanStatus().then((st) => st.running && setScan(st)).catch(() => {});
  }, [loadData]);

  // Poll the background scan while it runs
  useEffect(() => {
    if (!scan?.running) return;
    const timer = setInterval(async () => {
      try {
        const st = await getScanStatus();
        setScan(st);
        if (!st.running) {
          const s = st.stats;
          setScanResult(
            st.error
              ? `Chyba při skenování: ${st.error}`
              : s
                ? `Hotovo: ${s.movies_found} souborů — ${s.matched} spárováno, ${s.review} na kontrolu, ${s.unmatched} nespárováno · ${s.shows_found} seriálů (${s.episodes_matched} epizod)`
                : "Hotovo"
          );
          loadData();
        }
      } catch {
        // ignore, next tick retries
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [scan?.running, loadData]);

  async function handleScan(force = false) {
    setScanResult(null);
    try {
      setScan(await scanLibrary(force));
    } catch {
      setScanResult("Chyba při spuštění skenování");
    }
  }

  const groups = useMemo(() => groupMovies(movies), [movies]);
  const multiVersion = groups.filter((g) => g.versions.length > 1);
  const visibleGroups =
    movieFilter === "all" ? groups
    : movieFilter === "versions" ? multiVersion
    : groups.filter((g) => g.main.status === movieFilter);
  const [versionsOf, setVersionsOf] = useState<MovieGroup | null>(null);

  function openMovie(movie: LibraryMovie) {
    setFixingMovie(movie);
    setFixQuery(movie.filename.replace(/\.[^.]+$/, ""));
    setFixResults([]);
    setMoviePlan(null);
    setPlanError(null);
    setOrganizeResult(null);
  }

  async function loadMoviePlan(movieId: number) {
    setPlanBusy(true);
    setPlanError(null);
    try {
      setMoviePlan(await getOrganizePlan(movieId));
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setPlanBusy(false);
    }
  }

  async function openBulk() {
    setBulkOpen(true);
    setBulkPlans(null);
    setOrganizeResult(null);
    setPlanError(null);
    try {
      const plans = await getOrganizePlanAll();
      setBulkPlans(plans);
      setBulkSelected(new Set(plans.filter((p) => p.conflicts.length === 0).map((p) => p.movie_ids[0])));
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : "Chyba");
      setBulkPlans([]);
    }
  }

  async function runOrganize(movieIds: number[]) {
    setPlanBusy(true);
    try {
      const result = await applyOrganize(movieIds);
      setOrganizeResult(result);
      setMoviePlan(null);
      if (bulkOpen) setBulkPlans(null);
      await loadData();
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setPlanBusy(false);
    }
  }

  async function runUndo(batchId: string) {
    setPlanBusy(true);
    try {
      await undoOrganize(batchId);
      setOrganizeResult(null);
      await loadData();
    } finally {
      setPlanBusy(false);
    }
  }

  async function handleShowClick(show: LibraryShow) {
    setShowLoading(true);
    try {
      const detail = await getShowDetail(show.tmdb_id);
      setSelectedShow(detail);
    } catch {
      // ignore
    } finally {
      setShowLoading(false);
    }
  }

  /** Search this movie's offers in upgrade mode: only better than this version, same language or better. */
  function findBetterVersion(movie: LibraryMovie) {
    const tmdbMovie = {
      tmdb_id: movie.tmdb_id, title: movie.title, original_title: movie.original_title, year: movie.year,
      overview: "", poster_url: movie.poster_url, media_type: "movie",
    };
    router.push(`/?movie=${btoa(encodeURIComponent(JSON.stringify(tmdbMovie)))}&upgrade=${movie.id}`);
  }

  function handleSearchEpisode(showTitle: string, season: number, episode: number) {
    const se = `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
    const query = `${showTitle} ${se}`;
    const origTitle = selectedShow?.original_title || "";
    const tmdbId = selectedShow?.tmdb_id || 0;
    let url = `/?q=${encodeURIComponent(query)}&filesearch=1`;
    if (origTitle && origTitle !== showTitle) {
      url += `&original_title=${encodeURIComponent(origTitle + " " + se)}`;
    }
    if (tmdbId) {
      url += `&tmdb_id=${tmdbId}&media_type=tv`;
    }
    router.push(url);
  }

  return (
    <main className="flex flex-col gap-8 px-4 py-8 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/" className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm">
            &larr; Hledat
          </Link>
          <h1 className="text-2xl font-bold text-zinc-100">Knihovna</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={openBulk}
            disabled={scanning}
            title="Přejmenuje složky a soubory spárovaných filmů podle pravidel (s náhledem)"
            className="px-3 py-2 rounded-lg border border-zinc-700 hover:border-zinc-500 disabled:opacity-40 text-zinc-300 text-sm transition-colors"
          >
            Opravit názvy
          </button>
          <button
            onClick={() => handleScan(true)}
            disabled={scanning}
            title="Znovu ověří i už spárované filmy (ručně vybrané zůstanou)"
            className="px-3 py-2 rounded-lg border border-zinc-700 hover:border-zinc-500 disabled:opacity-40 text-zinc-300 text-sm transition-colors"
          >
            Ověřit vše
          </button>
          <button
            onClick={() => handleScan(false)}
            disabled={scanning}
            className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-zinc-700 text-white text-sm font-medium transition-colors"
          >
            {scanning ? "Skenuji..." : "Skenovat"}
          </button>
        </div>
      </div>

      {scanning && scan && (
        <div className="rounded-lg bg-zinc-900 border border-zinc-800 px-4 py-3 space-y-2">
          <div className="flex justify-between text-sm text-zinc-300">
            <span>{scan.phase === "tv" ? "Seriály…" : `Filmy ${scan.done ?? 0} / ${scan.total ?? 0}`}</span>
            <span className="text-zinc-500 truncate ml-4">{scan.current}</span>
          </div>
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-violet-600 transition-all"
              style={{ width: `${scan.total ? ((scan.done ?? 0) / scan.total) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {scanResult && (
        <div className="rounded-lg bg-violet-900/20 border border-violet-800 px-4 py-3 text-violet-300 text-sm">
          {scanResult}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-zinc-900 rounded-lg p-1 w-fit border border-zinc-800">
        <button
          onClick={() => { setTab("filmy"); setSelectedShow(null); }}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
            tab === "filmy" ? "bg-violet-600 text-white shadow" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Filmy ({movies.length})
        </button>
        <button
          onClick={() => { setTab("serialy"); setSelectedShow(null); }}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
            tab === "serialy" ? "bg-violet-600 text-white shadow" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Serialy ({shows.length})
        </button>
      </div>

      {loading ? (
        <div className="text-zinc-500 animate-pulse text-center py-12">Nacitam...</div>
      ) : tab === "filmy" ? (
        /* ═══ MOVIES TAB ═══ */
        movies.length === 0 ? (
          <div className="text-center py-12 text-zinc-500">
            Zadne filmy. Klikni &quot;Skenovat&quot; pro nacteni knihovny.
          </div>
        ) : (
          <div className="space-y-4">
          <div className="flex flex-wrap gap-2 text-sm">
            {([
              ["all", `Vše (${groups.length})`],
              ["versions", `Více verzí (${multiVersion.length})`],
              ["review", `Na kontrolu (${summary.review ?? 0})`],
              ["unmatched", `Nespárované (${summary.unmatched ?? 0})`],
            ] as [MovieFilter, string][]).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setMovieFilter(key)}
                className={`px-3 py-1 rounded-full border transition-colors ${
                  movieFilter === key
                    ? "border-violet-500 bg-violet-600/20 text-violet-200"
                    : "border-zinc-800 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {visibleGroups.length === 0 && (
            <div className="text-center py-8 text-zinc-500 text-sm">Nic k zobrazení.</div>
          )}
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {visibleGroups.map(({ key, main: movie, versions }) => (
              <div
                key={key}
                onClick={() => (versions.length > 1 ? setVersionsOf({ key, main: movie, versions }) : openMovie(movie))}
                className="group cursor-pointer rounded-lg overflow-hidden bg-zinc-900 border border-zinc-800 hover:border-violet-500 transition-colors"
              >
                <div className="aspect-[2/3] relative bg-zinc-800">
                  {movie.poster_url ? (
                    <Image
                      src={movie.poster_url}
                      alt={movie.title}
                      fill
                      sizes="(max-width: 640px) 33vw, 12.5vw"
                      className="object-cover"
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-zinc-600 text-xs">
                      Bez plakatu
                    </div>
                  )}
                  {movie.quality && movie.quality !== "unknown" && (
                    <span className={`absolute top-1 right-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${QUALITY_COLORS[movie.quality] || QUALITY_COLORS.unknown}`}>
                      {movie.quality}
                    </span>
                  )}
                  {STATUS_BADGE[movie.status] && (
                    <span
                      title={STATUS_BADGE[movie.status]!.title}
                      className={`absolute top-1 left-1 px-1.5 py-0.5 rounded text-[9px] font-bold ${STATUS_BADGE[movie.status]!.cls}`}
                    >
                      {STATUS_BADGE[movie.status]!.label}
                    </span>
                  )}
                  {versions.length > 1 && (
                    <span className="absolute bottom-1 left-1 px-1.5 py-0.5 rounded bg-violet-700/90 text-white text-[10px] font-bold">
                      {versions.length} verze
                    </span>
                  )}
                  {movie.media?.hdr && movie.media.hdr !== "SDR" && (
                    <span className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/70 text-yellow-300 text-[9px] font-bold">
                      {movie.media.hdr}
                    </span>
                  )}
                </div>
                <div className="p-2">
                  <p className="text-sm font-medium text-zinc-100 truncate">{movie.title}</p>
                  <div className="flex items-center gap-2 text-xs text-zinc-500">
                    {movie.year && <span>{movie.year}</span>}
                    <span>{formatSize(movie.file_size)}</span>
                  </div>
                  {movie.language && <p className="text-[10px] text-zinc-500 truncate">{movie.language}</p>}
                </div>
              </div>
            ))}
          </div>
          </div>
        )
      ) : selectedShow ? (
        /* ═══ SHOW DETAIL ═══ */
        <div className="space-y-4">
          <button
            onClick={() => setSelectedShow(null)}
            className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm"
          >
            &larr; Zpet na serialy
          </button>

          <div className="flex gap-6">
            {selectedShow.poster_url && (
              <div className="w-32 flex-shrink-0">
                <Image
                  src={selectedShow.poster_url}
                  alt={selectedShow.title}
                  width={128}
                  height={192}
                  className="rounded-lg"
                />
              </div>
            )}
            <div>
              <h2 className="text-xl font-bold text-zinc-100">
                {selectedShow.title}
                {selectedShow.year && <span className="text-zinc-500 font-normal ml-2">({selectedShow.year})</span>}
              </h2>
              <p className="text-sm text-zinc-400 mt-1 line-clamp-3">{selectedShow.overview}</p>
              <p className="text-xs text-zinc-500 mt-2">
                {selectedShow.total_seasons} sezon · {selectedShow.total_episodes} epizod celkem
              </p>
            </div>
          </div>

          {/* Seasons accordion */}
          {selectedShow.seasons.map((season) => {
            const owned = season.episodes.filter((e) => e.has_file).length;
            const total = season.episodes.length;
            return (
              <details key={season.season_number} className="group border border-zinc-800 rounded-lg">
                <summary className="flex items-center justify-between p-4 cursor-pointer hover:bg-zinc-900/50">
                  <div className="flex items-center gap-3">
                    <span className="text-zinc-100 font-medium">Sezona {season.season_number}</span>
                    <span className="text-xs text-zinc-500">{owned}/{total} epizod</span>
                  </div>
                  <div className="w-24 h-2 bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-violet-600 rounded-full transition-all"
                      style={{ width: `${total > 0 ? (owned / total) * 100 : 0}%` }}
                    />
                  </div>
                </summary>
                <div className="border-t border-zinc-800">
                  {season.episodes.map((ep) => (
                    <div
                      key={ep.episode}
                      className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800/50 last:border-0"
                    >
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        <span className={`w-5 h-5 flex items-center justify-center rounded-full text-xs ${
                          ep.has_file ? "bg-green-900/60 text-green-400" : "bg-zinc-800 text-zinc-600"
                        }`}>
                          {ep.has_file ? "✓" : ep.episode}
                        </span>
                        <div className="min-w-0 flex-1">
                          <span className="text-sm text-zinc-200">
                            E{String(ep.episode).padStart(2, "0")}
                            {ep.title && <span className="text-zinc-400 ml-2">{ep.title}</span>}
                          </span>
                          {ep.has_file && (
                            <span className="text-xs text-zinc-600 ml-2">
                              {ep.quality && <span className="mr-1">{ep.quality}</span>}
                              {formatSize(ep.file_size)}
                            </span>
                          )}
                        </div>
                      </div>
                      {!ep.has_file && (
                        <button
                          onClick={() => handleSearchEpisode(selectedShow.title, season.season_number, ep.episode)}
                          className="px-3 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white text-xs font-medium transition-colors flex-shrink-0"
                        >
                          Hledat
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            );
          })}
        </div>
      ) : (
        /* ═══ SHOWS GRID ═══ */
        shows.length === 0 ? (
          <div className="text-center py-12 text-zinc-500">
            Zadne serialy. Klikni &quot;Skenovat&quot; pro nacteni knihovny.
          </div>
        ) : (
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {shows.map((show) => {
              const progress = show.total_episodes > 0
                ? Math.round((show.owned_episodes / show.total_episodes) * 100)
                : 0;
              return (
                <button
                  key={show.tmdb_id}
                  onClick={() => handleShowClick(show)}
                  className="group rounded-lg overflow-hidden bg-zinc-900 border border-zinc-800 hover:border-violet-500 transition-colors text-left"
                >
                  <div className="aspect-[2/3] relative bg-zinc-800">
                    {show.poster_url ? (
                      <Image
                        src={show.poster_url}
                        alt={show.title}
                        fill
                        sizes="(max-width: 640px) 33vw, 12.5vw"
                        className="object-cover group-hover:opacity-80 transition-opacity"
                      />
                    ) : (
                      <div className="flex items-center justify-center h-full text-zinc-600 text-xs">
                        Bez plakatu
                      </div>
                    )}
                  </div>
                  <div className="p-2">
                    <p className="text-sm font-medium text-zinc-100 truncate">{show.title}</p>
                    <div className="flex items-center gap-2 text-xs text-zinc-500 mt-0.5">
                      {show.year && <span>{show.year}</span>}
                      <span>{show.owned_episodes}/{show.total_episodes}</span>
                    </div>
                    <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden mt-1.5">
                      <div
                        className="h-full bg-violet-600 rounded-full"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )
      )}

      {showLoading && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="text-zinc-300 animate-pulse">Nacitam serial...</div>
        </div>
      )}

      {/* Fix Match Modal */}
      {fixingMovie && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setFixingMovie(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-2xl w-full mx-4 space-y-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-zinc-100">
              {fixingMovie.status === "review" ? "Na kontrolu" : fixingMovie.status === "unmatched" ? "Nespárováno" : "Film v knihovně"}
            </h3>
            <div className="text-sm space-y-1">
              <p className="text-zinc-300 break-all">{fixingMovie.file_path || fixingMovie.filename}</p>
              <p className="text-zinc-500">
                {[
                  formatDuration(fixingMovie.duration_s),
                  fixingMovie.media?.width ? `${fixingMovie.media.width}×${fixingMovie.media.height}` : "",
                  fixingMovie.media?.video_codec,
                  fixingMovie.media?.hdr && fixingMovie.media.hdr !== "SDR" ? fixingMovie.media.hdr : "",
                  formatSize(fixingMovie.file_size),
                ].filter(Boolean).join(" · ")}
              </p>
              {(fixingMovie.media?.audio?.length ?? 0) > 0 && (
                <p className="text-zinc-500">
                  Zvuk: {fixingMovie.media.audio!.map((a) => `${(a.lang || "?").toUpperCase()} ${a.codec} ${a.channels}ch`).join(", ")}
                </p>
              )}
              {(fixingMovie.media?.subtitles?.length ?? 0) > 0 && (
                <p className="text-zinc-500">Titulky: {fixingMovie.media.subtitles!.map((l) => l.toUpperCase()).join(", ")}</p>
              )}
              <p className="text-zinc-400 flex items-center gap-2">
                Kvalita: <ScoreBadge score={fixingMovie.quality_score} tip={fixingMovie.quality_parts} />
                <span>{fixingMovie.quality_summary}</span>
                {fixingMovie.tmdb_id && (fixingMovie.status === "matched" || fixingMovie.status === "manual") ? (
                  <button onClick={() => findBetterVersion(fixingMovie)}
                    className="ml-auto rounded bg-violet-600 px-3 py-1 text-xs font-medium text-white hover:bg-violet-500">
                    Hledat lepší verzi
                  </button>
                ) : null}
              </p>
              {fixingMovie.tmdb_id ? (
                <p className="text-zinc-500">
                  Aktuálně: <span className="text-zinc-200">{fixingMovie.title} ({fixingMovie.year})</span> · TMDB {fixingMovie.tmdb_id} · skóre {fixingMovie.confidence}
                </p>
              ) : null}
            </div>

            {fixingMovie.candidates?.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs uppercase tracking-wide text-zinc-500">Kandidáti</p>
                {fixingMovie.candidates.map((c) => (
                  <button
                    key={c.tmdb_id}
                    onClick={async () => {
                      await fixMovieMatch(fixingMovie.id, c.tmdb_id);
                      setFixingMovie(null);
                      loadData();
                    }}
                    className={`flex items-start gap-3 w-full p-2 rounded-lg text-left transition-colors hover:bg-zinc-800 ${
                      c.tmdb_id === fixingMovie.tmdb_id ? "bg-zinc-800/60 ring-1 ring-violet-600/50" : ""
                    }`}
                  >
                    {c.poster_url ? (
                      <Image src={c.poster_url} alt="" width={40} height={60} className="rounded flex-shrink-0" />
                    ) : (
                      <div className="w-10 h-[60px] bg-zinc-700 rounded flex-shrink-0" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="text-sm text-zinc-100 truncate">
                          {c.title} <span className="text-zinc-500">({c.year ?? "?"})</span>
                        </p>
                        <span className={`text-xs font-mono flex-shrink-0 ${c.score >= 60 ? "text-green-400" : c.score >= 30 ? "text-orange-400" : "text-zinc-500"}`}>
                          {c.score}
                        </span>
                      </div>
                      <p className="text-xs text-zinc-500">
                        {c.original_title !== c.title && <span>{c.original_title} · </span>}
                        {c.runtime ? `${c.runtime} min · ` : ""}TMDB {c.tmdb_id}
                      </p>
                      <p className="text-[11px] text-zinc-400 mt-0.5">{c.reasons.join(" · ")}</p>
                    </div>
                  </button>
                ))}
              </div>
            )}

            {(fixingMovie.status === "matched" || fixingMovie.status === "manual") && (
              <div className="space-y-2 rounded-lg border border-zinc-800 p-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs uppercase tracking-wide text-zinc-500">Oprava na disku</p>
                  {!moviePlan && (
                    <button onClick={() => loadMoviePlan(fixingMovie.id)} disabled={planBusy}
                      className="text-xs px-3 py-1 rounded border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-50">
                      {planBusy ? "…" : "Zobrazit změny"}
                    </button>
                  )}
                </div>
                {planError && <p className="text-xs text-red-400">{planError}</p>}
                {moviePlan && (moviePlan.ops.length === 0 ? (
                  <p className="text-xs text-green-400">Název složky i souborů už odpovídá pravidlům.</p>
                ) : (
                  <>
                    <PlanOps plan={moviePlan} />
                    {moviePlan.conflicts.map((c) => <p key={c} className="text-xs text-red-400">{c}</p>)}
                    <button onClick={() => runOrganize([fixingMovie.id])} disabled={planBusy || moviePlan.conflicts.length > 0}
                      className="px-3 py-1.5 rounded bg-violet-600 hover:bg-violet-500 disabled:bg-zinc-700 text-white text-xs font-medium">
                      {planBusy ? "Opravuji…" : "Opravit na disku"}
                    </button>
                  </>
                ))}
                {organizeResult && <OrganizeResultView result={organizeResult} onUndo={runUndo} busy={planBusy} />}
              </div>
            )}

            <p className="text-xs uppercase tracking-wide text-zinc-500">Hledat na TMDB</p>

            <div className="flex gap-2">
              <input
                type="text"
                value={fixQuery}
                onChange={(e) => setFixQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && fixQuery.trim()) {
                    setFixSearching(true);
                    searchTMDBForFix(fixingMovie.id, fixQuery).then(setFixResults).finally(() => setFixSearching(false));
                  }
                }}
                placeholder="Hledej na TMDB..."
                className="flex-1 rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 focus:border-violet-500 outline-none"
              />
              <button
                onClick={() => {
                  setFixSearching(true);
                  searchTMDBForFix(fixingMovie.id, fixQuery).then(setFixResults).finally(() => setFixSearching(false));
                }}
                disabled={fixSearching}
                className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium hover:bg-violet-500 disabled:opacity-50"
              >
                {fixSearching ? "..." : "Hledat"}
              </button>
            </div>

            {fixResults.length > 0 && (
              <div className="max-h-64 overflow-y-auto space-y-1">
                {fixResults.map((r) => (
                  <button
                    key={r.tmdb_id}
                    onClick={async () => {
                      await fixMovieMatch(fixingMovie.id, r.tmdb_id);
                      setFixingMovie(null);
                      loadData();
                    }}
                    className="flex items-center gap-3 w-full p-2 rounded-lg hover:bg-zinc-800 transition-colors text-left"
                  >
                    {r.poster_url ? (
                      <Image src={r.poster_url} alt="" width={32} height={48} className="rounded" />
                    ) : (
                      <div className="w-8 h-12 bg-zinc-700 rounded" />
                    )}
                    <div>
                      <p className="text-sm text-zinc-100">{r.title}</p>
                      <p className="text-xs text-zinc-500">{r.year} · TMDB {r.tmdb_id}</p>
                    </div>
                  </button>
                ))}
              </div>
            )}

            <button onClick={() => setFixingMovie(null)} className="text-sm text-zinc-500 hover:text-zinc-300">
              Zavrit
            </button>
          </div>
        </div>
      )}

      {/* Versions of one movie */}
      {versionsOf && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setVersionsOf(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-2xl w-full mx-4 space-y-3 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex gap-4">
              {versionsOf.main.poster_url && (
                <Image src={versionsOf.main.poster_url} alt="" width={64} height={96} className="rounded flex-shrink-0" />
              )}
              <div>
                <h3 className="text-lg font-semibold text-zinc-100">
                  {versionsOf.main.title} <span className="text-zinc-500 font-normal">({versionsOf.main.year})</span>
                </h3>
                <p className="text-sm text-zinc-400">{versionsOf.versions.length} verze v knihovně</p>
              </div>
            </div>
            {versionsOf.versions.map((v) => (
              <button key={v.id} onClick={() => { setVersionsOf(null); openMovie(v); }}
                className="w-full text-left rounded-lg border border-zinc-800 hover:border-violet-600 p-3 transition-colors">
                <div className="flex flex-wrap items-center gap-2">
                  <ScoreBadge score={v.quality_score} tip={v.quality_parts} />
                  <span className="text-xs text-zinc-300">{v.quality_summary}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${QUALITY_COLORS[v.quality] || QUALITY_COLORS.unknown}`}>{v.quality}</span>
                  {v.media?.video_codec && <span className="text-xs text-zinc-400">{v.media.video_codec}</span>}
                  {v.media?.hdr && v.media.hdr !== "SDR" && <span className="text-xs text-yellow-300">{v.media.hdr}</span>}
                  {v.language && <span className="text-xs text-zinc-300">{v.language.replaceAll(",", "+")}</span>}
                  <span className="text-xs text-zinc-500">{formatSize(v.file_size)}</span>
                  {v.duration_s > 0 && <span className="text-xs text-zinc-500">{formatDuration(v.duration_s)}</span>}
                </div>
                <p className="text-[11px] text-zinc-500 mt-1 break-all">{v.filename}</p>
              </button>
            ))}
            <button onClick={() => setVersionsOf(null)} className="text-sm text-zinc-500 hover:text-zinc-300">Zavřít</button>
          </div>
        </div>
      )}

      {/* Bulk fix names on disk */}
      {bulkOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setBulkOpen(false)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-4xl w-full mx-4 space-y-4 max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-zinc-100">Opravit názvy na disku</h3>
              <button onClick={() => setBulkOpen(false)} className="text-sm text-zinc-500 hover:text-zinc-300">Zavřít</button>
            </div>
            <p className="text-xs text-zinc-500">
              Jen spárované filmy (ne „na kontrolu“). Nic se nepřepisuje, každou dávku lze vrátit.
            </p>
            {planError && <p className="text-sm text-red-400">{planError}</p>}
            {organizeResult && <OrganizeResultView result={organizeResult} onUndo={runUndo} busy={planBusy} />}
            {bulkPlans === null && !organizeResult ? (
              <p className="text-zinc-500 animate-pulse text-sm">Počítám změny…</p>
            ) : bulkPlans && bulkPlans.length === 0 ? (
              <p className="text-green-400 text-sm">Všechny spárované filmy už odpovídají pravidlům.</p>
            ) : bulkPlans ? (
              <>
                <div className="flex items-center gap-3 text-sm text-zinc-400">
                  <span>{bulkPlans.length} filmů ke změně · vybráno {bulkSelected.size}</span>
                  <button className="text-violet-400 hover:text-violet-300" onClick={() => setBulkSelected(new Set(bulkPlans.filter((p) => !p.conflicts.length).map((p) => p.movie_ids[0])))}>vše</button>
                  <button className="text-violet-400 hover:text-violet-300" onClick={() => setBulkSelected(new Set())}>nic</button>
                </div>
                <div className="overflow-y-auto space-y-2 pr-1">
                  {bulkPlans.map((plan) => {
                    const id = plan.movie_ids[0];
                    return (
                      <label key={id} className={`block rounded-lg border p-3 cursor-pointer ${bulkSelected.has(id) ? "border-violet-700 bg-violet-950/20" : "border-zinc-800"}`}>
                        <div className="flex items-center gap-2">
                          <input type="checkbox" disabled={plan.conflicts.length > 0} checked={bulkSelected.has(id)}
                            onChange={(e) => {
                              const next = new Set(bulkSelected);
                              if (e.target.checked) next.add(id); else next.delete(id);
                              setBulkSelected(next);
                            }} />
                          <span className="text-sm text-zinc-100">{plan.title} ({plan.year ?? "?"})</span>
                          <span className="text-xs text-zinc-500">{plan.ops.length} změn</span>
                        </div>
                        <PlanOps plan={plan} />
                        {plan.conflicts.map((c) => <p key={c} className="text-xs text-red-400 mt-1">{c}</p>)}
                      </label>
                    );
                  })}
                </div>
                <div className="flex justify-end">
                  <button onClick={() => runOrganize(Array.from(bulkSelected))} disabled={planBusy || bulkSelected.size === 0}
                    className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-zinc-700 text-white text-sm font-medium">
                    {planBusy ? "Opravuji…" : `Opravit ${bulkSelected.size} filmů`}
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}

      <DownloadPanel />
    </main>
  );
}

function PlanOps({ plan }: { plan: OrganizePlan }) {
  const videos = plan.ops.filter((op) => op.kind === "video");
  const rest = plan.ops.length - videos.length;
  return (
    <div className="mt-1 space-y-1 text-[11px] font-mono">
      {plan.folder !== plan.target_folder && (
        <p className="text-zinc-400 break-all">
          📁 <span className="text-red-300/80 line-through">{plan.folder}</span> → <span className="text-green-300">{plan.target_folder}</span>
        </p>
      )}
      {videos.map((op) => (
        <p key={op.src} className="text-zinc-400 break-all">
          🎞 <span className="text-red-300/80">{op.src.split("/").pop()}</span> → <span className="text-green-300">{op.dst.split("/").pop()}</span>
        </p>
      ))}
      {rest > 0 && <p className="text-zinc-500">+ {rest} dalších souborů (titulky, NFO, …) se přesune spolu</p>}
    </div>
  );
}

function OrganizeResultView({ result, onUndo, busy }: { result: OrganizeResult; onUndo: (batchId: string) => void; busy: boolean }) {
  return (
    <div className="rounded-lg bg-zinc-950 border border-zinc-800 p-3 text-sm space-y-1">
      {result.done.length > 0 && <p className="text-green-400">Opraveno: {result.done.map((d) => d.title).join(", ")}</p>}
      {result.failed.map((f) => <p key={f.movie_id} className="text-red-400">Chyba: {f.error}</p>)}
      {result.batch_id && (
        <button onClick={() => onUndo(result.batch_id!)} disabled={busy}
          className="text-xs px-3 py-1 rounded border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-50">
          Vrátit tuto změnu
        </button>
      )}
    </div>
  );
}

function ScoreBadge({ score, tip }: { score: number; tip?: [string, number][] }) {
  const cls = score >= 80 ? "bg-green-900/70 text-green-300"
    : score >= 60 ? "bg-lime-900/60 text-lime-300"
    : score >= 40 ? "bg-yellow-900/60 text-yellow-300"
    : "bg-red-900/50 text-red-300";
  return (
    <span title={(tip ?? []).map(([l, p], i) => `${l} ${i && p >= 0 ? "+" : ""}${p}`).join(" · ")}
      className={`inline-block min-w-[2rem] text-center rounded px-1.5 py-0.5 text-xs font-bold font-mono ${cls}`}>
      {score}
    </span>
  );
}
