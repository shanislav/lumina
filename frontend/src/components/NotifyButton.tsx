"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { NotificationItem, getNotifications, markNotificationsSeen } from "@/lib/api";

const LEVEL: Record<string, string> = {
  ok: "text-emerald-200", info: "text-zinc-100", warn: "text-amber-200", error: "text-red-300",
};
const ICON: Record<string, string> = { ok: "✓ ", warn: "⚠ ", error: "✕ ", info: "" };

function ago(at: string): string {
  const d = new Date(at.replace(" ", "T"));
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "teď";
  if (min < 60) return `před ${min} min`;
  if (min < 24 * 60) return `před ${Math.round(min / 60)} h`;
  return d.toLocaleDateString("cs-CZ", { day: "numeric", month: "numeric" }) + " " + d.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" });
}

/** What happened while you were away (backend modules/notify): downloads landed or failed, the wanted list or the
 *  nightly check found something, the TV automation found or started episodes. Opening the list marks it seen. */
export default function NotifyButton() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await getNotifications();
        if (!alive) return;
        setItems(r.items);
        setUnread(r.unread);
      } catch { /* the module may be off */ }
      timer = setTimeout(tick, 30000);
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && unread && items.length) {
      markNotificationsSeen(items[0].id).catch(() => {});
      setUnread(0);             // the "new" marks stay in the open list until it closes
    }
    if (!next) setItems((xs) => xs.map((x) => ({ ...x, new: false })));
  };

  return (
    <div ref={box} className="relative">
      <button onClick={toggle} title={unread ? `Nová upozornění: ${unread}` : "Upozornění"}
        className={`relative flex h-8 w-8 items-center justify-center rounded-full border transition-colors ${
          unread ? "border-amber-600 text-amber-300" : "border-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
        <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 min-w-[1rem] rounded-full bg-amber-600 px-1 text-[10px] leading-4 text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 max-h-[70vh] w-[min(24rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-xl space-y-2.5 text-xs">
          <p className="uppercase tracking-wide text-zinc-500">Upozornění</p>
          {!items.length && <p className="text-zinc-500">Zatím nic.</p>}
          {items.map((n) => {
            const title = <span className={LEVEL[n.level] ?? LEVEL.info}>{ICON[n.level] ?? ""}{n.title}</span>;
            return (
              <div key={n.id} className={`space-y-0.5 ${n.new ? "border-l-2 border-amber-500 pl-2" : "pl-2.5"}`}>
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1 break-words">
                    {n.link ? <Link href={n.link} onClick={() => setOpen(false)} className="hover:underline">{title}</Link> : title}
                  </div>
                  <span className="shrink-0 text-zinc-600">{ago(n.created_at)}</span>
                </div>
                {n.body && <p className="text-zinc-400 break-words">{n.body}</p>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
