"use client";

import { TMDBMovie } from "@/lib/api";
import Image from "next/image";
import { useEffect } from "react";
import WantButton from "@/components/WantButton";

interface Props {
  movie: TMDBMovie;
  onClose: () => void;
  onSearch: (movie: TMDBMovie) => void;
}

export default function MovieDetailModal({ movie, onClose, onSearch }: Props) {
  // Close on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative bg-zinc-900 border border-zinc-700 rounded-xl max-w-lg w-full mx-4 overflow-hidden shadow-2xl">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-10 text-zinc-400 hover:text-zinc-100 transition-colors bg-zinc-900/80 rounded-full p-1"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="flex gap-4 p-5">
          {/* Poster */}
          <div className="flex-shrink-0 w-32 aspect-[2/3] relative rounded-lg overflow-hidden bg-zinc-800">
            {movie.poster_url ? (
              <Image
                src={movie.poster_url}
                alt={movie.title}
                fill
                sizes="128px"
                className="object-cover"
              />
            ) : (
              <div className="flex items-center justify-center h-full text-zinc-600 text-xs">
                Bez plakatu
              </div>
            )}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0 flex flex-col gap-2">
            <h2 className="text-lg font-bold text-zinc-100 leading-tight">
              {movie.title}
            </h2>
            {movie.original_title && movie.original_title !== movie.title && (
              <p className="text-sm text-zinc-500">{movie.original_title}</p>
            )}
            {movie.year && (
              <span className="inline-flex items-center px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 text-xs w-fit">
                {movie.year}
              </span>
            )}
            {movie.overview && (
              <p className="text-sm text-zinc-400 line-clamp-5 mt-1">
                {movie.overview}
              </p>
            )}
          </div>
        </div>

        {/* Action */}
        <div className="px-5 pb-5">
          <button
            onClick={() => onSearch(movie)}
            className="w-full py-2.5 rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:from-violet-500 hover:to-fuchsia-500 text-white font-medium transition-all text-sm flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            Vyhledat soubory
          </button>
          <div className="mt-3 flex items-center justify-center gap-2 text-xs text-zinc-500">
            Ještě není ke stažení? <WantButton movie={movie} />
          </div>
        </div>
      </div>
    </div>
  );
}
