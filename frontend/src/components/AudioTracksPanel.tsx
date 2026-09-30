"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AudioSyncJob, AudioTrackInfo, LibraryMovie, TrackCheck,
  audioPreviewUrl, fixTrack, getAudioSyncJob, getAudioTracks, getTrackChecks, makeTrackPreview,
  startStripTracks, startTrackCheck,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

function trackLabel(t: AudioTrackInfo): string {
  return [(t.language || "?").toUpperCase(), t.title, t.codec, t.channels ? `${t.channels}ch` : ""].filter(Boolean).join(" · ");
}

function clock(s: number): string {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function checkBadge(c: TrackCheck | undefined): { text: string; cls: string } | null {
  if (!c) return null;
  if (c.fits) return { text: "✓ sedí", cls: "bg-emerald-900/60 text-emerald-200" };
  if (c.verdict === "no_match") return { text: "? nejde porovnat (komentář, jiný film…)", cls: "bg-zinc-700 text-zinc-200" };
  if (c.verdict === "cuts") return { text: "⚠ jiný střih", cls: "bg-orange-900/60 text-orange-200" };
  if (c.verdict === "speed") return { text: `⚠ jiná rychlost (×${c.speed.toFixed(4)})`, cls: "bg-orange-900/60 text-orange-200" };
  return { text: `⚠ posun ${c.offset > 0 ? "+" : ""}${c.offset.toFixed(2)} s`, cls: "bg-orange-900/60 text-orange-200" };
}

/** The audio tracks of one file: do they fit the picture, listen, fix, remove — and play the film. */
export default function AudioTracksPanel({ movie, onChanged }: { movie: LibraryMovie; onChanged?: () => void }) {
  const { can } = useAuth();
  const [tracks, setTracks] = useState<AudioTrackInfo[]>([]);
  const [duration, setDuration] = useState(0);
  const [checks, setChecks] = useState<TrackCheck[]>([]);
  const [reference, setReference] = useState(0);
  const [drop, setDrop] = useState<number[]>([]);
  const [job, setJob] = useState<AudioSyncJob | null>(null);
  const [clip, setClip] = useState<{ name: string; label: string } | null>(null);
  const [busyClip, setBusyClip] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [confirmDrop, setConfirmDrop] = useState(false);

  const loadChecks = useCallback(() => getTrackChecks(movie.id).then(setChecks).catch(() => {}), [movie.id]);

  useEffect(() => {
    getAudioTracks(movie.id).then((t) => { setTracks(t.audio); setDuration(t.duration); }).catch((e) => setError(e.message));
    loadChecks();
    getAudioSyncJob().then((j) => j.running && setJob(j)).catch(() => {});
  }, [movie.id, loadChecks]);

  useEffect(() => {
    if (!job?.running) return;
    const t = setTimeout(async () => {
      try {
        const j = await getAudioSyncJob();
        setJob(j);
        if (!j.running) {
          if (j.error) setError(j.error);
          else if (j.kind === "check") loadChecks();
          else if (j.kind === "strip" || j.kind === "transfer") {
            setDone(j.imported ? "✓ Hotovo — soubor v knihovně je upravený" : `Hotovo, ale knihovna soubor nepřevzala (${j.path ?? ""})`);
            onChanged?.();
          }
        }
      } catch { /* next tick */ }
    }, 2000);
    return () => clearTimeout(t);
  }, [job, loadChecks, onChanged]);

  const run = async (start: () => Promise<AudioSyncJob>) => {
    setError("");
    setDone("");
    try {
      setJob(await start());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se spustit");
    }
  };

  async function preview(track: number, fix?: TrackCheck) {
    setBusyClip(true);
    setError("");
    try {
      const at = duration * 0.4;
      const r = await makeTrackPreview(movie.id, track, at, fix?.result_id);
      setClip({ name: r.name, label: `${trackLabel(tracks[track])}${fix ? " — opravená" : ""} · od ${clock(r.at)}` });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ukázka selhala");
    } finally {
      setBusyClip(false);
    }
  }

  const byTrack = Object.fromEntries(checks.map((c) => [c.track, c]));
  const running = !!job?.running;

  return (
    <details className="rounded-lg border border-zinc-800 bg-zinc-950/40">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs uppercase tracking-wide text-zinc-400 hover:text-zinc-200">
        Zvukové stopy ({tracks.length || "…"}) — kontrola, ukázky, odebrání
      </summary>
      <div className="space-y-3 px-3 pb-3">
        {can("player") && (
          <Link href={`/play?id=${movie.id}`} className="inline-block rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500">
            ▶ Přehrát film v prohlížeči
          </Link>
        )}

        <ul className="space-y-1.5">
          {tracks.map((t) => {
            const c = byTrack[t.index];
            const badge = t.index === reference && tracks.length > 1 ? { text: "reference", cls: "bg-violet-900/60 text-violet-200" } : checkBadge(c);
            return (
              <li key={t.index} className="flex flex-wrap items-center gap-2 text-xs">
                {can("library.delete") && (
                  <input type="checkbox" title="Odebrat tuto stopu" checked={drop.includes(t.index)}
                    onChange={(e) => setDrop(e.target.checked ? [...drop, t.index] : drop.filter((x) => x !== t.index))} />
                )}
                <span className="text-zinc-500">{t.index + 1}.</span>
                <span className="text-zinc-200">{trackLabel(t)}</span>
                {badge && <span className={`rounded px-1.5 py-0.5 text-[10px] ${badge.cls}`}>{badge.text}</span>}
                <button onClick={() => preview(t.index)} disabled={busyClip}
                  className="text-violet-300 hover:text-violet-200 disabled:opacity-40">▶ ukázka</button>
                {c && !c.fits && c.verdict !== "no_match" && (
                  <>
                    <button onClick={() => preview(t.index, c)} disabled={busyClip}
                      className="text-green-300 hover:text-green-200 disabled:opacity-40">▶ opravená</button>
                    {can("library.delete") && (
                      <button onClick={() => run(() => fixTrack(c.result_id, t.index))} disabled={running}
                        className="rounded border border-green-800 px-2 py-0.5 text-green-200 hover:bg-green-950/40 disabled:opacity-40">
                        Opravit stopu
                      </button>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ul>

        {busyClip && <p className="text-xs text-violet-300 animate-pulse">Připravuji ukázku…</p>}
        {clip && (
          <div className="space-y-1">
            <p className="text-[11px] text-zinc-400">{clip.label}</p>
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video key={clip.name} src={audioPreviewUrl(clip.name)} controls autoPlay className="w-full max-h-64 rounded bg-black" />
          </div>
        )}

        {tracks.length > 1 && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-zinc-400" title="Stopa, o které víš, že sedí k obrazu (většinou původní jazyk filmu)">Reference:</span>
            <select value={reference} onChange={(e) => setReference(Number(e.target.value))} disabled={running}
              className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200 max-w-[16rem]">
              {tracks.map((t) => <option key={t.index} value={t.index}>{t.index + 1}. {trackLabel(t)}</option>)}
            </select>
            <button onClick={() => run(() => startTrackCheck(movie.id, reference))} disabled={running}
              className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
              Zkontrolovat, že stopy sedí
            </button>
          </div>
        )}

        {can("library.delete") && drop.length > 0 && (
          confirmDrop ? (
            <div className="rounded border border-red-800 bg-red-950/30 p-2 text-xs space-y-2">
              <p className="text-red-200">
                Odebrat {drop.length === 1 ? "stopu" : "stopy"} {drop.map((d) => d + 1).join(", ")} ze souboru?
                Soubor se přeskládá bez nich (nic se nepřekódovává) a nahradí původní.
              </p>
              <div className="flex gap-2">
                <button onClick={() => { setConfirmDrop(false); run(() => startStripTracks(movie.id, drop)); setDrop([]); }}
                  disabled={drop.length >= tracks.length}
                  className="rounded bg-red-700 px-3 py-1 text-white hover:bg-red-600 disabled:opacity-40">Ano, odebrat</button>
                <button onClick={() => setConfirmDrop(false)} className="text-zinc-400 hover:text-zinc-200">Zrušit</button>
              </div>
              {drop.length >= tracks.length && <p className="text-red-300">Aspoň jedna stopa musí zůstat.</p>}
            </div>
          ) : (
            <button onClick={() => setConfirmDrop(true)} disabled={running}
              className="rounded border border-red-900 px-3 py-1 text-xs text-red-300 hover:border-red-700 disabled:opacity-40">
              Odebrat vybrané stopy ({drop.length})
            </button>
          )
        )}

        {running && (
          <p className="text-xs text-violet-300 animate-pulse">
            {job?.kind === "check" ? `Kontroluji stopu ${(job.done ?? 0) + 1}/${job.total} (asi půl minuty na stopu)`
              : job?.phase === "mux" ? `Skládám soubor ${job.done ?? 0} %`
              : job?.phase === "verify" ? "Kontroluji výsledek…"
              : job?.phase === "import" ? "Předávám knihovně…" : "Pracuji…"}
          </p>
        )}
        {error && <p className="text-xs text-red-400">{error}</p>}
        {done && <p className="text-xs text-green-300">{done}</p>}
      </div>
    </details>
  );
}
