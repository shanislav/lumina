"use client";

import { useState } from "react";
import RenameChecklist from "@/components/RenameChecklist";
import {
  TvOrganizePlan, TvOrganizeResult, applyTvOrganize, checkPlexMigrationAndWait, getPlexMigration,
  getTvOrganizePlans, setTvNumbering, undoOrganize,
} from "@/lib/api";

/**
 * Opravit názvy seriálů na disku (backend modules/library/organize_tv): every show folder to the naming
 * rules — plan, preview, apply in one undoable batch. With a Plex migration running each batch goes in two
 * steps (names, then folders), Plex checked after each — as with movies (decisions/0008).
 */

const KIND_LABEL: Record<string, string> = { video: "dílů", sidecar: "titulků a dalších", extra: "bonusů", other: "ostatních" };

export default function TvRename({ onChanged }: { onChanged?: () => void }) {
  const [open, setOpen] = useState(false);
  const [plans, setPlans] = useState<TvOrganizePlan[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const [result, setResult] = useState<TvOrganizeResult | null>(null);
  const [batches, setBatches] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  async function load(keepResult = false) {
    setPlans(null);
    setError(null);
    if (!keepResult) setResult(null);
    try {
      const all = await getTvOrganizePlans();
      setPlans(all);
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
      setPlans([]);
    }
  }

  function show() {
    setOpen(true);
    load();
  }

  async function changeNumbering(plan: TvOrganizePlan, numbering: "files" | "tmdb") {
    try {
      await setTvNumbering(plan.tmdb_id, numbering);
      // every folder of the show gets the new plan
      const fresh = await Promise.all((plans ?? []).filter((p) => p.tmdb_id === plan.tmdb_id).map((p) => getTvOrganizePlans(p.folder)));
      const byFolder = new Map(fresh.flat().map((p) => [p.folder, p]));
      setPlans((plans ?? []).map((p) => byFolder.get(p.folder) ?? p));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    }
  }

  async function run() {
    const folders = Array.from(selected);
    setBusy(true);
    setError(null);
    try {
      const plex = await getPlexMigration("show").catch(() => null);
      // the movies' migration has Plex's scanning off for the whole server: TV renames would go unseen
      if (plex?.other_running) {
        setError("Běží přejmenování filmů s Plexem — nejdřív ho dokonči (Opravit názvy filmů → Dokončit), pak seriály.");
        return;
      }
      if (plex?.configured && !plex.error && !plex.active && !window.confirm(
        "Přejmenování s Plexem neběží — Plex si nezapamatuje zhlédnuté díly a do příštího prohledání může hlásit chybějící soubory. Pokračovat bez něj?")) {
        return;
      }
      let res: TvOrganizeResult;
      if (plex?.active) {
        setStep("Přejmenovávám soubory…");
        const names = await applyTvOrganize(folders, "names");
        setStep("Plex prohledává knihovnu (1/2)…");
        await checkPlexMigrationAndWait("show");
        setStep("Přesouvám složky…");
        // after the first step a folder may be the same — its plan is computed again from the renamed files
        const all = await applyTvOrganize(folders);
        setStep("Plex prohledává knihovnu (2/2)…");
        await checkPlexMigrationAndWait("show");
        const seen = new Set(all.done.map((d) => d.folder));
        res = { ...all, done: [...all.done, ...names.done.filter((d) => !seen.has(d.folder))], failed: [...names.failed, ...all.failed] };
      } else {
        res = await applyTvOrganize(folders);
      }
      setResult(res);
      setBatches((n) => n + 1);
      onChanged?.();
      await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(false);
      setStep(null);
    }
  }

  async function undo(batchId: string) {
    setBusy(true);
    try {
      await undoOrganize(batchId);
      if ((await getPlexMigration("show").catch(() => null))?.active) {
        setStep("Plex prohledává knihovnu…");
        await checkPlexMigrationAndWait("show");
      }
      setResult(null);
      setBatches((n) => n + 1);
      onChanged?.();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba");
    } finally {
      setBusy(false);
      setStep(null);
    }
  }

  // a show with numbering by names only suggested is picked by hand, after a look at its new numbers
  const ready = (plans ?? []).filter((p) => !p.conflicts.length && !p.suggested);

  return (
    <>
      <button onClick={show} title="Přejmenuje složky a soubory seriálů podle pravidel (s náhledem)"
        className="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500">
        Opravit názvy seriálů
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => !busy && setOpen(false)}>
          <div className="mx-2 flex max-h-[92vh] w-full max-w-5xl flex-col space-y-3 rounded-xl border border-zinc-700 bg-zinc-900 p-4 sm:mx-4 sm:p-6"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-zinc-100">Opravit názvy seriálů na disku</h3>
              <button onClick={() => setOpen(false)} disabled={busy} className="text-sm text-zinc-500 hover:text-zinc-300">Zavřít</button>
            </div>
            <p className="text-xs text-zinc-500">
              Čísla dílů zůstanou tvoje (jak je ukazuje Plex, jinak podle souboru), u seriálu jde zvolit číslování podle TMDB.
              Titulky jdou s dílem, bonusy se složkou seriálu, ostatní zůstane na svém místě v nové složce.
              Nic se nepřepisuje, každou dávku lze vrátit. Vychází z posledního skenu knihovny.
            </p>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <div className="max-h-[35vh] shrink-0 overflow-y-auto">
              <RenameChecklist kind="show" count={plans?.length ?? 0} batches={batches}
                onPick={(n) => setSelected(new Set(ready.slice(0, n).map((p) => p.folder)))} />
            </div>
            {result && (
              <div className="space-y-1 rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-sm">
                {result.done.length > 0 && <p className="text-green-400">Opraveno: {result.done.map((d) => d.title).join(", ")}</p>}
                {result.failed.map((f) => <p key={f.folder} className="text-red-400">{f.folder}: {f.error}</p>)}
                {result.batch_id && (
                  <button disabled={busy} onClick={() => undo(result.batch_id!)} className="text-xs text-violet-300 underline hover:text-violet-200">
                    Vrátit tuto dávku
                  </button>
                )}
              </div>
            )}
            {plans === null ? (
              <p className="animate-pulse text-sm text-zinc-500">Počítám změny…</p>
            ) : plans.length === 0 ? (
              !error && <p className="text-sm text-green-400">Všechny seriály už odpovídají pravidlům.</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
                  <span>{plans.length} seriálů ke změně · vybráno {selected.size}</span>
                  <button className="text-violet-400 hover:text-violet-300" onClick={() => setSelected(new Set(ready.map((p) => p.folder)))}>vše</button>
                  <button className="text-violet-400 hover:text-violet-300" onClick={() => setSelected(new Set())}>nic</button>
                </div>
                <div className="min-h-0 space-y-2 overflow-y-auto pr-1">
                  {plans.map((plan) => (
                    <ShowPlan key={plan.folder} plan={plan} checked={selected.has(plan.folder)} expanded={expanded === plan.folder}
                      onToggle={() => setExpanded(expanded === plan.folder ? null : plan.folder)}
                      onCheck={(on) => {
                        const next = new Set(selected);
                        if (on) next.add(plan.folder); else next.delete(plan.folder);
                        setSelected(next);
                      }}
                      onNumbering={(n) => changeNumbering(plan, n)} disabled={busy} />
                  ))}
                </div>
                <div className="flex justify-end">
                  <button onClick={run} disabled={busy || selected.size === 0}
                    className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:bg-zinc-700">
                    {busy ? (step || "Opravuji…") : `Opravit ${selected.size} seriálů`}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function ShowPlan({ plan, checked, expanded, onToggle, onCheck, onNumbering, disabled }: {
  plan: TvOrganizePlan; checked: boolean; expanded: boolean; disabled: boolean;
  onToggle: () => void; onCheck: (on: boolean) => void; onNumbering: (n: "files" | "tmdb") => void;
}) {
  const videos = plan.ops.filter((op) => op.kind === "video");
  const rest = plan.ops.filter((op) => op.kind !== "video");
  const name = (p: string) => p.split("/").pop();
  return (
    <div className={`rounded-lg border p-3 ${checked ? "border-violet-700 bg-violet-950/20" : "border-zinc-800"}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <input type="checkbox" disabled={plan.conflicts.length > 0 || disabled} checked={checked} onChange={(e) => onCheck(e.target.checked)} />
        <button onClick={onToggle} className="min-w-0 flex-1 truncate text-left text-sm text-zinc-100">
          {expanded ? "▾" : "▸"} {plan.title} {plan.year ? `(${plan.year})` : ""}
        </button>
        <span className="text-xs text-zinc-500">
          {Object.entries(plan.kinds).map(([k, v]) => `${v} ${KIND_LABEL[k] ?? k}`).join(" · ")}
        </span>
        <select value={plan.numbering} disabled={disabled} onChange={(e) => onNumbering(e.target.value as "files" | "tmdb")}
          className="rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 text-xs text-zinc-300" title="Číslování dílů">
          <option value="files">čísla podle souborů / Plexu</option>
          <option value="tmdb">čísla podle názvu dílu</option>
        </select>
      </div>
      {plan.source_folder !== plan.target_folder && (
        <p className="mt-1 break-all font-mono text-[11px] text-zinc-400">
          📁 <span className="text-red-300/80 line-through">{plan.source_folder}</span> → <span className="text-green-300">{plan.target_folder}</span>
        </p>
      )}
      {plan.conflicts.map((c) => <p key={c} className="mt-1 text-xs text-red-400">{c}</p>)}
      {plan.suggested && (
        <p className="mt-1 text-xs text-amber-300">
          Navrženo „čísla podle názvu dílu“: {plan.sure_names} souborů má v názvu jiný díl, než říká jejich číslo (např. české
          pořadí vysílání). Zkontroluj změny čísel níže — nebo přepni na čísla podle souborů.
        </p>
      )}
      {plan.tips.filter(() => !plan.suggested).map((t) => <p key={t} className="mt-1 text-xs text-amber-300">💡 {t}</p>)}
      {plan.renumber.length > 0 && (
        <div className="mt-1 space-y-0.5 font-mono text-[11px] text-sky-300">
          {(expanded ? plan.renumber : plan.renumber.slice(0, 6)).map((r) => (
            <p key={r.file} className="break-all">🔢 {r.from} → {r.to} {r.title && `„${r.title}“`} <span className="text-zinc-600">{name(r.file)}</span></p>
          ))}
          {!expanded && plan.renumber.length > 6 && <p className="text-zinc-500">+ dalších {plan.renumber.length - 6} změn čísel…</p>}
        </div>
      )}
      {plan.unsure.map((u) => <p key={u.file} className="mt-0.5 break-all text-[11px] text-amber-400/80">⚠ {name(u.file)}: {u.why}</p>)}
      {plan.renumbered > 0 && (
        <p className="mt-1 text-xs text-amber-300">
          {plan.renumbered} dílů dostane jiné číslo, než ukazuje Plex — Plex je přidá jako nové; přejmenování s Plexem jim
          při dokončení vrátí zhlédnuto a datum přidání.
        </p>
      )}
      {plan.media_missing > 0 && (
        <p className="mt-1 text-xs text-amber-300">{plan.media_missing} souborů ještě bez MediaInfo (rozlišení, jazyky) — dokonči sken knihovny.</p>
      )}
      {plan.skipped.length > 0 && <p className="mt-1 text-xs text-zinc-500">{plan.skipped.length} souborů zůstane, kde je (neznámý díl).</p>}
      <div className="mt-1 space-y-0.5 font-mono text-[11px]">
        {(expanded ? videos : videos.slice(0, 3)).map((op) => (
          <p key={op.src} className="break-all text-zinc-400">
            🎞 <span className="text-red-300/80">{name(op.src)}</span> → <span className="text-green-300">{op.dst.split("/").slice(1).join("/")}</span>
          </p>
        ))}
        {!expanded && videos.length > 3 && (
          <button onClick={onToggle} className="text-zinc-500 hover:text-zinc-300">+ dalších {videos.length - 3} dílů…</button>
        )}
        {expanded && rest.map((op) => (
          <p key={op.src} className="break-all text-zinc-500">
            {op.kind === "sidecar" ? "📝" : op.kind === "extra" ? "🎁" : "📄"} {op.src} → {op.dst}
          </p>
        ))}
        {expanded && plan.skipped.map((s) => <p key={s.file} className="break-all text-zinc-600">⏸ {s.file} — {s.why}</p>)}
      </div>
    </div>
  );
}
