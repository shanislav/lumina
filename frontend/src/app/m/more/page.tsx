"use client";

import Link from "next/link";
import { logout } from "@/lib/api";
import { useAuth } from "@/components/AuthGate";
import { setPro } from "@/lib/mobile";
import { UnseenBadge, useWantedUnseen } from "@/components/WantedMark";

/** Phone: "Více" — the rest of Lumina (pages of the full app) and the switch to it. */
const LINKS: { href: string; label: string; hint: string; perm?: string }[] = [
  { href: "/wanted", label: "Chci", hint: "filmy a seriály, které Lumina hlídá" },
  { href: "/library", label: "Knihovna", hint: "co máš", perm: "library.view" },
  { href: "/account", label: "Účet", hint: "heslo, kde jsem přihlášen" },
];

export default function MobileMore() {
  const { user, can } = useAuth();
  const unseen = useWantedUnseen(can("wanted"));
  return (
    <main className="space-y-4 px-4 py-4">
      <h1 className="text-xl font-semibold text-zinc-100">Více</h1>
      <div className="divide-y divide-zinc-800 rounded-2xl border border-zinc-800 bg-zinc-900/60">
        {LINKS.filter((l) => !l.perm || can(l.perm)).map((l) => (
          <Link key={l.href} href={l.href} className="flex min-h-14 items-center justify-between px-4 py-3 active:bg-zinc-800">
            <span><span className="flex items-center gap-2 text-base text-zinc-100">{l.label}{l.href === "/wanted" && <UnseenBadge count={unseen} />}</span><span className="text-sm text-zinc-500">{l.hint}</span></span>
            <span className="text-xl text-zinc-600">›</span>
          </Link>
        ))}
      </div>
      <button onClick={() => { setPro(true); window.location.assign("/"); }}
        className="flex min-h-14 w-full items-center justify-between rounded-2xl border border-violet-800/60 bg-violet-950/30 px-4 py-3 text-left active:bg-violet-950">
        <span>
          <span className="block text-base text-violet-100">PRO — verze pro počítač</span>
          <span className="text-sm text-zinc-400">celá Lumina jako na PC (nastavení, editor zvuku…); zpět jedním ťuknutím nahoře</span>
        </span>
        <span className="text-xl text-violet-300">›</span>
      </button>
      <button onClick={() => logout().then(() => window.location.assign("/"))}
        className="min-h-12 w-full rounded-2xl border border-zinc-800 px-4 text-base text-zinc-400 active:bg-zinc-800">
        Odhlásit ({user?.username})
      </button>
    </main>
  );
}
