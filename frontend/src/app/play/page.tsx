"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Hls from "hls.js";
import { PlayerInfo, PlayerMode, getPlayerInfo, playerUrl, startPlayer, stopPlayer } from "@/lib/api";

/** What this browser can decode itself (HEVC needs a hardware decoder; DV 5 is Safari's). */
function browserCaps(): { hevc: boolean; dv5: boolean } {
  const MS = typeof window !== "undefined" ? (window.MediaSource ?? (window as unknown as { ManagedMediaSource?: typeof MediaSource }).ManagedMediaSource) : undefined;
  const ok = (type: string) => {
    try {
      return !!MS?.isTypeSupported(type);
    } catch {
      return false;
    }
  };
  return {
    hevc: ok('video/mp4; codecs="hvc1.2.4.L153.B0"') || ok('video/mp4; codecs="hvc1.1.6.L150.B0"'),
    dv5: ok('video/mp4; codecs="dvh1.05.06"'),
  };
}

function clock(s: number): string {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export default function PlayPage() {
  return (
    <Suspense fallback={<main className="p-8 text-zinc-500">Načítám…</main>}>
      <Player />
    </Suspense>
  );
}

/** The whole film in the browser. The server converts from a chosen second, so the own seek bar
 *  (whole film) starts a new stream; the video element's time is relative to that start. */
function Player() {
  const router = useRouter();
  const params = useSearchParams();
  const movieId = Number(params.get("id"));
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const sessionRef = useRef<string | null>(null);
  const [info, setInfo] = useState<PlayerInfo | null>(null);
  const [audio, setAudio] = useState(Number(params.get("audio") ?? 0));
  const [start, setStart] = useState(0);
  const [now, setNow] = useState(0);
  const [seek, setSeek] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<PlayerMode>("auto");
  const [running, setRunning] = useState<{ mode: string; reason: string } | null>(null);
  // measured in the browser (the first render also runs on the server, without window)
  const caps = useRef({ hevc: false, dv5: false });
  const [canHevc, setCanHevc] = useState<boolean | null>(null);

  const play = useCallback(async (at: number, track: number, wanted: PlayerMode = mode) => {
    setLoading(true);
    setError("");
    try {
      if (sessionRef.current) stopPlayer(sessionRef.current);
      const s = await startPlayer(movieId, at, track, wanted, caps.current);
      setRunning({ mode: s.mode, reason: s.reason });
      sessionRef.current = s.session;
      setStart(s.start);
      setNow(s.start);
      const video = videoRef.current!;
      hlsRef.current?.destroy();
      if (Hls.isSupported()) {
        const hls = new Hls({ manifestLoadingMaxRetry: 10, levelLoadingMaxRetry: 10, fragLoadingMaxRetry: 10 });
        hls.loadSource(playerUrl(s.session));
        hls.attachMedia(video);
        hls.on(Hls.Events.ERROR, (_, data) => {
          if (data.fatal) setError(`Přehrávání selhalo (${data.details})`);
        });
        hlsRef.current = hls;
      } else {
        video.src = playerUrl(s.session);   // Safari plays HLS itself
      }
      video.play().catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se spustit");
    } finally {
      setLoading(false);
    }
  }, [movieId, mode]);

  useEffect(() => {
    if (!movieId) return;
    caps.current = browserCaps();
    setCanHevc(caps.current.hevc);
    getPlayerInfo(movieId).then((i) => {
      setInfo(i);
      play(Number(params.get("t") ?? 0), audio);
    }).catch((e) => setError(e.message));
    return () => {
      hlsRef.current?.destroy();
      if (sessionRef.current) stopPlayer(sessionRef.current);
    };
  }, [movieId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!movieId) return <main className="p-8 text-zinc-500">Chybí film.</main>;

  const duration = info?.duration ?? 0;
  const shown = seek ?? now;

  return (
    <main className="flex flex-col gap-3 px-4 py-6 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={() => router.back()} className="text-zinc-500 hover:text-zinc-300 text-sm whitespace-nowrap">&larr; Zpět</button>
        <h1 className="text-lg font-semibold text-zinc-100">
          {info?.title} {info?.year && <span className="text-zinc-500 font-normal">({info.year})</span>}
        </h1>
        {running && (
          <span className="text-xs text-zinc-500" title={running.reason}>
            {running.mode === "original"
              ? `originální obraz (${info?.video.codec?.toUpperCase()} ${info?.video.height}p${info?.video.hdr ? " HDR" : ""}) — bez převodu`
              : `převod na 720p${info?.video.hdr ? " (HDR → SDR)" : ""}`} · zvuk stereo AAC · {running.reason}
          </span>
        )}
      </div>

      <video ref={videoRef} controls playsInline className="w-full max-h-[75vh] rounded bg-black"
        onTimeUpdate={(e) => setNow(start + e.currentTarget.currentTime)} />

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-mono text-zinc-300 w-20">{clock(shown)}</span>
        <input type="range" min={0} max={Math.max(1, duration)} step={1} value={Math.min(shown, duration)}
          className="flex-1 min-w-[12rem] accent-violet-500"
          onChange={(e) => setSeek(Number(e.target.value))}
          onMouseUp={() => { if (seek != null) { play(seek, audio); setSeek(null); } }}
          onTouchEnd={() => { if (seek != null) { play(seek, audio); setSeek(null); } }}
          onKeyUp={() => { if (seek != null) { play(seek, audio); setSeek(null); } }} />
        <span className="font-mono text-zinc-500 w-20 text-right">{clock(duration)}</span>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2 text-zinc-400">
          Zvuk:
          <select value={audio} className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200"
            onChange={(e) => { const t = Number(e.target.value); setAudio(t); play(now, t); }}>
            {(info?.audio ?? []).map((a) => (
              <option key={a.index} value={a.index}>
                {[(a.language || "?").toUpperCase(), a.title, a.codec, a.channels ? `${a.channels}ch` : ""].filter(Boolean).join(" · ")}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-zinc-400"
          title="Originál = obraz z disku bez převodu (plná kvalita, ale plný datový tok — přes pomalou síť se zasekne). Převod = 720p H.264.">
          Obraz:
          <select value={mode} className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 text-zinc-200"
            onChange={(e) => { const m = e.target.value as PlayerMode; setMode(m); play(now, audio, m); }}>
            <option value="auto">automaticky</option>
            <option value="original">originál</option>
            <option value="transcode">převod 720p</option>
          </select>
        </label>
        {[-30, -10, 10, 30].map((d) => (
          <button key={d} onClick={() => play(Math.max(0, now + d), audio)}
            className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:border-zinc-500">
            {d > 0 ? `+${d}` : d} s
          </button>
        ))}
        {loading && <span className="text-violet-300 animate-pulse text-xs">připravuji…</span>}
        {error && <span className="text-red-400 text-xs">{error}</span>}
      </div>
      <p className="text-[11px] text-zinc-600">
        Na test mimo Plex. Prohlížeč neotevře MKV ani DTS, takže server film za běhu přebaluje (obraz buď beze změny,
        nebo převedený na 720p) a zvuk převádí na stereo. Přetáčení posuvníkem a změna zvuku spustí přehrávání od daného místa.
        {canHevc === null ? "" : canHevc ? " Tento prohlížeč umí HEVC." : " Tento prohlížeč neumí HEVC — HEVC filmy se převádějí."}
      </p>
    </main>
  );
}
