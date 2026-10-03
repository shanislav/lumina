"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getModules } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import TasksButton from "@/components/TasksButton";
import NotifyButton from "@/components/NotifyButton";

/** Top navigation — a page is shown only when the backend module behind it runs
 *  and the user may use it. */
const LINKS: { href: string; label: string; module?: string; perms?: string[] }[] = [
  { href: "/discover", label: "Objevit", module: "search", perms: ["search"] },
  { href: "/wanted", label: "Chci", module: "wanted" },
  { href: "/library", label: "Knihovna", module: "library", perms: ["library.view"] },
  { href: "/settings", label: "Nastavení", perms: ["settings", "profiles", "admin"] },
];

export default function NavLinks() {
  const { user, can } = useAuth();
  // until the modules are known, show everything (no flicker for the usual setup)
  const [active, setActive] = useState<Set<string> | null>(null);

  useEffect(() => {
    getModules()
      .then((mods) => setActive(new Set(mods.filter((m) => m.active).map((m) => m.name))))
      .catch(() => {});
  }, []);

  return (
    <div className="flex items-center gap-3 sm:gap-4">
      {LINKS.filter((l) => (!l.module || !active || active.has(l.module)) && (!l.perms || l.perms.some(can))).map((l) => (
        <Link key={l.href} href={l.href} className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
          {l.label}
        </Link>
      ))}
      <NotifyButton />
      <TasksButton />
      <Link href="/account" title={`Účet: ${user.username}`}
        className="flex items-center gap-1 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">
        <span aria-hidden>👤</span>
        <span className="hidden md:inline max-w-[8rem] truncate">{user.username}</span>
      </Link>
    </div>
  );
}
