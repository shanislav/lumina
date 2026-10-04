"use client";

import { useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import FileTable from "@/components/FileTable";
import SeasonPlan from "@/components/SeasonPlan";
import ShowPacks from "@/components/ShowPacks";
import EpisodeWindow from "@/components/EpisodeWindow";
import { AUTO_DUB, AUTO_NEW, AUTO_UPGRADE, AutoFields, ShowAutomation, WantShow } from "@/components/SeriesAuto";
import { useAuth } from "@/components/AuthGate";
import {
  DownloadItem, getDownloads, setAudioLanguage,
  MovieContext, QualityProfile, ScoredFile, SeriesDetail, SeriesEpisode, SeriesLangMode, SeriesSeason,
  getProfiles, getSeries, saveSeriesSettings, searchFiles,
} from "@/lib/api";

/**
 * A TV show (backend modules/series): every season with the state of each episode, the show's settings,
 * and finding + downloading one episode (the common file table with the episode's files).
 */

const STATE: Record<SeriesEpisode["state"], { label: string; cls: string; dot: string }> = {
  owned: { label: "mám", cls: "text-emerald-300", dot: "bg-emerald-500" },
  temp: { label: "čeká na dabing", cls: "text-amber-300", dot: "bg-amber-400" },
  unknown: { label: "zvuk nezjištěn", cls: "text-sky-300", dot: "bg-sky-500" },
  missing: { label: "chybí", cls: "text-red-300", dot: "bg-red-500" },
  upcoming: { label: "nevyšlo", cls: "text-zinc-500", dot: "bg-zinc-600" },
};
const LANG_MODES: [SeriesLangMode, string, string][] = [
  ["local_or_temp", "CZ/SK, jinak hned EN", "Stáhne se nejlepší dostupný jazyk hned; díl bez CZ/SK čeká na dabing a nahradí se, až vyjde."],
  ["local_only", "Jen CZ/SK", "Stahuje se jen dabovaná verze (např. pro děti)."],
  ["original", "Originál", "Dabing se nehledá."],
];
const L = (code: string) => (code === "cs" ? "CZ" : code.toUpperCase());
const se = (s: number, e: number) => `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
const czDate = (d: string) => (d ? new Date(d).toLocaleDateString("cs-CZ") : "");

/** An episode being downloaded: waiting in the queue, or how far it is. */
type EpisodeDownload = { queued: boolean; pos?: number; pct: number };

function downloadOf(d: DownloadItem): EpisodeDownload {
  if (d.status === "queued") return { queued: true, pos: d.queue_pos, pct: 0 };
  const pct = d.progress != null ? d.progress * 100 : d.total_length ? (d.completed_length / d.total_length) * 100 : 0;
  return { queued: false, pct: Math.min(100, Math.round(pct)) };
}

export default function SeriesView({ tmdbId }: { tmdbId: number }) {
  const { can } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<SeriesDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [searching, setSearching] = useState<{ season: number; episode: number } | null>(null);
  const [episodeWindow, setEpisodeWindow] = useState<number | null>(null);      // library_episodes id
  // the show's episodes being downloaded now ("<season>:<episode>"), watched while the page is open
  const [downloads, setDownloads] = useState<Record<string, EpisodeDownload>>({});
  const [dlTick, setDlTick] = useState(0);
  const watchDownloads = () => setDlTick((t) => t + 1);

  const load = (fresh = false) =>
    getSeries(tmdbId, fresh).then((d) => {
      setData(d);
      setError(null);
      // open the seasons with something missing (the first one when all is there)
      setOpen((prev) => Object.keys(prev).length ? prev : Object.fromEntries(
        d.seasons.filter((s) => s.season_number > 0 && (s.counts.missing || s.counts.temp || s.counts.unknown)).slice(0, 2).map((s) => [s.season_number, true])));
    }).catch((e) => setError(e instanceof Error ? e.message : "Chyba"));
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [tmdbId]);

  const active = Object.keys(downloads).length > 0;
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    let before = 0;
    async function poll() {
      try {
        const list = await getDownloads(1);
        if (!live) return;
        const mine: Record<string, EpisodeDownload> = {};
        for (const d of list.downloads) {
          if (d.tmdb_id === tmdbId && d.content_type === "tv" && d.season != null && d.episode) mine[`${d.season}:${d.episode}`] = downloadOf(d);
        }
        const now = Object.keys(mine).length;
        if (now < before) load();          // something finished — the episode is in the library now
        before = now;
        setDownloads(mine);
        timer = setTimeout(poll, now ? 4000 : 20000);
      } catch {
        if (live) timer = setTimeout(poll, 20000);
      }
    }
    poll();
    return () => { live = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tmdbId, dlTick]);

  if (error) return <main className="p-8 text-red-400">{error}</main>;
  if (!data) return <main className="p-8 text-zinc-500">Načítám seriál…</main>;
  const { show, totals } = data;

  return (
    <main className="mx-auto max-w-6xl space-y-5 px-4 py-6">
      <button onClick={() => (window.history.length > 1 ? router.back() : router.push("/library?tab=serialy"))}
        className="text-sm text-zinc-500 transition-colors hover:text-zinc-300">&larr; Zpět</button>
      <header className="flex gap-4">
        {show.poster_url && (
          <Image src={show.poster_url} alt="" width={120} height={180} unoptimized
            className="h-[180px] w-[120px] flex-shrink-0 rounded-lg object-cover" />
        )}
        <div className="min-w-0 space-y-1.5">
          <h1 className="text-2xl font-semibold text-zinc-100">
            {show.title} {show.year && <span className="text-zinc-500 font-normal">({show.year})</span>}
          </h1>
          {show.original_title && show.original_title !== show.title && <p className="text-sm text-zinc-500">{show.original_title}</p>}
          <p className="text-xs text-zinc-400">
            {[show.status === "Ended" ? "ukončený" : show.status === "Canceled" ? "zrušený" : show.status ? "vysílá se" : "",
              `${data.seasons.length} sérií`, show.networks.join(", "), show.genres.slice(0, 3).join(", "),
              show.rating ? `★ ${show.rating}` : ""].filter(Boolean).join(" · ")}
          </p>
          <p className="text-xs">
            <span className="text-emerald-300">mám {totals.owned}</span>
            {totals.temp > 0 && <span className="text-amber-300"> · čeká na dabing {totals.temp}</span>}
            {totals.unknown > 0 && <span className="text-sky-300" title="Zvuková stopa nemá v souboru jazyk — nevím, jestli je česky"> · zvuk nezjištěn {totals.unknown}</span>}
            <span className="text-red-300"> · chybí {totals.missing}</span>
            {totals.upcoming > 0 && <span className="text-zinc-500"> · nevyšlo {totals.upcoming}</span>}
            {show.next_episode && (
              <span className="text-zinc-400"> · další díl {se(show.next_episode.season, show.next_episode.episode)} {czDate(show.next_episode.air_date)}</span>
            )}
          </p>
          {show.overview && <p className="line-clamp-3 max-w-3xl text-sm text-zinc-400">{show.overview}</p>}
        </div>
      </header>

      {!data.in_library && can("library.edit") && data.settings.effective.auto_new === "off" && (
        <WantShow tmdbId={tmdbId} aired={totals.missing} onDone={() => load()} torrent={!!data.settings.effective.torrent && can("search")}
          onPackStarted={() => { watchDownloads(); setTimeout(() => load(), 1500); }} />
      )}
      <SettingsPanel data={data} onSaved={() => load()} editable={can("library.edit")} />
      <ShowAutomation tmdbId={tmdbId} canDownload={can("download")} canEdit={can("library.edit")}
        on={[data.settings.effective.auto_new, data.settings.effective.auto_dub, data.settings.effective.auto_upgrade]
          .some((v) => v && v !== "off")} />

      {/* the card above offers it already for a show not owned yet */}
      {can("search") && data.settings.effective.torrent && !(!data.in_library && can("library.edit") && data.settings.effective.auto_new === "off") && (
        <ShowPacks tmdbId={tmdbId} onStarted={() => { watchDownloads(); setTimeout(() => load(), 1500); }} />
      )}

      <div className="space-y-2">
        {data.seasons.map((s) => (
          <Season key={s.season_number} tmdbId={tmdbId} downloads={downloads}
            onStarted={() => { watchDownloads(); setTimeout(() => load(), 1500); }}
            season={s} open={!!open[s.season_number]}
            toggle={() => setOpen((o) => ({ ...o, [s.season_number]: !o[s.season_number] }))}
            canSearch={can("search")} canEdit={can("library.edit")} onChanged={() => load()}
            searching={searching} onOpen={setEpisodeWindow}
            onSearch={(episode) => setSearching(searching?.season === s.season_number && searching.episode === episode
              ? null : { season: s.season_number, episode })}>
            {searching?.season === s.season_number && (
              <EpisodeSearch data={data} season={s.season_number} episode={searching.episode}
                onDownloaded={() => { watchDownloads(); setSearching(null); }} />
            )}
          </Season>
        ))}
      </div>
      <button onClick={() => load(true)} className="text-xs text-zinc-500 hover:text-zinc-300">Načíst znovu z TMDB</button>
      {episodeWindow !== null && (
        <EpisodeWindow id={episodeWindow} onClose={() => setEpisodeWindow(null)} onChanged={() => load()} />
      )}
    </main>
  );
}

function SettingsPanel({ data, onSaved, editable }: { data: SeriesDetail; onSaved: () => void; editable: boolean }) {
  const [open, setOpen] = useState(false);
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) getProfiles("tv").then(setProfiles).catch(() => {}); }, [open]);
  const { own, effective } = data.settings;
  const mode = LANG_MODES.find(([m]) => m === effective.lang_mode);

  async function save(values: Parameters<typeof saveSeriesSettings>[1]) {
    setSaving(true);
    try { await saveSeriesSettings(data.show.tmdb_id, values); onSaved(); } finally { setSaving(false); }
  }
  const field = "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs text-zinc-200 disabled:opacity-50";
  const def = <span className="text-zinc-600"> (výchozí)</span>;

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm">
        <span className="text-zinc-200">Nastavení seriálu</span>
        <span className="text-xs text-zinc-500">
          {data.profile.name} · {mode?.[1]} · {effective.torrent ? "i torrenty" : "bez torrentů"}
          {effective.auto_new && effective.auto_new !== "off" ? ` · nové díly: ${AUTO_NEW.find(([m]) => m === effective.auto_new)?.[1]}` : ""}
          {effective.auto_dub && effective.auto_dub !== "off" ? ` · dabing: ${AUTO_DUB.find(([m]) => m === effective.auto_dub)?.[1]}` : ""}
          {effective.auto_upgrade && effective.auto_upgrade !== "off" ? ` · kvalita: ${AUTO_UPGRADE.find(([m]) => m === effective.auto_upgrade)?.[1]}` : ""} {open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div className="grid grid-cols-[9rem_1fr] items-center gap-x-3 gap-y-2.5 px-4 pb-4 text-xs">
          <label className="text-zinc-400">Profil kvality</label>
          <div>
            <select disabled={!editable || saving} value={own.profile_id ?? ""} className={field}
              onChange={(e) => save({ profile_id: e.target.value === "" ? null : Number(e.target.value) })}>
              <option value="">výchozí pro seriály</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {own.profile_id == null && def}
          </div>

          <label className="text-zinc-400 self-start pt-1">Jazyk</label>
          <div className="space-y-1">
            <select disabled={!editable || saving} value={own.lang_mode ?? ""} className={field}
              onChange={(e) => save({ lang_mode: (e.target.value || null) as SeriesLangMode | null })}>
              <option value="">výchozí ({LANG_MODES.find(([m]) => m === data.settings.defaults.lang_mode)?.[1]})</option>
              {LANG_MODES.map(([m, label]) => <option key={m} value={m}>{label}</option>)}
            </select>
            <p className="text-[11px] text-zinc-500">{mode?.[2]}</p>
          </div>

          <label className="text-zinc-400">Torrenty</label>
          <TriState value={own.torrent} fallback={data.settings.defaults.torrent ?? true} disabled={!editable || saving}
            yes="hledat i na torrentech" no="jen WebShare / FastShare" onChange={(v) => save({ torrent: v })} />

          <AutoFields own={own} defaults={data.settings.defaults} langMode={effective.lang_mode}
            disabled={!editable || saving} onChange={save} />
        </div>
      )}
    </section>
  );
}

function TriState({ value, fallback, yes, no, disabled, onChange }: {
  value: boolean | null; fallback: boolean; yes: string; no: string; disabled: boolean; onChange: (v: boolean | null) => void;
}) {
  return (
    <select disabled={disabled} value={value == null ? "" : value ? "1" : "0"}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "1")}
      className="w-fit rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs text-zinc-200 disabled:opacity-50">
      <option value="">výchozí ({fallback ? yes : no})</option>
      <option value="1">{yes}</option>
      <option value="0">{no}</option>
    </select>
  );
}

function Season({ tmdbId, onStarted, downloads, season, open, toggle, canSearch, canEdit, onChanged, searching, onSearch, onOpen, children }: {
  tmdbId: number; onStarted: () => void; downloads: Record<string, EpisodeDownload>;
  season: SeriesSeason; open: boolean; toggle: () => void; canSearch: boolean; canEdit: boolean; onChanged: () => void;
  searching: { season: number; episode: number } | null; onSearch: (episode: number) => void;
  onOpen: (episodeId: number) => void; children?: React.ReactNode;
}) {
  const [whole, setWhole] = useState(false);
  const [langBusy, setLangBusy] = useState("");
  const c = season.counts;

  // the user heard the episode: its sound's language goes into the file (MKV, MP4) and Lumina
  const setLangs = async (ids: number[], lang: string, ask: boolean) => {
    if (!ids.length) return;
    if (ask && !window.confirm(`Zapsat jazyk ${L(lang)} ke zvuku ${ids.length} dílů (do souborů MKV a MP4)?`)) return;
    setLangBusy(`zapisuji ${ids.length}…`);
    try {
      const r = await setAudioLanguage(ids, lang);
      setLangBusy(r.errors.length ? `chyba: ${r.errors[0]}` : "");
      onChanged();
    } catch (e) {
      setLangBusy(e instanceof Error ? e.message : "Chyba");
    }
  };
  const going = season.episodes.filter((e) => downloads[`${season.season_number}:${e.episode}`]).length;
  const total = season.episodes.length || 1;
  return (
    <section className="rounded-lg border border-zinc-800">
      <button onClick={toggle} className="flex w-full items-center gap-3 px-4 py-2.5 text-left">
        <span className="w-28 text-sm font-medium text-zinc-100">{season.name || `Série ${season.season_number}`}</span>
        <span className="flex h-2 flex-1 overflow-hidden rounded bg-zinc-800" title={`mám ${c.owned}, čeká na dabing ${c.temp}, zvuk nezjištěn ${c.unknown}, chybí ${c.missing}, nevyšlo ${c.upcoming}`}>
          {(["owned", "temp", "unknown", "missing", "upcoming"] as const).map((k) => c[k] > 0 && (
            <span key={k} className={STATE[k].dot} style={{ width: `${(c[k] / total) * 100}%` }} />
          ))}
        </span>
        {season.season_number === 0 ? (
        <span className="w-44 text-right text-xs text-zinc-500" title="Speciály, bonusy a souhrny — nepočítají se do chybějících">
          mám {c.owned + c.temp + c.unknown} z {season.episodes.length}
          {going > 0 && <span className="text-violet-300"> · stahuje se {going}</span>}
        </span>
        ) : (
        <span className="w-44 text-right text-xs text-zinc-400">
          {c.owned + c.temp + c.unknown}/{season.episodes.length - c.upcoming}
          {c.temp > 0 && <span className="text-amber-300"> · {c.temp} EN</span>}
          {c.unknown > 0 && <span className="text-sky-300" title="Zvuková stopa nemá v souboru jazyk"> · {c.unknown} ? zvuk</span>}
          {c.missing > 0 && <span className="text-red-300"> · chybí {c.missing}</span>}
          {going > 0 && <span className="text-violet-300"> · stahuje se {going}</span>}
        </span>
        )}
        <span className="text-xs text-zinc-500">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="border-t border-zinc-800">
          {canSearch && season.season_number > 0 && c.missing + c.temp + c.unknown > 0 && (
            <div className="flex items-center gap-3 px-4 py-1.5 text-xs">
              <button onClick={() => setWhole(!whole)} className="text-violet-300 hover:text-violet-200">
                {whole ? "Zavřít celou sérii" : `Celá série — chybějící díly najednou (${c.missing + c.temp + c.unknown})`}
              </button>
            </div>
          )}
          {canEdit && c.unknown > 0 && (
            <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 text-xs text-sky-300">
              <span title="Zvukové stopy nemají v souboru jazyk. Lumina ho zapíše do souboru (MKV, MP4), u AVI si ho pamatuje sama.">
                Zvuk nezjištěn u {c.unknown} {c.unknown === 1 ? "dílu" : "dílů"} — všechny jsou:
              </span>
              {["cs", "sk", "en"].map((l) => (
                <button key={l} disabled={!!langBusy}
                  onClick={() => setLangs(season.episodes.filter((e) => e.state === "unknown" && e.file?.id).map((e) => e.file!.id!), l, true)}
                  className="rounded border border-sky-800 px-1.5 py-0.5 font-medium uppercase hover:bg-sky-950 disabled:opacity-40">{L(l)}</button>
              ))}
              {langBusy && <span className="text-zinc-400">{langBusy}</span>}
            </div>
          )}
          {whole && (
            <SeasonPlan tmdbId={tmdbId} season={season.season_number} onStarted={onStarted}
              ownedEpisodes={season.episodes.filter((e) => e.file).map((e) => e.episode)}
              busyEpisodes={season.episodes.filter((e) => downloads[`${season.season_number}:${e.episode}`]).map((e) => e.episode)} />
          )}
          {season.episodes.map((ep) => {
            const active = searching?.season === season.season_number && searching.episode === ep.episode;
            const dl = downloads[`${season.season_number}:${ep.episode}`];
            return (
              <div key={ep.episode}>
                <div className={`flex items-center gap-3 px-4 py-1.5 text-xs ${active ? "bg-violet-950/30" : "hover:bg-zinc-900/60"}`}>
                  <span className={`h-2 w-2 flex-shrink-0 rounded-full ${STATE[ep.state].dot}`} />
                  <span className="w-16 font-mono text-zinc-400">{se(season.season_number, ep.episode)}</span>
                  {ep.file?.id ? (
                    <button onClick={() => onOpen(ep.file!.id!)} title={`${ep.overview}\n\nOtevřít díl: soubory, přehrát, titulky`.trim()}
                      className="min-w-0 flex-1 truncate text-left text-zinc-200 hover:text-violet-300 hover:underline">{ep.name || "—"}</button>
                  ) : (
                    <span className="min-w-0 flex-1 truncate text-zinc-200" title={ep.overview}>{ep.name || "—"}</span>
                  )}
                  <span className="hidden w-20 text-zinc-500 sm:block">{czDate(ep.air_date)}</span>
                  {ep.state === "unknown" && canEdit && ep.file?.id ? (
                    <span className="flex w-28 items-center gap-1 text-sky-300" title={`${ep.file.filename}\nZvuková stopa nemá jazyk — pusť si kousek a klikni, jaký je (zapíše se do souboru)`}>
                      <span className="mr-0.5">zvuk?</span>
                      {["cs", "sk", "en"].map((l) => (
                        <button key={l} disabled={!!langBusy} onClick={() => setLangs([ep.file!.id!], l, false)}
                          className="rounded border border-sky-800 px-1 text-[10px] font-medium uppercase leading-4 hover:bg-sky-950 disabled:opacity-40">{L(l)}</button>
                      ))}
                    </span>
                  ) : (
                  <span className={`w-28 ${STATE[ep.state].cls}`}
                    title={ep.file ? `${ep.file.filename}\n${(ep.file.size / 1e9).toFixed(2)} GB${ep.no_dub ? "\nCZ dabing nevznikl — nečeká se na něj" : ""}` : ""}>
                    {ep.file ? [ep.file.quality, ep.file.languages.map(L).join("+")].filter(Boolean).join(" · ") || STATE[ep.state].label
                      : STATE[ep.state].label}
                  </span>
                  )}
                  {dl ? (
                    <span className={`w-24 whitespace-nowrap text-right ${dl.queued ? "text-amber-300" : "text-violet-300 animate-pulse"}`}
                      title="Stahování běží — díl se po dokončení sám objeví v knihovně">
                      {dl.queued ? `ve frontě${dl.pos ? ` (${dl.pos}.)` : ""}` : `stahuje se ${dl.pct} %`}
                    </span>
                  ) : canSearch && ep.state !== "upcoming" ? (
                    <button onClick={() => onSearch(ep.episode)} className="w-24 text-right text-violet-300 hover:text-violet-200">
                      {active ? "Zavřít" : ep.file ? "Jiná verze" : "Hledat"}
                    </button>
                  ) : <span className="w-24" />}
                </div>
                {active && children}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function EpisodeSearch({ data, season, episode, onDownloaded }: {
  data: SeriesDetail; season: number; episode: number; onDownloaded: () => void;
}) {
  const [files, setFiles] = useState<ScoredFile[]>([]);
  const [movie, setMovie] = useState<MovieContext | null>(null);
  const [preferLocal, setPreferLocal] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const ep = data.seasons.find((s) => s.season_number === season)?.episodes.find((e) => e.episode === episode);
  const torrent = data.settings.effective.torrent;

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    searchFiles(data.show.title, undefined, data.show.original_title, data.show.tmdb_id, "tv", null,
      { season, episode, torrent })
      .then((r) => { if (live) { setFiles(r.files); setMovie(r.movie); setPreferLocal(r.prefer_local_audio); } })
      .catch((e) => live && setError(e instanceof Error ? e.message : "Chyba"))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [data.show.tmdb_id, data.show.title, data.show.original_title, season, episode, torrent]);

  // an owned episode is replaced (EN waiting for the dub, or the user wants another version)
  const action = useMemo(() => ({ mode: "episode" as const, season, episode, replace: !!ep?.file }), [season, episode, ep?.file]);
  return (
    <div className="space-y-2 border-y border-violet-900/40 bg-zinc-950/60 px-4 py-3">
      <p className="text-xs text-zinc-400">
        Soubory dílu <span className="text-zinc-200">{se(season, episode)}</span>
        {ep?.file && <> · stažený soubor nahradí <span className="text-zinc-300">{ep.file.filename}</span></>}
        {!torrent && " · bez torrentů (nastavení seriálu)"}
      </p>
      {error ? <p className="text-xs text-red-400">{error}</p> : (
        <FileTable files={files} loading={loading} tmdb_id={data.show.tmdb_id} title={data.show.title}
          year={data.show.year ?? undefined} mediaType="tv" movie={movie} preferLocalAudio={preferLocal}
          profileId={data.profile.id} libraryAction={action} onDownloadStarted={onDownloaded} />
      )}
    </div>
  );
}
