"use client";

import { useEffect, useMemo, useState } from "react";
import { MediaKind, SubtitleResult, SubtitleStatus, downloadSubtitle, getSubtitleStatus, searchSubtitles } from "@/lib/api";
import { langName } from "@/lib/mobile";
import { BigButton, ChoiceButton, Option, Sheet, Spinner, Tag } from "@/components/mobile/ui";

/** Phone: subtitles of a library film / episode in two taps — what it has, "Najít titulky" (OpenSubtitles), tap one
 *  = download (one of the day's downloads). The best ones first: made for this very file, then the most downloaded. */
const LANGS = ["cs", "sk", "en"];

function rank(r: SubtitleResult): number {
  return (r.hash_match ? 1e9 : 0) - (r.machine ? 1e8 : 0) - (r.hearing_impaired ? 1e6 : 0) + r.downloads;
}

export default function Subtitles({ id, kind }: { id: number; kind: MediaKind }) {
  const [status, setStatus] = useState<SubtitleStatus | null>(null);
  const [lang, setLang] = useState("cs");
  const [langSheet, setLangSheet] = useState(false);
  const [forced, setForced] = useState(false);
  const [results, setResults] = useState<{ results: SubtitleResult[]; fps: number } | null>(null);
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const load = () => getSubtitleStatus(id, kind).then(setStatus).catch((e) => setError(e instanceof Error ? e.message : "Chyba"));
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id, kind]);

  async function find() {
    setBusy("search");
    setError("");
    setDone("");
    try {
      const r = await searchSubtitles(id, forced, kind);
      setResults({ results: r.results, fps: r.video_fps });
    } catch (e) { setError(e instanceof Error ? e.message : "Hledání selhalo"); } finally { setBusy(""); }
  }
  async function take(r: SubtitleResult) {
    setBusy(String(r.file_id));
    setError("");
    try {
      const out = await downloadSubtitle(id, { file_id: r.file_id, language: r.language, forced: r.forced, fps: r.fps || results?.fps || 0, replace: false }, kind);
      setDone(`✓ Titulky ${langName(r.language)} staženy${out.remaining != null ? ` · dnes zbývá ${out.remaining} stažení` : ""}. Plex je za chvíli uvidí.`);
      setResults(null);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "Stažení selhalo"); } finally { setBusy(""); }
  }

  const shown = useMemo(() => (results?.results ?? []).filter((r) => r.language.toLowerCase() === lang)
    .sort((a, b) => rank(b) - rank(a)).slice(0, 8), [results, lang]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of results?.results ?? []) c[r.language.toLowerCase()] = (c[r.language.toLowerCase()] ?? 0) + 1;
    return c;
  }, [results]);

  if (!status && !error) return <Spinner text="Načítám titulky…" />;
  const have = [
    ...(status?.external ?? []).map((s) => `${langName(s.lang)}${s.forced ? " (jen cizí řeč)" : ""}`),
    ...(status?.embedded ?? []).map((s) => `${langName(s.lang)} ve videu`),
  ];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        <span className="text-zinc-400">Titulky:</span>
        {have.length ? have.map((h, i) => <Tag key={i} tone={/^(CZ|SK)/.test(h) ? "good" : "plain"}>{h}</Tag>) : <span className="text-zinc-500">žádné</span>}
      </div>
      {status && !status.configured && <p className="text-amber-300">OpenSubtitles není nastavené (Nastavení na počítači).</p>}
      {done && <p className="rounded-xl bg-emerald-950 px-4 py-3 text-emerald-200">{done}</p>}

      {!results ? (
        <div className="space-y-2">
          <div className="flex gap-2">
            <ChoiceButton label="Jazyk" value={langName(lang)} active={lang !== "cs"} onClick={() => setLangSheet(true)} />
            {status?.needs_forced && (
              <button onClick={() => setForced(!forced)}
                className={`rounded-full border px-4 py-2 text-sm ${forced ? "border-violet-600 bg-violet-950/60 text-violet-100" : "border-zinc-700 text-zinc-300"}`}>
                jen cizí řeč {forced ? "✓" : ""}
              </button>
            )}
          </div>
          <BigButton onClick={find} disabled={busy === "search" || (status ? !status.configured : true)}>
            {busy === "search" ? "Hledám…" : `Najít titulky ${langName(lang)}`}
          </BigButton>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <ChoiceButton label="Jazyk" value={`${langName(lang)} (${counts[lang] ?? 0})`} active onClick={() => setLangSheet(true)} />
            <button onClick={() => setResults(null)} className="px-2 py-2 text-violet-300">Zrušit</button>
          </div>
          {!shown.length && <p className="py-3 text-zinc-400">V tomhle jazyce nic. Zkus jiný jazyk.</p>}
          {shown.map((r) => (
            <button key={r.file_id} onClick={() => take(r)} disabled={!!busy}
              className="w-full space-y-1 rounded-xl border border-zinc-800 bg-zinc-900/60 p-3 text-left active:bg-zinc-800 disabled:opacity-50">
              <p className="flex flex-wrap items-center gap-1.5">
                {r.hash_match && <Tag tone="good">přesně k tomuto souboru</Tag>}
                {r.forced && <Tag>jen cizí řeč</Tag>}
                {r.machine && <Tag tone="warn">strojový překlad</Tag>}
                <span className="text-sm text-zinc-400">{r.downloads.toLocaleString("cs-CZ")}× staženo</span>
              </p>
              <p className="break-all text-sm text-zinc-300">{r.release || r.file_name}</p>
              <p className="text-base font-medium text-violet-300">{busy === String(r.file_id) ? "Stahuji…" : "Stáhnout"}</p>
            </button>
          ))}
        </div>
      )}
      {error && <p className="text-red-400">{error}</p>}

      <Sheet open={langSheet} title="Jazyk titulků" onClose={() => setLangSheet(false)}>
        {[...LANGS, ...Object.keys(counts).filter((l) => !LANGS.includes(l))].map((l) => (
          <Option key={l} active={lang === l} onClick={() => { setLang(l); setLangSheet(false); }}>
            {langName(l)}{results ? ` (${counts[l] ?? 0})` : ""}
          </Option>
        ))}
      </Sheet>
    </div>
  );
}
