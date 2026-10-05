"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { SeriesDetail, getSeries, searchFiles } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import { langName } from "@/lib/mobile";
import Offers from "@/components/mobile/Offers";
import Subtitles from "@/components/mobile/Subtitles";
import { BigButton, Spinner } from "@/components/mobile/ui";

/** Phone: one episode — owned: its subtitles and "another version"; missing: its files right away. */
function Episode() {
  const p = useSearchParams();
  const router = useRouter();
  const { can } = useAuth();
  const tmdb = Number(p.get("tmdb") || 0);
  const season = Number(p.get("s") || 0);
  const episode = Number(p.get("e") || 0);
  const [data, setData] = useState<SeriesDetail | null>(null);
  const [other, setOther] = useState(false);
  useEffect(() => { getSeries(tmdb).then(setData).catch(() => {}); }, [tmdb]);

  if (!data) return <main className="px-4"><Spinner text="Načítám díl…" /></main>;
  const ep = data.seasons.find((s) => s.season_number === season)?.episodes.find((e) => e.episode === episode);
  const label = `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
  const owned = ep?.file;
  const offers = (
    <Offers key={`${tmdb}-${season}-${episode}`} tmdbId={tmdb} title={data.show.title} contentType="tv"
      libraryAction={{ mode: "episode", season, episode, replace: !!owned }}
      load={() => searchFiles(data.show.title, undefined, data.show.original_title, tmdb, "tv", null,
        { season, episode, torrent: data.settings.effective.torrent })} />
  );
  return (
    <main className="space-y-4 px-4 py-4">
      <button onClick={() => router.back()} className="py-1 text-base text-violet-300">‹ {data.show.title}</button>
      <div>
        <p className="text-sm text-zinc-500">{data.show.title} · {label}</p>
        <h1 className="text-xl font-semibold text-zinc-100">{ep?.name || `${episode}. díl`}</h1>
        {ep?.air_date && <p className="text-sm text-zinc-400">{new Date(ep.air_date).toLocaleDateString("cs-CZ")}{ep.runtime ? ` · ${ep.runtime} min` : ""}</p>}
      </div>
      {owned ? (
        <>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <p className="text-base text-emerald-300">V knihovně · {owned.quality} · {owned.languages.map(langName).join("+") || "zvuk ?"}</p>
            <p className="break-all text-xs text-zinc-500">{owned.filename}</p>
            {(owned.parts?.length ?? 0) > 1 && <p className="mt-1 text-sm text-violet-200">Dvojdíl — {owned.parts!.length} části (soubory „- pt1“, „- pt2“)</p>}
            {!!owned.shared?.length && (
              <p className="mt-1 text-sm text-zinc-300">
                Jeden soubor s {owned.shared.map(([s, n]) => `S${String(s).padStart(2, "0")}E${String(n).padStart(2, "0")}`).join(", ")}
              </p>
            )}
          </div>
          {owned.id && can("subtitles") && <Subtitles id={owned.id} kind="episode" />}
          {can("download") && (other ? offers : <BigButton kind="secondary" onClick={() => setOther(true)}>Najít jinou verzi (nahradí tuhle)</BigButton>)}
        </>
      ) : ep?.state === "upcoming" ? (
        <p className="text-zinc-400">Tenhle díl ještě nevyšel.</p>
      ) : can("download") ? offers : null}
    </main>
  );
}

export default function MobileEpisodePage() {
  return <Suspense><Episode /></Suspense>;
}
