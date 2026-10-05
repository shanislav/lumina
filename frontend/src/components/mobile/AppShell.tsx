"use client";

import { ReactNode, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import NavLinks from "@/components/NavLinks";
import NotifyButton from "@/components/NotifyButton";
import { setPro, useMobile } from "@/lib/mobile";
import { useAuth } from "@/components/AuthGate";
import { UnseenBadge, useWantedUnseen } from "@/components/WantedMark";

/** Where a page of one version lives in the other (the phone version under /m). */
const TO_MOBILE: Record<string, string> = { "/": "/m", "/series": "/m/series" };
const TO_FULL: Record<string, string> = { "/m": "/", "/m/series": "/series", "/m/title": "/", "/m/watch": "/library",
  "/m/downloads": "/", "/m/more": "/", "/m/episode": "/series" };

const TABS = [
  { href: "/m", label: "Hledat", icon: "M21 21l-4.3-4.3M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15z", match: ["/m", "/m/title", "/m/series", "/m/episode"] },
  { href: "/discover", label: "Objevit", icon: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM15.5 8.5l-2 5-5 2 2-5z", match: ["/discover"] },
  { href: "/m/watch", label: "Sleduji", icon: "M8 5v14l11-7z", match: ["/m/watch"] },
  { href: "/m/downloads", label: "Stahuje", icon: "M12 3v12m0 0l-5-5m5 5l5-5M4 19h16", match: ["/m/downloads"] },
  { href: "/m/more", label: "Více", icon: "M4 6h16M4 12h16M4 18h16", match: ["/m/more"] },
];

/** The app's frame: the computer navigation on top, or — on a phone (unless "PRO") — a slim top bar and tabs at the
 *  bottom, with the pages of the phone version. */
export default function AppShell({ children }: { children: ReactNode }) {
  const m = useMobile();
  const path = usePathname() || "/";
  const router = useRouter();
  const { can } = useAuth();
  const unseen = useWantedUnseen(!!m?.mobile && can("wanted"));

  useEffect(() => {
    if (!m) return;
    const query = window.location.search;
    if (m.mobile && TO_MOBILE[path]) router.replace(TO_MOBILE[path] + query);
    else if (!m.mobile && TO_FULL[path]) router.replace(TO_FULL[path] + (path === "/m/series" || path === "/m/episode" ? query : ""));
  }, [m, path, router]);

  if (!m) return null;
  // a page about to move to the other version is not drawn (it would act on its address first: "/?movie=…")
  if ((m.mobile && TO_MOBILE[path]) || (!m.mobile && TO_FULL[path])) return null;

  if (!m.mobile) {
    return (
      <>
        <nav className="border-b border-zinc-800/50 px-4 py-3">
          <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <Link href="/" className="flex items-center gap-2 text-lg font-bold bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              <Image src="/favicon.svg" alt="" width={24} height={24} />
              <span className="hidden sm:inline">Lumina</span>
            </Link>
            <NavLinks />
          </div>
          {m.narrow && m.pro && (
            <button onClick={() => setPro(false)} className="mx-auto mt-2 block text-xs text-violet-300">← Zpět na mobilní verzi</button>
          )}
        </nav>
        {children}
      </>
    );
  }

  return (
    <div className="min-h-screen pb-[calc(4.5rem+env(safe-area-inset-bottom))]">
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-zinc-800/60 bg-zinc-950/95 px-4 py-2.5 backdrop-blur">
        <Link href="/m" className="flex items-center gap-2 text-lg font-bold text-violet-300">
          <Image src="/favicon.svg" alt="" width={24} height={24} /> Lumina
        </Link>
        <NotifyButton />
      </header>
      {children}
      <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-zinc-800 bg-zinc-950/95 pb-[env(safe-area-inset-bottom)] backdrop-blur">
        {TABS.map((t) => {
          const on = t.match.includes(path);
          return (
            <Link key={t.href} href={t.href}
              className={`relative flex flex-col items-center gap-0.5 py-2.5 text-xs ${on ? "text-violet-300" : "text-zinc-500"}`}>
              {t.href === "/m/more" && <UnseenBadge count={unseen} className="absolute right-[calc(50%-1.4rem)] top-1.5" />}
              <svg aria-hidden viewBox="0 0 24 24" className="h-6 w-6" fill={t.label === "Sleduji" && on ? "currentColor" : "none"}
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={t.icon} /></svg>
              {t.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
