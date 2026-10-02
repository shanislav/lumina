"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import SeriesView from "@/components/SeriesView";

export default function SeriesPage() {
  return (
    <Suspense fallback={<main className="p-8 text-zinc-500">Načítám…</main>}>
      <Page />
    </Suspense>
  );
}

function Page() {
  const params = useSearchParams();
  const tmdbId = Number(params.get("tmdb"));
  if (!tmdbId) return <main className="p-8 text-zinc-500">Chybí seriál.</main>;
  return <SeriesView key={tmdbId} tmdbId={tmdbId} />;
}
