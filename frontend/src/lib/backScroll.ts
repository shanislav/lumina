"use client";

import { useEffect, useRef } from "react";

/** "Back" to a list page returns to where it was scrolled. The page loads its list first, so the browser's own
 *  restoring (done before the list is there) leaves it at the top. Only a "back" restores — a link to the page
 *  (the top menu) opens it at the top. */

let cameBack = false;
if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => { cameBack = true; });
  const push = window.history.pushState.bind(window.history);
  window.history.pushState = (...args: Parameters<History["pushState"]>) => { cameBack = false; return push(...args); };
}

/** ``key``: the list (e.g. the library's tab); ``ready``: its items are drawn; ``extra``: a number to restore before
 *  scrolling (how many cards a lazily growing grid had drawn), given back to ``restoreExtra``. */
export function useBackScroll(key: string, ready: boolean, extra?: number, restoreExtra?: (n: number) => void) {
  // read at once: the list's own scroll events (the page still short, at the top) would overwrite it
  const saved = useRef<Record<string, { y: number; extra?: number }> | null>(null);
  if (saved.current === null) {
    saved.current = {};
    if (cameBack) {
      try {
        for (let i = 0; i < sessionStorage.length; i++) {
          const k = sessionStorage.key(i) || "";
          if (k.startsWith("lumina:scroll:")) saved.current[k.slice(14)] = JSON.parse(sessionStorage.getItem(k) || "null");
        }
      } catch { /* */ }
    }
    cameBack = false;
  }

  useEffect(() => {
    const save = () => {
      try { sessionStorage.setItem(`lumina:scroll:${key}`, JSON.stringify({ y: window.scrollY, extra })); } catch { /* */ }
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => window.removeEventListener("scroll", save);
  }, [key, extra]);

  const done = useRef(false);
  useEffect(() => {
    if (!ready || done.current) return;
    done.current = true;
    const was = saved.current?.[key];
    if (!was?.y) return;
    const { y } = was;
    if (was.extra && restoreExtra) restoreExtra(was.extra);
    let tries = 0;
    // the cards appear over a few frames (posters, a grown grid): until the page is long enough
    const go = () => {
      window.scrollTo(0, y);
      if (Math.abs(window.scrollY - y) > 2 && tries++ < 30) setTimeout(go, 50);
    };
    requestAnimationFrame(go);
  }, [ready, key, restoreExtra]);
}
