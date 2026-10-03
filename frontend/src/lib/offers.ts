"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MovieContext, ScoredFile, getFileDetails } from "@/lib/api";

/** The file offers of a search, verified at their sources in the background (as the desktop file table does it):
 *  WebShare / FastShare / Prowlarr files that may be the film, likely ones first, in small batches; the same file
 *  on several sources (same size) is one offer. */
const BATCH = 15;
const DETAIL_SOURCES = new Set(["webshare", "fastshare", "prowlarr"]);
const SOURCE_ORDER = ["webshare", "fastshare", "prowlarr"];
const TORRENT = new Set(["jackett", "prowlarr"]);
export const FILM_ORDER: Record<string, number> = { yes: 0, unsure: 1, length: 2, no: 3 };

export const keyOf = (f: ScoredFile) => `${f.source_id}:${f.ident}`;
export const isTorrent = (f: ScoredFile) => TORRENT.has(f.source);

export interface Offer {
  key: string;
  file: ScoredFile;       // the representative (a verified copy if any)
  copies: ScoredFile[];
}

export function useVerifiedOffers(files: ScoredFile[], movie: MovieContext | null) {
  const [updates, setUpdates] = useState<Record<string, Partial<ScoredFile>>>({});
  const [verify, setVerify] = useState({ done: 0, total: 0, running: false });
  const generation = useRef(0);

  useEffect(() => {
    const gen = ++generation.current;
    setUpdates({});
    const groups = new Map<number, ScoredFile[]>();
    for (const f of files) {
      if (!DETAIL_SOURCES.has(f.source) || f.film === "no") continue;
      groups.set(f.size, [...(groups.get(f.size) ?? []), f]);
    }
    const queues = Array.from(groups.values())
      .filter((copies) => !copies.some((c) => c.verified))
      .map((copies) => [...copies].sort((a, b) => SOURCE_ORDER.indexOf(a.source) - SOURCE_ORDER.indexOf(b.source)))
      .sort((a, b) => Number(b[0].film === "yes") - Number(a[0].film === "yes"));
    const total = queues.length;
    setVerify({ done: 0, total, running: total > 0 });
    (async () => {
      let pending = queues;
      let done = 0;
      while (pending.length) {
        const current = pending.slice(0, BATCH);
        pending = pending.slice(BATCH);
        const res = await getFileDetails(
          current.map(([f]) => ({ source_id: f.source_id, ident: f.ident, name: f.name, size: f.size })), movie ?? null,
        ).catch(() => ({} as Record<string, Partial<ScoredFile> | null>));
        if (gen !== generation.current) return;
        const got: Record<string, Partial<ScoredFile>> = {};
        for (const [k, v] of Object.entries(res)) if (v) got[k] = v;
        setUpdates((prev) => ({ ...prev, ...got }));
        const retry: ScoredFile[][] = [];
        for (const [first, ...rest] of current) {
          if (got[keyOf(first)] || !rest.length) done += 1;
          else retry.push(rest);
        }
        pending = [...retry, ...pending];
        setVerify({ done, total, running: pending.length > 0 });
      }
    })();
  }, [files, movie]);

  const offers = useMemo(() => {
    const merged = files.map((f) => ({ ...f, ...(updates[keyOf(f)] ?? {}) }) as ScoredFile);
    const bySize = new Map<string, ScoredFile[]>();
    for (const f of merged) {
      const k = isTorrent(f) ? `t:${keyOf(f)}` : `s:${f.size}`;
      bySize.set(k, [...(bySize.get(k) ?? []), f]);
    }
    return Array.from(bySize, ([key, copies]): Offer => ({ key, copies, file: copies.find((c) => c.verified) ?? copies[0] }));
  }, [files, updates]);

  return { offers, verify };
}
