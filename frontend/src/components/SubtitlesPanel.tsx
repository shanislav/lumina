"use client";

import { useEffect, useState } from "react";
import { SubtitleResult, SubtitleStatus, downloadSubtitle, getSubtitleStatus, searchSubtitles } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

const L = (code: string) => (code === "cs" ? "CZ" : (code || "?").toUpperCase());

/** Subtitles of a library film: what it has, whether it may need forced ones, OpenSubtitles search. */
export default function SubtitlesPanel({ movieId }: { movieId: number }) {
  const { can } = useAuth();
  const [status, setStatus] = useState<SubtitleStatus | null>(null);
  const [results, setResults] = useState<SubtitleResult[] | null>(null);
  const [videoFps, setVideoFps] = useState(0);
  const [searching, setSearching] = useState<"" | "all" | "forced">("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Record<number, string>>({});

  const load = () => getSubtitleStatus(movieId).then(setStatus).catch(() => setStatus(null));
  useEffect(() => { setResults(null); setError(null); setDone({}); load(); /* eslint-disable-next-line */ }, [movieId]);

  async function search(forced: boolean) {
    setSearching(forced ? "forced" : "all");
    setError(null);
    try {
      const r = await searchSubtitles(movieId, forced);
      setResults(r.results);
      setVideoFps(r.video_fps);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setSearching("");
    }
  }

  async function download(r: SubtitleResult, replace = false) {
    setDone((d) => ({ ...d, [r.file_id]: "…" }));
    try {
      const out = await downloadSubtitle(movieId, { file_id: r.file_id, language: r.language, forced: r.forced, fps: r.fps, replace });
      setDone((d) => ({ ...d, [r.file_id]: `✓ ${out.file}${out.note ? ` (${out.note})` : ""}${out.remaining != null ? ` · zbývá ${out.remaining} dnes` : ""}` }));
      load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Chyba";
      if (msg.includes("už existuje") && confirm(`${msg}. Nahradit?`)) return download(r, true);
      setDone((d) => ({ ...d, [r.file_id]: msg }));
    }
  }

  if (!status) return null;
  const tracks = status.embedded.map((t) => `${L(t.lang)}${t.forced ? " forced" : ""}`);
  const missingForced = status.needs_forced && !status.has_forced;
  return (
    <div className="space-y-1.5 pt-1 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-zinc-400">
        <span className="text-zinc-300">Titulky:</span>
        <span>v souboru {tracks.length ? tracks.join(", ") : "žádné"}</span>
        <span>vedle {status.external.length ? status.external.map((f) => `${L(f.lang)}${f.forced ? " forced" : ""}`).join(", ") : "žádné"}</span>
        {status.spoken_languages.length > 1 && (
          <span className={missingForced ? "text-orange-300" : "text-zinc-500"}
            title="Jazyky, kterými se ve filmu mluví (TMDB). Víc jazyků = části v cizí řeči, na které bývají forced titulky.">
            mluví se {status.spoken_languages.map(L).join(", ")}{missingForced ? " — chybí forced titulky" : ""}
          </span>
        )}
      </div>
      {can("subtitles") && (status.configured ? (
        <div className="flex flex-wrap items-center gap-3">
          <button onClick={() => search(true)} disabled={!!searching} className="text-violet-300 hover:text-violet-200 disabled:opacity-40">
            {searching === "forced" ? "Hledám…" : `Hledat forced ${status.local_langs.map(L).join("/")}`}
          </button>
          <button onClick={() => search(false)} disabled={!!searching} className="text-violet-300 hover:text-violet-200 disabled:opacity-40">
            {searching === "all" ? "Hledám…" : `Hledat titulky ${status.local_langs.map(L).join("/")}`}
          </button>
          <span className="text-zinc-600">OpenSubtitles</span>
        </div>
      ) : (
        <p className="text-zinc-600">Hledání titulků: nastav OpenSubtitles v Nastavení.</p>
      ))}
      {error && <p className="text-red-400">{error}</p>}
      {results && (results.length === 0 ? <p className="text-zinc-500">Nic nenalezeno.</p> : (
        <div className="max-h-64 overflow-y-auto rounded border border-zinc-800">
          {results.map((r) => (
            <div key={r.file_id} className="flex items-center gap-2 border-b border-zinc-800/60 px-2 py-1.5 last:border-0">
              <span className="w-7 font-medium text-zinc-200">{L(r.language)}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-zinc-300" title={r.release || r.file_name}>{r.release || r.file_name}</p>
                <p className="text-[10px] text-zinc-500">
                  {r.hash_match && <span className="text-emerald-400">✓ sedí na tento soubor · </span>}
                  {r.forced && <span className="text-violet-300">forced · </span>}
                  {r.hearing_impaired && "pro neslyšící · "}{r.machine && <span className="text-orange-300">strojový překlad · </span>}
                  {r.downloads}× stažené{r.fps ? ` · ${r.fps} fps` : ""}
                  {r.fps && videoFps && Math.abs(r.fps / videoFps - 1) > 0.005 ? <span className="text-orange-300"> (film {videoFps} — přečasuji)</span> : ""}
                  {r.uploader && ` · ${r.uploader}`}{r.uploaded && ` · ${r.uploaded}`}
                </p>
                {done[r.file_id] && <p className="text-[10px] text-violet-300">{done[r.file_id]}</p>}
              </div>
              <button onClick={() => download(r)} disabled={!!done[r.file_id] && done[r.file_id] !== "…" && done[r.file_id].startsWith("✓")}
                className="rounded bg-violet-600 px-2 py-0.5 text-white hover:bg-violet-500 disabled:opacity-40">Stáhnout</button>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
