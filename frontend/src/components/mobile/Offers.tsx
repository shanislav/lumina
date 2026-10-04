"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { LibraryAction, MovieContext, OwnedVersion, ScoredFile, pickForProfile, startDownload, versionLabel } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import { FILM_ORDER, Offer, isTorrent, keyOf, useVerifiedOffers } from "@/lib/offers";
import { langName, size } from "@/lib/mobile";
import { BigButton, ChoiceButton, Option, Sheet, Spinner, Tag } from "@/components/mobile/ui";

/** Phone: the files of a film / an episode — the recommended one big on top, the others as cards; filters are
 *  buttons that open a choice (Zvuk ▾, Kvalita ▾, Velikost ▾, Řadit ▾). */

type Audio = "any" | "local" | "local_or_subs" | "custom";
type Sort = "recommended" | "quality" | "small";
interface Filters { audio: Audio; langs: string[]; quality: string; maxGb: number; sort: Sort }
const DEFAULT: Filters = { audio: "any", langs: [], quality: "", maxGb: 0, sort: "recommended" };
const KEY = "lumina.m.filters";

const AUDIO: [Audio, string, string?][] = [
  ["any", "Všechny"], ["local", "CZ nebo SK", "jen s českým / slovenským zvukem"],
  ["local_or_subs", "CZ/SK zvuk nebo titulky"], ["custom", "Vybrat jazyky…"],
];
const QUALITY: [string, string][] = [["", "Všechny"], ["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"], ["SD", "SD"]];
const SIZES: [number, string][] = [[0, "Bez limitu"], [2, "do 2 GB"], [5, "do 5 GB"], [10, "do 10 GB"], [20, "do 20 GB"]];
const SORTS: [Sort, string][] = [["recommended", "Doporučené"], ["quality", "Nejlepší kvalita"], ["small", "Nejmenší"]];
const SOURCE_NAMES: Record<string, string> = { webshare: "WebShare", fastshare: "FastShare", jackett: "torrent", prowlarr: "torrent" };

/** The file in words: "1080p · H.265 · HDR", "CZ dabing", "titulky CZ". */
export function describe(f: ScoredFile): { head: string; tech: string } {
  const head = [f.resolution === "2160p" ? "4K" : f.resolution || "?", size(f.size)].join(" · ");
  const ch = f.audio?.[0]?.channels;
  const tech = [f.codec, f.hdr, ch ? (ch >= 6 ? "5.1" : ch === 2 ? "stereo" : `${ch} kan.`) : ""].filter(Boolean).join(" · ");
  return { head, tech };
}

function FileFacts({ f }: { f: ScoredFile }) {
  const audio = Array.from(new Set((f.audio_langs ?? []).map((l) => l.toLowerCase())));
  const local: string[] = audio.filter((l) => l === "cs" || l === "sk");
  const subs = Array.from(new Set((f.subtitle_langs ?? []).map((l) => l.toLowerCase()))).filter((l) => ["cs", "sk"].includes(l));
  return (
    <div className="flex flex-wrap gap-1.5">
      {local.map((l) => <Tag key={l} tone="good">{langName(l)} dabing</Tag>)}
      {audio.filter((l) => !local.includes(l)).slice(0, 3).map((l) => <Tag key={l}>{langName(l)}</Tag>)}
      {!audio.length && <Tag>zvuk ?</Tag>}
      {subs.map((l) => <Tag key={`s${l}`}>titulky {langName(l)}</Tag>)}
      {f.film === "unsure" && <Tag tone="warn">možná jiný díl</Tag>}
      {f.film === "length" && <Tag tone="warn">nesedí délka</Tag>}
      {f.name_ok && <Tag tone="good">název dílu sedí</Tag>}
      {f.cinema === "audio" && <Tag tone="warn">🎤 {(f.cinema_langs ?? []).map(langName).join("+") || "CZ/SK"} zvuk z kina</Tag>}
      {f.cinema === "likely" && <Tag tone="warn">🎬 nejspíš z kina</Tag>}
      {f.cinema === "suspect" && <Tag tone="warn">⚠ podezřelé</Tag>}
    </div>
  );
}

export function OfferCard({ offer, featured, state, onDownload, canDownload }: {
  offer: Offer; featured?: boolean; state?: string; onDownload: () => void; canDownload: boolean;
}) {
  const f = offer.file;
  const { head, tech } = describe(f);
  const [showName, setShowName] = useState(false);
  const src = Array.from(new Set(offer.copies.map((c) => SOURCE_NAMES[c.source] ?? c.source))).join(" + ");
  return (
    <div className={`space-y-2.5 rounded-2xl border p-4 ${featured ? "border-violet-600 bg-violet-950/20" : "border-zinc-800 bg-zinc-900/60"}`}>
      {featured && <p className="text-sm font-medium text-violet-300">★ Doporučeno</p>}
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xl font-semibold text-zinc-100">{head}</p>
        <span className={`rounded-md px-2 py-0.5 text-sm font-medium ${f.quality_score >= 70 ? "bg-emerald-900/60 text-emerald-200"
          : f.quality_score >= 45 ? "bg-amber-900/50 text-amber-200" : "bg-zinc-800 text-zinc-300"}`} title="skóre kvality">{f.quality_score}</span>
      </div>
      <p className="text-sm text-zinc-400">
        {[tech, src + (isTorrent(f) && f.seeders != null ? ` (${f.seeders} seedů)` : ""), f.verified ? "" : "neověřeno"].filter(Boolean).join(" · ")}
      </p>
      <FileFacts f={f} />
      <button onClick={() => setShowName(!showName)} className={`w-full text-left text-xs text-zinc-500 ${showName ? "break-all" : "truncate"}`}>
        {f.name}
      </button>
      {canDownload && (state ? (
        <div className={`rounded-xl px-4 py-3 text-center text-base ${state === "error" ? "bg-red-950 text-red-300" : "bg-emerald-950 text-emerald-200"}`}>
          {state === "starting" ? "Odesílám…" : state === "error" ? "Stahování se nepovedlo" : state === "queued" ? "✓ Ve frontě"
            : <>✓ Stahuje se · <Link href="/m/downloads" className="underline">průběh</Link></>}
        </div>
      ) : (
        <BigButton kind={featured ? "primary" : "secondary"} onClick={onDownload}>Stáhnout</BigButton>
      ))}
    </div>
  );
}

export default function Offers({ load, tmdbId, title, year, contentType, libraryAction, owned = [], usePick }: {
  load: () => Promise<{ files: ScoredFile[]; movie: MovieContext }>;
  tmdbId?: number; title: string; year?: number; contentType: "movie" | "tv";
  libraryAction?: LibraryAction; owned?: OwnedVersion[]; usePick?: boolean;
}) {
  const { can } = useAuth();
  const [files, setFiles] = useState<ScoredFile[] | null>(null);
  const [movie, setMovie] = useState<MovieContext | null>(null);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState<Filters>(DEFAULT);
  const [sheet, setSheet] = useState<"" | "audio" | "quality" | "size" | "sort">("");
  const [all, setAll] = useState(false);
  const [states, setStates] = useState<Record<string, string>>({});
  const [choosing, setChoosing] = useState<ScoredFile | null>(null);
  const [pickKey, setPickKey] = useState<string | null>(null);

  useEffect(() => {
    try { setFilters({ ...DEFAULT, ...JSON.parse(localStorage.getItem(KEY) || "{}") }); } catch { /* defaults */ }
  }, []);
  const update = (patch: Partial<Filters>) => setFilters((prev) => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* private */ }
    return next;
  });

  useEffect(() => {
    let alive = true;
    setFiles(null);
    load().then((r) => { if (alive) { setFiles(r.files); setMovie(r.movie); } })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Hledání selhalo"));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { offers, verify } = useVerifiedOffers(files ?? [], movie);

  // what the default profile would take (films) — the recommended file
  useEffect(() => {
    if (!usePick) return;
    const cand = offers.map((o) => o.file).filter((f) => f.film === "yes" || f.film === "unsure");
    if (!cand.length) { setPickKey(null); return; }
    const t = setTimeout(() => pickForProfile(null, cand).then((p) => setPickKey(p.key)).catch(() => {}), 500);
    return () => clearTimeout(t);
  }, [offers, usePick]);

  const langsAround = useMemo(() => {
    const count = new Map<string, number>();
    for (const o of offers) for (const l of new Set((o.file.audio_langs ?? []).map((x) => x.toLowerCase()))) count.set(l, (count.get(l) ?? 0) + 1);
    const rest = Array.from(count).filter(([l]) => !["cs", "sk", "en"].includes(l) && l.length <= 3).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([l]) => l);
    return ["cs", "sk", "en", ...rest];
  }, [offers]);

  const view = useMemo(() => {
    const pass = (f: ScoredFile) => {
      if (f.film === "no") return false;
      if (filters.quality && f.resolution !== filters.quality) return false;
      if (filters.maxGb && f.size > filters.maxGb * 1e9) return false;
      if (filters.audio === "local" && f.lang_tier < 2) return false;
      if (filters.audio === "local_or_subs" && f.lang_tier < 1) return false;
      if (filters.audio === "custom" && filters.langs.length) {
        const have = new Set((f.audio_langs ?? []).map((l) => l.toLowerCase()));
        if (!filters.langs.some((l) => have.has(l))) return false;
      }
      return true;
    };
    return offers.filter((o) => pass(o.file)).sort((x, y) => {
      const a = x.file, b = y.file;
      if (filters.sort === "small") return a.size - b.size;
      if (filters.sort === "quality") return b.quality_score - a.quality_score || a.size - b.size;
      return (FILM_ORDER[a.film] ?? 1) - (FILM_ORDER[b.film] ?? 1) || b.lang_tier - a.lang_tier
        || b.quality_score - a.quality_score || a.size - b.size;
    });
  }, [offers, filters]);

  // the recommended one: the profile's pick when it passes the filters, else the first sure one
  // never a cinema recording (before the digital release everything is one: nothing is recommended)
  const recommendable = (o: Offer) => o.file.film === "yes" && !["likely", "suspect"].includes(o.file.cinema ?? "");
  const featured = (pickKey && view.find((o) => o.copies.some((c) => keyOf(c) === pickKey))) || view.find(recommendable);
  const rest = view.filter((o) => o !== featured);
  const shown = all ? rest : rest.slice(0, 6);

  async function run(file: ScoredFile, action?: LibraryAction) {
    setChoosing(null);
    setStates((s) => ({ ...s, [file.ident]: "starting" }));
    try {
      const r = await startDownload(file, undefined, tmdbId, title, year, contentType, action ?? libraryAction);
      setStates((s) => ({ ...s, [file.ident]: r.queued ? "queued" : "ok" }));
    } catch {
      setStates((s) => ({ ...s, [file.ident]: "error" }));
    }
  }
  const download = (f: ScoredFile) => (owned.length && contentType === "movie" ? setChoosing(f) : run(f));

  if (error) return <p className="py-6 text-red-400">{error}</p>;
  if (!files) return <Spinner text="Hledám soubory… (chvíli to trvá)" />;
  if (!files.length) return <p className="py-6 text-zinc-400">Žádné soubory se nenašly.</p>;

  const audioLabel = filters.audio === "custom" && filters.langs.length ? filters.langs.map(langName).join("+")
    : AUDIO.find(([k]) => k === filters.audio)?.[1] ?? "";
  return (
    <div className="space-y-4">
      <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1">
        <ChoiceButton label="Zvuk" value={audioLabel} active={filters.audio !== "any"} onClick={() => setSheet("audio")} />
        <ChoiceButton label="Kvalita" value={QUALITY.find(([k]) => k === filters.quality)?.[1] ?? ""} active={!!filters.quality} onClick={() => setSheet("quality")} />
        <ChoiceButton label="Velikost" value={SIZES.find(([k]) => k === filters.maxGb)?.[1] ?? ""} active={!!filters.maxGb} onClick={() => setSheet("size")} />
        <ChoiceButton label="Řadit" value={SORTS.find(([k]) => k === filters.sort)?.[1] ?? ""} active={filters.sort !== "recommended"} onClick={() => setSheet("sort")} />
      </div>
      {verify.running && <p className="text-sm text-zinc-500">Ověřuji soubory u zdrojů… {verify.done}/{verify.total}</p>}
      {movie?.pre_digital && (
        <p className="rounded-xl border border-amber-800/70 bg-amber-950/30 px-4 py-3 text-amber-200">
          🎬 Film zatím nevyšel digitálně{movie.releases?.digital ? ` (vyjde ${new Date(movie.releases.digital).toLocaleDateString("cs-CZ")})` : ""}.
          Soubory jsou nejspíš z kina.
        </p>
      )}

      {featured ? (
        <OfferCard offer={featured} featured state={states[featured.file.ident]} onDownload={() => download(featured.file)} canDownload={can("download")} />
      ) : (
        <p className="rounded-xl border border-zinc-800 p-4 text-zinc-400">
          {movie?.pre_digital ? "Nic k doporučení — dokud film nevyjde digitálně, jsou soubory jen z kina." : "S těmito filtry nic jistého. Zkus filtr povolit."}
        </p>
      )}
      {rest.length > 0 && <p className="pt-2 text-sm text-zinc-500">Další soubory ({rest.length})</p>}
      {shown.map((o) => (
        <OfferCard key={o.key} offer={o} state={states[o.file.ident]} onDownload={() => download(o.file)} canDownload={can("download")} />
      ))}
      {rest.length > shown.length && <BigButton kind="secondary" onClick={() => setAll(true)}>Ukázat všechny ({rest.length})</BigButton>}

      <Sheet open={sheet === "audio"} title="Zvuk" onClose={() => setSheet("")}>
        {AUDIO.map(([k, l, hint]) => (
          <Option key={k} active={filters.audio === k} hint={hint} onClick={() => { update({ audio: k }); if (k !== "custom") setSheet(""); }}>{l}</Option>
        ))}
        {filters.audio === "custom" && (
          <div className="mt-2 flex flex-wrap gap-2 px-2">
            {langsAround.map((l) => {
              const on = filters.langs.includes(l);
              return (
                <button key={l} onClick={() => update({ langs: on ? filters.langs.filter((x) => x !== l) : [...filters.langs, l] })}
                  className={`min-h-11 min-w-14 rounded-xl border px-4 text-base ${on ? "border-violet-500 bg-violet-950 text-violet-100" : "border-zinc-700 text-zinc-300"}`}>
                  {langName(l)}
                </button>
              );
            })}
            <p className="w-full pt-1 text-sm text-zinc-500">Soubor musí mít aspoň jeden z vybraných jazyků.</p>
          </div>
        )}
      </Sheet>
      <Sheet open={sheet === "quality"} title="Kvalita" onClose={() => setSheet("")}>
        {QUALITY.map(([k, l]) => <Option key={k} active={filters.quality === k} onClick={() => { update({ quality: k }); setSheet(""); }}>{l}</Option>)}
      </Sheet>
      <Sheet open={sheet === "size"} title="Velikost" onClose={() => setSheet("")}>
        {SIZES.map(([k, l]) => <Option key={k} active={filters.maxGb === k} onClick={() => { update({ maxGb: k }); setSheet(""); }}>{l}</Option>)}
      </Sheet>
      <Sheet open={sheet === "sort"} title="Řadit" onClose={() => setSheet("")}>
        {SORTS.map(([k, l]) => <Option key={k} active={filters.sort === k} onClick={() => { update({ sort: k }); setSheet(""); }}>{l}</Option>)}
      </Sheet>

      <Sheet open={!!choosing} title="Tento film už máš" onClose={() => setChoosing(null)}>
        {choosing && (
          <div className="space-y-3">
            <BigButton onClick={() => run(choosing, { mode: "version" })}>Stáhnout jako další verzi</BigButton>
            <p className="px-1 text-sm text-zinc-500">Stávající zůstane, nová se uloží vedle ní.</p>
            {can("library.delete") && owned.map((v) => (
              <BigButton key={v.id} kind="secondary" onClick={() => run(choosing, { mode: "replace", file_id: v.id })}>
                Nahradit {versionLabel(v) || "starou verzi"}
              </BigButton>
            ))}
          </div>
        )}
      </Sheet>
    </div>
  );
}
