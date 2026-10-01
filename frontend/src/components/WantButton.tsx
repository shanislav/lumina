"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { QualityProfile, TMDBMovie, addWanted, getProfiles } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

/** "+ Chci": put a film on the wanted list with a quality profile (checked right away). */
type ProfileProps = { profileId?: number | ""; onProfileChange?: (id: number | "") => void };

export default function WantButton({ movie, ...profile }: { movie: TMDBMovie } & ProfileProps) {
  const { can } = useAuth();
  return can("wanted") ? <WantButtonInner movie={movie} {...profile} /> : null;
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

function WantButtonInner({ movie, profileId: shared, onProfileChange }: { movie: TMDBMovie } & ProfileProps) {
  const [own, setOwn] = useState<number | "">("");
  const profileId = shared ?? own;
  const setProfileId = onProfileChange ?? setOwn;
  const [state, setState] = useState<"idle" | "busy" | "added" | string>("idle");

  useEffect(() => setState("idle"), [movie.tmdb_id, movie.wikidata_id]);

  if (movie.media_type === "tv" || (!movie.tmdb_id && !movie.wikidata_id)) return null;

  async function add() {
    setState("busy");
    try {
      await addWanted({
        tmdb_id: movie.tmdb_id || null, wikidata_id: movie.wikidata_id || null, title: movie.title,
        original_title: movie.original_title, year: movie.year || "", poster_url: movie.poster_url,
        profile_id: profileId === "" ? null : profileId,
      });
      setState("added");
    } catch (e) {
      setState(e instanceof Error ? e.message : "Nepodařilo se přidat");
    }
  }

  if (state === "added") {
    return <Link href="/wanted" className="text-xs text-green-300 hover:text-green-200">✓ V seznamu Chci →</Link>;
  }
  return (
    <div className="flex items-center gap-1.5 text-xs">
      {!onProfileChange && <ProfileSelect value={profileId} onChange={setProfileId} />}
      <button onClick={add} disabled={state === "busy"}
        title="Přidat do seznamu Chci — Lumina ho bude hledat podle profilu"
        className="whitespace-nowrap rounded border border-violet-700 px-2 py-1 text-violet-200 hover:bg-violet-900/40 disabled:opacity-50">
        {state === "busy" ? "…" : "+ Chci"}
      </button>
      {state !== "idle" && state !== "busy" && <span className="text-red-400">{state}</span>}
    </div>
  );
}
