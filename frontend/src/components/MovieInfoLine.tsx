"use client";

import { useEffect, useState } from "react";
import { FriendCopy, MovieInfo, TMDBMovie, getFriendCopies, getMovieInfo } from "@/lib/api";

const res = (r: string) => (!r ? "" : /^\d+$/.test(r) ? `${r}p` : r.toUpperCase());

/** "Milan has it too" — like the note about your own library, just information. */
export function FriendCopies({ movie }: { movie: TMDBMovie }) {
  const [friends, setFriends] = useState<FriendCopy[]>([]);
  useEffect(() => {
    setFriends([]);
    if (movie.media_type !== "tv" && movie.tmdb_id) getFriendCopies(movie.tmdb_id).then(setFriends).catch(() => {});
  }, [movie]);
  if (!friends.length) return null;
  return (
    <p className="text-xs text-zinc-500">
      👥 Má i{" "}
      {friends.map((f, k) => (
        <span key={f.url} title={`server ${f.server}${f.online ? "" : ` — teď nedostupný (naposledy ${f.seen_at ?? "?"})`}`}
          className={f.online ? "text-zinc-300" : "text-zinc-500"}>
          {k > 0 && ", "}{f.owner}
          <span className="text-zinc-500">
            {[res(f.resolution), f.audio.map((l) => l.toUpperCase()).join("+")].filter(Boolean).length > 0 &&
              ` (${[res(f.resolution), f.audio.map((l) => l.toUpperCase()).join("+")].filter(Boolean).join(" · ")}${f.online ? "" : ", offline"})`}
          </span>
        </span>
      ))}
    </p>
  );
}

/** Genres, length, rating, director, cast and links (ČSFD, IMDb) under a film's title. */
export default function MovieInfoLine({ movie }: { movie: TMDBMovie }) {
  const [info, setInfo] = useState<MovieInfo | null>(null);

  useEffect(() => {
    setInfo(null);
    if (movie.media_type === "tv") return;
    getMovieInfo(movie).then(setInfo).catch(() => setInfo(null));
  }, [movie]);

  if (!info) return null;
  const parts = [
    info.genres.join(", "),
    info.runtime ? `${info.runtime} min` : "",
    info.rating && info.votes >= 20 ? `★ ${info.rating.toFixed(1)}` : "",
    info.directors.length ? `režie ${info.directors.join(", ")}` : "",
    info.cast.length ? `hrají ${info.cast.join(", ")}` : "",
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
      {parts.map((p, i) => <span key={i}>{p}</span>)}
      {info.csfd_url && (
        <a href={info.csfd_url} target="_blank" rel="noreferrer"
          title={info.csfd_exact ? "Stránka filmu na ČSFD" : "Hledat na ČSFD"}
          className="rounded border border-red-900 px-1.5 py-0.5 text-red-300 hover:bg-red-950/40">
          ČSFD{info.csfd_exact ? "" : " 🔍"} ↗
        </a>
      )}
      {info.imdb_url && (
        <a href={info.imdb_url} target="_blank" rel="noreferrer"
          className="rounded border border-yellow-900 px-1.5 py-0.5 text-yellow-300 hover:bg-yellow-950/40">IMDb ↗</a>
      )}
    </div>
  );
}
