"use client";

import { WantShow } from "@/components/SeriesAuto";
import WantButton from "@/components/WantButton";
import SeriesSources from "@/components/SeriesSources";
import { Suspense, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  SeasonOffers, SeriesDetail, SeriesEpisode, SeriesSeason, downloadSeason, getSeasonOffers, getSeries,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import { langName, size } from "@/lib/mobile";
import { BigButton, Sheet, Spinner, Tag } from "@/components/mobile/ui";

/** Phone: a TV show — its seasons, a tap on an episode opens it (download / subtitles), a season's missing
 *  episodes in one go. */
const se = (s: number, e: number) => `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
const STATE: Record<SeriesEpisode["state"], [string, string]> = {
  owned: ["mám", "text-emerald-300"], temp: ["čeká na dabing", "text-amber-300"], unknown: ["mám (zvuk ?)", "text-sky-300"],
  missing: ["chybí", "text-red-300"], upcoming: ["nevyšlo", "text-zinc-500"],
};

function episodeHref(show: SeriesDetail["show"], season: number, ep: SeriesEpisode): string {
  const q = new URLSearchParams({ tmdb: String(show.tmdb_id), s: String(season), e: String(ep.episode) });
  return `/m/episode?${q}`;
}

function Season({ data, season, open, toggle }: { data: SeriesDetail; season: SeriesSeason; open: boolean; toggle: () => void }) {
  const { can } = useAuth();
  const c = season.counts;
  const wanted = season.episodes.filter((e) => e.state === "missing" || e.state === "temp").length;
  const [plan, setPlan] = useState<SeasonOffers | null | "loading">(null);
  const [result, setResult] = useState("");
  const total = season.episodes.length;

  async function findPlan() {
    setPlan("loading");
    try { setPlan(await getSeasonOffers(data.show.tmdb_id, season.season_number)); } catch { setPlan(null); setResult("Hledání selhalo"); }
  }
  async function download(p: SeasonOffers) {
    try {
      const r = await downloadSeason(data.show.tmdb_id, season.season_number, p.plan.map((x) => ({ episode: x.episode, row: x.row })));
      setResult(`✓ Stahuje se ${r.started} dílů${r.errors.length ? ` · ${r.errors.length} chyb` : ""}`);
    } catch (e) { setResult(e instanceof Error ? e.message : "Chyba"); }
    setPlan(null);
  }

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60">
      <button onClick={toggle} className="flex min-h-14 w-full items-center justify-between gap-3 px-4 py-3 text-left">
        <span>
          <span className="block text-base font-medium text-zinc-100">{season.name || `${season.season_number}. série`}</span>
          <span className="text-sm text-zinc-400">
            mám {c.owned + c.temp + (c.unknown ?? 0)} z {total - c.upcoming}
            {c.missing > 0 && <span className="text-red-300"> · chybí {c.missing}</span>}
            {c.temp > 0 && <span className="text-amber-300"> · bez dabingu {c.temp}</span>}
          </span>
        </span>
        <span className="text-xl text-zinc-500">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-zinc-800 px-3 pb-3 pt-3">
          {can("download") && wanted > 0 && !result && (
            <BigButton onClick={findPlan} disabled={plan === "loading"}>
              {plan === "loading" ? "Hledám celou sérii…" : `Stáhnout chybějící (${wanted})`}
            </BigButton>
          )}
          {result && <p className="rounded-xl bg-emerald-950 px-4 py-3 text-emerald-200">{result}</p>}
          {season.episodes.map((e) => (
            <Link key={e.episode} href={episodeHref(data.show, season.season_number, e)}
              className="flex min-h-12 items-center gap-3 rounded-xl px-2 py-2 active:bg-zinc-800">
              <span className="w-10 flex-none font-mono text-sm text-zinc-500">E{String(e.episode).padStart(2, "0")}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-base text-zinc-100">{e.name || `${e.episode}. díl`}</span>
                <span className={`text-sm ${STATE[e.state][1]}`}>
                  {STATE[e.state][0]}{e.file ? ` · ${e.file.quality} ${e.file.languages.map(langName).join("+")}` : ""}
                  {(e.file?.parts?.length ?? 0) > 1 && " · dvojdíl"}
                  {!!e.file?.shared?.length && ` · v souboru s ${e.file.shared.map(([s, n]) => `S${String(s).padStart(2, "0")}E${String(n).padStart(2, "0")}`).join(", ")}`}
                </span>
              </span>
              <span className="text-xl text-zinc-600">›</span>
            </Link>
          ))}
        </div>
      )}
      <Sheet open={!!plan && plan !== "loading"} title={`${season.name || `${season.season_number}. série`}`} onClose={() => setPlan(null)}>
        {plan && plan !== "loading" && (
          plan.plan.length ? (
            <div className="space-y-3">
              <p className="text-base text-zinc-200">Nalezeno {plan.plan.length} z {plan.wanted.length} dílů · celkem {size(plan.plan.reduce((n, x) => n + x.row.size, 0))}</p>
              <div className="space-y-1.5">
                {plan.plan.map((x) => (
                  <div key={x.episode} className="flex items-center gap-2 text-sm">
                    <span className="w-10 font-mono text-zinc-500">E{String(x.episode).padStart(2, "0")}</span>
                    <span className="text-zinc-200">{x.row.resolution || "?"} · {size(x.row.size)}</span>
                    {(x.row.audio_langs ?? []).slice(0, 2).map((l) => <Tag key={l} tone={["cs", "sk"].includes(l) ? "good" : "plain"}>{langName(l)}</Tag>)}
                  </div>
                ))}
              </div>
              <BigButton onClick={() => download(plan)}>Stáhnout {plan.plan.length} dílů</BigButton>
            </div>
          ) : <p className="pb-4 text-zinc-400">Pro chybějící díly se nic nenašlo.</p>
        )}
      </Sheet>
    </div>
  );
}

function Series() {
  const p = useSearchParams();
  const router = useRouter();
  const { can } = useAuth();
  const tmdb = Number(p.get("tmdb") || 0);
  const [data, setData] = useState<SeriesDetail | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [more, setMore] = useState(false);
  const load = () => getSeries(tmdb).then((d) => {
    setData(d);
    // open the season where something is missing, the newest one first
    const target = [...d.seasons].reverse().find((s) => !("specials" in s) && (s.counts.missing || s.counts.temp));
    if (target) setOpen((o) => (Object.keys(o).length ? o : { [target.season_number]: true }));
  }).catch((e) => setError(e instanceof Error ? e.message : "Chyba"));
  useEffect(() => { if (tmdb) load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [tmdb]);

  if (error) return <main className="p-4 text-red-400">{error}</main>;
  if (!data) return <main className="px-4"><Spinner text="Načítám seriál…" /></main>;
  const { show, totals } = data;
  return (
    <main className="space-y-4 px-4 py-4">
      <button onClick={() => router.back()} className="py-1 text-base text-violet-300">‹ Zpět</button>
      <div className="flex gap-3">
        <div className="relative h-28 w-[75px] flex-none overflow-hidden rounded-lg bg-zinc-800">
          {show.poster_url && <Image src={show.poster_url} alt="" fill sizes="75px" className="object-cover" unoptimized />}
        </div>
        <div className="min-w-0 space-y-1">
          <h1 className="text-xl font-semibold text-zinc-100">{show.title}</h1>
          <p className="text-zinc-400">{[show.year, "seriál", `${data.seasons.filter((s) => s.season_number > 0).length} sérií`].filter(Boolean).join(" · ")}</p>
          <p className="text-sm">
            <span className="text-emerald-300">mám {totals.owned + totals.temp + (totals.unknown ?? 0)}</span>
            {totals.missing > 0 && <span className="text-red-300"> · chybí {totals.missing}</span>}
          </p>
          {show.next_episode && <p className="text-sm text-zinc-400">další díl {se(show.next_episode.season, show.next_episode.episode)} · {new Date(show.next_episode.air_date).toLocaleDateString("cs-CZ")}</p>}
        </div>
      </div>
      {show.overview && (
        <button onClick={() => setMore(!more)} className={`text-left text-sm text-zinc-400 ${more ? "" : "line-clamp-2"}`}>{show.overview}</button>
      )}
      {!data.in_library && can("library.edit") && data.settings.effective.auto_new === "off" && (
        <WantShow big tmdbId={tmdb} aired={totals.missing} onDone={load} langDefault={data.settings.defaults.lang_mode} />
      )}
      {!can("download") && !data.in_library && (
        <WantButton big movie={{ tmdb_id: tmdb, title: show.title, original_title: show.original_title || "",
          year: show.year ? String(show.year) : "", overview: "", poster_url: show.poster_url, media_type: "tv" }} />
      )}
      {can("download") && <SeriesSources tmdbId={tmdb} canStart />}
      <div className="space-y-2">
        {data.seasons.map((s) => (
          <Season key={s.season_number} data={data} season={s} open={!!open[s.season_number]}
            toggle={() => setOpen((o) => ({ ...o, [s.season_number]: !o[s.season_number] }))} />
        ))}
      </div>
    </main>
  );
}

export default function MobileSeriesPage() {
  return <Suspense><Series /></Suspense>;
}
