"use client";

import { formatSize } from "@/lib/api";
import { PickOffer, SourceBadge } from "@/components/FileTable";

/** Next to the profile select: the file that profile would download now, with a button. */
export default function PickOfferView({ offer, canDownload }: { offer: PickOffer | null; canDownload: boolean }) {
  if (!offer) return null;
  const { pick, file, downloading } = offer;
  if (!file) {
    return (
      <span className="text-xs text-zinc-500" title={pick.reasons.map(([why, n]) => `${why} (${n}×)`).join(", ")}>
        🎯 profilu teď nic nevyhovuje{pick.reasons[0] ? ` (${pick.reasons[0][0]})` : ""}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs" title={`Podle profilu „${pick.profile}“ (${pick.suitable} vyhovuje): ${file.name}`}>
      <span>🎯</span>
      <SourceBadge file={file} />
      <span className="text-zinc-300">{file.quality_summary || "?"} · {formatSize(file.size)}</span>
      {!file.verified && <span className="text-zinc-500">· neověřeno</span>}
      {canDownload && (downloading ? (
        <span className="text-green-400">
          {downloading === "starting" ? "Odesílám…" : downloading === "error" ? "Chyba" : downloading === "queued" ? "Ve frontě" : "Stahuje se"}
        </span>
      ) : (
        <button onClick={offer.download} className="rounded bg-violet-600 px-2.5 py-1 font-medium text-white hover:bg-violet-500">
          Stáhnout
        </button>
      ))}
    </span>
  );
}
