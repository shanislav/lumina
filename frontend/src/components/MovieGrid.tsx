"use client";

import Image from "next/image";
import { TMDBMovie, OwnedVersion, versionLabel } from "@/lib/api";

interface Props {
  movies: TMDBMovie[];
  onSelect: (movie: TMDBMovie) => void;
  owned?: Record<string, OwnedVersion[]>;
}

export default function MovieGrid({ movies, onSelect, owned = {} }: Props) {
  if (movies.length === 0) return null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4 w-full">
      {movies.map((movie) => (
        <button
          key={movie.tmdb_id}
          onClick={() => onSelect(movie)}
          className="group rounded-lg overflow-hidden bg-zinc-900 border border-zinc-800 hover:border-violet-500 transition-colors text-left"
        >
          <div className="aspect-[2/3] relative bg-zinc-800">
            {movie.poster_url ? (
              <Image
                src={movie.poster_url}
                alt={movie.title}
                fill
                sizes="(max-width: 640px) 50vw, (max-width: 1024px) 25vw, 16vw"
                className="object-cover group-hover:opacity-80 transition-opacity"
              />
            ) : (
              <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                Bez plakátu
              </div>
            )}
            <span className={`absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold text-white uppercase tracking-wide ${
              movie.media_type === "tv" ? "bg-violet-600" : "bg-blue-600"
            }`}>
              {movie.media_type === "tv" ? "TV" : "Film"}
            </span>
            {movie.media_type !== "tv" && owned[String(movie.tmdb_id)] && (
              <span
                title={owned[String(movie.tmdb_id)].map(versionLabel).join("\n")}
                className="absolute bottom-0 inset-x-0 bg-emerald-900/90 text-emerald-200 text-[10px] font-medium px-2 py-1 truncate"
              >
                ✓ V knihovně · {owned[String(movie.tmdb_id)].map((v) => v.quality).join(" + ")}
              </span>
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
  );
}
