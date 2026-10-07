"use client";

import { useEffect, useRef } from "react";

/** "Back" to a list page returns to where it was scrolled. The page loads its list first, so the browser's own
 *  restoring (done before the list is there) leaves it at the top. Only a "back" restores — a link to the page
 *  (the top menu) opens it at the top.
 *
 *  Not the pixels: the card at the top of the screen (``data-anchor``) and where it was — panels above the list
 *  load later and push it down, the same pixels then showed a bit higher. */

let cameBack = false;
if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => { cameBack = true; });
  const push = window.history.pushState.bind(window.history);
  window.history.pushState = (...args: Parameters<History["pushState"]>) => { cameBack = false; return push(...args); };
}

type Saved = { y: number; extra?: number; anchor?: string; top?: number };

/** The first card whose bottom is on the screen, and its distance from the top. */
function topCard(): { anchor: string; top: number } | null {
  for (const el of document.querySelectorAll<HTMLElement>("[data-anchor]")) {
    const r = el.getBoundingClientRect();
    if (r.bottom > 0) return { anchor: el.dataset.anchor || "", top: r.top };
  }
  return null;
}

/** ``key``: the list (e.g. the library's tab); ``ready``: its items are drawn; ``extra``: a number to restore before
 *  scrolling (how many cards a lazily growing grid had drawn), given back to ``restoreExtra``. */
export function useBackScroll(key: string, ready: boolean, extra?: number, restoreExtra?: (n: number) => void) {
  // read at once: the list's own scroll events (the page still short, at the top) would overwrite it
  const saved = useRef<Record<string, Saved> | null>(null);
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
  const restoring = useRef(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const save = () => {
      if (restoring.current || timer) return;
      timer = setTimeout(() => {
        timer = null;
        const card = topCard();
        try {
          sessionStorage.setItem(`lumina:scroll:${key}`, JSON.stringify({ y: window.scrollY, extra, ...card }));
        } catch { /* */ }
      }, 150);
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => {
      window.removeEventListener("scroll", save);
      if (timer) clearTimeout(timer);
    };
  }, [key, extra]);

  const done = useRef(false);
  useEffect(() => {
    if (!ready || done.current) return;
    done.current = true;
    const was = saved.current?.[key];
    if (!was?.y) return;
    if (was.extra && restoreExtra) restoreExtra(was.extra);
    restoring.current = true;
    let stopped = false;
    const stop = () => { stopped = true; };         // the user scrolls by themself: leave it
    window.addEventListener("wheel", stop, { passive: true, once: true });
    window.addEventListener("touchstart", stop, { passive: true, once: true });
    window.addEventListener("keydown", stop, { once: true });
    const place = () => {
      const el = was.anchor ? document.querySelector<HTMLElement>(`[data-anchor="${CSS.escape(was.anchor)}"]`) : null;
      if (el && was.top !== undefined) window.scrollBy(0, el.getBoundingClientRect().top - was.top);
      else window.scrollTo(0, was.y);
    };
    // the panels above and the posters come over a while: keep the card in its place until the page settles
    let tick = 0;
    const timer = setInterval(() => {
      if (!stopped) place();
      if (stopped || ++tick >= 30) {
        clearInterval(timer);
        restoring.current = false;
        window.removeEventListener("wheel", stop);
        window.removeEventListener("touchstart", stop);
        window.removeEventListener("keydown", stop);
      }
    }, 100);
    requestAnimationFrame(place);
    return () => { clearInterval(timer); restoring.current = false; };
  }, [ready, key, restoreExtra]);
}
