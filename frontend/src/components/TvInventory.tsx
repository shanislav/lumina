"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthGate";
import { Suggestion, TvInventory as Inventory, TvInventoryFolder, getTvInventory, setTvOverride, suggest } from "@/lib/api";

/**
 * Kontrola knihovny seriálů (backend modules/library/tv_inventory): what the last scan found in each show
 * folder — which show it is and who says so (Plex, Lumina, the user), and the files with a problem. Before
 * the renamer tidies the library, the folders Lumina and Plex disagree on are decided here.
 */

const SOURCE: Record<string, string> = { plex: "podle Plexu", lumina: "podle názvů", user: "tvoje volba" };

export default function TvInventory() {
  const { can } = useAuth();
  const [data, setData] = useState<Inventory | null>(null);
  const [open, setOpen] = useState(false);
  const [onlyProblems, setOnlyProblems] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const load = () => getTvInventory().then(setData).catch(() => setData(null));
  useEffect(() => { load(); }, []);
  if (!data || !data.folders.length) return null;

  const problems = (f: TvInventoryFolder) => Object.entries(f.counts).filter(([k]) => k !== "ok").reduce((a, [, v]) => a + v, 0);
  const bad = data.folders.filter((f) => f.disagree || !f.tmdb_id || problems(f) > 0);
  const okFiles = data.total.ok ?? 0;
  const allFiles = Object.values(data.total).reduce((a, b) => a + b, 0);
  const list = onlyProblems ? bad : data.folders;

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm">
        <span className="text-zinc-200">Kontrola knihovny seriálů</span>
        <span className="text-xs text-zinc-500">
          v pořádku {okFiles} z {allFiles} souborů · {bad.length ? <span className="text-amber-300">k řešení {bad.length} složek</span> : "vše sedí"} {open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div className="space-y-2 px-4 pb-4 text-xs">
          <p className="text-zinc-500">
            Co poslední sken našel ve složkách seriálů. Seriál složky určuje tvoje volba, jinak Plex (jeho spárování často
            někdo opravil ručně), jinak Lumina podle názvů. Než se knihovna přejmenuje, vyřeš složky, kde se Lumina a Plex neshodnou.
            Po změně spusť znovu „Skenovat“. {data.scanned_at && <>Sken: {data.scanned_at}.</>}
          </p>
          <div className="flex flex-wrap gap-3 text-zinc-400">
            {Object.entries(data.total).filter(([k]) => k !== "ok").map(([k, v]) => (
              <span key={k}>{data.labels[k] ?? k}: <span className="text-zinc-200">{v}</span></span>
            ))}
            <label className="ml-auto flex items-center gap-1">
              <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} /> jen k řešení
            </label>
          </div>
          <div className="divide-y divide-zinc-800/70 rounded-lg border border-zinc-800">
            {list.map((f) => (
              <Folder key={f.folder} f={f} labels={data.labels} problems={problems(f)} canEdit={can("library.edit")}
                expanded={expanded === f.folder} toggle={() => setExpanded(expanded === f.folder ? null : f.folder)} onChanged={load} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function Folder({ f, labels, problems, canEdit, expanded, toggle, onChanged }: {
  f: TvInventoryFolder; labels: Record<string, string>; problems: number; canEdit: boolean;
  expanded: boolean; toggle: () => void; onChanged: () => void;
}) {
  const [choosing, setChoosing] = useState(false);
  async function choose(tmdbId: number | null) {
    await setTvOverride(f.folder, tmdbId);
    setChoosing(false);
    onChanged();
  }
  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button onClick={toggle} className="min-w-0 flex-1 truncate text-left text-zinc-200" title={f.folder}>
          {expanded ? "▾" : "▸"} {f.folder}
        </button>
        <span className={f.tmdb_id ? "text-zinc-300" : "text-red-300"}>
          {f.tmdb_id ? <>{f.title || `TMDB ${f.tmdb_id}`}{f.year ? ` (${f.year})` : ""}</> : "seriál nenalezen"}
          {f.source && <span className="text-zinc-500"> · {SOURCE[f.source] ?? f.source}</span>}
        </span>
        <span className="w-24 text-right text-zinc-500">{f.files} souborů{problems ? <span className="text-amber-300"> · {problems} ✗</span> : ""}</span>
      </div>
      {(f.disagree || !f.tmdb_id || choosing) && canEdit && (
        <div className="mt-1 flex flex-wrap items-center gap-2 pl-4">
          {f.disagree && <span className="text-amber-300">Neshoda:</span>}
          {f.plex_tmdb_id && (
            <button onClick={() => choose(f.plex_tmdb_id!)} className={`rounded border px-2 py-0.5 ${f.tmdb_id === f.plex_tmdb_id && f.source === "user" ? "border-violet-500 text-violet-200" : "border-zinc-700 text-zinc-300 hover:border-violet-500"}`}>
              Plex: {f.plex_title || f.plex_tmdb_id}
            </button>
          )}
          {f.lumina_tmdb_id && f.lumina_tmdb_id !== f.plex_tmdb_id && (
            <button onClick={() => choose(f.lumina_tmdb_id!)} className={`rounded border px-2 py-0.5 ${f.tmdb_id === f.lumina_tmdb_id && f.source === "user" ? "border-violet-500 text-violet-200" : "border-zinc-700 text-zinc-300 hover:border-violet-500"}`}>
              Lumina: {f.lumina_title || f.lumina_tmdb_id}
            </button>
          )}
          <ShowPicker onPick={(id) => choose(id)} />
          {f.source === "user" && <button onClick={() => choose(null)} className="text-zinc-500 hover:text-zinc-300">zrušit volbu</button>}
        </div>
      )}
      {canEdit && !(f.disagree || !f.tmdb_id) && !choosing && (
        <button onClick={() => setChoosing(true)} className="ml-4 mt-0.5 text-[11px] text-zinc-500 hover:text-violet-300">jiný seriál…</button>
      )}
      {expanded && (
        <div className="mt-1.5 space-y-0.5 pl-4">
          {f.problems.length === 0 ? <p className="text-zinc-500">Všechny soubory v pořádku.</p> : f.problems.map((p) => (
            <div key={p.file} className="flex gap-2">
              <span className="w-16 font-mono text-zinc-500">
                {p.season != null && p.episodes[0] != null ? `S${String(p.season).padStart(2, "0")}E${String(p.episodes[0]).padStart(2, "0")}` : "—"}
              </span>
              <span className="w-40 text-amber-300">{labels[p.status] ?? p.status}</span>
              <span className="min-w-0 flex-1 truncate text-zinc-400" title={`${p.file}\n${p.note}`}>{p.file.split("/").slice(-2).join("/")}</span>
              {p.note && <span className="hidden w-64 truncate text-zinc-500 lg:block" title={p.note}>{p.note}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Find a show by name (TMDB) to say which one a folder is. */
function ShowPicker({ onPick }: { onPick: (tmdbId: number) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Suggestion[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) { setItems([]); return; }
    const ctl = new AbortController();
    const t = setTimeout(() => suggest(q, ctl.signal).then((r) => setItems(r.filter((x) => x.media_type === "tv"))).catch(() => {}), 300);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q]);
  return (
    <span className="relative">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="jiný: hledat seriál…"
        className="w-44 rounded border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-zinc-200 outline-none focus:border-violet-500" />
      {items.length > 0 && (
        <span className="absolute left-0 top-6 z-20 block w-72 rounded border border-zinc-700 bg-zinc-900 shadow-xl">
          {items.map((s) => (
            <button key={s.tmdb_id} onClick={() => { onPick(s.tmdb_id); setQ(""); setItems([]); }}
              className="block w-full truncate px-2 py-1 text-left text-zinc-200 hover:bg-zinc-800">
              {s.title} {s.year && <span className="text-zinc-500">({s.year})</span>}
              {s.original_title && s.original_title !== s.title && <span className="text-zinc-500"> · {s.original_title}</span>}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}
