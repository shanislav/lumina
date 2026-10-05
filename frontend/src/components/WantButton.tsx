"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { QualityProfile, TMDBMovie, addWanted, getProfiles, getWantedOf } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

/** "+ Chci": put a film on the wanted list with a quality profile (checked right away). Who may not download (a
 *  child's account) only asks — a film or a show; the admin sees it in Chci and decides. */
type ProfileProps = { profileId?: number | ""; onProfileChange?: (id: number | "") => void };

export default function WantButton({ movie, big, ...profile }: { movie: TMDBMovie; big?: boolean } & ProfileProps) {
  const { can } = useAuth();
  return can("wanted") ? <WantButtonInner movie={movie} big={big} {...profile} /> : null;
}

/** The quality profile chosen for this film — "+ Chci" and the offer picked for download share it. */
export function ProfileSelect({ value, onChange }: { value: number | ""; onChange: (id: number | "") => void }) {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  useEffect(() => { getProfiles().then(setProfiles).catch(() => {}); }, []);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
      title="Profil kvality" className="rounded bg-zinc-800 border border-zinc-700 px-1.5 py-1 text-xs text-zinc-300">
      <option value="">výchozí profil</option>
      {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}

function WantButtonInner({ movie, profileId: shared, onProfileChange, big = false }: { movie: TMDBMovie; big?: boolean } & ProfileProps) {
  const { can } = useAuth();
  const ask = !can("download");                  // only asks: no profile, the admin picks
  const tv = movie.media_type === "tv";
  const [own, setOwn] = useState<number | "">("");
  const profileId = shared ?? own;
  const setProfileId = onProfileChange ?? setOwn;
  const [state, setState] = useState<"idle" | "busy" | "added" | string>("idle");

  useEffect(() => {
    setState("idle");
    if ((tv && !ask) || (!movie.tmdb_id && !movie.wikidata_id)) return;
    getWantedOf(movie.tmdb_id, movie.wikidata_id, tv ? "tv" : "movie").then((w) => { if (w) setState("added"); }).catch(() => {});
  }, [movie.tmdb_id, movie.wikidata_id, tv, ask]);

  // a show: who may download sets it on its page (Chci = the automation); here only who asks
  if ((tv && !ask) || (!movie.tmdb_id && !movie.wikidata_id)) return null;

  async function add() {
    setState("busy");
    try {
      await addWanted({
        tmdb_id: movie.tmdb_id || null, wikidata_id: movie.wikidata_id || null, title: movie.title,
        original_title: movie.original_title, year: movie.year || "", poster_url: movie.poster_url,
        profile_id: profileId === "" || ask ? null : profileId, media_type: tv ? "tv" : "movie",
      });
      setState("added");
    } catch (e) {
      setState(e instanceof Error ? e.message : "Nepodařilo se přidat");
    }
  }

  if (state === "added") {
    return (
      <Link href="/wanted" className={big ? "block rounded-xl border border-green-800 bg-green-950/40 px-4 py-3 text-base text-green-200"
        : "text-xs text-green-300 hover:text-green-200"}>
        {ask ? "✓ Požádáno — je v Chci" : "✓ V seznamu Chci"} →
      </Link>
    );
  }
  const label = state === "busy" ? "…" : ask ? (big ? "★ Chci tohle vidět" : "★ Chci") : "+ Chci";
  return (
    <div className={big ? "space-y-1" : "flex items-center gap-1.5 text-xs"}>
      {!onProfileChange && !ask && !big && <ProfileSelect value={profileId} onChange={setProfileId} />}
      <button onClick={add} disabled={state === "busy"}
        title={ask ? "Přidat do Chci — správce uvidí, že to chceš" : "Přidat do seznamu Chci — Lumina ho bude hledat podle profilu"}
        className={big ? "min-h-12 w-full rounded-xl bg-violet-600 px-4 text-base font-medium text-white active:bg-violet-700 disabled:opacity-50"
          : ask ? "whitespace-nowrap rounded bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
          : "whitespace-nowrap rounded border border-violet-700 px-2 py-1 text-violet-200 hover:bg-violet-900/40 disabled:opacity-50"}>
        {label}
      </button>
      {state !== "idle" && state !== "busy" && <span className="text-red-400">{state}</span>}
    </div>
  );
}
