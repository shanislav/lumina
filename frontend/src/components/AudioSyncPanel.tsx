"use client";

import { useEffect, useState } from "react";
import {
  AudioSyncJob, AudioSyncResult, AudioTrackInfo, LibraryMovie,
  audioPreviewUrl, getAudioSyncJob, getAudioSyncResults, getAudioTracks, makeAudioPreview, setAudioAdjust,
  startAudioSync, startAudioTransfer,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

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
export default function AudioSyncPanel({ versions, onChanged }: { versions: LibraryMovie[]; onChanged?: () => void }) {
  const { can } = useAuth();
  const byScore = [...versions].sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0));
  const [refId, setRefId] = useState(byScore[0].id);
  const [otherId, setOtherId] = useState(byScore[1].id);
  const [tracks, setTracks] = useState<Record<number, AudioTrackInfo[]>>({});
  const [refTrack, setRefTrack] = useState(0);
  const [otherTrack, setOtherTrack] = useState(0);
  const [job, setJob] = useState<AudioSyncJob | null>(null);
  const [result, setResult] = useState<AudioSyncResult | null>(null);
  const [resultId, setResultId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [confirmReplace, setConfirmReplace] = useState(false);

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
    setResultId(null);
    getAudioSyncResults(refId, otherId)
      .then((r) => {
        const same = r.find((x) => x.reference_track === refTrack && x.other_track === otherTrack);
        if (same) {
          setResult(same.result);
          setResultId(same.id);
        }
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
          if (j.kind === "transfer" && !j.error) {
            setDone(j.imported ? "✓ Hotovo — soubor s novou stopou je v knihovně"
              : `Soubor je hotový, ale knihovna ho nepřevzala — zůstal ve stahování (${j.path ?? ""})`);
            onChanged?.();
          } else if (j.result) {
            setResult(j.result);
            setResultId(j.result_id ?? null);
          }
        }
      } catch { /* next tick */ }
    }, 2000);
    return () => clearTimeout(t);
  }, [job]);

  async function transfer(mode: "version" | "replace") {
    if (!resultId) return;
    setError("");
    setDone("");
    setConfirmReplace(false);
    try {
      setJob(await startAudioTransfer(resultId, mode));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se spustit");
    }
  }

  async function run() {
    setError("");
    setDone("");
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
            {job.phase === "windows" ? `Porovnávám úseky filmu ${job.done}/${job.total} (asi minuta)`
              : job.phase === "speed" ? `Zjišťuji rychlost ${job.done}/${job.total} (asi minuta)`
              : job.phase === "cuts" ? `Hledám přesná místa střihu ${job.done}/${job.total}`
              : job.phase === "prepare" ? "Připravuji stopu…"
              : job.phase === "mux" ? `Skládám soubor ${job.done} % (pár minut)`
              : job.phase === "verify" ? "Kontroluji výsledek…"
              : job.phase === "import" ? "Předávám knihovně…" : "Začínám…"}
          </span>
        )}
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>

      {result && <ResultView result={result} />}
      {result && resultId && result.verdict !== "no_match" && !job?.running && (
        <PreviewBox key={resultId} resultId={resultId} result={result}
          onSaved={(ms) => setResult({ ...result, adjust_ms: ms })} />
      )}
      {done && <p className="text-sm text-green-300">{done}</p>}

      {result && resultId && (result.verdict === "constant" || result.verdict === "speed"
        || (result.verdict === "cuts" && (result.pieces?.length ?? 0) > 1)) && !job?.running && (
        <div className="space-y-2 border-t border-zinc-800 pt-3">
          <p className="text-xs text-zinc-400">
            Vložit stopu do souboru s obrazem z „{label(versions.find((v) => v.id === refId) ?? versions[0])}“
            {result.verdict === "cuts" ? " (stopa se poskládá po částech podle střihu — AC-3; kde druhé verzi scéna chybí, bude ticho)"
              : result.verdict === "speed" ? " (stopa se přepočítá na správnou rychlost — AC-3)" : " (bez překódování)"}.
            Výsledek se před použitím zkontroluje.
          </p>
          <div className="flex flex-wrap gap-2">
            {can("library.edit") && (
              <button onClick={() => transfer("version")}
                className="rounded border border-violet-700 px-3 py-1.5 text-xs text-violet-200 hover:bg-violet-900/40">
                Uložit jako novou verzi
              </button>
            )}
            {can("library.delete") && !confirmReplace && (
              <button onClick={() => setConfirmReplace(true)}
                className="rounded border border-orange-800 px-3 py-1.5 text-xs text-orange-200 hover:bg-orange-950/40">
                Nahradit původní soubor
              </button>
            )}
          </div>
          {confirmReplace && (
            <div className="rounded border border-orange-800 bg-orange-950/20 p-2 text-xs space-y-2">
              <p className="text-orange-200">Nový soubor nahradí původní (ten se smaže, až bude nový v knihovně a sedí délka).</p>
              <div className="flex gap-2">
                <button onClick={() => transfer("replace")} className="rounded bg-orange-700 px-3 py-1 text-white hover:bg-orange-600">Ano, nahradit</button>
                <button onClick={() => setConfirmReplace(false)} className="text-zinc-400 hover:text-zinc-200">Zrušit</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Short clips of the picture with the other audio — to check the lips, and correct by hand. */
function PreviewBox({ resultId, result, onSaved }: {
  resultId: number; result: AudioSyncResult; onSaved: (ms: number) => void;
}) {
  const dur = result.reference.duration || 0;
  const points: { at: number; label: string }[] = [0.25, 0.5, 0.75].map((f) => ({ at: dur * f, label: clock(dur * f) }));
  for (const p of result.pieces ?? []) {
    if (p.start > 1 && p.offset != null) points.push({ at: p.start + 2, label: `po střihu ${clock(p.start)}` });
  }
  points.sort((a, b) => a.at - b.at);
  const saved = result.adjust_ms ?? 0;
  const [at, setAt] = useState(points[Math.floor(points.length / 2)]?.at ?? 0);
  const [adjust, setAdjust] = useState(saved);
  const [clip, setClip] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function show(nextAt = at, nextAdjust = adjust) {
    setBusy(true);
    setErr("");
    try {
      const r = await makeAudioPreview(resultId, nextAt, nextAdjust);
      setClip(r.name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ukázka selhala");
    } finally {
      setBusy(false);
    }
  }

  const nudge = (ms: number) => {
    const next = adjust + ms;
    setAdjust(next);
    show(at, next);
  };

  return (
    <div className="space-y-2 rounded border border-zinc-800 p-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-zinc-400">Ukázka (20 s):</span>
        {points.map((p) => (
          <button key={p.at} onClick={() => { setAt(p.at); show(p.at, adjust); }} disabled={busy}
            className={`rounded px-2 py-0.5 border ${Math.abs(p.at - at) < 1 && clip ? "border-violet-500 text-violet-200" : "border-zinc-700 text-zinc-300"} disabled:opacity-40`}>
            {p.label}
          </button>
        ))}
        {busy && <span className="text-violet-300 animate-pulse">připravuji…</span>}
      </div>
      {err && <p className="text-xs text-red-400">{err}</p>}
      {clip && (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <video key={clip} src={audioPreviewUrl(clip)} controls autoPlay className="w-full max-h-72 rounded bg-black" />
      )}
      {clip && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-zinc-400" title="Když zvuk předbíhá pusu, posuň ho později (+)">Posun zvuku:</span>
          {[-200, -40].map((ms) => (
            <button key={ms} onClick={() => nudge(ms)} disabled={busy} className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300 disabled:opacity-40">{ms}</button>
          ))}
          <span className="w-16 text-center font-mono text-zinc-100">{adjust > 0 ? "+" : ""}{adjust} ms</span>
          {[40, 200].map((ms) => (
            <button key={ms} onClick={() => nudge(ms)} disabled={busy} className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300 disabled:opacity-40">+{ms}</button>
          ))}
          {adjust !== saved && (
            <button onClick={async () => { await setAudioAdjust(resultId, adjust); onSaved(adjust); }}
              className="ml-2 rounded bg-violet-600 px-2 py-0.5 text-white hover:bg-violet-500">
              Uložit posun
            </button>
          )}
          {saved !== 0 && adjust === saved && <span className="ml-2 text-green-300">uloženo — přenos ho použije</span>}
        </div>
      )}
    </div>
  );
}

function ResultView({ result: r }: { result: AudioSyncResult }) {
  const pct = Math.round(r.confidence * 100);
  const head = r.verdict === "no_match"
    ? <p className="text-sm text-red-300">✗ Zvuk k tomuto obrazu nesedí</p>
    : r.verdict === "cuts"
      ? <p className="text-sm text-amber-300">⚠ Jiný střih — zvuk se poskládá po částech</p>
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
          {(r.pieces?.length ? r.pieces : r.segments).map((s, i) => (
            <li key={i} className={s.offset == null ? "text-amber-300/80" : ""}>
              {clock(s.start)} – {clock(s.end)}:{" "}
              {s.offset == null ? "ticho — ve druhé verzi tato část chybí"
                : `posun ${s.offset >= 0 ? "+" : ""}${s.offset.toFixed(2)} s`}
            </li>
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
