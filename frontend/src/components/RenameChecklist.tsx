"use client";

import { useEffect, useState } from "react";
import { PlexMigrationCheck, getPlexMigrationCheck } from "@/lib/api";

/** What to do before renaming many films at once — so Plex keeps them as they are
 *  (no flood of "recently added", watched state and custom posters kept). decisions/0008 */
export default function RenameChecklist({ count, onPick }: { count: number; onPick: (n: number) => void }) {
  const [plex, setPlex] = useState<PlexMigrationCheck | null | undefined>(undefined);

  useEffect(() => {
    getPlexMigrationCheck().then(setPlex).catch(() => setPlex(null));
  }, []);

  const risky = plex?.settings?.filter((s) => s.on) ?? [];

  return (
    <details open={count > 20} className="rounded-lg border border-amber-900/60 bg-amber-950/10 text-xs">
      <summary className="cursor-pointer select-none px-3 py-2 text-amber-200">
        Před velkým přejmenováním — ať Plex filmy nebere jako nové
      </summary>
      <ol className="list-decimal space-y-1.5 px-3 pb-3 pl-7 text-zinc-300">
        <li>
          <b>Vypni Radarr</b> (nebo mu vypni sledování filmů) — jinak uvidí, že soubory zmizely, a může je začít stahovat znovu.
        </li>
        <li>
          <b>Plex</b> — dočasně vypni v Nastavení → Knihovna:
          {plex === undefined ? <span className="text-zinc-500"> (zjišťuji…)</span>
            : plex?.configured && plex.reachable ? (
              <ul className="mt-1 space-y-0.5">
                {plex.settings.map((s) => (
                  <li key={s.id} className={s.on ? "text-amber-300" : "text-emerald-300"}>
                    {s.on ? "⚠ zapnuto" : "✓ vypnuto"} — {s.title}
                  </li>
                ))}
                {!risky.length && <li className="text-emerald-300">V pořádku, Plex nic neudělá sám.</li>}
              </ul>
            ) : (
              <span className="text-zinc-400">
                {" "}„Automaticky prohledávat knihovnu“, „Částečné prohledání při zjištění změn“ a hlavně „Automaticky vysypat koš
                po každém prohledání“.{plex?.configured && plex.reachable === false ? ` (Plex se nepodařilo zeptat: ${plex.error})` : ""}
              </span>
            )}
          <p className="mt-1 text-zinc-500">
            Proč: sledování disku rozdělí přejmenování na stovky malých skenů; když se mezi nimi vysype koš, Plex film smaže a
            přidá znovu jako nový (záplava „Nedávno přidané“, přijdeš o vlastní plakáty a kolekce).
            {plex?.lumina_scans ? " Lumina po každé dávce spustí jeden sken sama." : " Po každé dávce spusť v Plexu „Prohledat soubory knihovny“."}
          </p>
        </li>
        <li>
          <b>Nejdřív zkouška</b>:{" "}
          <button className="text-violet-300 hover:text-violet-200 underline" onClick={() => onPick(5)}>vyber prvních 5</button>,
          oprav, v Plexu zkontroluj „Nedávno přidané“, zhlédnuto a plakát. Pak po dávkách{" "}
          <button className="text-violet-300 hover:text-violet-200 underline" onClick={() => onPick(50)}>po 50</button>.
        </li>
        <li>Nakonec zkontroluj koš v Plexu (jen opravdu chybějící), vysyp ho a automatiku zapni zpět.</li>
      </ol>
    </details>
  );
}
