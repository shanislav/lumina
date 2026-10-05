"use client";

import { WantedShows, WatchedShows } from "@/components/SeriesAuto";
import { useCallback, useEffect, useState } from "react";
import WatchedList from "@/components/WatchedList";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthGate";
import {
  QualityProfile,
  ScoredFile,
  WantedItem,
  WantedJob,
  checkWanted,
  formatSize,
  getProfiles,
  getWanted,
  getWantedJob,
  getWantedUnseen,
  markWantedSeen,
  removeWanted,
  startDownload,
  updateWanted,
} from "@/lib/api";

const STATUS: Record<WantedItem["status"], { label: string; cls: string }> = {
  wanted: { label: "Hledám", cls: "bg-zinc-800 text-zinc-300" },
  found: { label: "Nalezeno", cls: "bg-green-900/70 text-green-200" },
  downloading: { label: "Stahuje se", cls: "bg-violet-900/70 text-violet-200" },
  done: { label: "V knihovně", cls: "bg-emerald-950 text-emerald-400" },
};

export default function WantedPage() {
  const router = useRouter();
  const { can, user } = useAuth();
  const manage = can("wanted");
  // who may not download (a child's account) only asks: no profiles, searches or downloads here
  const admin = can("download");
  const [since, setSince] = useState("");     // what others added after this: marked "nové"
  const [items, setItems] = useState<WantedItem[]>([]);
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [job, setJob] = useState<WantedJob | null>(null);
  const [downloading, setDownloading] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"wanted" | "watched">("wanted");

  const load = useCallback(async () => {
    try {
      setItems(await getWanted());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    if (manage) getWantedUnseen().then((u) => { if (u.count) setSince(u.since); markWantedSeen(); }).catch(() => {});
    getProfiles().then(setProfiles).catch(() => {});
    getWantedJob().then(setJob).catch(() => {});
  }, [load]);

  // follow a running check
  useEffect(() => {
    if (!job?.running) return;
    const t = setTimeout(async () => {
      try {
        setJob(await getWantedJob());
        await load();
      } catch { /* next tick */ }
    }, 4000);
    return () => clearTimeout(t);
  }, [job, load]);

  const defaultProfile = profiles.find((p) => p.is_default);
  const profileName = (id: number | null) => (profiles.find((p) => p.id === id) ?? defaultProfile)?.name ?? "—";

  async function download(item: WantedItem) {
    const b = item.best;
    if (!b.ident || !b.source) return;
    setDownloading((prev) => ({ ...prev, [item.id]: "…" }));
    try {
      const file = { ident: b.ident, name: b.name ?? "", size: b.size ?? 0, source: b.source, source_id: b.source_id ?? 0,
        magnet_url: b.magnet_url ?? null } as ScoredFile;
      const r = await startDownload(file, undefined, item.tmdb_id ?? 0, item.title, parseInt(item.year || "0"), "movie");
      setDownloading((prev) => ({ ...prev, [item.id]: r.queued ? "ve frontě" : "stahuje se" }));
    } catch {
      setDownloading((prev) => ({ ...prev, [item.id]: "chyba" }));
    }
  }

  function openOffers(item: WantedItem) {
    const movie = { tmdb_id: item.tmdb_id ?? 0, title: item.title, original_title: item.original_title, year: item.year,
      overview: "", poster_url: item.poster_url, media_type: "movie", wikidata_id: item.wikidata_id || null };
    router.push(`/?movie=${btoa(encodeURIComponent(JSON.stringify(movie)))}`);
  }

  const open = items.filter((i) => i.status !== "done");

  return (
    <main className="flex flex-col gap-6 px-4 py-8 max-w-5xl mx-auto">
      {admin && <div className="flex gap-1 bg-zinc-900 p-1 rounded-lg w-fit">
        {([["wanted", "Chci"], ["watched", "Hlídám lepší verzi"]] as const).map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium ${tab === key ? "bg-violet-600 text-white" : "text-zinc-400 hover:text-zinc-200"}`}>
            {label}
          </button>
        ))}
      </div>}
      {tab === "watched" ? (
        <div className="space-y-8">
          <WatchedList profiles={profiles} />
          <WatchedShows canEdit={can("library.edit")} canDownload={can("download")} />
        </div>
      ) : (<>
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-bold text-zinc-100">Chci</h1>
        <span className="text-sm text-zinc-500">{open.length} čeká · {items.length - open.length} hotovo</span>
        <div className="flex-1" />
        {job?.running ? (
          <span className="text-sm text-violet-300 animate-pulse">
            Hledám {job.done}/{job.total}{job.current ? ` · ${job.current}` : ""} · nalezeno {job.found}
          </span>
        ) : manage && admin && (
          <button disabled={!open.length} onClick={async () => setJob(await checkWanted())}
            className="rounded bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-40">
            Zkontrolovat vše
          </button>
        )}
      </div>
      {!admin ? (
        <p className="text-xs text-zinc-500 -mt-3">
          Co chceš vidět, přidáš tlačítkem „★ Chci“ u filmu nebo seriálu. Správce uvidí, že to chceš, a postará se o to.
        </p>
      ) : (
      <p className="text-xs text-zinc-500 -mt-3">
        Film přidáš tlačítkem „+ Chci“ u hledaného filmu, seriál tlačítkem „Chci“ na jeho stránce. Lumina hledá postupně (šetrně k WS/FS) a ukáže nejlepší soubor,
        který splní profil. Až bude film v knihovně, přesune se do „hotovo“. Co přidá účet bez práva stahovat,
        Lumina sama nestáhne (ani se zapnutou automatikou) — rozhodneš ty.
      </p>
      )}
      <WantedShows />

      {loading ? (
        <p className="text-zinc-500 animate-pulse">Načítám…</p>
      ) : items.length === 0 ? (
        <p className="text-zinc-500">Seznam je prázdný.</p>
      ) : (
        <div className="space-y-3">
          {items.map((item) => {
            const tv = item.media_type === "tv";
            const mine = (item.added_by || "").toLowerCase() === user.username.toLowerCase();
            const fresh = !!since && item.status !== "done" && item.added_at > since && !mine;
            return (
            <div key={item.id} className={`flex gap-4 rounded-xl border bg-zinc-900/50 p-3 ${fresh ? "border-amber-600/80" : "border-zinc-800"} ${item.status === "done" ? "opacity-60" : ""}`}>
              <div className="w-16 h-24 relative flex-shrink-0 rounded overflow-hidden bg-zinc-800">
                {item.poster_url && (
                  <Image src={item.poster_url} alt="" fill sizes="64px" className="object-cover" unoptimized={!!item.wikidata_id} />
                )}
              </div>
              <div className="flex-1 min-w-0 space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  {tv && item.tmdb_id ? (
                    <Link href={`/series?tmdb=${item.tmdb_id}`} className="text-zinc-100 font-medium hover:text-violet-300">{item.title}</Link>
                  ) : <span className="text-zinc-100 font-medium">{item.title}</span>}
                  {item.year && <span className="text-zinc-500">({item.year})</span>}
                  {tv && <span className="rounded px-1.5 py-0.5 text-[10px] bg-sky-900/70 text-sky-200">seriál</span>}
                  {fresh && <span className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-amber-600 text-amber-50">nové</span>}
                  {tv && item.status !== "done" ? (
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-amber-900/70 text-amber-200">Čeká na správce</span>
                  ) : <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${STATUS[item.status].cls}`}>{STATUS[item.status].label}</span>}
                  {item.wikidata_id && <span className="rounded px-1.5 py-0.5 text-[10px] bg-zinc-700 text-zinc-200">Wikidata</span>}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
                  {tv ? (admin && <span>Otevři seriál a nastav „Chci“ (profil, zvuk) — žádost se tím vyřídí.</span>) : admin && <>
                  Profil:
                  <select disabled={!manage} value={item.profile_id ?? ""} className="rounded bg-zinc-800 border border-zinc-700 px-1.5 py-0.5 text-zinc-300"
                    onChange={async (e) => {
                      await updateWanted(item.id, { profile_id: e.target.value === "" ? null : Number(e.target.value) });
                      load();
                    }}>
                    <option value="">výchozí ({defaultProfile?.name ?? "—"})</option>
                    {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                  <label title="Plánovač se zapnutým automatickým stahováním ho stáhne sám, jakmile ho najde"
                    className={`flex items-center gap-1 ${item.auto === 0 ? "text-amber-300" : ""}`}>
                    <input type="checkbox" checked={item.auto !== 0}
                      onChange={async (e) => { await updateWanted(item.id, { auto: e.target.checked }); load(); }} />
                    stáhnout automaticky
                  </label>
                  </>}
                  {item.added_by && <span className="text-zinc-500">přidal {item.added_by}</span>}
                  {item.checked_at && <span className="text-zinc-600">kontrola {item.checked_at}</span>}
                </div>
                {item.status === "found" && item.best.name ? (
                  <div className="text-xs">
                    <p className="text-green-300">
                      Nejlepší z {item.matches}: {item.best.quality_summary} · skóre {item.best.quality_score}
                      {" · "}{formatSize(item.best.size ?? 0)} · {item.best.source === "webshare" ? "WS" : item.best.source === "fastshare" ? "FS" : "torrent"}
                      {item.best.verified ? " · ověřeno" : " · podle názvu"}
                    </p>
                    <p className="text-zinc-500 truncate" title={item.best.name}>{item.best.name}</p>
                  </div>
                ) : item.status === "wanted" && item.waiting ? (
                  <p className="text-xs text-amber-300">🎬 {item.waiting} — zatím existují jen záznamy z kina, Lumina je nehledá.</p>
                ) : item.status === "wanted" && item.checked_at ? (
                  <p className="text-xs text-zinc-500">Zatím nic, co by splnilo profil „{profileName(item.profile_id)}“.</p>
                ) : null}
              </div>
              <div className="flex flex-col items-end gap-1.5 text-xs">
                {item.status === "found" && can("download") && (
                  downloading[item.id] ? <span className="text-green-300">{downloading[item.id]}</span> : (
                    <button onClick={() => download(item)} className="rounded bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-500">
                      Stáhnout nejlepší
                    </button>
                  )
                )}
                {item.status === "downloading" && manage && admin && (
                  <button title="Stahování se nepovedlo? Vrátit mezi hledané" className="text-zinc-400 hover:text-zinc-200"
                    onClick={async () => { await updateWanted(item.id, { status: "wanted" }); load(); }}>
                    Znovu hledat
                  </button>
                )}
                {item.status !== "done" && tv && !!item.tmdb_id && (
                  <Link href={`/series?tmdb=${item.tmdb_id}`} className="text-violet-300 hover:text-violet-200">Otevřít seriál</Link>
                )}
                {item.status !== "done" && !tv && admin && (
                  <>
                    <button onClick={() => openOffers(item)} className="text-violet-300 hover:text-violet-200">Všechny nabídky</button>
                    {manage && <button disabled={!!job?.running} onClick={async () => setJob(await checkWanted([item.id]))}
                      className="text-zinc-400 hover:text-zinc-200 disabled:opacity-40">Hledat teď</button>}
                  </>
                )}
                {manage && (admin || mine) && (
                  <button onClick={async () => { await removeWanted(item.id); load(); }} className="text-zinc-600 hover:text-red-400">
                    {tv && admin ? "Vyřízeno / odebrat" : "Odebrat"}
                  </button>
                )}
              </div>
            </div>
            );
          })}
        </div>
      )}
      </>)}
    </main>
  );
}
