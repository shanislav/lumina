"use client";

import { ReactNode, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { confirmCinema } from "@/lib/cinema";
import {
  LibraryAction, MovieContext, OwnedVersion, ProfilePick, QualityProfile, ScoredFile, getProfiles, pickForProfile, startDownload,
  versionLabel,
} from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import { FILM_ORDER, Offer, isTorrent, keyOf, useVerifiedOffers } from "@/lib/offers";
import { langName, size } from "@/lib/mobile";
import { BigButton, ChoiceButton, Option, Sheet, Spinner, Tag } from "@/components/mobile/ui";

/** Phone: the files of a film / an episode. On top what the quality profile would take (its profile changed right
 *  in that card, as the computer's "výchozí profil ▾" / 🎯), under it the classic recommendation (the best sure
 *  file by the filters), then every other file; filters are buttons that open a choice (Zvuk ▾, Kvalita ▾,
 *  Velikost ▾, Řadit ▾) — the profile hides nothing. */

type Audio = "any" | "local" | "local_or_subs" | "custom";
type Sort = "recommended" | "quality" | "small";
interface Filters { audio: Audio; langs: string[]; quality: string; maxGb: number; sort: Sort }
const DEFAULT: Filters = { audio: "any", langs: [], quality: "", maxGb: 0, sort: "recommended" };
const KEY = "lumina.m.filters";
// the chosen profile per kind: "" = the default one, "off" = no profile (everything shown)
type ProfileChoice = number | "" | "off";
const PROFILE_KEY = (kind: string) => `lumina.m.profile.${kind}`;

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

export function OfferCard({ offer, featured, label, state, onDownload, canDownload }: {
  offer: Offer; featured?: boolean; label?: ReactNode; state?: string; onDownload: () => void; canDownload: boolean;
}) {
  const f = offer.file;
  const { head, tech } = describe(f);
  const [showName, setShowName] = useState(false);
  const src = Array.from(new Set(offer.copies.map((c) => SOURCE_NAMES[c.source] ?? c.source))).join(" + ");
  return (
    <div className={`space-y-2.5 rounded-2xl border p-4 ${featured ? "border-violet-600 bg-violet-950/20" : "border-zinc-800 bg-zinc-900/60"}`}>
      {label ?? (featured && <p className="text-sm font-medium text-violet-300">★ Doporučeno</p>)}
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
  const [sheet, setSheet] = useState<"" | "profile" | "audio" | "quality" | "size" | "sort">("");
  const [all, setAll] = useState(false);
  const [states, setStates] = useState<Record<string, string>>({});
  const [choosing, setChoosing] = useState<ScoredFile | null>(null);
  const [pick, setPick] = useState<ProfilePick | null>(null);
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [profile, setProfile] = useState<ProfileChoice>("");
  void usePick;

  useEffect(() => {
    getProfiles(contentType).then(setProfiles).catch(() => {});
    try {
      const v = localStorage.getItem(PROFILE_KEY(contentType));
      if (v) setProfile(v === "off" ? "off" : Number(v) || "");
    } catch { /* private */ }
  }, [contentType]);
  const chooseProfile = (v: ProfileChoice) => {
    setProfile(v);
    try { localStorage.setItem(PROFILE_KEY(contentType), String(v)); } catch { /* private */ }
  };

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

  // what the chosen profile would take (the card on top)
  useEffect(() => {
    if (profile === "off") { setPick(null); return; }
    const cand = offers.map((o) => o.file).filter((f) => f.film === "yes" || f.film === "unsure");
    if (!cand.length) { setPick(null); return; }
    let alive = true;
    const t = setTimeout(() => pickForProfile(profile === "" ? null : profile, cand, contentType)
      .then((p) => { if (alive) setPick(p); }).catch(() => {}), 500);
    return () => { alive = false; clearTimeout(t); };
  }, [offers, profile, contentType]);
  const pickKey = pick?.key ?? null;

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
  const defaultProfile = profiles.find((p) => p.is_default);
  const profileLabel = profile === "off" ? "žádný" : profile === ""
    ? `${defaultProfile?.name ?? "výchozí"}` : profiles.find((p) => p.id === profile)?.name ?? "—";

  // the profile's pick (whatever the filters say — the profile is its own filter), then the classic recommendation:
  // the first sure file by the filters, never a cinema recording (before the digital release everything is one)
  const recommendable = (o: Offer) => o.file.film === "yes" && !["likely", "suspect"].includes(o.file.cinema ?? "");
  const byProfile = profile !== "off" && pickKey ? offers.find((o) => o.copies.some((c) => keyOf(c) === pickKey)) : undefined;
  const best = view.find(recommendable);
  const featured = best !== byProfile ? best : undefined;        // the same file twice: shown once (🎯)
  const rest = view.filter((o) => o !== featured && o !== byProfile);
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
  const download = (f: ScoredFile) => {
    if (!confirmCinema(f)) return;
    if (owned.length && contentType === "movie") setChoosing(f); else run(f);
  };

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

      {/* 🎯 what the profile would take — the profile changed right here */}
      {byProfile ? (
        <OfferCard offer={byProfile} featured state={states[byProfile.file.ident]} onDownload={() => download(byProfile.file)}
          canDownload={can("download")} label={<ProfileLine label={profileLabel} onChange={() => setSheet("profile")} />} />
      ) : (
        <div className="space-y-1.5 rounded-2xl border border-violet-900/60 bg-violet-950/10 p-4">
          <ProfileLine label={profileLabel} onChange={() => setSheet("profile")} />
          <p className="text-sm text-zinc-400">
            {profile === "off" ? "Bez profilu — doporučení jen podle filtrů níž."
              : !pick ? "Zjišťuji, co by profil vzal…"
                : `Nic nesplňuje profil${pick.reasons?.length ? ` (${pick.reasons.map(([r]) => r).join(", ")})` : ""}.`}
          </p>
        </div>
      )}

      {/* ★ the classic recommendation: the best sure file by the filters */}
      {featured ? (
        <OfferCard offer={featured} state={states[featured.file.ident]} onDownload={() => download(featured.file)} canDownload={can("download")}
          label={<p className="text-sm font-medium text-zinc-300">★ Doporučeno podle filtrů</p>} />
      ) : !byProfile && (
        <p className="rounded-xl border border-zinc-800 p-4 text-zinc-400">
          {movie?.pre_digital ? "Nic k doporučení — dokud film nevyjde digitálně, jsou soubory jen z kina." : "S těmito filtry nic jistého. Zkus filtr povolit."}
        </p>
      )}
      {rest.length > 0 && <p className="pt-2 text-sm text-zinc-500">Další soubory ({rest.length})</p>}
      {shown.map((o) => (
        <OfferCard key={o.key} offer={o} state={states[o.file.ident]} onDownload={() => download(o.file)} canDownload={can("download")} />
      ))}
      {rest.length > shown.length && <BigButton kind="secondary" onClick={() => setAll(true)}>Ukázat všechny ({rest.length})</BigButton>}

      <Sheet open={sheet === "profile"} title="Profil kvality" onClose={() => setSheet("")}>
        <Option active={profile === ""} hint="nastavený v Nastavení jako výchozí" onClick={() => { chooseProfile(""); setSheet(""); }}>
          Výchozí ({defaultProfile?.name ?? "—"})
        </Option>
        {profiles.map((p) => (
          <Option key={p.id} active={profile === p.id} onClick={() => { chooseProfile(p.id); setSheet(""); }}>{p.name}</Option>
        ))}
        <Option active={profile === "off"} hint="jen doporučení podle filtrů" onClick={() => { chooseProfile("off"); setSheet(""); }}>
          Bez profilu
        </Option>
      </Sheet>
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

/** "🎯 Podle profilu  [Standard ▾]" — the head of the profile's card. */
function ProfileLine({ label, onChange }: { label: string; onChange: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm font-medium text-violet-300">🎯 Podle profilu</span>
      <button onClick={onChange}
        className="min-h-10 rounded-xl border border-violet-700 bg-violet-950/40 px-3 text-sm font-medium text-violet-100 active:bg-violet-900">
        {label} ▾
      </button>
    </div>
  );
}
