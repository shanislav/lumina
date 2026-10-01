"use client";

import { useEffect, useState } from "react";
import { FriendCopy, MovieInfo, TMDBMovie, getFriendCopies, getMovieInfo } from "@/lib/api";

/** Genres, length, rating, director, cast and links (ČSFD, IMDb) under a film's title. */
export default function MovieInfoLine({ movie }: { movie: TMDBMovie }) {
  const [info, setInfo] = useState<MovieInfo | null>(null);
  const [friends, setFriends] = useState<FriendCopy[]>([]);

  useEffect(() => {
    setInfo(null);
    setFriends([]);
    if (movie.media_type === "tv") return;
    getMovieInfo(movie).then(setInfo).catch(() => setInfo(null));
    if (movie.tmdb_id) getFriendCopies(movie.tmdb_id).then(setFriends).catch(() => {});
  }, [movie]);

  if (!info && !friends.length) return null;
  const parts = !info ? [] : [
    info.genres.join(", "),
    info.runtime ? `${info.runtime} min` : "",
    info.rating && info.votes >= 20 ? `★ ${info.rating.toFixed(1)}` : "",
    info.directors.length ? `režie ${info.directors.join(", ")}` : "",
    info.cast.length ? `hrají ${info.cast.join(", ")}` : "",
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
      {parts.map((p, i) => <span key={i}>{p}</span>)}
      {friends.map((f) => (
        <a key={f.url} href={f.url} target="_blank" rel="noreferrer"
          title={`${f.owner} má film na serveru „${f.server}“${f.online ? "" : ` — server teď nejede (naposledy ${f.seen_at ?? "?"})`}. Otevřít v Plexu`}
          className={`rounded border px-1.5 py-0.5 ${f.online ? "border-amber-700 text-amber-300 hover:bg-amber-950/40" : "border-zinc-700 text-zinc-500"}`}>
          👥 Má {f.owner}{f.resolution ? ` · ${/^\d+$/.test(f.resolution) ? f.resolution + "p" : f.resolution.toUpperCase()}` : ""}{f.online ? "" : " (offline)"} ↗
        </a>
      ))}
      {info?.csfd_url && (
        <a href={info.csfd_url} target="_blank" rel="noreferrer"
          title={info.csfd_exact ? "Stránka filmu na ČSFD" : "Hledat na ČSFD"}
          className="rounded border border-red-900 px-1.5 py-0.5 text-red-300 hover:bg-red-950/40">
          ČSFD{info.csfd_exact ? "" : " 🔍"} ↗
        </a>
      )}
      {info?.imdb_url && (
        <a href={info.imdb_url} target="_blank" rel="noreferrer"
          className="rounded border border-yellow-900 px-1.5 py-0.5 text-yellow-300 hover:bg-yellow-950/40">IMDb ↗</a>
      )}
    </div>
  );
}
