"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  AppSettings,
  SourceCreate,
  updateAppSettings,
  createSource,
  testSourceConfig,
} from "@/lib/api";

const SOURCE_TYPES = [
  {
    type: "webshare",
    label: "WebShare",
    fields: [
      { key: "username", label: "Username", inputType: "text" },
      { key: "password", label: "Password", inputType: "password" },
    ],
  },
  {
    type: "fastshare",
    label: "FastShare",
    fields: [
      { key: "login", label: "Login", inputType: "text" },
      { key: "heslo", label: "Heslo", inputType: "password" },
    ],
  },
  {
    type: "jackett",
    label: "Jackett",
    fields: [
      { key: "url", label: "URL", inputType: "text", placeholder: "http://jackett:9117" },
      { key: "api_key", label: "API Key", inputType: "password" },
    ],
  },
];

type Step = "welcome" | "api-keys" | "download" | "sources" | "done";

const STEPS: Step[] = ["welcome", "api-keys", "download", "sources", "done"];

export default function SetupPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("welcome");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // API keys
  const [tmdbKey, setTmdbKey] = useState("");
  const [groqKey, setGroqKey] = useState("");

  // Download settings
  const [plexDir, setPlexDir] = useState("/downloads/plex");
  const [aria2Url, setAria2Url] = useState("http://aria2:6800/jsonrpc");
  const [aria2Secret, setAria2Secret] = useState("");
  const [qbtUrl, setQbtUrl] = useState("http://qbittorrent:8080");
  const [qbtUser, setQbtUser] = useState("admin");
  const [qbtPass, setQbtPass] = useState("");

  // Sources
  const [sources, setSources] = useState<SourceCreate[]>([]);
  const [addType, setAddType] = useState("webshare");
  const [addName, setAddName] = useState("");
  const [addConfig, setAddConfig] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<boolean | null | "loading">(null);

  const stepIndex = STEPS.indexOf(step);
  const progress = Math.round((stepIndex / (STEPS.length - 1)) * 100);

  function next() {
    const idx = STEPS.indexOf(step);
    if (idx < STEPS.length - 1) setStep(STEPS[idx + 1]);
  }

  function prev() {
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
  }

  async function handleAddSource() {
    const typeDef = SOURCE_TYPES.find((t) => t.type === addType)!;
    const src: SourceCreate = {
      type: addType,
      name: addName || typeDef.label,
      config: { ...addConfig },
    };
    setSources((prev) => [...prev, src]);
    setAddName("");
    setAddConfig({});
    setTestResult(null);
  }

  function removeSource(idx: number) {
    setSources((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleTestSource() {
    setTestResult("loading");
    const typeDef = SOURCE_TYPES.find((t) => t.type === addType)!;
    try {
      const result = await testSourceConfig({
        type: addType,
        name: addName || typeDef.label,
        config: addConfig,
      });
      setTestResult(result.ok);
    } catch {
      setTestResult(false);
    }
  }

  async function handleFinish() {
    setSaving(true);
    setError(null);
    try {
      // Save settings
      const settings: AppSettings = {
        tmdb_api_key: tmdbKey,
        groq_api_key: groqKey,
        plex_media_dir: plexDir,
        aria2_rpc_url: aria2Url,
        aria2_rpc_secret: aria2Secret,
        qbittorrent_url: qbtUrl,
        qbittorrent_username: qbtUser,
        qbittorrent_password: qbtPass,
      };
      await updateAppSettings(settings);

      // Create sources
      for (const src of sources) {
        await createSource(src);
      }

      next(); // go to "done"
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba pri ukladani");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="flex flex-col items-center justify-center min-h-[calc(100vh-57px)] px-4 py-12">
      <div className="w-full max-w-xl space-y-8">
        {/* Progress bar */}
        {step !== "welcome" && step !== "done" && (
          <div className="w-full bg-zinc-800 rounded-full h-1.5">
            <div
              className="bg-gradient-to-r from-violet-500 to-fuchsia-500 h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}

        {/* ===== WELCOME ===== */}
        {step === "welcome" && (
          <div className="text-center space-y-6">
            <div className="space-y-3">
              <h1 className="text-4xl font-bold bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
                Lumina
              </h1>
              <p className="text-zinc-400 text-lg">
                DDL a torrent obsah pro tvuj Plex server
              </p>
            </div>
            <div className="space-y-2 text-sm text-zinc-500 max-w-md mx-auto">
              <p>Vitej! Projdeme spolu rychle nastaveni:</p>
              <div className="grid grid-cols-3 gap-3 pt-4 text-center">
                <div className="rounded-lg border border-zinc-800 p-3 space-y-1">
                  <div className="text-2xl">🔑</div>
                  <div className="text-xs text-zinc-400">API klice</div>
                </div>
                <div className="rounded-lg border border-zinc-800 p-3 space-y-1">
                  <div className="text-2xl">📁</div>
                  <div className="text-xs text-zinc-400">Stahovani</div>
                </div>
                <div className="rounded-lg border border-zinc-800 p-3 space-y-1">
                  <div className="text-2xl">🔌</div>
                  <div className="text-xs text-zinc-400">Zdroje</div>
                </div>
              </div>
            </div>
            <button
              onClick={next}
              className="rounded-lg bg-violet-600 px-8 py-3 font-medium text-white hover:bg-violet-500 transition-colors text-lg"
            >
              Zacit nastaveni
            </button>
          </div>
        )}

        {/* ===== API KEYS ===== */}
        {step === "api-keys" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl font-bold text-zinc-100">API klice</h2>
              <p className="text-sm text-zinc-500 mt-1">
                Potrebujeme dva klice pro vyhledavani a hodnoceni souboru
              </p>
            </div>

            <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
              <div>
                <label className="block text-sm font-medium text-zinc-300 mb-1">
                  TMDB API Key
                </label>
                <input
                  type="password"
                  value={tmdbKey}
                  onChange={(e) => setTmdbKey(e.target.value)}
                  placeholder="Pristupovy token API ke cteni"
                  className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                />
                <p className="text-xs text-zinc-600 mt-1">
                  Zaregistruj se na{" "}
                  <a
                    href="https://www.themoviedb.org/settings/api"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-violet-400 hover:text-violet-300"
                  >
                    themoviedb.org
                  </a>
                  {" "}→ API → Pristupovy token ke cteni
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-zinc-300 mb-1">
                  Groq API Key <span className="text-zinc-500 font-normal">(volitelné)</span>
                </label>
                <input
                  type="password"
                  value={groqKey}
                  onChange={(e) => setGroqKey(e.target.value)}
                  placeholder="gsk_..."
                  className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                />
                <p className="text-xs text-zinc-600 mt-1">
                  Zdarma na{" "}
                  <a
                    href="https://console.groq.com/keys"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-violet-400 hover:text-violet-300"
                  >
                    console.groq.com
                  </a>
                  {" "}→ API Keys → Create. Bez klíče Lumina hodnotí nalezené soubory podle názvu (méně přesně).
                </p>
              </div>
            </div>

            <StepNav
              onPrev={prev}
              onNext={next}
              nextDisabled={!tmdbKey}
              nextLabel="Dalsi"
            />
          </div>
        )}

        {/* ===== DOWNLOAD CONFIG ===== */}
        {step === "download" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl font-bold text-zinc-100">Stahovani</h2>
              <p className="text-sm text-zinc-500 mt-1">
                Nastav cilovou slozku a pripojeni k download klientum
              </p>
            </div>

            <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
              <h3 className="text-sm font-medium text-zinc-300">
                Cilova slozka
              </h3>
              <div>
                <input
                  type="text"
                  value={plexDir}
                  onChange={(e) => setPlexDir(e.target.value)}
                  className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm"
                />
                <p className="text-xs text-zinc-600 mt-1">
                  Cesta v Docker kontejneru kde Plex hleda media
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
              <h3 className="text-sm font-medium text-zinc-300">
                Aria2 (prime stahovani)
              </h3>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">RPC URL</label>
                  <input
                    type="text"
                    value={aria2Url}
                    onChange={(e) => setAria2Url(e.target.value)}
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">RPC Secret</label>
                  <input
                    type="password"
                    value={aria2Secret}
                    onChange={(e) => setAria2Secret(e.target.value)}
                    placeholder="volitelne"
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                  />
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
              <h3 className="text-sm font-medium text-zinc-300">
                qBittorrent (torrenty)
              </h3>
              <div>
                <label className="block text-xs text-zinc-400 mb-1">URL</label>
                <input
                  type="text"
                  value={qbtUrl}
                  onChange={(e) => setQbtUrl(e.target.value)}
                  className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">Username</label>
                  <input
                    type="text"
                    value={qbtUser}
                    onChange={(e) => setQbtUser(e.target.value)}
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">Password</label>
                  <input
                    type="password"
                    value={qbtPass}
                    onChange={(e) => setQbtPass(e.target.value)}
                    placeholder="volitelne"
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                  />
                </div>
              </div>
            </div>

            <StepNav onPrev={prev} onNext={next} nextLabel="Dalsi" />
          </div>
        )}

        {/* ===== SOURCES ===== */}
        {step === "sources" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl font-bold text-zinc-100">Zdroje souboru</h2>
              <p className="text-sm text-zinc-500 mt-1">
                Pridej alespon jeden zdroj odkud se budou hledat soubory
              </p>
            </div>

            {/* Added sources list */}
            {sources.length > 0 && (
              <div className="space-y-2">
                {sources.map((src, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3"
                  >
                    <div>
                      <span className="text-zinc-100 font-medium">{src.name}</span>
                      <span className="ml-2 text-xs text-zinc-500 uppercase">{src.type}</span>
                    </div>
                    <button
                      onClick={() => removeSource(idx)}
                      className="text-xs text-red-400 hover:text-red-300"
                    >
                      Odebrat
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Add source form */}
            <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-5 space-y-4">
              <h3 className="text-sm font-medium text-zinc-300">Pridat zdroj</h3>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">Typ</label>
                  <select
                    value={addType}
                    onChange={(e) => {
                      setAddType(e.target.value);
                      setAddConfig({});
                      setTestResult(null);
                    }}
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm"
                  >
                    {SOURCE_TYPES.map((t) => (
                      <option key={t.type} value={t.type}>{t.label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">Nazev</label>
                  <input
                    type="text"
                    value={addName}
                    onChange={(e) => setAddName(e.target.value)}
                    placeholder={SOURCE_TYPES.find((t) => t.type === addType)?.label}
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                  />
                </div>
              </div>

              {SOURCE_TYPES.find((t) => t.type === addType)?.fields.map((field) => (
                <div key={field.key}>
                  <label className="block text-xs text-zinc-400 mb-1">{field.label}</label>
                  <input
                    type={field.inputType}
                    value={addConfig[field.key] || ""}
                    onChange={(e) => setAddConfig((p) => ({ ...p, [field.key]: e.target.value }))}
                    placeholder={"placeholder" in field ? (field as { placeholder: string }).placeholder : ""}
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                  />
                </div>
              ))}

              <div className="flex gap-3">
                <button
                  onClick={handleAddSource}
                  disabled={
                    !SOURCE_TYPES.find((t) => t.type === addType)?.fields.every(
                      (f) => addConfig[f.key]
                    )
                  }
                  className="rounded bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-40 transition-colors"
                >
                  Pridat
                </button>
                <button
                  onClick={handleTestSource}
                  className="rounded bg-zinc-700 px-4 py-2 text-sm text-zinc-200 hover:bg-zinc-600 transition-colors"
                >
                  {testResult === "loading"
                    ? "Testuji..."
                    : testResult === true
                    ? "OK"
                    : testResult === false
                    ? "Chyba"
                    : "Test"
                  }
                </button>
              </div>
            </div>

            {error && (
              <div className="rounded-lg bg-red-900/30 border border-red-800 px-4 py-3 text-red-300 text-sm">
                {error}
              </div>
            )}

            <StepNav
              onPrev={prev}
              onNext={handleFinish}
              nextDisabled={sources.length === 0 || saving}
              nextLabel={saving ? "Ukladam..." : "Dokoncit nastaveni"}
            />
          </div>
        )}

        {/* ===== DONE ===== */}
        {step === "done" && (
          <div className="text-center space-y-6">
            <div className="text-6xl">🎉</div>
            <div className="space-y-2">
              <h2 className="text-2xl font-bold text-zinc-100">Lumina je pripravena!</h2>
              <p className="text-zinc-500">
                Nastaveni dokonceno. Muzes zacit hledat a stahovat.
              </p>
            </div>
            <button
              onClick={() => router.push("/")}
              className="rounded-lg bg-violet-600 px-8 py-3 font-medium text-white hover:bg-violet-500 transition-colors text-lg"
            >
              Zacit pouzivat Lumina
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

function StepNav({
  onPrev,
  onNext,
  nextDisabled,
  nextLabel = "Dalsi",
}: {
  onPrev: () => void;
  onNext: () => void;
  nextDisabled?: boolean;
  nextLabel?: string;
}) {
  return (
    <div className="flex justify-between pt-2">
      <button
        onClick={onPrev}
        className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
      >
        &larr; Zpet
      </button>
      <button
        onClick={onNext}
        disabled={nextDisabled}
        className="rounded-lg bg-violet-600 px-6 py-2.5 font-medium text-white hover:bg-violet-500 disabled:opacity-40 transition-colors"
      >
        {nextLabel} &rarr;
      </button>
    </div>
  );
}
