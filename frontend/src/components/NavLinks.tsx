"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getModules } from "@/lib/api";

/** Top navigation — a page is shown only when the backend module behind it runs. */
const LINKS: { href: string; label: string; module?: string }[] = [
  { href: "/discover", label: "Objevit", module: "search" },
  { href: "/library", label: "Knihovna", module: "library" },
  { href: "/settings", label: "Nastavení" },
];

export default function NavLinks() {
  // until the modules are known, show everything (no flicker for the usual setup)
  const [active, setActive] = useState<Set<string> | null>(null);

  useEffect(() => {
    getModules()
      .then((mods) => setActive(new Set(mods.filter((m) => m.active).map((m) => m.name))))
      .catch(() => {});
  }, []);

  return (
    <div className="flex items-center gap-4">
      {LINKS.filter((l) => !l.module || !active || active.has(l.module)).map((l) => (
        <Link key={l.href} href={l.href} className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
          {l.label}
        </Link>
      ))}
    </div>
  );
}
