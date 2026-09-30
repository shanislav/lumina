"use client";

import { useCallback, useEffect, useState } from "react";
import { ManagedUser, PermissionInfo, createUser, deleteUser, getPermissions, getUsers, updateUser } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";

const INPUT = "rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 focus:border-violet-500 outline-none";

/** Users and what each may do (admin only; the backend checks everything again). */
export default function UsersAdmin() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [perms, setPerms] = useState<PermissionInfo[]>([]);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    getUsers().then(setUsers).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    getPermissions().then(setPerms).catch(() => {});
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se");
    }
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-zinc-300">👥 Uživatelé</h3>
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500">
            + Přidat uživatele
          </button>
        )}
      </div>
      <p className="text-[11px] text-zinc-500">
        Správce může všechno včetně správy uživatelů. Uživatel jen to, co mu zde povolíš. Změna platí hned;
        zablokování, nové heslo nebo smazání uživatele ho odhlásí.
      </p>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {adding && <NewUserForm perms={perms} onCancel={() => setAdding(false)}
        onCreate={(u) => run(async () => { await createUser(u); setAdding(false); })} />}
      <ul className="space-y-3">
        {users.map((u) => (
          <UserRow key={u.id} user={u} perms={perms} isMe={u.id === me.id} run={run} />
        ))}
      </ul>
    </div>
  );
}

function PermChecks({ perms, value, onChange, disabled }: {
  perms: PermissionInfo[]; value: string[]; onChange: (v: string[]) => void; disabled?: boolean;
}) {
  return (
    <div className="grid gap-1 sm:grid-cols-2">
      {perms.map((p) => (
        <label key={p.name} className={`flex items-start gap-2 text-xs ${disabled ? "text-zinc-600" : "text-zinc-300"}`}>
          <input type="checkbox" className="mt-0.5" disabled={disabled} checked={disabled || value.includes(p.name)}
            onChange={(e) => onChange(e.target.checked ? [...value, p.name] : value.filter((n) => n !== p.name))} />
          {p.title}
        </label>
      ))}
    </div>
  );
}

function NewUserForm({ perms, onCreate, onCancel }: {
  perms: PermissionInfo[];
  onCreate: (u: { username: string; password: string; role: string; permissions: string[] }) => void;
  onCancel: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [chosen, setChosen] = useState<string[]>(perms.filter((p) => p.default).map((p) => p.name));

  return (
    <div className="rounded-lg border border-violet-800/60 bg-zinc-950/50 p-4 space-y-3">
      <div className="flex flex-wrap gap-2">
        <input className={`${INPUT} flex-1 min-w-[10rem]`} placeholder="Jméno" autoComplete="off"
          value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className={`${INPUT} flex-1 min-w-[10rem]`} placeholder="Heslo (aspoň 8 znaků)" type="password" autoComplete="new-password"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <select className={INPUT} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="user">Uživatel</option>
          <option value="admin">Správce</option>
        </select>
      </div>
      <PermChecks perms={perms} value={chosen} onChange={setChosen} disabled={role === "admin"} />
      <div className="flex gap-2">
        <button disabled={!username || !password} onClick={() => onCreate({ username, password, role, permissions: chosen })}
          className="rounded bg-violet-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
          Vytvořit
        </button>
        <button onClick={onCancel} className="text-xs text-zinc-500 hover:text-zinc-300">Zrušit</button>
      </div>
    </div>
  );
}

function UserRow({ user, perms, isMe, run }: {
  user: ManagedUser; perms: PermissionInfo[]; isMe: boolean; run: (a: () => Promise<unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [role, setRole] = useState(user.role);
  const [chosen, setChosen] = useState<string[]>(user.permissions);
  const [password, setPassword] = useState("");

  useEffect(() => {
    setRole(user.role);
    setChosen(user.permissions);
  }, [user]);

  const dirty = role !== user.role || (role === "user" && [...chosen].sort().join() !== [...user.permissions].sort().join());

  return (
    <li className={`rounded-lg border border-zinc-800 p-3 ${user.disabled ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setOpen(!open)} className="font-medium text-zinc-100 hover:text-violet-300">
          {open ? "▾" : "▸"} {user.username}
        </button>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${user.is_admin ? "bg-violet-900/70 text-violet-200" : "bg-zinc-800 text-zinc-300"}`}>
          {user.is_admin ? "správce" : "uživatel"}
        </span>
        {isMe && <span className="text-[10px] text-zinc-500">(ty)</span>}
        {user.disabled && <span className="rounded bg-red-900/60 px-1.5 py-0.5 text-[10px] text-red-200">zablokovaný</span>}
        <span className="ml-auto text-[11px] text-zinc-500">
          {user.last_login ? `naposledy ${user.last_login} UTC` : "ještě se nepřihlásil"}
        </span>
      </div>
      {open && (
        <div className="mt-3 space-y-3 border-t border-zinc-800 pt-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-zinc-500">Role:</span>
            <select className={INPUT} value={role} onChange={(e) => setRole(e.target.value as "admin" | "user")}>
              <option value="user">Uživatel</option>
              <option value="admin">Správce</option>
            </select>
          </div>
          <PermChecks perms={perms} value={chosen} onChange={setChosen} disabled={role === "admin"} />
          <div className="flex flex-wrap items-center gap-2">
            <button disabled={!dirty} onClick={() => run(() => updateUser(user.id, { role, permissions: chosen }))}
              className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
              Uložit oprávnění
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input className={`${INPUT} flex-1 min-w-[10rem]`} type="password" placeholder="Nové heslo" autoComplete="new-password"
              value={password} onChange={(e) => setPassword(e.target.value)} />
            <button disabled={!password} onClick={() => run(async () => { await updateUser(user.id, { password }); setPassword(""); })}
              className="rounded border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40">
              Nastavit heslo
            </button>
          </div>
          {!isMe && (
            <div className="flex flex-wrap gap-2">
              <button onClick={() => run(() => updateUser(user.id, { disabled: !user.disabled }))}
                className="rounded border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500">
                {user.disabled ? "Odblokovat" : "Zablokovat"}
              </button>
              <button onClick={() => { if (confirm(`Smazat uživatele ${user.username}?`)) run(() => deleteUser(user.id)); }}
                className="rounded border border-red-900 px-3 py-1.5 text-xs text-red-400 hover:border-red-700">
                Smazat
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
