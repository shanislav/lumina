"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AuthUser, UNAUTHORIZED_EVENT, getAuthStatus, login, logout, setupAdmin } from "@/lib/api";

interface AuthState {
  user: AuthUser;
  /** Admins can do everything; others what the admin allowed (backend checks it anyway). */
  can: (permission: string) => boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthGate");
  return ctx;
}

/** Shows the app only to a signed-in user; otherwise the sign-in (or first-admin) form. */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"loading" | "setup" | "login" | "ok" | "offline">("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const router = useRouter();
  const pathname = usePathname();

  const load = useCallback(() => {
    getAuthStatus()
      .then((s) => {
        setUser(s.user);
        setState(s.setup_required ? "setup" : s.user ? "ok" : "login");
      })
      .catch(() => setState("offline"));
  }, []);

  useEffect(() => {
    load();
    const lost = () => {
      setUser(null);
      setState("login");
    };
    window.addEventListener(UNAUTHORIZED_EVENT, lost);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, lost);
  }, [load]);

  const signedIn = (u: AuthUser) => {
    // the sign-in form keeps the URL; after "Odhlásit se" on the account page start on the home page
    if (pathname?.startsWith("/account")) router.replace("/");
    setUser(u);
    setState("ok");
  };

  if (state === "loading") return <div className="min-h-screen" />;
  if (state === "offline")
    return (
      <Centered title="Lumina">
        <p className="text-sm text-zinc-400">Backend neodpovídá.</p>
        <button onClick={load} className="mt-4 rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">Zkusit znovu</button>
      </Centered>
    );
  if (state === "setup") return <SetupForm onDone={signedIn} />;
  if (state === "login" || !user) return <LoginForm onDone={signedIn} />;

  const value: AuthState = {
    user,
    can: (p) => user.is_admin || user.permissions.includes(p),
    signOut: async () => {
      try {
        await logout();
      } finally {
        router.replace("/");
        setUser(null);
        setState("login");
      }
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function Centered({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl border border-zinc-800 bg-zinc-900/60 p-6">
        <div className="mb-5 flex items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <h1 className="text-xl font-bold bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">{title}</h1>
        </div>
        {children}
      </div>
    </main>
  );
}

const INPUT = "w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-violet-500";

function LoginForm({ onDone }: { onDone: (u: AuthUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onDone((await login(username, password, remember)).user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Přihlášení selhalo");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Centered title="Lumina">
      <form onSubmit={submit} className="space-y-3">
        <input className={INPUT} placeholder="Jméno" autoComplete="username" autoFocus
          value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className={INPUT} placeholder="Heslo" type="password" autoComplete="current-password"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Zůstat přihlášen (30 dní)
        </label>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button type="submit" disabled={busy || !username || !password}
          className="w-full rounded-lg bg-violet-600 py-2 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
          {busy ? "…" : "Přihlásit"}
        </button>
      </form>
    </Centered>
  );
}

function SetupForm({ onDone }: { onDone: (u: AuthUser) => void }) {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== again) {
      setError("Hesla se neshodují");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onDone((await setupAdmin(code, username, password, true)).user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nepodařilo se");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Centered title="Vítej v Lumině">
      <p className="mb-4 text-sm text-zinc-400">
        Vytvoř účet správce. Jednorázový kód je v logu backendu
        (<code className="text-zinc-300">docker compose logs backend</code>, řádek „setup code“).
      </p>
      <form onSubmit={submit} className="space-y-3">
        <input className={INPUT} placeholder="Kód z logu" autoComplete="one-time-code" autoFocus
          value={code} onChange={(e) => setCode(e.target.value)} />
        <input className={INPUT} placeholder="Jméno správce" autoComplete="username"
          value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className={INPUT} placeholder="Heslo (aspoň 8 znaků)" type="password" autoComplete="new-password"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <input className={INPUT} placeholder="Heslo znovu" type="password" autoComplete="new-password"
          value={again} onChange={(e) => setAgain(e.target.value)} />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button type="submit" disabled={busy || !code || !username || !password}
          className="w-full rounded-lg bg-violet-600 py-2 font-medium text-white hover:bg-violet-500 disabled:opacity-40">
          {busy ? "…" : "Vytvořit správce"}
        </button>
      </form>
    </Centered>
  );
}
