"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { SessionInfo, changePassword, endOtherSessions, endSession, getSessions } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

const INPUT = "w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-violet-500";

/** "Firefox na Windows" from a user agent — enough to recognise one's own devices. */
function device(ua: string): string {
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari" : "Prohlížeč";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows"
    : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} · ${os}` : browser;
}

export default function AccountPage() {
  const { user, signOut } = useAuth();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => getSessions().then(setSessions).catch(() => {});
  useEffect(() => {
    load();
  }, []);

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    if (next !== again) {
      setMessage({ ok: false, text: "Nová hesla se neshodují" });
      return;
    }
    try {
      await changePassword(current, next);
      setMessage({ ok: true, text: "Heslo změněno — ostatní zařízení byla odhlášena" });
      setCurrent("");
      setNext("");
      setAgain("");
      load();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : "Nepodařilo se" });
    }
  }

  return (
    <main className="flex flex-col gap-6 px-4 py-8 max-w-3xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          <Link href="/" className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm whitespace-nowrap">&larr; Hledat</Link>
          <h1 className="text-2xl font-bold text-zinc-100">Účet</h1>
        </div>
        <button onClick={signOut} className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:border-zinc-500">
          Odhlásit se
        </button>
      </div>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <p className="text-lg text-zinc-100">{user.username}</p>
        <p className="text-sm text-zinc-500">{user.is_admin ? "Správce — může všechno" : "Uživatel"}</p>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="mb-3 font-semibold text-zinc-200">Změna hesla</h2>
        <form onSubmit={submitPassword} className="grid gap-3 sm:max-w-sm">
          <input className={INPUT} type="password" placeholder="Současné heslo" autoComplete="current-password"
            value={current} onChange={(e) => setCurrent(e.target.value)} />
          <input className={INPUT} type="password" placeholder="Nové heslo (aspoň 8 znaků)" autoComplete="new-password"
            value={next} onChange={(e) => setNext(e.target.value)} />
          <input className={INPUT} type="password" placeholder="Nové heslo znovu" autoComplete="new-password"
            value={again} onChange={(e) => setAgain(e.target.value)} />
          {message && <p className={`text-sm ${message.ok ? "text-green-400" : "text-red-400"}`}>{message.text}</p>}
          <button type="submit" disabled={!current || !next}
            className="rounded-lg bg-violet-600 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-40">
            Změnit heslo
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold text-zinc-200">Kde jsem přihlášen</h2>
          {sessions.length > 1 && (
            <button onClick={() => endOtherSessions().then(load)}
              className="rounded border border-zinc-700 px-3 py-1 text-xs text-zinc-300 hover:border-zinc-500">
              Odhlásit všude jinde
            </button>
          )}
        </div>
        <ul className="divide-y divide-zinc-800">
          {sessions.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <div>
                <p className="text-zinc-200">
                  {device(s.user_agent)}
                  {s.current && <span className="ml-2 rounded bg-emerald-900/70 px-1.5 py-0.5 text-[10px] text-emerald-300">toto zařízení</span>}
                </p>
                <p className="text-xs text-zinc-500">
                  {s.ip} · naposledy {s.last_seen} UTC · {s.remember ? "zapamatováno" : "do zavření prohlížeče"}
                </p>
              </div>
              {!s.current && (
                <button onClick={() => endSession(s.id).then(load)} className="text-xs text-red-400 hover:text-red-300">
                  Odhlásit
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
