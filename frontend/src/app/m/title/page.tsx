"use client";

import { WantedBanner } from "@/components/WantedMark";
import { Suspense, useEffect, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { OwnedVersion, getOwned, searchFiles, versionLabel } from "@/lib/api";
import Offers from "@/components/mobile/Offers";
import WantButton from "@/components/WantButton";
import { useAuth } from "@/components/AuthGate";

/** Phone: a film — what is owned already, and its files (the recommended one big on top). */
function Title() {
  const p = useSearchParams();
  const router = useRouter();
  const { can } = useAuth();
  const tmdb = Number(p.get("tmdb") || 0);
  const title = p.get("title") || "";
  const year = p.get("year") || "";
  const orig = p.get("orig") || "";
  const poster = p.get("poster") || "";
  const wd = p.get("wd");
  const [owned, setOwned] = useState<OwnedVersion[]>([]);
  useEffect(() => { if (tmdb) getOwned([tmdb]).then((o) => setOwned(o[String(tmdb)] ?? [])).catch(() => {}); }, [tmdb]);

  return (
    <main className="space-y-4 px-4 py-4">
      <button onClick={() => router.back()} className="py-1 text-base text-violet-300">‹ Zpět</button>
      <div className="flex gap-3">
        <div className="relative h-28 w-[75px] flex-none overflow-hidden rounded-lg bg-zinc-800">
          {poster && <Image src={poster} alt="" fill sizes="75px" className="object-cover" unoptimized />}
        </div>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-zinc-100">{title}</h1>
          <p className="text-zinc-400">{[year, "film"].filter(Boolean).join(" · ")}</p>
          {owned.length > 0 && (
            <div className="mt-1 text-sm text-emerald-300">
              Už máš: {owned.map((v) => versionLabel(v)).join(" · ")}
            </div>
          )}
        </div>
      </div>
      <WantedBanner big movie={{ tmdb_id: tmdb, wikidata_id: wd, title, original_title: orig, year, overview: "", poster_url: poster || null }} />
      {/* "+ Chci" with a profile: Lumina looks for it (now or later) */}
      {can("download") && (tmdb || wd) ? (
        <WantButton movie={{ tmdb_id: tmdb, wikidata_id: wd, title, original_title: orig, year, overview: "",
          poster_url: poster || null }} />
      ) : null}
      {/* who may not download (a child's account) sees no offers — only "Chci" (the admin decides) */}
      {!can("download") ? (
        owned.length === 0 && (tmdb || wd) ? (
          <WantButton big movie={{ tmdb_id: tmdb, wikidata_id: wd, title, original_title: orig, year, overview: "",
            poster_url: poster || null }} />
        ) : null
      ) : <Offers key={`${tmdb}-${title}`} tmdbId={tmdb || undefined} title={title} year={Number(year) || undefined}
        contentType="movie" owned={owned} usePick
        load={() => searchFiles(year ? `${title} ${year}` : title, undefined, orig, tmdb || undefined, "movie", wd)} />}
    </main>
  );
}

export default function MobileTitlePage() {
  return <Suspense><Title /></Suspense>;
}
