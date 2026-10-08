"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthGate";
import SubtitlesPanel from "@/components/SubtitlesPanel";
import { EpisodeDetail, EpisodeVersion, deleteEpisodeFile, getEpisodeDetail, setAudioLanguage, setFileSpan, setNoDub } from "@/lib/api";

/**
 * The window of an owned episode (show page): its files with sound and subtitles, the player, subtitles from
 * OpenSubtitles, deleting a version — like a film's window in the library (backend library/episodes.py).
 */

function videoLabel(m: EpisodeVersion["media"]): string {
  const h = m.height || 0, w = m.width || 0;
  const res = w >= 3200 || h >= 1600 ? "2160p" : w >= 1800 || h >= 900 ? "1080p" : w >= 1200 || h >= 650 ? "720p" : h ? `${h}p` : "";
  return [res, m.video_codec, m.hdr && m.hdr !== "SDR" ? m.hdr : "", m.bitrate ? `${(m.bitrate / 1e6).toFixed(1)} Mb/s` : ""]
    .filter(Boolean).join(" · ");
}

const LANGS: [string, string][] = [["cs", "CZ"], ["sk", "SK"], ["en", "EN"], ["de", "DE"], ["pl", "PL"], ["hu", "HU"], ["fr", "FR"], ["ja", "JA"]];

function trackLabel(a: { codec?: string; channels?: number }): string {
  const ch = a.channels ? (a.channels > 2 ? `${a.channels - 1}.1` : `${a.channels}.0`) : "";
  return [ch, a.codec].filter(Boolean).join(" ");
}

function audioLabel(a: { lang?: string; codec?: string; channels?: number }): string {
  const ch = a.channels ? (a.channels > 2 ? `${a.channels - 1}.1` : `${a.channels}.0`) : "";
  return [(a.lang || "?").toUpperCase(), ch, a.codec].filter(Boolean).join(" ");
}

export default function EpisodeWindow({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const [data, setData] = useState<EpisodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [langMsg, setLangMsg] = useState<string | null>(null);

  // the user heard it: the track's language into the file (MKV, MP4) and Lumina (AVI)
  async function setLang(path: string, track: number, lang: string) {
    setLangMsg("zapisuji…");
    try {
      const r = await setAudioLanguage([], lang, track, [path]);
      const d = r.done[0];
      setLangMsg(r.errors.length ? r.errors[0] : (d?.written ? "uloženo do souboru" : "uloženo v Lumině (AVI jazyk neukládá)")
        + (d?.renamed ? ` · přejmenováno na ${d.renamed}` : ""));
      await load();
      onChanged();
    } catch (e) {
      setLangMsg(e instanceof Error ? e.message : "Chyba");
    }
  }

  const load = () => getEpisodeDetail(id).then(setData).catch((e) => setError(e instanceof Error ? e.message : "Chyba"));
  useEffect(() => { setData(null); setError(null); load(); /* eslint-disable-next-line */ }, [id]);

  async function remove(path: string) {
    setBusy(true);
    try {
      await deleteEpisodeFile(id, path);
      setConfirm(null);
      onChanged();
      if (data && data.versions.length > 1) await load(); else onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(false);
    }
  }

  const code = data ? `S${String(data.season).padStart(2, "0")}E${String(data.episode).padStart(2, "0")}` : "";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="mx-2 max-h-[92vh] w-full max-w-3xl space-y-4 overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-900 p-4 sm:mx-4 sm:p-6"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-lg font-semibold text-zinc-100">
              {data ? <>{data.show_title} · <span className="font-mono">{code}</span></> : "Díl"}
            </h3>
            {data?.episode_title && <p className="text-sm text-zinc-400">{data.episode_title}</p>}
          </div>
          <button onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-300">Zavřít</button>
        </div>
        {error && <p className="text-sm text-red-400">{error}</p>}
        {!data && !error && <p className="animate-pulse text-sm text-zinc-500">Načítám…</p>}
        {data && (
          <>
            <div className="space-y-2">
              {data.versions.map((v) => (
                <div key={v.file_path} className={`rounded-lg border p-3 text-xs ${v.current ? "border-violet-800 bg-violet-950/20" : "border-zinc-800"}`}>
                  {v.part && <p className="mb-1 text-sm font-medium text-violet-200">Část {v.part}</p>}
                  <p className="break-all text-zinc-200">{v.filename}</p>
                  <p className="mt-0.5 break-all text-[11px] text-zinc-600">{v.file_path}</p>
                  <p className="mt-1 text-zinc-400">
                    {[videoLabel(v.media), `${(v.size / 1e9).toFixed(2)} GB`,
                      v.media.duration_s ? `${Math.round(v.media.duration_s / 60)} min` : ""].filter(Boolean).join(" · ")}
                    {v.current && !v.part && data.versions.length > 1 && <span className="ml-2 text-violet-300">v knihovně</span>}
                  </p>
                  {!!v.media.audio?.length && (can("library.edit") ? (
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-zinc-400">
                      <span>🔊</span>
                      {v.media.audio.map((a, i) => (
                        <span key={i} className="flex items-center gap-1">
                          <select value={a.lang || ""} title="Jazyk stopy — když nesedí, vyber správný (zapíše se do souboru)"
                            onChange={(e) => e.target.value && setLang(v.file_path, i, e.target.value)}
                            className={`rounded border bg-zinc-950 px-1 py-0.5 text-[11px] uppercase ${a.lang ? "border-zinc-700 text-zinc-200" : "border-sky-700 text-sky-300"}`}>
                            {!a.lang && <option value="">?</option>}
                            {a.lang && !LANGS.some(([c]) => c === a.lang) && <option value={a.lang}>{a.lang}</option>}
                            {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                          </select>
                          <span>{trackLabel(a)}</span>
                        </span>
                      ))}
                      {langMsg && <span className="text-[11px] text-violet-300">{langMsg}</span>}
                    </div>
                  ) : (
                    <p className="mt-0.5 text-zinc-400">🔊 {v.media.audio.map(audioLabel).join(" · ")}</p>
                  ))}
                  {v.current && (v.part || 1) === 1 && can("library.edit") && (
                    <label className="mt-1 flex w-fit cursor-pointer items-center gap-1.5 text-[11px] text-zinc-400"
                      title="Díl bez CZ zvuku se jinak počítá jako „čeká na dabing“ — u dílů, které dabing nikdy nedostaly, na něj nečekat">
                      <input type="checkbox" checked={!!data.no_dub}
                        onChange={async (e) => { await setNoDub(data.show_tmdb_id, data.season, data.episode, e.target.checked); await load(); onChanged(); }} />
                      CZ dabing tohoto dílu nevznikl
                    </label>
                  )}
                  {v.current && !v.part && data.in_file && data.in_file.choices.length > 1 && can("library.edit") && (
                    <label className="mt-1 flex w-fit items-center gap-1.5 text-[11px] text-zinc-400"
                      title="Když soubor obsahuje víc dílů (dvojdílná premiéra v jednom souboru) — počítá se za všechny a přejmenuje se na „E01-E02“">
                      Díly v souboru:
                      <select value={data.in_file.episodes.length}
                        onChange={async (e) => {
                          const r = await setFileSpan(v.file_path, Number(e.target.value));
                          setLangMsg(r.renamed ? `přejmenováno na ${r.renamed}` : "uloženo");
                          await load(); onChanged();
                        }}
                        className={`rounded border bg-zinc-950 px-1 py-0.5 text-[11px] ${data.in_file.suggested && data.in_file.said == null ? "border-amber-600 text-amber-200" : "border-zinc-700 text-zinc-200"}`}>
                        {data.in_file.choices.map((c) => <option key={c.count} value={c.count}>{c.label}</option>)}
                      </select>
                      {data.in_file.suggested && data.in_file.said == null && (
                        <span className="text-amber-300">délka sedí na dva díly — obsahuje i E{String(data.in_file.suggested).padStart(2, "0")}?</span>
                      )}
                    </label>
                  )}
                  {!!v.media.subtitles?.length && (
                    <p className="mt-0.5 text-zinc-500">💬 v souboru: {v.media.subtitles.map((l) => (l === "und" ? "bez jazyka" : l.toUpperCase())).join(", ")}</p>
                  )}
                  {!Object.keys(v.media).length && <p className="mt-0.5 text-zinc-600">MediaInfo zatím není (proběhne při skenu knihovny).</p>}
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    {v.current && can("player") && (
                      <Link href={`/play?id=${data.id}&kind=episode${v.part ? `&part=${v.part}` : ""}`}
                        className="rounded bg-violet-700 px-2.5 py-1 font-medium text-white hover:bg-violet-600">▶ Přehrát</Link>
                    )}
                    {can("library.delete") && (confirm === v.file_path ? (
                      <span className="flex items-center gap-2 text-red-300">
                        Smazat soubor z disku natrvalo (i jeho titulky)?
                        <button disabled={busy} onClick={() => remove(v.file_path)} className="rounded bg-red-700 px-2 py-0.5 text-white hover:bg-red-600">
                          {busy ? "Mažu…" : "Smazat"}
                        </button>
                        <button onClick={() => setConfirm(null)} className="text-zinc-400 hover:text-zinc-200">Ne</button>
                      </span>
                    ) : (
                      <button onClick={() => setConfirm(v.file_path)} className="text-red-400/80 hover:text-red-300">Smazat tuto verzi</button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <SubtitlesPanel movieId={data.id} kind="episode" />
            <p className="text-[11px] text-zinc-600">Editor zvuku (přenos dabingu mezi verzemi) zatím umí jen filmy.</p>
          </>
        )}
      </div>
    </div>
  );
}
