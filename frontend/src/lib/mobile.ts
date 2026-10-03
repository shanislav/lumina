"use client";

import { useEffect, useState } from "react";
import type { TMDBMovie } from "@/lib/api";

/** The phone version (pages under /m): on a narrow screen unless the user switched to "PRO" (the full app as on a
 *  computer). The choice stays on the device. */
const PRO_KEY = "lumina.pro";
export const MOBILE_MAX = 768;

export function readPro(): boolean {
  try { return localStorage.getItem(PRO_KEY) === "1"; } catch { return false; }
}

export function setPro(on: boolean) {
  try { localStorage.setItem(PRO_KEY, on ? "1" : "0"); } catch { /* private mode */ }
  window.dispatchEvent(new Event("lumina:pro"));
}

/** {narrow: a phone-sized screen, pro: the full app chosen, mobile: show the phone version}; null until known. */
export function useMobile(): { narrow: boolean; pro: boolean; mobile: boolean } | null {
  const [state, setState] = useState<{ narrow: boolean; pro: boolean; mobile: boolean } | null>(null);
  useEffect(() => {
    const read = () => {
      // a phone also when Chrome shows it the "desktop site" (a 980 px wide page): its real screen is narrow
      // and touch-only — a small window on a computer is no phone
      const screenSide = Math.min(window.screen.width, window.screen.height);
      const touch = window.matchMedia?.("(pointer: coarse)").matches && !window.matchMedia?.("(any-pointer: fine)").matches;
      const narrow = window.innerWidth < MOBILE_MAX || (touch && screenSide < MOBILE_MAX);
      const pro = readPro();
      setState({ narrow, pro, mobile: narrow && !pro });
    };
    read();
    window.addEventListener("resize", read);
    window.addEventListener("lumina:pro", read);
    return () => { window.removeEventListener("resize", read); window.removeEventListener("lumina:pro", read); };
  }, []);
  return state;
}

/** Languages as people say them. */
export const LANG_NAMES: Record<string, string> = {
  cs: "CZ", sk: "SK", en: "EN", de: "DE", fr: "FR", es: "ES", it: "IT", pl: "PL", hu: "HU", ru: "RU", ja: "JA", uk: "UK",
};
export const langName = (l: string) => LANG_NAMES[l.toLowerCase()] ?? l.toUpperCase();

/** The phone page of a film (its files) or a show (its seasons). */
export function titleHref(m: TMDBMovie): string {
  if (m.media_type === "tv") return `/m/series?tmdb=${m.tmdb_id}`;
  const q = new URLSearchParams({ tmdb: String(m.tmdb_id || 0), title: m.title, year: m.year || "",
    orig: m.original_title || "", poster: m.poster_url || "" });
  if (m.wikidata_id) q.set("wd", m.wikidata_id);
  return `/m/title?${q}`;
}

/** "6,2 GB" / "380 MB". */
export function size(bytes: number): string {
  if (!bytes) return "?";
  return bytes >= 1e9 ? `${(bytes / 1e9).toLocaleString("cs-CZ", { maximumFractionDigits: 1 })} GB` : `${Math.round(bytes / 1e6)} MB`;
}
