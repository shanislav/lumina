"use client";

/**
 * Pracovníci (backend modules/convert/workers): computers that encode for Lumina — the little app on the user's PC
 * with the server's share mounted. Adding one gives its token once; the list shows who is online, what it does or
 * why it waits (outside its hours, another program runs …).
 */
import { useCallback, useEffect, useState } from "react";
import { ConvertWorker, addConvertWorker, getConvertWorkers, removeConvertWorker } from "@/lib/api";

const base = (p: string) => p.split("/").pop() ?? p;

export default function WorkersTab({ phases }: { phases: Record<string, string> }) {
  const [workers, setWorkers] = useState<ConvertWorker[]>([]);
  const [name, setName] = useState("");
  const [token, setToken] = useState<{ name: string; token: string } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setWorkers((await getConvertWorkers()).workers);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  async function add() {
    setError("");
    try {
      const r = await addConvertWorker(name || "Můj počítač");
      setToken({ name: r.name, token: r.token });
      setName("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function remove(w: ConvertWorker) {
    if (!confirm(`Odebrat pracovníka „${w.name}“? Jeho token přestane fungovat, rozdělaná úloha se vrátí do fronty.`)) return;
    await removeConvertWorker(w.id);
    load();
  }

  return (
    <div className="space-y-4 text-sm">
      {error && <p className="rounded-lg bg-red-950/60 px-3 py-2 text-red-300">{error}</p>}
      <p className="text-xs text-zinc-400">
        Pracovník je malá aplikace na tvém počítači (Windows / Linux), která kóduje místo serveru — výkonnější PC to zvládne
        mnohonásobně rychleji. Počítač musí mít připojený sdílený disk serveru. Kdy smí pracovat (hodiny, vytížení procesoru,
        běžící programy, baterie), si nastavíš v něm. U zmenšení pak zvolíš „Kde kódovat“.
      </p>

      {token && (
        <div className="space-y-2 rounded-lg border border-emerald-700 bg-emerald-950/40 p-3 text-xs">
          <p className="text-emerald-200">Pracovník „{token.name}“ přidán. Jeho token (ukáže se jen teď — zkopíruj ho do nastavení aplikace):</p>
          <code className="block select-all break-all rounded bg-zinc-950 px-2 py-1.5 text-emerald-300">{token.token}</code>
          <button onClick={() => setToken(null)} className="text-zinc-400 hover:text-zinc-200">Mám, zavřít</button>
        </div>
      )}

      <div className="flex gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Název počítače (např. Notebook)"
          className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-zinc-200" />
        <button onClick={add} className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500">
          Přidat pracovníka
        </button>
      </div>

      {workers.length === 0 ? <p className="text-xs text-zinc-500">Zatím žádný pracovník.</p> : (
        <ul className="divide-y divide-zinc-800 rounded-lg border border-zinc-800">
          {workers.map((w) => {
            const p = w.job?.progress;
            return (
              <li key={w.id} className="space-y-1 px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${w.online ? "bg-emerald-400" : "bg-zinc-600"}`} title={w.online ? "připojen" : "nepřipojen"} />
                  <span className="font-medium text-zinc-200">{w.name}</span>
                  <span className="text-zinc-500">{w.online ? "připojen" : w.last_seen ? `naposledy ${w.last_seen.slice(5, 16)}` : "ještě se nepřipojil"}</span>
                  <button onClick={() => remove(w)} className="ml-auto text-zinc-500 hover:text-red-300" title="Odebrat">✕</button>
                </div>
                {w.info.cpu && (
                  <div className="text-zinc-500">
                    {w.info.system} · {w.info.cpu} ({w.info.threads} vláken){w.info.gpu ? ` · ${w.info.gpu}` : ""} · kodéry: {(w.info.encoders || []).join(", ") || "—"}
                    {w.info.x265 ? ` · x265 ${w.info.x265}` : ""}
                  </div>
                )}
                {w.info.cpu && w.parts && <div className="text-zinc-500">Nekóduje části souborů se serverem: {w.parts}</div>}
                {w.job ? (
                  <div className={p?.paused ? "text-amber-300" : "text-sky-300"}>
                    {p?.paused ? `pozastaveno (${p.paused})` : `${phases[p?.phase || ""] ?? p?.phase ?? "pracuje"}${p?.value != null ? ` ${p.phase === "encode" ? `${Math.round(p.value)} %` : p.value}` : ""}`}
                    {" — "}{w.job.part ? `část ${w.job.part.idx}/${w.job.part.count} z ` : ""}{base(w.job.path)}
                  </div>
                ) : w.online && (
                  <div className={w.state.waiting ? "text-amber-300/80" : "text-zinc-400"}>
                    {w.state.waiting ? `čeká: ${w.state.waiting}` : "volný, čeká na práci"}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
