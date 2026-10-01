"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AudioReference, AudioSyncJob, AudioTrackInfo, FilmMap, FilmMapMember, LibraryMovie, TargetTrackCheck,
  PlannedTrack, applyFilmMap, audioPreviewUrl, planFilmMap, deleteVersionFile, formatSize, getAudioReference, getAudioSyncJob, getFilmMap,
  getAudioTracks, getLibraryMovies, makeAudioPreview, makeTrackPreview, setAudioAdjust, setAudioReference, startFilmMap,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

const LANG: Record<string, string> = {
  cs: "CZ", cze: "CZ", ces: "CZ", sk: "SK", slo: "SK", slk: "SK", en: "EN", eng: "EN", de: "DE", ger: "DE", deu: "DE",
  fr: "FR", fre: "FR", fra: "FR", pl: "PL", pol: "PL", hu: "HU", hun: "HU", ru: "RU", rus: "RU", it: "IT", ita: "IT",
  es: "ES", spa: "ES", ja: "JA", jpn: "JA",
};
const lang = (code: string) => (code ? LANG[code.toLowerCase()] ?? code.toUpperCase() : "?");
const channels = (n: number) => ({ 1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1" } as Record<number, string>)[n] ?? `${n}ch`;
const trackLabel = (a: { language: string; codec: string; channels: number; title?: string; bitrate?: number }) =>
  `${lang(a.language)} ${channels(a.channels)} ${(a.codec || "?").toUpperCase()}${a.bitrate ? ` ${Math.round(a.bitrate / 1000)} kbps` : ""}${a.title ? ` „${a.title}“` : ""}`;

/** How a track of another version fits the reference: its own measurement or its version's. */
function fitBadge(verdict: string | undefined, delta: number, own: boolean): { text: string; cls: string; tip: string } {
  const how = verdict === "speed" ? " — jiná rychlost, přepočítá se" : verdict === "cuts" ? " — jiný střih, poskládá se"
    : delta ? ` po posunu ${delta > 0 ? "+" : ""}${delta.toFixed(2)} s` : "";
  const tip = own ? "Nesedí s časováním své verze (uploader ji vložil jinak) — změřena zvlášť přímo proti referenci"
    : delta ? "Ve své verzi je posunutá oproti ostatním stopám — Lumina to vyrovná" : "Změřeno proti referenční stopě";
  return { text: `✓ sedí s referencí${how}${own ? " (změřena zvlášť)" : ""}`,
    cls: how || own ? "bg-amber-900/40 text-amber-200" : "bg-emerald-900/60 text-emerald-200", tip };
}

const PHASE: Record<string, string> = {
  target: "Kontroluji stopy cíle vůči referenci", align: "Srovnávám verzi s referencí", tracks: "Měřím jednotlivé stopy",
  track: "Stopa nesedí s časováním verze — měřím ji zvlášť", compare: "Porovnávám dabingy", speed: "Zjišťuji rychlost",
  windows: "Porovnávám úseky", cuts: "Hledám střihy", prepare: "Připravuji stopy", mux: "Skládám soubor",
  verify: "Kontroluji výsledek", import: "Předávám knihovně", start: "Začínám",
};

function checkBadge(c: TargetTrackCheck | undefined): { text: string; cls: string; tip?: string } {
  if (!c) return { text: "neměřeno", cls: "text-zinc-500" };
  if (c.ok) return { text: "✓ sedí", cls: "bg-emerald-900/60 text-emerald-200" };
  if (c.verdict === "constant") return { text: `posun ${c.offset > 0 ? "+" : ""}${c.offset.toFixed(2)} s`, cls: "bg-amber-900/50 text-amber-200", tip: c.note };
  if (c.verdict === "speed") return { text: "jiná rychlost", cls: "bg-amber-900/50 text-amber-200", tip: c.note };
  if (c.verdict === "cuts") return { text: "rozchází se / střih", cls: "bg-amber-900/50 text-amber-200", tip: c.note };
  return { text: "✗ nesedí vůbec", cls: "bg-red-900/50 text-red-200", tip: c.note };
}

type Clip = { name: string; label: string; resultId?: number; base: number; saved: number; trial: number; at: number; track?: number };

/** The audio editor of one film: pick the reference track (checked by watching), measure every track of
 *  every version against it, then fix, add or drop tracks of the chosen version. */
export default function AudioEditor({ tmdbId, initialTarget }: { tmdbId: number; initialTarget: number | null }) {
  const { can } = useAuth();
  const [versions, setVersions] = useState<LibraryMovie[]>([]);
  const [ref, setRef] = useState<AudioReference | null>(null);
  const [target, setTarget] = useState<number | null>(initialTarget);
  const [refTrack, setRefTrack] = useState<number | null>(null);
  const [audioOf, setAudioOf] = useState<Record<number, AudioTrackInfo[]>>({});   // tracks of every version
  const [sel, setSel] = useState<Record<number, number[]> | null>(null);         // tracks to measure (null = all)
  const [map, setMap] = useState<FilmMap | null>(null);
  const [job, setJob] = useState<AudioSyncJob | null>(null);
  const [add, setAdd] = useState<Record<number, FilmMapMember | null>>({});   // dub id → source
  const [fix, setFix] = useState<number[]>([]);
  const [drop, setDrop] = useState<number[]>([]);
  const [clip, setClip] = useState<Clip | null>(null);
  const [clipBusy, setClipBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [confirm, setConfirm] = useState<"replace" | "version" | null>(null);
  const [defaultKey, setDefaultKey] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});   // the user's own track names
  const [plan, setPlan] = useState<{ tracks: PlannedTrack[]; reference_dropped: boolean } | null>(null);
  const [planError, setPlanError] = useState("");

  const loadVersions = useCallback(async () => {
    const [all, r, m] = await Promise.all([getLibraryMovies(), getAudioReference(tmdbId), getFilmMap(tmdbId)]);
    const vs = all.filter((v) => v.tmdb_id === tmdbId && v.file_path && (v.status === "matched" || v.status === "manual"));
    setVersions(vs);
    Promise.all(vs.map((v) => getAudioTracks(v.id).then((t) => [v.id, t.audio] as const).catch(() => [v.id, []] as const)))
      .then((pairs) => setAudioOf(Object.fromEntries(pairs)));
    setRef(r);
    setMap(m);
    return { vs, r };
  }, [tmdbId]);

  useEffect(() => {
    loadVersions().then(({ vs, r }) => {
      setTarget((t) => {
        if (t && vs.some((v) => v.id === t)) return t;
        if (r.chosen && vs.some((v) => v.id === r.chosen!.movie_id)) return r.chosen.movie_id;
        return [...vs].sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0))[0]?.id ?? null;
      });
    }).catch((e) => setError(e instanceof Error ? e.message : "Nepodařilo se načíst"));
    getAudioSyncJob().then((j) => j.running && setJob(j)).catch(() => {});
  }, [loadVersions]);

  // the reference track of the chosen version: the stored one, else the default (original language)
  useEffect(() => {
    if (!target || !ref) return;
    setRefTrack(ref.chosen?.movie_id === target ? ref.chosen.track : ref.defaults[String(target)] ?? 0);
  }, [target, ref]);

  useEffect(() => {
    if (!job?.running) return;
    const t = setTimeout(async () => {
      try {
        const j = await getAudioSyncJob();
        setJob(j);
        if (!j.running) {
          if (j.error) setError(j.error);
          else if (j.kind === "apply") {
            const added = j.report?.added?.length ? ` Nové stopy: ${j.report.added.join(", ")}.` : "";
            setDone((j.imported ? "✓ Hotovo — soubor je v knihovně." : `Hotovo, ale knihovna soubor nepřevzala (${j.path ?? ""}).`) + added);
            setAdd({});
            setDefaultKey(null);
            setNames({});
            setFix([]);
            setDrop([]);
            setClip(null);
            const { vs, r } = await loadVersions();
            // the edited file replaced the target: follow it
            setTarget((cur) => vs.some((v) => v.id === cur) ? cur : r.chosen?.movie_id ?? vs[0]?.id ?? null);
          } else await loadVersions();
        }
      } catch { /* next tick */ }
    }, 2000);
    return () => clearTimeout(t);
  }, [job, loadVersions]);

  const tv = versions.find((v) => v.id === target);
  // the chosen tracks in one comparable form ("all" when nothing is left out)
  const selKey = (x: Record<string, number[]> | null | undefined) => {
    if (!x) return "all";
    const ids = versions.map((v) => v.id);
    const full = ids.every((id) => (x[id] ?? []).length === (audioOf[id] ?? []).length);
    return full ? "all" : JSON.stringify(ids.map((id) => [...(x[id] ?? [])].sort((a, b) => a - b)));
  };
  const sameRun = !!map && map.target_id === target && map.ref_track === refTrack;
  const mapOk = sameRun && !map!.stale && selKey(map!.selection) === selKey(sel);
  const targetAudio: AudioTrackInfo[] = useMemo(
    () => (mapOk ? map!.versions.find((v) => v.id === target)?.audio : null) ?? [], [map, mapOk, target]);
  const chosen = ref?.chosen;
  const isStored = !!chosen && chosen.movie_id === target && chosen.track === refTrack;
  const verified = isStored && chosen!.verified;
  const running = !!job?.running;

  const run = async (start: () => Promise<AudioSyncJob>) => {
    setError("");
    setDone("");
    try {
      setJob(await start());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se spustit");
    }
  };

  async function saveReference(isVerified: boolean) {
    if (!target || refTrack === null) return;
    try {
      setRef(await setAudioReference(tmdbId, target, refTrack, isVerified));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Uložení selhalo");
    }
  }

  // a finished measurement of this version + reference shows what it measured
  useEffect(() => {
    if (map && map.target_id === target && map.ref_track === refTrack) setSel(map.selection ?? null);
  }, [map, target, refTrack]);

  // the result as it will be built — asked from the backend whenever the edit changes
  const edits = useMemo(() => ({
    picks: Object.values(add).filter((m): m is FilmMapMember => !!m).map((m) => ({ version_id: m.version_id, track: m.track })),
    fixTracks: fix, dropTracks: drop, defaultKey,
    names: Object.fromEntries(Object.entries(names).filter(([, v]) => v.trim())),
  }), [add, fix, drop, defaultKey, names]);
  useEffect(() => {
    if (!mapOk || !map) { setPlan(null); return; }
    let alive = true;
    const t = setTimeout(() => {
      planFilmMap(map.id, edits).then((p) => { if (alive) { setPlan(p); setPlanError(""); } })
        .catch((e) => { if (alive) { setPlan(null); setPlanError(e instanceof Error ? e.message : "Náhled selhal"); } });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [map, mapOk, edits]);

  const selected = (vid: number, t: number) => !sel || (sel[vid] ?? []).includes(t);
  function toggle(vid: number, t: number, on: boolean) {
    const base: Record<number, number[]> = sel ?? Object.fromEntries(versions.map((v) => [v.id, (audioOf[v.id] ?? []).map((a) => a.index)]));
    const cur = base[vid] ?? [];
    setSel({ ...base, [vid]: on ? [...cur, t] : cur.filter((x) => x !== t) });
  }
  const selCount = versions.reduce((n, v) => n + (audioOf[v.id] ?? []).filter((a) => selected(v.id, a.index)).length, 0);
  const allCount = versions.reduce((n, v) => n + (audioOf[v.id] ?? []).length, 0);

  async function measure() {
    if (!target || refTrack === null) return;
    if (!isStored) await saveReference(false);
    run(() => startFilmMap(tmdbId, target, refTrack, selKey(sel) === "all" ? null : sel));
  }

  const fitOf = (m: FilmMapMember) =>
    map?.versions.find((x) => x.id === m.version_id)?.alignment?.tracks?.[String(m.track)] ?? { delta: 0, ok: true };

  async function showClip(make: () => Promise<{ name: string; saved_ms: number }>, c: Omit<Clip, "name" | "saved">) {
    setClipBusy(true);
    setError("");
    try {
      const r = await make();
      setClip({ ...c, name: r.name, saved: r.saved_ms });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ukázka selhala");
    } finally {
      setClipBusy(false);
    }
  }

  const at0 = () => (map?.duration ?? 3000) * 0.4;

  function previewMember(m: FilmMapMember, at = at0(), trial = 0) {
    const v = map!.versions.find((x) => x.id === m.version_id)!;
    const fit = fitOf(m);
    // a track with its own shift: placed where it really fits (adjust + = later ⇒ −delta)
    const resultId = fit.result_id ?? v.alignment!.result_id;
    const base = fit.result_id ? 0 : -Math.round(fit.delta * 1000);
    showClip(() => makeAudioPreview(resultId, at, base + trial, m.track),
      { label: `${trackLabel(m)} z ${v.quality} — na obraze cíle`, resultId, base, trial, at, track: m.track });
  }

  function previewTarget(track: number, fixed: boolean, at = at0(), trial = 0) {
    const c = map?.target_tracks?.[String(track)];
    const a = targetAudio[track];
    if (fixed && c) {
      showClip(() => makeAudioPreview(c.result_id, at, trial, track),
        { label: `${trackLabel(a)} — opravená (posunutá na referenci)`, resultId: c.result_id, base: 0, trial, at, track });
    } else {
      showClip(() => makeTrackPreview(target!, track, at),
        { label: `${trackLabel(a)} — tak, jak je v souboru`, base: 0, trial: 0, at, track });
    }
  }

  function retryClip(trial: number, at?: number) {
    if (!clip?.resultId) return;
    const c = clip;
    showClip(() => makeAudioPreview(c.resultId!, at ?? c.at, c.base + trial, c.track),
      { ...c, trial, at: at ?? c.at });
  }

  if (!versions.length || !tv) {
    return (
      <main className="max-w-5xl mx-auto p-4 sm:p-6 text-sm text-zinc-400">
        {error ? <p className="text-red-400">{error}</p> : "Načítám…"}
      </main>
    );
  }

  const picks = Object.values(add).filter((m): m is FilmMapMember => !!m);
  const currentDefault = targetAudio.find((a) => a.default);
  const defaultChanged = !!defaultKey && defaultKey !== (currentDefault ? `t:${currentDefault.index}` : null);
  const renamedByUser = Object.values(names).filter((v) => v.trim()).length;
  const anyChange = picks.length > 0 || fix.length > 0 || drop.length > 0 || defaultChanged || renamedByUser > 0;
  const otherDubs = mapOk ? map!.dubs.filter((d) => !d.members.some((m) => m.version_id === target)) : [];
  const versionName = (id: number) => {
    const v = versions.find((x) => x.id === id);
    return v ? `${v.quality_summary || v.quality} · ${formatSize(v.file_size)}` : `#${id}`;
  };

  return (
    <main className="max-w-5xl mx-auto p-4 sm:p-6 space-y-5 text-sm">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href="/library" className="text-xs text-zinc-500 hover:text-zinc-300">← Knihovna</Link>
        <h1 className="text-xl font-semibold text-zinc-100">Editor zvuku — {tv.title} {tv.year ? `(${tv.year})` : ""}</h1>
      </div>

      {/* 1. reference */}
      <section className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-4 space-y-3">
        <h2 className="text-xs uppercase tracking-wide text-zinc-400">1. Zdroj pravdy — referenční stopa</h2>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-zinc-400 text-xs">Verze (obraz, který chceš nechat):</span>
          <select value={target ?? ""} disabled={running}
            onChange={(e) => { setTarget(Number(e.target.value)); setAdd({}); setFix([]); setDrop([]); setDefaultKey(null); setNames({}); setClip(null); }}
            className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200 max-w-full">
            {versions.map((v) => <option key={v.id} value={v.id}>{versionName(v.id)}</option>)}
          </select>
        </div>
        <RefTrackPicker movieId={tv.id} value={refTrack} disabled={running} onChange={(t) => { setRefTrack(t); setFix([]); setDrop([]); }} />
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {verified
            ? <span className="rounded bg-emerald-900/60 px-2 py-0.5 text-emerald-200">✓ ověřeno{chosen?.verified_by ? ` (${chosen.verified_by})` : ""}</span>
            : <span className="rounded bg-amber-900/50 px-2 py-0.5 text-amber-200">neověřeno</span>}
          {can("player") && refTrack !== null && (
            <Link href={`/play?id=${tv.id}&audio=${refTrack}&t=${Math.round(at0())}`}
              className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500">▶ Pustit film s touto stopou</Link>
          )}
          {!verified && <button onClick={() => saveReference(true)} className="rounded border border-emerald-700 px-3 py-1 text-emerald-200 hover:bg-emerald-900/40">Sedí na obraz — ověřeno</button>}
          {verified && <button onClick={() => saveReference(false)} className="text-zinc-500 hover:text-zinc-300">zrušit ověření</button>}
        </div>
        <p className="text-[11px] text-zinc-500">
          Předvolená je stopa v původním jazyce filmu{ref?.original_language ? ` (${lang(ref.original_language)} podle TMDB)` : ""}.
          Pusť si film na pár místech (začátek, střed, konec) a zkontroluj, že pusa sedí se zvukem. Všechny ostatní stopy se měří proti ní.
        </p>
      </section>

      {/* 2. measure */}
      <section className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xs uppercase tracking-wide text-zinc-400">2. Stopy změřené vůči referenci</h2>
          {!mapOk && (
            <button onClick={measure} disabled={running || refTrack === null}
              className="rounded bg-violet-600 px-3 py-1 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
              {map && map.target_id === target && map.ref_track === refTrack && map.stale ? "Verze se změnily — změřit znovu" : "Změřit stopy"}
            </button>
          )}
          {mapOk && !running && (
            <button onClick={measure} className="text-xs text-zinc-500 hover:text-zinc-300">změřit znovu</button>
          )}
        </div>
        {!running && allCount > 0 && (
          <details className="rounded border border-zinc-800" open={!mapOk || undefined}>
            <summary className="cursor-pointer select-none px-2 py-1.5 text-xs text-zinc-400 hover:text-zinc-200">
              Které stopy měřit: {selCount === allCount ? "všechny" : `${selCount} z ${allCount}`}
            </summary>
            <div className="space-y-2 px-2 pb-2 text-xs">
              <div className="flex gap-3">
                <button onClick={() => setSel(null)} className="text-violet-300 hover:text-violet-200">vše</button>
                <button onClick={() => setSel(Object.fromEntries(versions.map((v) => [v.id, v.id === target && refTrack !== null ? [refTrack] : []])))}
                  className="text-violet-300 hover:text-violet-200">nic</button>
                <button onClick={() => setSel(Object.fromEntries(versions.map((v) => [v.id, (audioOf[v.id] ?? [])
                  .filter((a) => (v.id === target && a.index === refTrack) || ["cs", "cze", "ces", "sk", "slo", "slk"].includes(a.language.toLowerCase()))
                  .map((a) => a.index)])))} className="text-violet-300 hover:text-violet-200">jen CZ/SK</button>
              </div>
              {versions.map((v) => (
                <div key={v.id}>
                  <p className={v.id === target ? "text-violet-200" : "text-zinc-400"}>{v.id === target ? "Tato verze" : versionName(v.id)}</p>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 pl-2">
                    {(audioOf[v.id] ?? []).map((a) => {
                      const isRef = v.id === target && a.index === refTrack;
                      return (
                        <label key={a.index} className="flex items-center gap-1 text-zinc-300">
                          <input type="checkbox" disabled={isRef} checked={isRef || selected(v.id, a.index)}
                            onChange={(e) => toggle(v.id, a.index, e.target.checked)} />
                          {a.index + 1}. {trackLabel(a)}{isRef && " ★"}
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
              <p className="text-[11px] text-zinc-500">Měří se jen vybrané stopy (asi minuta na stopu a na verzi). Nevybrané stopy této verze v souboru zůstanou.</p>
            </div>
          </details>
        )}
        {running && (
          <div className="space-y-1">
            <p className="text-xs text-violet-300">{PHASE[job?.phase ?? ""] ?? job?.phase}{job?.current ? ` — ${job.current}` : ""}</p>
            <div className="h-1.5 rounded bg-zinc-800 overflow-hidden">
              <div className={`h-full bg-violet-500 ${job?.total ? "" : "w-1/3 animate-pulse"}`}
                style={job?.total ? { width: `${Math.round(((job.done ?? 0) / job.total) * 100)}%` } : undefined} />
            </div>
          </div>
        )}

        {mapOk && (
          <>
            <div>
              <p className="mb-1 text-xs text-zinc-400">V této verzi</p>
              <table className="w-full text-xs">
                <tbody>
                  {targetAudio.map((a) => {
                    const isRef = a.index === map!.ref_track;
                    const c = map!.target_tracks?.[String(a.index)];
                    const b = isRef ? { text: "★ reference", cls: "bg-violet-900/60 text-violet-200" } : checkBadge(c);
                    return (
                      <tr key={a.index} className="border-b border-zinc-800/50">
                        <td className="py-1.5 pr-2 text-zinc-500">{a.index + 1}</td>
                        <td className={`py-1.5 pr-3 ${drop.includes(a.index) ? "text-red-300 line-through" : "text-zinc-100"}`}>{trackLabel(a)}</td>
                        <td className="py-1.5 pr-3"><span title={b.tip} className={`rounded px-1.5 py-0.5 text-[10px] ${b.cls}`}>{b.text}</span></td>
                        <td className="py-1.5 pr-3 whitespace-nowrap">
                          <button onClick={() => previewTarget(a.index, false)} disabled={clipBusy} className="text-violet-300 hover:text-violet-200 disabled:opacity-40">▶ jak je</button>
                          {c?.fixable && (
                            <button onClick={() => previewTarget(a.index, true)} disabled={clipBusy} className="ml-2 text-violet-300 hover:text-violet-200 disabled:opacity-40">▶ opravená</button>
                          )}
                        </td>
                        <td className="py-1.5 whitespace-nowrap text-zinc-300">
                          {c?.fixable && !drop.includes(a.index) && (
                            <label className="mr-3"><input type="checkbox" checked={fix.includes(a.index)}
                              onChange={(e) => setFix(e.target.checked ? [...fix, a.index] : fix.filter((x) => x !== a.index))} /> opravit</label>
                          )}
                          {can("library.delete") && (
                            <label title={isRef ? "Referenční stopa se použije ke kontrole nových stop a odebere se až nakonec; referencí se stane stopa, která s ní sedí" : undefined}>
                              <input type="checkbox" checked={drop.includes(a.index)}
                              onChange={(e) => { setDrop(e.target.checked ? [...drop, a.index] : drop.filter((x) => x !== a.index)); setFix(fix.filter((x) => x !== a.index)); }} /> odebrat</label>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div>
              <p className="mb-1 text-xs text-zinc-400">Dabingy z ostatních verzí, které tahle verze nemá</p>
              {!otherDubs.length && <p className="text-xs text-zinc-600">{versions.length > 1 ? "Žádné — všechny dabingy už má." : "Film má jen tuto verzi."}</p>}
              <div className="space-y-2">
                {otherDubs.map((d) => {
                  const sources = d.members.filter((m) => {
                    const v = map!.versions.find((x) => x.id === m.version_id);
                    return v?.alignment && v.alignment.verdict !== "no_match" && fitOf(m).ok;
                  });
                  const chosenSrc = add[d.id];
                  return (
                    <div key={d.id} className="rounded border border-zinc-800 p-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-zinc-800 px-1 text-[10px] text-zinc-300">{lang(d.lang)}</span>
                        <span className="text-zinc-100">{d.name}</span>
                        {sources.length > 0 ? (
                          <label className="ml-auto text-zinc-300"><input type="checkbox" checked={!!chosenSrc}
                            onChange={(e) => setAdd({ ...add, [d.id]: e.target.checked ? sources[0] : null })} /> přidat</label>
                        ) : <span className="ml-auto text-[11px] text-red-300">k referenci nesedí — nejde přidat</span>}
                      </div>
                      {d.members.map((m) => {
                        const fit = fitOf(m);
                        const v = map!.versions.find((x) => x.id === m.version_id);
                        const usable = sources.includes(m);
                        return (
                          <div key={`${m.version_id}:${m.track}`} className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                            {chosenSrc && sources.length > 1 && usable && (
                              <input type="radio" name={`src-${d.id}`} checked={chosenSrc === m} onChange={() => setAdd({ ...add, [d.id]: m })} />
                            )}
                            <span className={chosenSrc === m ? "text-green-300" : ""}>{trackLabel(m)}</span>
                            <span className="text-zinc-600">z {v?.quality} ({versionName(m.version_id)})</span>
                            {!usable && <span className="rounded bg-red-900/50 px-1 text-red-200">✗ nesedí</span>}
                            {usable && (() => {
                              const b = fitBadge(fit.verdict ?? v?.alignment?.verdict, fit.result_id ? 0 : fit.delta, !!fit.result_id);
                              return <span title={b.tip} className={`rounded px-1 ${b.cls}`}>{b.text}</span>;
                            })()}
                            {usable && <button onClick={() => previewMember(m)} disabled={clipBusy} className="text-violet-300 hover:text-violet-200 disabled:opacity-40">▶ ukázka</button>}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {clipBusy && <p className="text-xs text-violet-300 animate-pulse">Připravuji ukázku…</p>}
        {clip && (
          <div className="space-y-2 rounded border border-zinc-800 p-2">
            <p className="text-[11px] text-zinc-400">{clip.label}</p>
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video key={clip.name} src={audioPreviewUrl(clip.name)} controls autoPlay className="w-full max-h-72 rounded bg-black" />
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {[0.15, 0.4, 0.7].map((f) => (
                <button key={f} disabled={clipBusy} className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
                  onClick={() => clip.resultId ? retryClip(clip.trial, (map?.duration ?? 0) * f)
                    : clip.track !== undefined && previewTarget(clip.track, false, (map?.duration ?? 0) * f)}>
                  jiné místo ({Math.round(f * 100)} %)
                </button>
              ))}
              {clip.resultId && (
                <>
                  <span className="ml-2 text-zinc-400">Doladit:</span>
                  {[-200, -50, 50, 200].map((d) => (
                    <button key={d} disabled={clipBusy} onClick={() => retryClip(clip.trial + d)}
                      className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300 hover:border-zinc-500 disabled:opacity-40">
                      {d > 0 ? "+" : ""}{d} ms
                    </button>
                  ))}
                  <span className="text-zinc-400">zkouším {clip.trial > 0 ? "+" : ""}{clip.trial} ms (uloženo {clip.saved} ms)</span>
                  {clip.trial !== 0 && (
                    <button onClick={async () => {
                      try {
                        await setAudioAdjust(clip.resultId!, clip.saved + clip.trial);
                        setClip({ ...clip, saved: clip.saved + clip.trial, trial: 0 });
                      } catch (e) { setError(e instanceof Error ? e.message : "Uložení selhalo"); }
                    }} className="rounded bg-violet-600 px-2 py-0.5 text-white hover:bg-violet-500"
                      title="Platí pro všechny stopy změřené tímto měřením (u verze = všechny její stopy se stejným časováním)">
                      Uložit posun
                    </button>
                  )}
                  <span className="text-[10px] text-zinc-500">+ = zvuk později</span>
                </>
              )}
            </div>
          </div>
        )}
      </section>

      {/* 3. save */}
      {mapOk && !running && (
        <section className="rounded-lg border border-violet-900 bg-violet-950/20 p-4 space-y-3 text-xs">
          <h2 className="uppercase tracking-wide text-zinc-400">3. Výsledný soubor</h2>
          {planError && <p className="text-red-400">{planError}</p>}
          {plan && (
            <table className="w-full">
              <thead>
                <tr className="text-left text-zinc-500 border-b border-zinc-800">
                  <th className="py-1 pr-2 font-normal">#</th>
                  <th className="py-1 pr-3 font-normal">Název v souboru</th>
                  <th className="py-1 pr-3 font-normal">Stopa</th>
                  <th className="py-1 pr-3 font-normal">Odkud</th>
                  <th className="py-1 font-normal" title="Stopa, kterou přehrávač pustí sám (MKV příznak default)">Výchozí</th>
                </tr>
              </thead>
              <tbody>
                {plan.tracks.map((p, i) => (
                  <tr key={p.key} className="border-b border-zinc-800/50">
                    <td className="py-1 pr-2 text-zinc-500">{i + 1}</td>
                    <td className="py-1 pr-3">
                      <div className="flex items-center gap-1">
                        <input value={names[p.key] ?? p.name} placeholder="(bez názvu)" maxLength={120}
                          title="Název stopy v souboru — můžeš ho přepsat"
                          onChange={(e) => setNames({ ...names, [p.key]: e.target.value })}
                          className={`w-full min-w-[10rem] rounded border bg-zinc-900 px-1.5 py-0.5 ${
                            p.custom ? "border-violet-700 text-violet-100" : "border-zinc-800 text-zinc-100"}`} />
                        {names[p.key] !== undefined && (
                          <button title="Vrátit automatický název" className="text-zinc-500 hover:text-zinc-300"
                            onClick={() => { const n = { ...names }; delete n[p.key]; setNames(n); }}>↺</button>
                        )}
                      </div>
                    </td>
                    <td className="py-1 pr-3 text-zinc-400 whitespace-nowrap">
                      {lang(p.language)} {channels(p.channels)} {(p.codec || "?").toUpperCase()}{p.bitrate ? ` ${Math.round(p.bitrate / 1000)} kbps` : ""}
                    </td>
                    <td className="py-1 pr-3 text-zinc-400">
                      {p.origin === "keep" ? (p.renamed ? "ponechaná, nový název" : "ponechaná")
                        : p.origin === "fix" ? "opravená (posunutá na referenci)"
                        : `z ${p.from}${p.reencoded ? " — přepočítaná do AC3" : ""}`}
                    </td>
                    <td className="py-1">
                      <input type="radio" name="default-track" checked={p.default} onChange={() => setDefaultKey(p.key)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {plan?.reference_dropped && <p className="text-zinc-500">Referenční stopa poslouží ke kontrole nových stop a pak se odebere.</p>}
          {anyChange ? (
            <p className="text-zinc-300">
              {[picks.length && `přidat ${picks.length}`, fix.length && `opravit ${fix.length}`, drop.length && `odebrat ${drop.length}`,
                defaultChanged && "změnit výchozí stopu", renamedByUser && `přejmenovat ${renamedByUser}`].filter(Boolean).join(" · ")}.
              Nové a opravené stopy se před použitím zkontrolují proti referenci.
            </p>
          ) : <p className="text-zinc-500">Zatím beze změn — přidej, oprav nebo odeber stopy výš, nebo zvol jinou výchozí stopu.</p>}
          {!anyChange ? null : confirm ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-orange-200">{confirm === "replace" ? "Nahradit soubor této verze upraveným?" : "Uložit jako novou verzi?"}</span>
              <button onClick={() => { const m = confirm; setConfirm(null); run(() => applyFilmMap(map!.id, edits, m)); }}
                className="rounded bg-violet-600 px-3 py-1 text-white hover:bg-violet-500">Ano</button>
              <button onClick={() => setConfirm(null)} className="text-zinc-400 hover:text-zinc-200">Zrušit</button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {can("library.delete") && (
                <button onClick={() => setConfirm("replace")} className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500">Upravit tuto verzi</button>
              )}
              {can("library.edit") && (
                <button onClick={() => setConfirm("version")} className="rounded border border-violet-700 px-3 py-1 text-violet-200 hover:bg-violet-900/40">Uložit jako novou verzi</button>
              )}
            </div>
          )}
        </section>
      )}

      {error && <p className="text-xs text-red-400">{error}</p>}
      {done && (
        <div className="space-y-2 text-xs">
          <p className="text-green-300">{done}</p>
          {can("library.delete") && versions.filter((v) => v.id !== target).length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-zinc-400">Ostatní verze už nepotřebuješ?</span>
              {versions.filter((v) => v.id !== target).map((v) => (
                <button key={v.id} className="rounded border border-red-900 px-2 py-0.5 text-red-300 hover:border-red-700"
                  onClick={async () => {
                    if (!window.confirm(`Smazat z disku ${versionName(v.id)}?`)) return;
                    try {
                      await deleteVersionFile(v.id);
                      await loadVersions();
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "Smazání selhalo");
                    }
                  }}>
                  Smazat {versionName(v.id)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </main>
  );
}

/** The tracks of the chosen version (read from the file — before any measurement). */
function RefTrackPicker({ movieId, value, disabled, onChange }: {
  movieId: number; value: number | null; disabled: boolean; onChange: (t: number) => void;
}) {
  const [audio, setAudio] = useState<AudioTrackInfo[] | null>(null);
  useEffect(() => {
    let alive = true;
    setAudio(null);
    getAudioTracks(movieId)
      .then((t) => alive && setAudio(t.audio)).catch(() => alive && setAudio([]));
    return () => { alive = false; };
  }, [movieId]);
  if (!audio) return <p className="text-xs text-zinc-500">Čtu stopy…</p>;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-zinc-400 text-xs">Referenční stopa:</span>
      <select value={value ?? ""} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))}
        className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200 max-w-full">
        {audio.map((a) => <option key={a.index} value={a.index}>{a.index + 1}. {trackLabel(a)}</option>)}
      </select>
    </div>
  );
}
