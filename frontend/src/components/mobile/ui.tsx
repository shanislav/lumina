"use client";

import { ReactNode, useEffect } from "react";

/** Building blocks of the phone version: big touch targets, a sheet from the bottom, a choice button. */

export function BigButton({ children, onClick, kind = "primary", disabled, className = "" }: {
  children: ReactNode; onClick?: () => void; kind?: "primary" | "secondary" | "danger"; disabled?: boolean; className?: string;
}) {
  const style = kind === "primary" ? "bg-violet-600 text-white active:bg-violet-700"
    : kind === "danger" ? "border border-red-800 text-red-300 active:bg-red-950"
      : "border border-zinc-700 text-zinc-100 active:bg-zinc-800";
  return (
    <button onClick={onClick} disabled={disabled}
      className={`flex min-h-12 w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-base font-medium disabled:opacity-40 ${style} ${className}`}>
      {children}
    </button>
  );
}

/** A panel sliding from the bottom over the page; tap outside or "Hotovo" closes it. */
export function Sheet({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end bg-black/60" onClick={onClose}>
      <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl border-t border-zinc-700 bg-zinc-900 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3"
        onClick={(e) => e.stopPropagation()}>
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-zinc-700" />
        <div className="mb-3 flex items-center justify-between">
          <p className="text-lg font-semibold text-zinc-100">{title}</p>
          <button onClick={onClose} className="rounded-lg px-3 py-2 text-violet-300 active:bg-zinc-800">Hotovo</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** One option of a sheet: a full-width row with a check mark. */
export function Option({ active, onClick, children, hint }: { active: boolean; onClick: () => void; children: ReactNode; hint?: string }) {
  return (
    <button onClick={onClick}
      className={`flex min-h-12 w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left text-base active:bg-zinc-800 ${
        active ? "bg-violet-950/60 text-violet-100" : "text-zinc-200"}`}>
      <span>{children}{hint && <span className="block text-sm text-zinc-500">{hint}</span>}</span>
      {active && <span className="text-violet-300">✓</span>}
    </button>
  );
}

/** A filter: a pill with what is chosen ("Zvuk: CZ/SK ▾"); a tap opens its sheet. */
export function ChoiceButton({ label, value, active, onClick }: { label: string; value: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={`flex shrink-0 items-center gap-1 rounded-full border px-4 py-2 text-sm ${
        active ? "border-violet-600 bg-violet-950/60 text-violet-100" : "border-zinc-700 text-zinc-300"}`}>
      {label}: <span className="font-medium">{value}</span> <span className="text-zinc-500">▾</span>
    </button>
  );
}

export function Spinner({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-3 py-8 text-zinc-400">
      <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      {text}
    </div>
  );
}

export function Tag({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "good" | "warn" }) {
  const style = tone === "good" ? "bg-emerald-900/60 text-emerald-200" : tone === "warn" ? "bg-amber-900/50 text-amber-200" : "bg-zinc-800 text-zinc-300";
  return <span className={`inline-block rounded-md px-2 py-0.5 text-sm ${style}`}>{children}</span>;
}
