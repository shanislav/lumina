"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AudioSyncJob, FilmMap, FilmMapMember, LibraryMovie,
  applyFilmMap, audioPreviewUrl, deleteVersionFile, formatSize, getAudioSyncJob, getFilmMap, makeAudioPreview,
  makeTrackPreview, startFilmMap,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

const LANG_LABEL: Record<string, string> = { cs: "CZ", sk: "SK", en: "EN", de: "DE", fr: "FR", pl: "PL", hu: "HU", ru: "RU" };

function memberLabel(m: FilmMapMember): string {
  const ch = m.channels === 6 ? "5.1" : m.channels === 8 ? "7.1" : m.channels === 2 ? "2.0" : m.channels === 1 ? "1.0" : `${m.channels}ch`;
  return `${(m.codec || "?").toUpperCase()} ${ch}`;
}

function alignLabel(a: FilmMap["versions"][number]["alignment"]): { text: string; cls: string } {
  if (!a) return { text: "cíl", cls: "bg-violet-900/60 text-violet-200" };
  if (a.verdict === "constant") return { text: "✓ sedí", cls: "bg-emerald-900/60 text-emerald-200" };
  if (a.verdict === "speed") return { text: "jiná rychlost — přepočítá se", cls: "bg-amber-900/50 text-amber-200" };
  if (a.verdict === "cuts") return { text: "jiný střih — poskládá se", cls: "bg-amber-900/50 text-amber-200" };
  return { text: "✗ zvuk nesedí", cls: "bg-red-900/50 text-red-200" };
}

/** All dubs of a film across its versions: which are the same, what the target lacks, add in one go. */
export default function FilmAudioMap({ versions, onChanged }: { versions: LibraryMovie[]; onChanged?: () => void }) {
  const { can } = useAuth();
  const tmdbId = versions[0].tmdb_id;
  const best = [...versions].sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0))[0];
  const [target, setTarget] = useState(best.id);
  const [map, setMap] = useState<FilmMap | null>(null);
  const [job, setJob] = useState<AudioSyncJob | null>(null);
  const [add, setAdd] = useState<Record<number, FilmMapMember | null>>({});   // dub id → source
  const [drop, setDrop] = useState<number[]>([]);                            // target tracks
  const [clip, setClip] = useState<{ name: string; label: string } | null>(null);
  const [clipBusy, setClipBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [confirm, setConfirm] = useState<"replace" | "version" | null>(null);
  const [deleted, setDeleted] = useState<number[]>([]);

  const load = useCallback(() => getFilmMap(tmdbId).then((m) => {
    setMap(m);
    if (m) setTarget(m.target_id);
  }).catch(() => {}), [tmdbId]);

  useEffect(() => {
    load();
    getAudioSyncJob().then((j) => j.running && (j.kind === "map" || j.kind === "apply") && setJob(j)).catch(() => {});
  }, [load]);

  useEffect(() => {
    if (!job?.running) return;
    const t = setTimeout(async () => {
      try {
        const j = await getAudioSyncJob();
        setJob(j);
        if (!j.running) {
          if (j.error) setError(j.error);
          else if (j.kind === "map") load();
          else if (j.kind === "apply") {
            const added = j.report?.added?.length ? ` Přidáno: ${j.report.added.join(", ")}.` : "";
            setDone((j.imported ? "✓ Hotovo — soubor je v knihovně." : `Hotovo, ale knihovna soubor nepřevzala (${j.path ?? ""}).`) + added);
            setAdd({});
            setDrop([]);
            onChanged?.();
          }
        }
      } catch { /* next tick */ }
    }, 2000);
    return () => clearTimeout(t);
  }, [job, load, onChanged]);

  const cols = useMemo(() => {
    if (!map) return [];
    const t = map.versions.find((v) => v.id === map.target_id);
    return t ? [t, ...map.versions.filter((v) => v.id !== map.target_id)] : map.versions;
  }, [map]);

  const run = async (start: () => Promise<AudioSyncJob>) => {
    setError("");
    setDone("");
    try {
      setJob(await start());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se spustit");
    }
  };

  async function preview(m: FilmMapMember) {
    if (!map) return;
    setClipBusy(true);
    setError("");
    try {
      const at = map.duration * 0.4;
      const v = map.versions.find((x) => x.id === m.version_id)!;
      const r = m.version_id === map.target_id
        ? await makeTrackPreview(map.target_id, m.track, at)
        : await makeAudioPreview(v.alignment!.result_id, at, 0, m.track);
      setClip({ name: r.name, label: `${memberLabel(m)}${m.title ? ` „${m.title}“` : ""} z ${v.quality} — na obraze cíle` });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ukázka selhala");
    } finally {
      setClipBusy(false);
    }
  }

  const running = !!job?.running;
  const needsMap = !map || map.stale || map.target_id !== target;
  const picks = Object.values(add).filter((m): m is FilmMapMember => !!m);
  const targetLabel = versions.find((v) => v.id === target)?.quality_summary || versions.find((v) => v.id === target)?.quality;

  return (
    <details className="rounded-lg border border-zinc-800 bg-zinc-950/40" open={running || undefined}>
      <summary className="cursor-pointer select-none px-3 py-2 text-xs uppercase tracking-wide text-zinc-400 hover:text-zinc-200">
        Zvuk filmu — dabingy ve verzích{map && !map.stale ? ` (${map.dubs.length})` : ""}
      </summary>
      <div className="space-y-3 px-3 pb-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-zinc-400">Cíl (obraz, který chceš nechat):</span>
          <select value={target} onChange={(e) => { setTarget(Number(e.target.value)); setAdd({}); setDrop([]); }} disabled={running}
            className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200 max-w-[18rem]">
            {versions.map((v) => <option key={v.id} value={v.id}>{v.quality_summary || v.quality} · {formatSize(v.file_size)}</option>)}
          </select>
          {needsMap && (
            <button onClick={() => run(() => startFilmMap(tmdbId, target))} disabled={running}
              className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
              {map?.stale ? "Verze se změnily — zmapovat znovu" : "Zmapovat zvuk"}
            </button>
          )}
          {needsMap && !running && <span className="text-zinc-500">porovná všechny stopy všech verzí (asi minuta na verzi)</span>}
        </div>

        {running && (
          <p className="text-xs text-violet-300 animate-pulse">
            {job?.kind === "map"
              ? job.phase === "compare" ? `Porovnávám stopy ${job.done}/${job.total}` : `Srovnávám verze s cílem ${(job.done ?? 0) + 1}/${job.total}`
              : job?.phase === "mux" ? `Skládám soubor ${job.done} %` : job?.phase === "verify" ? "Kontroluji výsledek…"
              : job?.phase === "import" ? "Předávám knihovně…" : "Připravuji stopy…"}
          </p>
        )}

        {map && !needsMap && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-zinc-400 border-b border-zinc-800">
                  <th className="py-1.5 pr-3 font-medium">Dabing</th>
                  {cols.map((v) => {
                    const a = alignLabel(v.alignment);
                    return (
                      <th key={v.id} className="py-1.5 px-2 font-medium whitespace-nowrap">
                        <div className={v.id === map.target_id ? "text-violet-200" : "text-zinc-300"}>{v.quality}</div>
                        <span className={`rounded px-1 py-0.5 text-[10px] font-normal ${a.cls}`}>{a.text}</span>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {map.dubs.map((d) => {
                  const inTarget = d.members.filter((m) => m.version_id === map.target_id);
                  const sources = d.members.filter((m) => {
                    const v = map.versions.find((x) => x.id === m.version_id);
                    return m.version_id !== map.target_id && v?.alignment && v.alignment.verdict !== "no_match";
                  });
                  const chosen = add[d.id];
                  return (
                    <tr key={d.id} className="border-b border-zinc-800/50 align-top">
                      <td className="py-1.5 pr-3 whitespace-nowrap">
                        <span className="mr-1 rounded bg-zinc-800 px-1 text-[10px] text-zinc-300">{LANG_LABEL[d.lang] ?? (d.lang || "?").toUpperCase()}</span>
                        <span className="text-zinc-100">{d.name}</span>
                      </td>
                      {cols.map((v) => {
                        const here = d.members.filter((m) => m.version_id === v.id);
                        const isTarget = v.id === map.target_id;
                        return (
                          <td key={v.id} className={`py-1.5 px-2 ${isTarget ? "bg-violet-950/20" : ""}`}>
                            {here.map((m) => (
                              <div key={m.track} className="flex items-center gap-1.5 whitespace-nowrap">
                                {isTarget && can("library.delete") && (
                                  <input type="checkbox" title="Odebrat z cíle" checked={drop.includes(m.track)}
                                    onChange={(e) => setDrop(e.target.checked ? [...drop, m.track] : drop.filter((x) => x !== m.track))} />
                                )}
                                <span className={isTarget && drop.includes(m.track) ? "text-red-300 line-through" : isTarget ? "text-emerald-200" : chosen === m ? "text-green-300" : "text-zinc-300"}>
                                  {isTarget ? "✓ " : ""}{memberLabel(m)}
                                </span>
                                <button onClick={() => preview(m)} disabled={clipBusy} title="Ukázka na obraze cíle"
                                  className="text-violet-300 hover:text-violet-200 disabled:opacity-40">▶</button>
                              </div>
                            ))}
                            {isTarget && !inTarget.length && (
                              sources.length ? (
                                <label className="flex items-center gap-1.5 text-zinc-300 whitespace-nowrap">
                                  <input type="checkbox" checked={!!chosen}
                                    onChange={(e) => setAdd({ ...add, [d.id]: e.target.checked ? sources[0] : null })} />
                                  přidat
                                  {chosen && sources.length > 1 && (
                                    <select value={`${chosen.version_id}:${chosen.track}`}
                                      onChange={(e) => setAdd({ ...add, [d.id]: sources.find((s) => `${s.version_id}:${s.track}` === e.target.value) ?? null })}
                                      className="rounded bg-zinc-800 border border-zinc-700 px-1 py-0.5 text-[11px]">
                                      {sources.map((s) => (
                                        <option key={`${s.version_id}:${s.track}`} value={`${s.version_id}:${s.track}`}>
                                          z {map.versions.find((x) => x.id === s.version_id)?.quality} ({memberLabel(s)})
                                        </option>
                                      ))}
                                    </select>
                                  )}
                                </label>
                              ) : <span className="text-zinc-600">—</span>
                            )}
                            {!isTarget && !here.length && <span className="text-zinc-700">—</span>}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {clipBusy && <p className="text-xs text-violet-300 animate-pulse">Připravuji ukázku…</p>}
        {clip && (
          <div className="space-y-1">
            <p className="text-[11px] text-zinc-400">{clip.label}</p>
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video key={clip.name} src={audioPreviewUrl(clip.name)} controls autoPlay className="w-full max-h-64 rounded bg-black" />
          </div>
        )}

        {map && !needsMap && (picks.length > 0 || drop.length > 0) && !running && (
          <div className="rounded border border-zinc-800 p-2 text-xs space-y-2">
            <p className="text-zinc-300">
              Do „{targetLabel}“: {picks.length ? `přidat ${picks.length} ${picks.length === 1 ? "dabing" : "dabingy"}` : ""}
              {picks.length && drop.length ? ", " : ""}{drop.length ? `odebrat ${drop.length} ${drop.length === 1 ? "stopu" : "stopy"}` : ""}.
              Výsledek se před použitím zkontroluje.
            </p>
            {confirm ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-orange-200">{confirm === "replace" ? "Nahradit cílový soubor novým?" : "Uložit jako novou verzi?"}</span>
                <button onClick={() => { const m = confirm; setConfirm(null); run(() => applyFilmMap(map.id, picks, drop, m)); }}
                  className="rounded bg-violet-600 px-3 py-1 text-white hover:bg-violet-500">Ano</button>
                <button onClick={() => setConfirm(null)} className="text-zinc-400 hover:text-zinc-200">Zrušit</button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {can("library.delete") && (
                  <button onClick={() => setConfirm("replace")} className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500">
                    Upravit cílový soubor
                  </button>
                )}
                {can("library.edit") && !drop.length && (
                  <button onClick={() => setConfirm("version")} className="rounded border border-violet-700 px-3 py-1 text-violet-200 hover:bg-violet-900/40">
                    Uložit jako novou verzi
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {error && <p className="text-xs text-red-400">{error}</p>}
        {done && (
          <div className="space-y-1 text-xs">
            <p className="text-green-300">{done}</p>
            {can("library.delete") && versions.filter((v) => v.id !== target && !deleted.includes(v.id)).length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-zinc-400">Ostatní verze už nepotřebuješ?</span>
                {versions.filter((v) => v.id !== target && !deleted.includes(v.id)).map((v) => (
                  <button key={v.id} className="rounded border border-red-900 px-2 py-0.5 text-red-300 hover:border-red-700"
                    onClick={async () => {
                      if (window.confirm(`Smazat z disku ${v.quality_summary || v.quality} (${formatSize(v.file_size)})?`)) {
                        try {
                          await deleteVersionFile(v.id);
                          setDeleted([...deleted, v.id]);
                          onChanged?.();
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "Smazání selhalo");
                        }
                      }
                    }}>
                    Smazat {v.quality_summary || v.quality}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <p className="text-[10px] text-zinc-600">
          Stejný dabing (i 5.1 a 2.0, jiné kódování) je jeden řádek — Lumina ho pozná podle obsahu. Když je dabing ve víc verzích,
          předvybere ten lepší zdroj (víc kanálů).
        </p>
      </div>
    </details>
  );
}
