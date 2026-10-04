"use client";

import { WantedTag } from "@/components/WantedMark";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { TMDBMovie, getTrending, getRecentlyDigital, getRecentlyDigitalTV, getOwned, OwnedVersion, versionLabel } from "@/lib/api";

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

const NO_INDIAN_KEY = "lumina.discover.noIndian";
const INDIAN = new Set(["hi", "ta", "te", "ml", "kn", "bn", "mr", "pa", "gu", "or", "ur"]);

export default function DiscoverPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("filmy");
  const [filmData, setFilmData] = useState<Record<string, TMDBMovie[]>>({});
  const [tvData, setTvData] = useState<Record<string, TMDBMovie[]>>({});
  const [filmLoading, setFilmLoading] = useState(true);
  const [tvLoading, setTvLoading] = useState(true);
  const [owned, setOwned] = useState<Record<string, OwnedVersion[]>>({});
  // "Bez Bollywoodu": Indian films and shows (by their original language) left out — on unless switched off
  const [noIndian, setNoIndian] = useState(true);
  useEffect(() => { try { setNoIndian(localStorage.getItem(NO_INDIAN_KEY) !== "0"); } catch { /* private */ } }, []);
  const toggleIndian = () => setNoIndian((v) => {
    try { localStorage.setItem(NO_INDIAN_KEY, v ? "0" : "1"); } catch { /* private */ }
    return !v;
  });

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
      const ids = Object.values(map).flat().map((m) => m.tmdb_id);
      getOwned(ids.filter((id, i) => id && ids.indexOf(id) === i)).then(setOwned).catch(() => {});
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
    if (movie.media_type === "tv" && movie.tmdb_id) {
      router.push(`/series?tmdb=${movie.tmdb_id}`);
      return;
    }
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
      <button onClick={toggleIndian}
        className={`-mt-5 w-fit rounded-full border px-3 py-1 text-xs ${noIndian
          ? "border-violet-700 bg-violet-950/40 text-violet-200" : "border-zinc-700 text-zinc-500"}`}
        title="Indické filmy a seriály (hindština, tamilština, telugština …) podle původního jazyka">
        {noIndian ? "✓ Bez Bollywoodu" : "Bez Bollywoodu"}
      </button>

      {loading ? (
        <div className="text-zinc-500 animate-pulse text-center py-12">
          Nacitam...
        </div>
      ) : (
        sections.map((section) => {
          const items = (data[section.title] || []).filter((m) => !(noIndian && INDIAN.has(m.original_language || "")));
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
                    onClick={() => handleSearch(movie)}
                    title={movie.overview || movie.title}
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
                      {tab === "filmy" && owned[String(movie.tmdb_id)] && (
                        <span
                          title={owned[String(movie.tmdb_id)].map(versionLabel).join("\n")}
                          className="absolute bottom-1 left-1 right-1 rounded bg-emerald-900/90 px-1.5 py-0.5 text-[10px] font-medium text-emerald-200 truncate"
                        >
                          ✓ V knihovně · {owned[String(movie.tmdb_id)].map((v) => v.quality).join(" + ")}
                        </span>
                      )}
                      {movie.wanted && !(tab === "filmy" && owned[String(movie.tmdb_id)]) && (
                        <WantedTag w={movie.wanted} className="absolute bottom-1 left-1 right-1 rounded" />
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

    </main>
  );
}
