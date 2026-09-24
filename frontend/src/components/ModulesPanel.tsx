"use client";

import { useEffect, useState } from "react";
import { ModuleInfo, getModules, updateAppSettings } from "@/lib/api";

/**
 * Switch modules on/off (setting "disabled_modules"). Modules are loaded when the backend
 * starts, so a change applies after a restart — the list says which ones are waiting for it.
 */
export default function ModulesPanel() {
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => { getModules().then(setModules).catch(() => {}); }, []);

  async function toggle(name: string) {
    const next = modules.map((m) => (m.name === name ? { ...m, enabled: !m.enabled } : m));
    setSaving(true);
    try {
      await updateAppSettings({
        disabled_modules: next.filter((m) => !m.required && !m.enabled).map((m) => m.name).join(","),
      });
      setModules(next);
    } finally {
      setSaving(false);
    }
  }

  if (!modules.length) return null;
  const pending = modules.some((m) => m.enabled !== m.active);

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 px-5 py-4 space-y-3">
      <div>
        <p className="text-zinc-200 font-medium">🧩 Moduly</p>
        <p className="text-xs text-zinc-500">Každá funkce je samostatný modul. Povinné nejdou vypnout.</p>
      </div>
      <div className="grid sm:grid-cols-2 gap-2">
        {modules.filter((m) => m.name !== "core").map((m) => (
          <label key={m.name} className={`flex items-center gap-2 text-sm ${m.required ? "text-zinc-500" : "text-zinc-300"}`}>
            <input type="checkbox" checked={m.enabled} disabled={m.required || saving} onChange={() => toggle(m.name)} />
            {m.title} <span className="text-[10px] text-zinc-600 font-mono">{m.name}</span>
            {m.enabled !== m.active && <span className="text-[10px] text-amber-400">po restartu</span>}
          </label>
        ))}
      </div>
      {pending && (
        <p className="text-xs text-amber-300">Změna se projeví po restartu backendu (docker compose restart backend).</p>
      )}
    </div>
  );
}
