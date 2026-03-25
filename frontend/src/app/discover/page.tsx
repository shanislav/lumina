"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { TMDBMovie, getTrending, getRecentlyDigital, getRecentlyDigitalTV } from "@/lib/api";
import DownloadPanel from "@/components/DownloadPanel";
import MovieDetailModal from "@/components/MovieDetailModal";

interface Section {
  title: string;
  fetcher: () => Promise<TMDBMovie[]>;
}

const FILM_SECTIONS: Section[] = [
  { title: "Nedavno online", fetcher: getRecentlyDigital },
  { title: "Trending tento tyden", fetcher: getTrending },
];

const TV_SECTIONS: Section[] = [
  { title: "Nedavno online", fetcher: getRecentlyDigitalTV },
];

type Tab = "filmy" | "serialy";

export default function DiscoverPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("filmy");
  const [filmData, setFilmData] = useState<Record<string, TMDBMovie[]>>({});
  const [tvData, setTvData] = useState<Record<string, TMDBMovie[]>>({});
  const [filmLoading, setFilmLoading] = useState(true);
  const [tvLoading, setTvLoading] = useState(true);
  const [selectedMovie, setSelectedMovie] = useState<TMDBMovie | null>(null);

  useEffect(() => {
    Promise.all(
      FILM_SECTIONS.map(async (s) => {
        try {
          const movies = await s.fetcher();
          return [s.title, movies] as const;
        } catch {
          return [s.title, [] as TMDBMovie[]] as const;
        }
      })
    ).then((results) => {
      const map: Record<string, TMDBMovie[]> = {};
      for (const [title, movies] of results) map[title] = movies;
      setFilmData(map);
      setFilmLoading(false);
    });

    Promise.all(
      TV_SECTIONS.map(async (s) => {
        try {
          const shows = await s.fetcher();
          return [s.title, shows] as const;
        } catch {
          return [s.title, [] as TMDBMovie[]] as const;
        }
      })
    ).then((results) => {
      const map: Record<string, TMDBMovie[]> = {};
      for (const [title, shows] of results) map[title] = shows;
      setTvData(map);
      setTvLoading(false);
    });
  }, []);

  function handleSearch(movie: TMDBMovie) {
    const movieData = btoa(encodeURIComponent(JSON.stringify(movie)));
    router.push(`/?movie=${movieData}`);
  }

  const sections = tab === "filmy" ? FILM_SECTIONS : TV_SECTIONS;
  const data = tab === "filmy" ? filmData : tvData;
  const loading = tab === "filmy" ? filmLoading : tvLoading;

  return (
    <main className="flex flex-col gap-8 px-4 py-8 max-w-7xl mx-auto">
      <div className="flex items-center gap-4">
        <Link
          href="/"
          className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm"
        >
          &larr; Hledat
        </Link>
        <h1 className="text-2xl font-bold text-zinc-100">Objevit</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-zinc-900 rounded-lg p-1 w-fit border border-zinc-800">
        <button
          onClick={() => setTab("filmy")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
            tab === "filmy"
              ? "bg-violet-600 text-white shadow"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Filmy
        </button>
        <button
          onClick={() => setTab("serialy")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
            tab === "serialy"
              ? "bg-violet-600 text-white shadow"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Serialy
        </button>
      </div>

      {loading ? (
        <div className="text-zinc-500 animate-pulse text-center py-12">
          Nacitam...
        </div>
      ) : (
        sections.map((section) => {
          const items = data[section.title] || [];
          if (items.length === 0) return null;
          return (
            <section key={`${tab}-${section.title}`}>
              <h2 className="text-lg font-semibold text-zinc-200 mb-4">
                {section.title}
              </h2>
              <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-3">
                {items.map((movie) => (
                  <button
                    key={movie.tmdb_id}
                    onClick={() => setSelectedMovie(movie)}
                    className="group rounded-lg overflow-hidden bg-zinc-900 border border-zinc-800 hover:border-violet-500 transition-colors text-left"
                  >
                    <div className="aspect-[2/3] relative bg-zinc-800">
                      {movie.poster_url ? (
                        <Image
                          src={movie.poster_url}
                          alt={movie.title}
                          fill
                          sizes="(max-width: 640px) 33vw, (max-width: 768px) 25vw, (max-width: 1024px) 20vw, 12.5vw"
                          className="object-cover group-hover:opacity-80 transition-opacity"
                        />
                      ) : (
                        <div className="flex items-center justify-center h-full text-zinc-600 text-xs">
                          Bez plakatu
                        </div>
                      )}
                    </div>
                    <div className="p-2">
                      <p className="text-sm font-medium text-zinc-100 truncate">
                        {movie.title}
                      </p>
                      {movie.year && (
                        <p className="text-xs text-zinc-500">{movie.year}</p>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </section>
          );
        })
      )}

      {/* Detail modal */}
      {selectedMovie && (
        <MovieDetailModal
          movie={selectedMovie}
          onClose={() => setSelectedMovie(null)}
          onSearch={handleSearch}
        />
      )}

      <DownloadPanel />
    </main>
  );
}
