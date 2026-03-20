"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  Source,
  AppSettings,
  LanguageOption,
  getSources,
  createSource,
  updateSource,
  deleteSource,
  testSource,
  testSourceConfig,
  getAppSettings,
  updateAppSettings,
  getLanguages,
} from "@/lib/api";
import { FLAG } from "@/components/LanguageSelect";
import FolderBrowser from "@/components/FolderBrowser";

const SOURCE_TYPES = [
  {
    type: "webshare",
    label: "WebShare",
    fields: [
      { key: "username", label: "Username", type: "text" },
      { key: "password", label: "Password", type: "password" },
    ],
  },
  {
    type: "fastshare",
    label: "FastShare",
    fields: [
      { key: "login", label: "Login", type: "text" },
      { key: "heslo", label: "Heslo", type: "password" },
    ],
  },
  {
    type: "jackett",
    label: "Jackett",
    fields: [
      { key: "url", label: "URL", type: "text", placeholder: "http://jackett:9117" },
      { key: "api_key", label: "API Key", type: "password" },
    ],
    hint: "Manage indexers (sktorrent, etc.) in Jackett UI",
  },
];

// Settings field definitions grouped by section
const SETTINGS_SECTIONS = [
  {
    title: "API klíče",
    icon: "🔑",
    fields: [
      { key: "tmdb_api_key", label: "TMDB API Key", type: "password", hint: "Pro vyhledávání filmů (themoviedb.org)" },
      { key: "groq_api_key", label: "Groq API Key", type: "password", hint: "Pro AI hodnocení souborů (console.groq.com)" },
    ],
  },
  {
    title: "Stahování",
    icon: "📁",
    fields: [
      { key: "plex_media_dir", label: "Složka pro stahování", type: "folder", hint: "Cílová složka pro Plex (v Docker kontejneru)" },
    ],
  },
  {
    title: "Aria2 (přímé stahování)",
    icon: "⬇️",
    fields: [
      { key: "aria2_rpc_url", label: "RPC URL", type: "text", hint: "Výchozí: http://aria2:6800/jsonrpc" },
      { key: "aria2_rpc_secret", label: "RPC Secret", type: "password" },
    ],
  },
  {
    title: "qBittorrent (torrenty)",
    icon: "🧲",
    fields: [
      { key: "qbittorrent_url", label: "URL", type: "text", hint: "Např. http://qbittorrent:8080" },
      { key: "qbittorrent_username", label: "Username", type: "text" },
      { key: "qbittorrent_password", label: "Password", type: "password" },
    ],
  },
  {
    title: "Vyhledávání",
    icon: "🔍",
    fields: [
      { key: "min_relevance_score", label: "Minimální skóre relevance", type: "range", hint: "Soubory s nižším skóre se nezobrazí (0 = vše, 100 = pouze perfektní shoda)" },
    ],
  },
];

export default function SettingsPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [testResults, setTestResults] = useState<Record<number, boolean | null>>({});

  // App settings
  const [settings, setSettings] = useState<AppSettings>({});
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);

  // Languages
  const [allLanguages, setAllLanguages] = useState<LanguageOption[]>([]);

  // Folder browser
  const [browsingField, setBrowsingField] = useState<string | null>(null);

  const loadSources = useCallback(async () => {
    try {
      setSources(await getSources());
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      setSettings(await getAppSettings());
    } finally {
      setSettingsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSources();
    loadSettings();
    getLanguages().then(setAllLanguages).catch(() => {});
  }, [loadSources, loadSettings]);

  function handleSettingChange(key: string, value: string) {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setSettingsDirty(true);
    setSettingsSaved(false);
  }

  async function handleSettingsSave() {
    setSettingsSaving(true);
    try {
      const updated = await updateAppSettings(settings);
      setSettings(updated);
      setSettingsDirty(false);
      setSettingsSaved(true);
      setTimeout(() => setSettingsSaved(false), 3000);
    } finally {
      setSettingsSaving(false);
    }
  }

  async function handleToggle(source: Source) {
    await updateSource(source.id, { enabled: !source.enabled });
    await loadSources();
  }

  async function handleDelete(id: number) {
    if (!confirm("Opravdu smazat tento zdroj?")) return;
    await deleteSource(id);
    await loadSources();
  }

  async function handleTest(id: number) {
    setTestResults((prev) => ({ ...prev, [id]: null }));
    const result = await testSource(id);
    setTestResults((prev) => ({ ...prev, [id]: result.ok }));
  }

  return (
    <main className="flex flex-col items-center gap-8 px-4 py-12 max-w-4xl mx-auto">
      <div className="flex items-center gap-4 w-full">
        <Link
          href="/"
          className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm"
        >
          &larr; Zpět
        </Link>
        <h1 className="text-2xl font-bold text-zinc-100">Nastavení</h1>
      </div>

      {/* ========== GENERAL SETTINGS ========== */}
      <section className="w-full space-y-6">
        <h2 className="text-lg font-semibold text-zinc-200 border-b border-zinc-800 pb-2">
          Obecné nastavení
        </h2>

        {settingsLoading ? (
          <p className="text-zinc-500">Načítám nastavení...</p>
        ) : (
          <>
            {SETTINGS_SECTIONS.map((section) => (
              <div
                key={section.title}
                className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4"
              >
                <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
                  <span>{section.icon}</span>
                  {section.title}
                </h3>
                {section.fields.map((field) => (
                  <div key={field.key}>
                    <label className="block text-sm text-zinc-400 mb-1">
                      {field.label}
                      {field.type === "range" && (
                        <span className="ml-2 text-violet-400 font-medium">
                          {settings[field.key] || "70"}
                        </span>
                      )}
                    </label>
                    {field.type === "range" ? (
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="5"
                        value={settings[field.key] || "70"}
                        onChange={(e) =>
                          handleSettingChange(field.key, e.target.value)
                        }
                        className="w-full h-2 rounded-lg appearance-none cursor-pointer bg-zinc-700 accent-violet-500"
                      />
                    ) : field.type === "folder" ? (
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={settings[field.key] || ""}
                          onChange={(e) =>
                            handleSettingChange(field.key, e.target.value)
                          }
                          className="flex-1 rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                        />
                        <button
                          type="button"
                          onClick={() => setBrowsingField(field.key)}
                          className="rounded bg-zinc-700 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-600 transition-colors flex items-center gap-1.5"
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                          </svg>
                          Browse
                        </button>
                      </div>
                    ) : (
                      <input
                        type={field.type}
                        value={settings[field.key] || ""}
                        onChange={(e) =>
                          handleSettingChange(field.key, e.target.value)
                        }
                        placeholder={
                          field.type === "password" ? "********" : ""
                        }
                        className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600 text-sm"
                      />
                    )}
                    {"hint" in field && field.hint && (
                      <p className="text-xs text-zinc-600 mt-1">{field.hint}</p>
                    )}
                  </div>
                ))}
              </div>
            ))}

            <div className="flex items-center gap-3">
              <button
                onClick={handleSettingsSave}
                disabled={!settingsDirty || settingsSaving}
                className="rounded bg-violet-600 px-5 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-40 transition-colors"
              >
                {settingsSaving ? "Ukládám..." : "Uložit nastavení"}
              </button>
              {settingsSaved && (
                <span className="text-sm text-green-400">Uloženo</span>
              )}
            </div>
          </>
        )}
      </section>

      {/* ========== LANGUAGES ========== */}
      <section className="w-full space-y-4">
        <h2 className="text-lg font-semibold text-zinc-200 border-b border-zinc-800 pb-2">
          Preferred Languages
        </h2>
        <p className="text-sm text-zinc-500">
          Select languages for dubbing detection and TMDB metadata. The search dropdown lets you filter by a specific language.
        </p>
        {allLanguages.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {allLanguages.map((lang) => {
              const enabledCodes = (settings.languages || "cs").split(",").map((c) => c.trim()).filter(Boolean);
              const isSelected = enabledCodes.includes(lang.code);
              return (
                <button
                  key={lang.code}
                  onClick={() => {
                    let next: string[];
                    if (isSelected) {
                      next = enabledCodes.filter((c) => c !== lang.code);
                    } else {
                      next = [...enabledCodes, lang.code];
                    }
                    if (next.length === 0) next = ["cs"]; // at least one
                    handleSettingChange("languages", next.join(","));
                  }}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                    isSelected
                      ? "bg-violet-600 text-white"
                      : "bg-zinc-800 text-zinc-500 hover:bg-zinc-700 hover:text-zinc-300"
                  }`}
                >
                  {FLAG[lang.code] || ""} {lang.label} <span className="opacity-60">{lang.name}</span>
                </button>
              );
            })}
          </div>
        )}
        {settingsDirty && (
          <p className="text-xs text-amber-400">
            Don&apos;t forget to save settings above.
          </p>
        )}
      </section>

      {/* ========== SOURCES ========== */}
      <section className="w-full space-y-4">
        <h2 className="text-lg font-semibold text-zinc-200 border-b border-zinc-800 pb-2">
          Zdroje souborů
        </h2>

        {loading ? (
          <p className="text-zinc-500">Načítám...</p>
        ) : (
          <div className="w-full space-y-3">
            {sources.map((source) => (
              <div
                key={source.id}
                className="flex items-center gap-4 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3"
              >
                <button
                  onClick={() => handleToggle(source)}
                  className={`w-10 h-6 rounded-full relative transition-colors ${
                    source.enabled ? "bg-green-600" : "bg-zinc-700"
                  }`}
                >
                  <span
                    className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                      source.enabled ? "left-5" : "left-1"
                    }`}
                  />
                </button>

                <div className="flex-1 min-w-0">
                  <span className="text-zinc-100 font-medium">{source.name}</span>
                  <span className="ml-2 text-xs text-zinc-500 uppercase">
                    {source.type}
                  </span>
                </div>

                <button
                  onClick={() => handleTest(source.id)}
                  className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
                >
                  {testResults[source.id] === null
                    ? "..."
                    : testResults[source.id] === true
                    ? "OK"
                    : testResults[source.id] === false
                    ? "Chyba"
                    : "Test"}
                </button>

                <button
                  onClick={() => setEditId(editId === source.id ? null : source.id)}
                  className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
                >
                  Upravit
                </button>

                <button
                  onClick={() => handleDelete(source.id)}
                  className="text-xs text-red-400 hover:text-red-300 transition-colors"
                >
                  Smazat
                </button>

                {source.type === "jackett" && source.config.url && (
                  <a
                    href={source.config.url.replace("jackett:9117", "localhost:9117")}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-violet-400 hover:text-violet-300 transition-colors"
                  >
                    Jackett UI
                  </a>
                )}
              </div>
            ))}

            {sources.length === 0 && (
              <p className="text-zinc-500 text-center py-8">
                Žádné zdroje. Přidej první kliknutím na tlačítko níže.
              </p>
            )}
          </div>
        )}

        {/* Edit inline */}
        {editId && (
          <SourceForm
            key={`edit-${editId}`}
            source={sources.find((s) => s.id === editId)}
            onDone={() => {
              setEditId(null);
              loadSources();
            }}
            onCancel={() => setEditId(null)}
          />
        )}

        {/* Add new */}
        {showAdd ? (
          <SourceForm
            key="add-new"
            onDone={() => {
              setShowAdd(false);
              loadSources();
            }}
            onCancel={() => setShowAdd(false)}
          />
        ) : (
          <button
            onClick={() => setShowAdd(true)}
            className="rounded-lg bg-violet-600 px-6 py-3 font-medium text-white hover:bg-violet-500 transition-colors"
          >
            + Přidat zdroj
          </button>
        )}
      </section>

      {/* Folder browser modal */}
      {browsingField && (
        <FolderBrowser
          initialPath={settings[browsingField] || "/downloads"}
          onSelect={(path) => {
            handleSettingChange(browsingField, path);
            setBrowsingField(null);
          }}
          onCancel={() => setBrowsingField(null)}
        />
      )}
    </main>
  );
}

function SourceForm({
  source,
  onDone,
  onCancel,
}: {
  source?: Source;
  onDone: () => void;
  onCancel: () => void;
}) {
  const isEdit = !!source;
  const [type, setType] = useState(source?.type || "webshare");
  const [name, setName] = useState(source?.name || "");
  const [config, setConfig] = useState<Record<string, string>>(source?.config || {});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<boolean | null | "loading">(null);

  const typeDef = SOURCE_TYPES.find((t) => t.type === type)!;

  function setField(key: string, value: string) {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }

  async function handleTest() {
    setTesting("loading");
    const result = await testSourceConfig({ type, name: name || type, config });
    setTesting(result.ok);
  }

  async function handleSubmit() {
    setSaving(true);
    try {
      if (isEdit && source) {
        await updateSource(source.id, { name, config });
      } else {
        await createSource({ type, name: name || typeDef.label, config });
      }
      onDone();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="w-full rounded-lg border border-zinc-700 bg-zinc-900 p-6 space-y-4">
      <h3 className="text-lg font-medium text-zinc-100">
        {isEdit ? "Upravit zdroj" : "Nový zdroj"}
      </h3>

      {!isEdit && (
        <div>
          <label className="block text-sm text-zinc-400 mb-1">Typ</label>
          <select
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              setConfig({});
            }}
            className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100"
          >
            {SOURCE_TYPES.map((t) => (
              <option key={t.type} value={t.type}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <div>
        <label className="block text-sm text-zinc-400 mb-1">Název</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={typeDef.label}
          className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600"
        />
      </div>

      {typeDef.fields.map((field) => (
        <div key={field.key}>
          <label className="block text-sm text-zinc-400 mb-1">{field.label}</label>
          <input
            type={field.type}
            value={config[field.key] || ""}
            onChange={(e) => setField(field.key, e.target.value)}
            placeholder={"placeholder" in field ? (field as { placeholder: string }).placeholder : ""}
            className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 placeholder-zinc-600"
          />
        </div>
      ))}

      {typeDef.hint && (
        <p className="text-xs text-zinc-500">{typeDef.hint}</p>
      )}

      <div className="flex gap-3 pt-2">
        <button
          onClick={handleSubmit}
          disabled={saving}
          className="rounded bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-40 transition-colors"
        >
          {saving ? "Ukládám..." : isEdit ? "Uložit" : "Přidat"}
        </button>
        <button
          onClick={handleTest}
          className="rounded bg-zinc-700 px-4 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-600 transition-colors"
        >
          {testing === "loading"
            ? "Testuji..."
            : testing === true
            ? "Spojení OK"
            : testing === false
            ? "Chyba spojení"
            : "Test spojení"
          }
        </button>
        <button
          onClick={onCancel}
          className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          Zrušit
        </button>
      </div>
    </div>
  );
}
