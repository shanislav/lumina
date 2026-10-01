"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import {
  QualityProfile, WatchedFilm, checkUpgrades, downloadUpgrade, formatSize, getUpgradeJob, getWatched, setFilmSettings,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

export const ON_BETTER: [string, string][] = [
  ["", "podle plánovače"],
  ["notify", "jen ukázat"],
  ["version", "stáhnout jako další verzi"],
  ["replace", "stáhnout a nahradit"],
];

/** Films the library watches for a better version: what is owned, the last check, what to do. */
export default function WatchedList({ profiles }: { profiles: QualityProfile[] }) {
  const { can } = useAuth();
  const router = useRouter();
  const [films, setFilms] = useState<WatchedFilm[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState<Record<number, string>>({});

  const load = useCallback(() => getWatched().then(setFilms).catch(() => setFilms([])), []);
  useEffect(() => { load(); }, [load]);

  // while a check runs: refresh until it is done
  useEffect(() => {
    if (!checking) return;
    const t = setInterval(async () => {
      const job = await getUpgradeJob().catch(() => null);
      if (!job?.running) { setChecking(false); load(); }
    }, 3000);
    return () => clearInterval(t);
  }, [checking, load]);

  async function save(f: WatchedFilm, patch: Partial<WatchedFilm>) {
    const next = { ...f, ...patch };
    setFilms((prev) => prev?.map((x) => (x.tmdb_id === f.tmdb_id ? next : x)) ?? null);
    await setFilmSettings(f.tmdb_id, { profile_id: next.profile_id, watch_upgrades: true, on_better: next.on_better });
  }

  async function stop(f: WatchedFilm) {
    await setFilmSettings(f.tmdb_id, { profile_id: f.profile_id, watch_upgrades: false, on_better: f.on_better });
    setFilms((prev) => prev?.filter((x) => x.tmdb_id !== f.tmdb_id) ?? null);
  }

  async function check(ids: number[]) {
    setChecking(true);
    await checkUpgrades(ids).catch(() => setChecking(false));
  }

  async function download(f: WatchedFilm, mode: "version" | "replace") {
    if (mode === "replace" && !confirm(`Stáhnout lepší verzi „${f.title}“ a po stažení smazat tu současnou?`)) return;
    setBusy((b) => ({ ...b, [f.tmdb_id]: "…" }));
    try {
      await downloadUpgrade(f.tmdb_id, mode);
      setBusy((b) => ({ ...b, [f.tmdb_id]: "stahuje se" }));
    } catch (e) {
      setBusy((b) => ({ ...b, [f.tmdb_id]: e instanceof Error ? e.message : "chyba" }));
    }
  }

  function search(f: WatchedFilm) {
    const movie = { tmdb_id: f.tmdb_id, title: f.title, original_title: f.title, year: f.year, overview: "",
                    poster_url: f.poster_url, media_type: "movie" };
    router.push(`/?movie=${btoa(encodeURIComponent(JSON.stringify(movie)))}&upgrade=${f.owned.id}`);
  }

  const def = profiles.find((p) => p.is_default);
  const edit = can("library.edit");

  if (films === null) return <p className="text-zinc-500 animate-pulse">Načítám…</p>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-xs text-zinc-500 flex-1">
          Plánovač u těchto filmů hledá verzi lepší než tu, kterou máš, a jen takovou, kterou dovolí profil filmu.
          Hlídání zapneš v Knihovně v detailu filmu. Co se stane s nalezenou verzí („když najde lepší“), platí pro noční
          plánovač i pro „Zkontrolovat“.
        </p>
        {edit && films.length > 0 && (checking ? (
          <span className="text-sm text-violet-300 animate-pulse">Kontroluji…</span>
        ) : (
          <button onClick={() => check(films.map((f) => f.tmdb_id))}
            className="rounded bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-500">
            Zkontrolovat vše
          </button>
        ))}
      </div>
      {films.length === 0 ? (
        <p className="text-zinc-500">Nic se nehlídá.</p>
      ) : films.map((f) => {
        const c = f.check;
        return (
          <div key={f.tmdb_id} className="flex gap-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
            <div className="w-12 h-[72px] relative flex-shrink-0 rounded overflow-hidden bg-zinc-800">
              {f.poster_url && <Image src={f.poster_url} alt="" fill sizes="48px" className="object-cover" />}
            </div>
            <div className="flex-1 min-w-0 space-y-1 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-zinc-100 font-medium">{f.title}</span>
                {f.year && <span className="text-zinc-500">({f.year})</span>}
                <span className="text-xs text-zinc-500">
                  máš: {f.owned.quality || "?"} · skóre {f.owned.score}{f.owned.language && ` · ${f.owned.language.replaceAll(",", "+")}`} · {formatSize(f.owned.size)}
                </span>
              </div>
              <div className="text-xs">
                {!c ? <span className="text-zinc-500">zatím nekontrolováno</span>
                  : c.status === "better" ? (
                    <span className="text-emerald-300">
                      lepší: {c.best.quality_summary} · skóre {c.best.quality_score} · {formatSize(c.best.size ?? 0)}
                      {!c.best.verified && <span className="text-zinc-500"> · neověřeno</span>}
                      <span className="text-zinc-500"> ({c.upgrades}× · {c.checked_at})</span>
                    </span>
                  ) : c.status === "downloading" ? <span className="text-violet-300">stahuje se lepší verze</span>
                  : c.status === "done" ? <span className="text-zinc-400">✓ {c.note || "cíl profilu splněn"}</span>
                  : c.status === "error" ? <span className="text-red-400">kontrola selhala: {c.error}</span>
                  : <span className="text-zinc-500">nic lepšího ({c.checked_at})</span>}
              </div>
              {edit && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
                  <label className="flex items-center gap-1">profil
                    <select value={f.profile_id ?? ""} onChange={(e) => save(f, { profile_id: e.target.value === "" ? null : Number(e.target.value) })}
                      className="rounded bg-zinc-800 border border-zinc-700 px-1 py-0.5 text-zinc-300">
                      <option value="">výchozí ({def?.name ?? "—"})</option>
                      {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </label>
                  <label className="flex items-center gap-1">když najde lepší
                    <select value={f.on_better} onChange={(e) => save(f, { on_better: e.target.value })}
                      className="rounded bg-zinc-800 border border-zinc-700 px-1 py-0.5 text-zinc-300">
                      {ON_BETTER.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </label>
                  <button onClick={() => check([f.tmdb_id])} disabled={checking} className="text-violet-300 hover:text-violet-200 disabled:opacity-40">Zkontrolovat</button>
                  <button onClick={() => search(f)} className="text-violet-300 hover:text-violet-200">Hledat ručně</button>
                  <button onClick={() => stop(f)} className="text-zinc-500 hover:text-zinc-300">Přestat hlídat</button>
                </div>
              )}
            </div>
            {c?.status === "better" && can("download") && (
              <div className="flex flex-col items-end justify-center gap-1 text-xs">
                {busy[f.tmdb_id] ? <span className="text-violet-300">{busy[f.tmdb_id]}</span> : (
                  <>
                    <button onClick={() => download(f, "version")} className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500">
                      Stáhnout jako verzi
                    </button>
                    {can("library.delete") && (
                      <button onClick={() => download(f, "replace")} className="rounded border border-violet-700 px-3 py-1 text-violet-200 hover:bg-violet-900/40">
                        Stáhnout a nahradit
                      </button>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
