"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import AudioEditor from "@/components/AudioEditor";
import { useAuth } from "@/components/AuthGate";

export default function AudioEditorPage() {
  return (
    <Suspense fallback={<main className="p-8 text-zinc-500">Načítám…</main>}>
      <Page />
    </Suspense>
  );
}

function Page() {
  const params = useSearchParams();
  const { can } = useAuth();
  const tmdbId = Number(params.get("tmdb"));
  const target = Number(params.get("target")) || null;
  if (!can("audiosync")) return <main className="p-8 text-zinc-500">Editor zvuku potřebuje oprávnění „audiosync“.</main>;
  if (!tmdbId) return <main className="p-8 text-zinc-500">Chybí film.</main>;
  return <AudioEditor key={tmdbId} tmdbId={tmdbId} initialTarget={target} />;
}
