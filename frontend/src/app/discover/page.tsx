"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { TMDBMovie, getTrending, getRecentlyDigital } from "@/lib/api";
import DownloadPanel from "@/components/DownloadPanel";

interface Section {
  title: string;
  fetcher: () => Promise<TMDBMovie[]>;
}

const SECTIONS: Section[] = [
  { title: "Nedavno online", fetcher: getRecentlyDigital },
  { title: "Trending tento tyden", fetcher: getTrending },
];

export default function DiscoverPage() {
  const router = useRouter();
  const [data, setData] = useState<Record<string, TMDBMovie[]>>({} as Record<string, TMDBMovie[]>);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all(
      SECTIONS.map(async (s) => {
        try {
          const movies = await s.fetcher();
          return [s.title, movies] as const;
        } catch {
          return [s.title, [] as TMDBMovie[]] as const;
        }
      })
    ).then((results) => {
      const map: Record<string, TMDBMovie[]> = {};
      for (const [title, movies] of results) {
        map[title] = movies;
      }
      setData(map);
      setLoading(false);
    });
  }, []);

  function handleSelect(movie: TMDBMovie) {
    // Pass movie data so home page can skip TMDB search and go straight to file search
    const movieData = btoa(encodeURIComponent(JSON.stringify(movie)));
    router.push(`/?movie=${movieData}`);
  }

  return (
    <main className="flex flex-col gap-10 px-4 py-8 max-w-7xl mx-auto">
      <div className="flex items-center gap-4">
        <Link
          href="/"
          className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm"
        >
          &larr; Hledat
        </Link>
        <h1 className="text-2xl font-bold text-zinc-100">Objevit</h1>
      </div>

      {loading ? (
        <div className="text-zinc-500 animate-pulse text-center py-12">
          Nacitam filmy...
        </div>
      ) : (
        SECTIONS.map((section) => {
          const movies = data[section.title] || [];
          if (movies.length === 0) return null;
          return (
            <section key={section.title}>
              <h2 className="text-lg font-semibold text-zinc-200 mb-4">
                {section.title}
              </h2>
              <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-zinc-700">
                {movies.map((movie) => (
                  <button
                    key={movie.tmdb_id}
                    onClick={() => handleSelect(movie)}
                    className="group flex-shrink-0 w-36 rounded-lg overflow-hidden bg-zinc-900 border border-zinc-800 hover:border-violet-500 transition-colors text-left"
                  >
                    <div className="aspect-[2/3] relative bg-zinc-800">
                      {movie.poster_url ? (
                        <Image
                          src={movie.poster_url}
                          alt={movie.title}
                          fill
                          sizes="144px"
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

      <DownloadPanel />
    </main>
  );
}
