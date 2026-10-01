"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PlexMigration, checkPlexMigration, finishPlexMigration, getPlexMigration, startPlexMigration } from "@/lib/api";

/** Renaming many films at once so that Plex keeps them (no flood of "recently added", watched state,
 *  posters and collections kept) — decisions/0008. With Plex set up in Lumina, Lumina switches Plex's
 *  own scanning and trash off, checks every batch and puts everything back at the end. */
export default function RenameChecklist({ count, onPick, batches }: {
  count: number;
  onPick: (n: number) => void;
  batches: number;            // goes up after each applied or undone batch
}) {
  const [plex, setPlex] = useState<PlexMigration | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [emptyTrash, setEmptyTrash] = useState(true);
  const [repair, setRepair] = useState(true);
  const lastBatches = useRef(batches);

  const load = useCallback(async () => {
    try {
      setPlex(await getPlexMigration());
    } catch {
      setPlex(null);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // while Plex scans: ask again
  useEffect(() => {
    if (!plex?.job?.running) return;
    const t = setTimeout(load, 2000);
    return () => clearTimeout(t);
  }, [plex, load]);

  // after a batch: one scan + check
  useEffect(() => {
    if (batches === lastBatches.current) return;
    lastBatches.current = batches;
    if (plex?.active) runCheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batches]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(false);
    }
  }

  const runCheck = () => act(checkPlexMigration);
  const report = plex?.report;
  const problems = (report?.missing.length ?? 0) + (report?.readded.length ?? 0);

  if (plex === undefined) return <p className="text-xs text-zinc-500">Zjišťuji Plex…</p>;

  const radarr = (
    <li><b>Vypni Radarr</b> (pokud ještě běží) — jinak uvidí, že soubory zmizely, a může je začít stahovat znovu.</li>
  );
  const trial = (
    <li>
      <b>Nejdřív zkouška</b>:{" "}
      <button className="text-violet-300 hover:text-violet-200 underline" onClick={() => onPick(5)}>vyber prvních 5</button>,
      oprav a v Plexu zkontroluj „Nedávno přidané“, zhlédnuto a plakát. Pak po dávkách{" "}
      <button className="text-violet-300 hover:text-violet-200 underline" onClick={() => onPick(50)}>po 50</button>.
    </li>
  );

  // Plex not set up in Lumina: what to do by hand
  if (!plex || !plex.configured || (plex.error && !plex.active)) {
    return (
      <details open={count > 20} className="rounded-lg border border-amber-900/60 bg-amber-950/10 text-xs">
        <summary className="cursor-pointer select-none px-3 py-2 text-amber-200">
          Před velkým přejmenováním — ať Plex filmy nebere jako nové
        </summary>
        <ol className="list-decimal space-y-1.5 px-3 pb-3 pl-7 text-zinc-300">
          {radarr}
          <li>
            <b>Plex</b> — dočasně vypni v Nastavení → Knihovna „Automaticky prohledávat knihovnu“, „Částečné prohledání“,
            „Pravidelně prohledávat“ a hlavně „Automaticky vysypat koš po každém prohledání“. Po každé dávce spusť
            „Prohledat soubory knihovny“.
            {plex?.error && <p className="mt-1 text-amber-400">Lumina to za tebe udělá, když v ní bude Plex nastavený: {plex.error}</p>}
          </li>
          {trial}
          <li>Nakonec zkontroluj koš v Plexu (jen opravdu chybějící), vysyp ho a automatiku zapni zpět.</li>
        </ol>
      </details>
    );
  }

  const settingsList = (
    <ul className="space-y-0.5">
      {plex.settings?.map((s) => (
        <li key={s.id} className="text-zinc-400">
          {s.title}: <span className={s.on ? "text-amber-300" : "text-zinc-300"}>{s.on ? "zapnuto" : "vypnuto"}</span>
          {s.was_on !== undefined && <span className="text-zinc-600"> (původně {s.was_on ? "zapnuto" : "vypnuto"})</span>}
        </li>
      ))}
    </ul>
  );

  if (!plex.active) {
    return (
      <div className="space-y-2 rounded-lg border border-amber-900/60 bg-amber-950/10 p-3 text-xs text-zinc-300">
        <p className="text-amber-200">
          Přejmenování s Plexem („{plex.section}“) — ať Plex filmy nebere jako nové
          {count > 20 && <b> · {count} filmů, doporučeno</b>}
        </p>
        <p>
          Lumina si zapamatuje filmy v Plexu (zhlédnuto, datum přidání), vypne Plexu automatiku níže, po každé dávce Plex jednou
          prohledá a zkontroluje, že každý film zůstal stejný. Na konci vysype koš a vrátí nastavení, jak bylo.
        </p>
        {settingsList}
        <ol className="list-decimal space-y-1 pl-5">{radarr}{trial}</ol>
        <button disabled={busy} onClick={() => act(startPlexMigration)}
          className="rounded bg-amber-700 px-3 py-1.5 font-medium text-white hover:bg-amber-600 disabled:opacity-50">
          {busy ? "Připravuji…" : "Zahájit přejmenování s Plexem"}
        </button>
        {error && <p className="text-red-400">{error}</p>}
      </div>
    );
  }

  const running = plex.job?.running;
  return (
    <div className="space-y-2 rounded-lg border border-violet-800 bg-violet-950/20 p-3 text-xs text-zinc-300">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-violet-200">
          Přejmenování s Plexem běží — automatika Plexu vypnutá, {plex.movies} filmů zapamatováno
          {plex.started_at && <span className="text-zinc-500"> (od {new Date(plex.started_at + "Z").toLocaleString("cs")})</span>}
        </p>
        <button disabled={busy || running} onClick={runCheck} className="text-violet-300 underline hover:text-violet-200 disabled:opacity-50">
          Zkontrolovat Plex teď
        </button>
      </div>
      <ol className="list-decimal space-y-1 pl-5">{trial}</ol>
      {running ? (
        <p className="animate-pulse text-violet-300">
          {plex.job.phase === "scan" ? "Plex prohledává knihovnu…" : "Porovnávám filmy…"}
        </p>
      ) : plex.job?.error ? (
        <p className="text-red-400">Kontrola selhala: {plex.job.error}</p>
      ) : report ? (
        <div className="space-y-1">
          <p>
            Poslední kontrola: <span className="text-emerald-300">{report.moved} přejmenováno a v Plexu zachováno</span>
            {" · "}{report.unchanged} beze změny{report.new.length > 0 && ` · ${report.new.length} nových`}
            {!problems && <span className="text-emerald-300"> · vše v pořádku</span>}
          </p>
          {report.readded.length > 0 && (
            <div className="text-amber-300">
              <p>Plex přidal znovu jako nový film ({report.readded.length}) — při dokončení lze vrátit zhlédnuto a datum přidání:</p>
              <ul className="pl-4 text-amber-200/80">{report.readded.map((r) => <li key={r.old_key}>{r.title} ({r.year ?? "?"}){r.watched ? " · zhlédnuto" : ""}</li>)}</ul>
            </div>
          )}
          {report.missing.length > 0 && (
            <div className="text-red-300">
              <p>Chybí v Plexu ({report.missing.length}) — soubory nenalezeny, film je v koši:</p>
              <ul className="pl-4 text-red-200/80">{report.missing.map((m) => <li key={m.rating_key}>{m.title} ({m.year ?? "?"})</li>)}</ul>
            </div>
          )}
        </div>
      ) : <p className="text-zinc-500">Zatím bez kontroly — po každé dávce proběhne sama.</p>}
      {settingsList}

      {!finishing ? (
        <button disabled={busy || running} onClick={() => { setEmptyTrash(!report?.missing.length); setRepair(true); setFinishing(true); }}
          className="rounded bg-violet-700 px-3 py-1.5 font-medium text-white hover:bg-violet-600 disabled:opacity-50">
          Dokončit přejmenování…
        </button>
      ) : (
        <div className="space-y-1.5 rounded border border-zinc-700 p-2">
          {report && report.readded.length > 0 && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={repair} onChange={(e) => setRepair(e.target.checked)} />
              Vrátit zhlédnuto a datum přidání u {report.readded.length} znovu přidaných filmů
            </label>
          )}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={emptyTrash} onChange={(e) => setEmptyTrash(e.target.checked)} />
            Vysypat koš v Plexu
            {!!report?.missing.length && <span className="text-red-300"> — smaže i {report.missing.length} chybějících filmů výš</span>}
          </label>
          <p className="text-zinc-500">Nastavení Plexu se vrátí, jak bylo před začátkem.</p>
          <div className="flex gap-2">
            <button disabled={busy} onClick={() => act(async () => { await finishPlexMigration(emptyTrash, repair); setFinishing(false); })}
              className="rounded bg-violet-700 px-3 py-1.5 font-medium text-white hover:bg-violet-600 disabled:opacity-50">
              {busy ? "Dokončuji…" : "Dokončit a vrátit nastavení Plexu"}
            </button>
            <button onClick={() => setFinishing(false)} className="text-zinc-500 hover:text-zinc-300">Zpět</button>
          </div>
        </div>
      )}
      {error && <p className="text-red-400">{error}</p>}
    </div>
  );
}
