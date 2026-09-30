"use client";

import { useEffect, useState } from "react";
import {
  AudioSyncJob, AudioSyncResult, AudioTrackInfo, LibraryMovie,
  getAudioSyncJob, getAudioSyncResults, getAudioTracks, startAudioSync,
} from "@/lib/api";

const LOCAL = ["cs", "cze", "ces", "sk", "slo", "slk"];
const SELECT = "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs text-zinc-200 max-w-full";

function clock(s: number): string {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function trackLabel(t: AudioTrackInfo): string {
  return [(t.language || "?").toUpperCase(), t.codec, t.channels ? `${t.channels}ch` : "", t.title].filter(Boolean).join(" · ");
}

function speedLabel(speed: number): string {
  if (Math.abs(speed - 1) < 1e-4) return "stejná rychlost";
  const known: [number, string][] = [
    [23.976 / 25, "zrychlená PAL verze (25 fps) — zvuk je o 4,3 % rychlejší"],
    [25 / 23.976, "zpomalená proti PAL (23,976 fps)"],
    [24 / 25, "PAL z 24 fps"],
    [25 / 24, "24 fps proti PAL"],
  ];
  const hit = known.find(([k]) => Math.abs(k - speed) < 1e-3);
  return hit ? hit[1] : `rychlost ×${speed.toFixed(5)}`;
}

/** Step 1 of moving an audio track between versions: does the other version's audio fit this video? */
export default function AudioSyncPanel({ versions }: { versions: LibraryMovie[] }) {
  const byScore = [...versions].sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0));
  const [refId, setRefId] = useState(byScore[0].id);
  const [otherId, setOtherId] = useState(byScore[1].id);
  const [tracks, setTracks] = useState<Record<number, AudioTrackInfo[]>>({});
  const [refTrack, setRefTrack] = useState(0);
  const [otherTrack, setOtherTrack] = useState(0);
  const [job, setJob] = useState<AudioSyncJob | null>(null);
  const [result, setResult] = useState<AudioSyncResult | null>(null);
  const [error, setError] = useState("");

  // audio tracks of both files (ffprobe on the server)
  useEffect(() => {
    for (const id of [refId, otherId]) {
      if (tracks[id]) continue;
      getAudioTracks(id)
        .then((t) => {
          setTracks((prev) => ({ ...prev, [id]: t.audio }));
          if (id === otherId) {
            const local = t.audio.find((a) => LOCAL.includes(a.language));
            setOtherTrack(local ? local.index : 0);
          }
        })
        .catch((e) => setError(e.message));
    }
  }, [refId, otherId]); // eslint-disable-line react-hooks/exhaustive-deps

  // switching the source version: pick its CZ/SK track again
  useEffect(() => {
    const local = tracks[otherId]?.find((a) => LOCAL.includes(a.language));
    if (tracks[otherId]) setOtherTrack(local ? local.index : 0);
  }, [otherId]); // eslint-disable-line react-hooks/exhaustive-deps

  // the last comparison of this pair
  useEffect(() => {
    setResult(null);
    getAudioSyncResults(refId, otherId)
      .then((r) => {
        const same = r.find((x) => x.reference_track === refTrack && x.other_track === otherTrack);
        if (same) setResult(same.result);
      })
      .catch(() => {});
  }, [refId, otherId, refTrack, otherTrack]);

  // a comparison already running (e.g. after reopening the dialog)
  useEffect(() => {
    getAudioSyncJob().then((j) => j.running && setJob(j)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!job?.running) return;
    const t = setTimeout(async () => {
      try {
        const j = await getAudioSyncJob();
        setJob(j);
        if (!j.running) {
          if (j.error) setError(j.error);
          if (j.result) setResult(j.result);
        }
      } catch { /* next tick */ }
    }, 2000);
    return () => clearTimeout(t);
  }, [job]);

  async function run() {
    setError("");
    setResult(null);
    try {
      setJob(await startAudioSync({ reference_id: refId, other_id: otherId, reference_track: refTrack, other_track: otherTrack }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se spustit");
    }
  }

  const label = (v: LibraryMovie) => `${v.quality_summary || v.quality} · ${v.language?.replaceAll(",", "+") || "?"}`;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3 space-y-3">
      <p className="text-xs uppercase tracking-wide text-zinc-500">Přenos zvuku — porovnání verzí</p>
      <div className="grid gap-2 sm:grid-cols-2 text-xs">
        <label className="space-y-1">
          <span className="block text-zinc-400">Obraz z verze</span>
          <select className={SELECT} value={refId} onChange={(e) => { setRefId(Number(e.target.value)); setRefTrack(0); }}>
            {versions.filter((v) => v.id !== otherId).map((v) => <option key={v.id} value={v.id}>{label(v)}</option>)}
          </select>
          <select className={SELECT} value={refTrack} onChange={(e) => setRefTrack(Number(e.target.value))}
            title="Stopa, se kterou se porovnává (hudba a efekty bývají ve všech stejné)">
            {(tracks[refId] ?? []).map((t) => <option key={t.index} value={t.index}>{trackLabel(t)}</option>)}
          </select>
        </label>
        <label className="space-y-1">
          <span className="block text-zinc-400">Zvuk z verze</span>
          <select className={SELECT} value={otherId} onChange={(e) => setOtherId(Number(e.target.value))}>
            {versions.filter((v) => v.id !== refId).map((v) => <option key={v.id} value={v.id}>{label(v)}</option>)}
          </select>
          <select className={SELECT} value={otherTrack} onChange={(e) => setOtherTrack(Number(e.target.value))}>
            {(tracks[otherId] ?? []).map((t) => <option key={t.index} value={t.index}>{trackLabel(t)}</option>)}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button onClick={run} disabled={!!job?.running}
          className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
          {result ? "Porovnat znovu" : "Porovnat zvuk"}
        </button>
        {job?.running && (
          <span className="text-xs text-violet-300 animate-pulse">
            {job.phase === "windows" ? `Porovnávám úseky filmu ${job.done}/${job.total}`
              : job.phase === "speed" ? `Zjišťuji rychlost ${job.done}/${job.total}` : "Začínám…"} (asi minuta)
          </span>
        )}
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>

      {result && <ResultView result={result} />}
      <p className="text-[10px] text-zinc-600">Zatím jen analýza — soubory se nemění. Samotný přenos stopy přijde v dalším kroku.</p>
    </div>
  );
}

function ResultView({ result: r }: { result: AudioSyncResult }) {
  const pct = Math.round(r.confidence * 100);
  const head = r.verdict === "no_match"
    ? <p className="text-sm text-red-300">✗ Zvuk k tomuto obrazu nesedí</p>
    : r.verdict === "cuts"
      ? <p className="text-sm text-amber-300">⚠ Jiný střih — zvuk půjde přenést jen po částech</p>
      : <p className="text-sm text-green-300">✓ Zvuk sedí — {speedLabel(r.speed)}, posun {r.offset >= 0 ? "+" : ""}{r.offset.toFixed(2)} s</p>;
  return (
    <div className="space-y-2">
      {head}
      <p className="text-[11px] text-zinc-500">
        Jistota {pct} % ({r.windows.filter((w) => w.score >= 0.04 && w.sharpness >= 1.8).length}/{r.windows.length} úseků spárováno)
        {Math.abs(r.drift_s) > 0.02 && r.verdict !== "cuts" ? ` · rozjezd za celý film ${r.drift_s.toFixed(2)} s` : ""}
        {r.note && r.verdict !== "no_match" ? ` · ${r.note}` : ""}
      </p>
      {r.verdict === "cuts" && (
        <ul className="text-[11px] text-zinc-400">
          {r.segments.map((s, i) => (
            <li key={i}>{clock(s.start)} – {clock(s.end)}: posun {s.offset >= 0 ? "+" : ""}{s.offset.toFixed(2)} s</li>
          ))}
        </ul>
      )}
      <OffsetChart result={r} />
    </div>
  );
}

/** Offset of every compared piece along the film — a flat line = fits, steps = different cut. */
function OffsetChart({ result: r }: { result: AudioSyncResult }) {
  const W = 600, H = 110, P = 8;
  const dur = r.reference.duration || Math.max(...r.windows.map((w) => w.at), 1);
  const good = r.windows.filter((w) => w.score >= 0.04 && w.sharpness >= 1.8);
  const ys = (good.length ? good : r.windows).map((w) => w.offset);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 0.5) { const mid = (hi + lo) / 2; lo = mid - 0.25; hi = mid + 0.25; }
  const x = (t: number) => P + (t / dur) * (W - 2 * P);
  const y = (o: number) => H - P - ((Math.min(Math.max(o, lo), hi) - lo) / (hi - lo)) * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto rounded bg-zinc-900 border border-zinc-800">
      <text x={P} y={12} className="fill-zinc-500" fontSize="10">{hi.toFixed(2)} s</text>
      <text x={P} y={H - 2} className="fill-zinc-500" fontSize="10">{lo.toFixed(2)} s</text>
      <text x={W - P} y={H - 2} textAnchor="end" className="fill-zinc-500" fontSize="10">{clock(dur)}</text>
      {r.windows.map((w, i) => {
        const ok = w.score >= 0.04 && w.sharpness >= 1.8;
        return (
          <circle key={i} cx={x(w.at)} cy={y(w.offset)} r={ok ? 3.5 : 2.5} className={ok ? "fill-green-400" : "fill-zinc-600"}>
            <title>{`${clock(w.at)} · posun ${w.offset.toFixed(3)} s · shoda ${w.score.toFixed(2)} · ostrost ${w.sharpness.toFixed(1)}`}</title>
          </circle>
        );
      })}
    </svg>
  );
}
