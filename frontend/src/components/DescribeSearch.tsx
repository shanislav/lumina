"use client";

import { useEffect, useRef, useState, FormEvent, KeyboardEvent } from "react";
import { AiQuotas, DescribeHit, DescribeStatus, DescribeTurn, TMDBMovie, describeTitle, getDescribeStatus } from "@/lib/api";

interface Entry { role: "user" | "assistant"; text: string; hits?: DescribeHit[]; ask?: string; error?: boolean; by?: string }

const k = (n: number) => (n >= 1000 ? `${String(Math.round(n / 100) / 10).replace(".", ",")}k` : String(n));

/** what the AIs have left: "Groq: 987/1000 dotazů dnes · 6,5k/8k tokenů za minutu · Gemini: dnes 12 dotazů" */
function aiText(q: AiQuotas): string {
  const out: string[] = [];
  const g = q.groq;
  if (g) {
    if (g.requests_left == null) out.push("Groq: zbytek limitu se ukáže po prvním dotazu");
    else out.push(`Groq: ${g.requests_left}/${g.requests_limit} dotazů dnes`
      + (g.tokens_left == null ? "" : ` · ${k(g.tokens_left)}/${k(g.tokens_limit ?? 0)} tokenů za minutu`));
  }
  const m = q.gemini;
  if (m) out.push(m.exhausted ? "Gemini: všechny modely mají dnešní limit vyčerpaný — odpovídá Groq (zítra zase)"
    : `Gemini: dnes ${m.calls} dotazů${m.searches ? ` (${m.searches}× hledal na Googlu)` : ""}`
      + (m.out?.length && m.model ? ` · teď odpovídá ${m.model} (${m.out.length} model${m.out.length > 1 ? "y" : ""} má limit vyčerpaný)` : "")
      + (m.no_search ? " · hledání na Googlu klíč nemá (odpovídá z paměti)" : ""));
  return out.join(" · ");
}

const QUICK = ["Nic z toho", "Je to seriál", "Je to film", "Novější", "Starší", "Český / slovenský"];

/** „Neznáš název?“ under the search box (only with Groq): the user describes the film in their own words,
 *  the AI guesses titles, each one opens like a search result. Saying more narrows the guesses. */
/** A sentence typed into the plain search ("film kde chlap ..."): looks like a description, not a name. */
export function looksLikeDescription(q: string): boolean {
  return q.trim().split(/\s+/).length >= 4;
}

export default function DescribeSearch({ onPick, big = false, ask }: {
  onPick: (movie: TMDBMovie) => void;
  big?: boolean;                               // the phone: a big button instead of a line of text
  ask?: { text: string; n: number } | null;    // open and ask this (the plain search's "Zeptat se AI"); n: each new ask
}) {
  const [status, setStatus] = useState<DescribeStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => { getDescribeStatus().then(setStatus).catch(() => {}); }, []);
  // asked from the plain search: open and send its text (once the status is known)
  const asked = useRef(0);
  useEffect(() => {
    if (!ask?.text || !status?.enabled || asked.current === ask.n) return;
    asked.current = ask.n;
    setOpen(true);
    send(ask.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask, status]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [entries, busy]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  // a click outside folds the chat away (the talk stays — opening it again goes on with it)
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const fold = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", fold);
    return () => document.removeEventListener("mousedown", fold);
  }, [open]);

  if (!status?.enabled) return null;

  /** what the AI said, for its memory of the talk: the titles it guessed and its question */
  function talkOf(list: Entry[]): DescribeTurn[] {
    return list.filter((e) => !e.error).map((e) => ({ role: e.role, content: e.text }));
  }

  async function send(said: string) {
    const msg = said.trim();
    if (!msg || busy || !status) return;
    const next = [...entries, { role: "user" as const, text: msg }];
    setEntries(next);
    setText("");
    setBusy(true);
    try {
      const a = await describeTitle(talkOf(next));
      setStatus({ ...status, left: a.left, ai: a.ai ?? status.ai });
      const summary = (a.guessed.length ? `Tipy: ${a.guessed.join(", ")}.` : "Nic mě nenapadlo.") + (a.ask ? ` ${a.ask}` : "");
      setEntries([...next, { role: "assistant", text: summary, hits: a.results, ask: a.ask,
        by: a.searched ? `${a.by} · hledal na Googlu` : a.by }]);
    } catch (e) {
      setEntries([...next, { role: "assistant", text: e instanceof Error ? e.message : "Chyba", error: true }]);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) { e.preventDefault(); send(text); }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); }
  }

  if (!open) {
    if (big) {
      return (
        <button onClick={() => setOpen(true)}
          className="w-full rounded-2xl border border-violet-800/70 bg-violet-950/30 px-4 py-3 text-left active:bg-violet-950">
          <span className="block text-base font-medium text-violet-100">
            ✨ {entries.length ? "Pokračovat v hledání podle popisu" : "Nevíš název? Popiš, o čem to bylo"}
          </span>
          <span className="text-sm text-zinc-400">Napiš děj, scénu nebo herce — AI zkusí uhodnout film nebo seriál.</span>
        </button>
      );
    }
    return (
      <button onClick={() => setOpen(true)}
        className="-mt-4 rounded-full border border-violet-800/70 bg-violet-950/30 px-4 py-1.5 text-sm text-violet-200 hover:border-violet-600 hover:bg-violet-900/40 transition-colors">
        ✨ {entries.length ? "Pokračovat v hledání podle popisu" : "Nevíš název? Popiš, o čem to bylo — najde to AI"}
      </button>
    );
  }

  const out = status.left !== null && status.left <= 0;
  return (
    <div ref={box} className="-mt-4 w-full max-w-2xl rounded-xl border border-violet-900/60 bg-zinc-900/70 shadow-lg">
      <div className="flex items-center gap-2 border-b border-zinc-800 px-4 py-2">
        <span className="text-sm font-medium text-violet-300">✨ Najdi podle popisu</span>
        <span className="ml-auto text-[11px] text-zinc-500"
          title={status.left == null ? aiText(status.ai) : "Groq má denní limity — Lumina hlídá, ať se jich neplýtvá"}>
          {status.left == null ? "bez limitu" : `dnes zbývá ${status.left}/${status.daily}`}
        </span>
        {entries.length > 0 && (
          <button onClick={() => setEntries([])} className="text-[11px] text-zinc-500 hover:text-zinc-300">znovu</button>
        )}
        <button onClick={() => setOpen(false)} className="text-zinc-500 hover:text-zinc-300" aria-label="Zavřít">✕</button>
      </div>

      {status.left == null && aiText(status.ai) && (
        <p className="border-b border-zinc-800 px-4 py-1 text-[11px] text-zinc-500">{aiText(status.ai)}</p>
      )}
      <div className="max-h-[60vh] space-y-3 overflow-y-auto px-4 py-3">
        {entries.length === 0 && (
          <p className="text-sm text-zinc-500">
            Napiš, co si pamatuješ — děj, scénu, herce, kdy jsi to viděl.
            <span className="block text-xs text-zinc-600 mt-1">
              Např. „film o klukovi co cestuje časem autem, 80. léta“ nebo „seriál o chemikáři co vaří drogy“.
            </span>
          </p>
        )}
        {entries.map((e, i) => e.role === "user" ? (
          <div key={i} className="flex justify-end">
            <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-violet-700/70 px-3 py-2 text-sm text-white">{e.text}</p>
          </div>
        ) : e.error ? (
          <p key={i} className="rounded-lg border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-300">{e.text}</p>
        ) : (
          <div key={i} className="space-y-2">
            {e.hits && e.hits.length > 0 ? (
              <ul className="space-y-1.5">
                {e.hits.map((h) => (
                  <li key={`${h.media_type}-${h.tmdb_id}`}>
                    <button onClick={() => onPick(h)}
                      className="flex w-full items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-950/40 p-2 text-left hover:border-violet-700 hover:bg-violet-950/30 transition-colors">
                      {h.poster_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={h.poster_url.replace("/w500/", "/w92/")} alt="" className="h-16 w-11 flex-shrink-0 rounded object-cover" />
                      ) : <div className="h-16 w-11 flex-shrink-0 rounded bg-zinc-800" />}
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-zinc-100">
                          {h.title} {h.year && <span className="text-zinc-500">({h.year})</span>}
                          <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{h.media_type === "tv" ? "seriál" : "film"}</span>
                        </p>
                        {h.original_title && h.original_title !== h.title && <p className="truncate text-xs text-zinc-500">{h.original_title}</p>}
                        {h.why && <p className="mt-0.5 text-xs text-violet-300/80">{h.why}</p>}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-zinc-400">Nic jsem nenašel. Zkus přidat víc podrobností.</p>
            )}
            {e.ask && <p className="text-sm text-zinc-300">🤔 {e.ask}</p>}
            {e.by && <p className="text-[10px] text-zinc-600">odpověděl {e.by}</p>}
          </div>
        ))}
        {busy && <p className="animate-pulse text-sm text-zinc-500">Přemýšlím…</p>}
        <div ref={end} />
      </div>

      {entries.length > 0 && !busy && !out && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2">
          {QUICK.map((q) => (
            <button key={q} onClick={() => send(q)}
              className="rounded-full border border-zinc-700 px-2.5 py-0.5 text-xs text-zinc-400 hover:border-violet-600 hover:text-violet-200">
              {q}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="flex items-end gap-2 border-t border-zinc-800 p-3">
        <textarea ref={input} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} rows={2}
          disabled={out} maxLength={600}
          placeholder={out ? "Dnešní limit AI je vyčerpaný — zítra zase" : entries.length ? "Doplň nebo oprav…" : "O čem to bylo?"}
          className="min-w-0 flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-violet-500 disabled:opacity-50" />
        <button type="submit" disabled={busy || out || !text.trim()}
          className="flex-shrink-0 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40">
          Najít
        </button>
      </form>
    </div>
  );
}
