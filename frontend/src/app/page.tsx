"use client";

import { Suspense, useState, useEffect, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import SearchBar from "@/components/SearchBar";
import MovieGrid from "@/components/MovieGrid";
import FileTable from "@/components/FileTable";
import DownloadPanel from "@/components/DownloadPanel";
import {
  TMDBMovie,
  ScoredFile,
  MovieContext,
  searchMovies,
  searchFiles,
  getSetupStatus,
  getOwned,
  OwnedVersion,
  versionLabel,
  formatSize,
} from "@/lib/api";

export default function Home() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center min-h-[calc(100vh-57px)]"><div className="text-zinc-600 text-sm animate-pulse">Nacitam...</div></div>}>
      <HomeContent />
    </Suspense>
  );
}

function HomeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [ready, setReady] = useState(false);
  const [movies, setMovies] = useState<TMDBMovie[]>([]);
  const [files, setFiles] = useState<ScoredFile[]>([]);
  const [selectedMovie, setSelectedMovie] = useState<TMDBMovie | null>(null);
  const [moviesLoading, setMoviesLoading] = useState(false);
  const [filesLoading, setFilesLoading] = useState(false);
  const [resultsCollapsed, setResultsCollapsed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [owned, setOwned] = useState<Record<string, OwnedVersion[]>>({});
  const [movieCtx, setMovieCtx] = useState<MovieContext | null>(null);
  const [preferLocal, setPreferLocal] = useState(true);

  function showFiles(res: { files: ScoredFile[]; movie: MovieContext; prefer_local_audio: boolean }) {
    setFiles(res.files);
    setMovieCtx(res.movie);
    setPreferLocal(res.prefer_local_audio);
  }

  // "Already in the library?" for everything shown (search results + the selected movie)
  useEffect(() => {
    const ids = [...movies.map((m) => m.tmdb_id), selectedMovie?.tmdb_id ?? 0];
    getOwned(ids.filter((id, i) => id && ids.indexOf(id) === i)).then(setOwned).catch(() => {});
  }, [movies, selectedMovie]);
  const selectedOwned = selectedMovie && selectedMovie.media_type !== "tv" ? owned[String(selectedMovie.tmdb_id)] ?? [] : [];

  useEffect(() => {
    getSetupStatus()
      .then((status) => {
        if (!status.complete) {
          router.replace("/setup");
        } else {
          setReady(true);
        }
      })
      .catch(() => setReady(true)); // if API is down, show the page anyway
  }, [router]);

  const [searchLang, setSearchLang] = useState<string | undefined>(undefined);

  // Handle incoming movie from Discover page — go straight to file search
  const handleDiscoverMovie = useCallback(async (movie: TMDBMovie) => {
    setSelectedMovie(movie);
    setMovies([]);
    setFiles([]);
    setFilesLoading(true);
    setError(null);
    try {
      const query = movie.year
        ? `${movie.title} ${movie.year}`
        : movie.title;
      showFiles(await searchFiles(query, undefined, movie.original_title, movie.tmdb_id, movie.media_type));
    } catch (e) {
      setError(e instanceof Error ? e.message : "File search error");
    } finally {
      setFilesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!ready) return;

    const movieParam = searchParams.get("movie");
    if (movieParam) {
      try {
        const movie: TMDBMovie = JSON.parse(decodeURIComponent(atob(movieParam)));
        handleDiscoverMovie(movie);
      } catch { /* ignore bad data */ }
      window.history.replaceState({}, "", "/");
      return;
    }

    // Direct file search (e.g. from Library "Hledat" for episodes)
    const qParam = searchParams.get("q");
    const directSearch = searchParams.get("filesearch");
    if (qParam && directSearch) {
      const origTitle = searchParams.get("original_title") || undefined;
      const tmdbId = searchParams.get("tmdb_id") ? parseInt(searchParams.get("tmdb_id")!) : undefined;
      const mediaType = searchParams.get("media_type") || "tv";
      setSelectedMovie({ tmdb_id: tmdbId || 0, title: qParam, original_title: origTitle || "", year: "", overview: "", poster_url: null, media_type: mediaType as "movie" | "tv" });
      setFiles([]);
      setFilesLoading(true);
      setError(null);
      searchFiles(qParam, undefined, origTitle, tmdbId, mediaType)
        .then(showFiles)
        .catch((e) => setError(e instanceof Error ? e.message : "Search error"))
        .finally(() => setFilesLoading(false));
      window.history.replaceState({}, "", "/");
    }
  }, [searchParams, ready, handleDiscoverMovie]);

  async function handleSearch(query: string, language?: string) {
    setError(null);
    setMovies([]);
    setFiles([]);
    setSelectedMovie(null);
    setResultsCollapsed(false);
    setMoviesLoading(true);
    setSearchLang(language);
    try {
      const results = await searchMovies(query, language);
      setMovies(results);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search error");
    } finally {
      setMoviesLoading(false);
    }
  }

  async function handleSelectMovie(movie: TMDBMovie) {
    setSelectedMovie(movie);
    setFiles([]);
    setFilesLoading(true);
    setError(null);
    try {
      const query = movie.year
        ? `${movie.title} ${movie.year}`
        : movie.title;
      showFiles(await searchFiles(query, searchLang, movie.original_title, movie.tmdb_id, movie.media_type));
    } catch (e) {
      setError(e instanceof Error ? e.message : "File search error");
    } finally {
      setFilesLoading(false);
    }
  }

  if (!ready) {
    return (
      <main className="flex items-center justify-center min-h-[calc(100vh-57px)]">
        <div className="text-zinc-600 text-sm animate-pulse">Nacitam...</div>
      </main>
    );
  }

  return (
    <main className="flex flex-col items-center gap-8 px-4 py-12 max-w-7xl mx-auto">
      <div className="text-center space-y-3 flex flex-col items-center">
        <img src="/logo.svg" alt="Lumina" className="w-20 h-20 drop-shadow-[0_0_24px_rgba(167,139,250,0.4)]" />
        <h1 className="text-4xl font-bold tracking-tight bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
          Lumina
        </h1>
        <p className="text-zinc-500 text-sm">
          DDL a torrent obsah &mdash; vyhledej, vyber, stáhni
        </p>
      </div>

      <SearchBar onSearch={handleSearch} loading={moviesLoading} />

      {error && (
        <div className="rounded-lg bg-red-900/30 border border-red-800 px-4 py-3 text-red-300 text-sm w-full max-w-2xl">
          {error}
        </div>
      )}

      {!selectedMovie && (
        <MovieGrid movies={movies} onSelect={handleSelectMovie} owned={owned} />
      )}

      {selectedMovie && (
        <div className="w-full space-y-4">
          <div className="flex items-center gap-4">
            <button
              onClick={() => {
                setSelectedMovie(null);
                setFiles([]);
                setResultsCollapsed(false);
              }}
              className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm"
            >
              &larr; Zpět na výsledky
            </button>
            <h2 className="text-xl font-semibold text-zinc-100">
              {selectedMovie.title}
              {selectedMovie.year && (
                <span className="text-zinc-500 font-normal ml-2">
                  ({selectedMovie.year})
                </span>
              )}
            </h2>
            {resultsCollapsed && files.length > 0 && (
              <button
                onClick={() => setResultsCollapsed(false)}
                className="ml-auto flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 transition-colors text-sm"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
                Zobrazit výsledky ({files.length})
              </button>
            )}
          </div>
          {selectedOwned.length > 0 && (
            <div className="rounded-lg border border-emerald-900 bg-emerald-950/30 px-4 py-3 text-sm">
              <p className="text-emerald-300 font-medium">
                ✓ Už v knihovně — {selectedOwned.length === 1 ? "1 verze" : `${selectedOwned.length} verze`}
              </p>
              {selectedOwned.map((v) => (
                <p key={v.id} className="text-emerald-200/80 text-xs mt-1">
                  {versionLabel(v)} · {formatSize(v.file_size)}{v.duration_s ? ` · ${Math.round(v.duration_s / 60)} min` : ""}
                  <span className="text-zinc-500 ml-2">{v.filename}</span>
                </p>
              ))}
            </div>
          )}
          {!resultsCollapsed && (
            <FileTable
            owned={selectedOwned}
            movie={movieCtx}
            preferLocalAudio={preferLocal}
            tmdb_id={selectedMovie?.tmdb_id}
            title={selectedMovie?.title}
            year={parseInt(selectedMovie?.year || "0")}
            mediaType={selectedMovie?.media_type || "movie"}
              files={files}
              loading={filesLoading}
              onDownloadStarted={() => setResultsCollapsed(true)}
            />
          )}
        </div>
      )}

      <DownloadPanel />
    </main>
  );
}
