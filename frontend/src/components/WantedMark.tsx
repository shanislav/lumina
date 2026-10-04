"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { TMDBMovie, WantedMark, getWantedOf } from "@/lib/api";

/** "Already on the wanted list" (backend search.mark_known / wanted/of): on posters, in lists, on a film's page —
 *  so nobody adds it again or downloads it by hand meanwhile. */

const STATUS: Record<string, string> = {
  wanted: "hledá se", found: "nalezeno, čeká na stažení", downloading: "stahuje se", auto: "automatika hledá díly",
};

const day = (at: string) => {
  const [y, m, d] = (at || "").slice(0, 10).split("-");
  return d ? `${Number(d)}. ${Number(m)}. ${y}` : "";
};

/** "přidal vanco 3. 10. 2026 · hledá se" */
export function wantedDetail(w: WantedMark): string {
  return [w.added_by && `přidal ${w.added_by}`, day(w.added_at), STATUS[w.status] ?? ""].filter(Boolean).join(" · ");
}

/** A poster's band (as "✓ V knihovně"). */
export function WantedTag({ w, className = "absolute bottom-0 inset-x-0" }: { w: WantedMark; className?: string }) {
  return (
    <span title={wantedDetail(w)}
      className={`${className} truncate bg-amber-800/95 px-2 py-1 text-[10px] font-semibold text-amber-50`}>
      ★ V Chci{w.added_by && ` · ${w.added_by}`}
    </span>
  );
}

/** The film's page: a clear note when it is on the list (it asks the server when the film came without the mark). */
export function WantedBanner({ movie, big = false }: { movie: TMDBMovie; big?: boolean }) {
  const [w, setW] = useState<WantedMark | null>(movie.wanted ?? null);
  useEffect(() => {
    setW(movie.wanted ?? null);
    if (movie.media_type === "tv" || (!movie.tmdb_id && !movie.wikidata_id)) return;
    getWantedOf(movie.tmdb_id, movie.wikidata_id).then(setW).catch(() => {});
  }, [movie.tmdb_id, movie.wikidata_id, movie.media_type, movie.wanted]);
  if (!w) return null;
  return (
    <Link href="/wanted"
      className={`inline-flex flex-wrap items-center gap-x-2 rounded-lg border border-amber-700/80 bg-amber-950/50 text-amber-100 hover:bg-amber-900/50 ${
        big ? "w-full px-4 py-3 text-base" : "px-3 py-1.5 text-sm"}`}>
      <span className="font-semibold">★ Už je v Chci</span>
      <span className={`text-amber-200/80 ${big ? "text-sm" : "text-xs"}`}>{wantedDetail(w)} ›</span>
    </Link>
  );
}
