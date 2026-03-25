"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  Source,
  Automation,
  AppSettings,
  LanguageOption,
  getSources,
  createSource,
  updateSource,
  deleteSource,
  testSource,
  testSourceConfig,
  getIntegrations,
  updateIntegration,
  getIntegrationOptions,
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
      { key: "login", label: "Login (KODI/API)", type: "text", placeholder: "g-xxxxxxxxxx-xxx" },
      { key: "password", label: "Heslo (KODI/API)", type: "password" },
    ],
    hint: "Použij přihlašovací údaje z \"Login pro PC, Android a KODI aplikace\" (ne webový login)",
  },
  {
    type: "jackett",
    label: "Jackett",
    fields: [
      { key: "url", label: "URL", type: "text", placeholder: "http://jackett:9117" },
      { key: "api_key", label: "API Key", type: "password" },
    ],
  },
];

const INTEGRATION_TYPES = [
  {
    type: "radarr",
    label: "Radarr",
    icon: "🎬",
    fields: [
      { key: "api_key", label: "Radarr API Key", type: "password", hint: "Found in Radarr -> Settings -> General" },
      { key: "url", label: "Radarr URL", type: "text", hint: "e.g. http://localhost:7878" },
      { key: "root_folder", label: "Root Folder", type: "select", option_key: "root_folders", hint: "Select target library folder in Radarr" },
      { key: "profile_id", label: "Quality Profile", type: "select", option_key: "quality_profiles", hint: "Select quality profile in Radarr" },
      { key: "blackhole_path", label: "Blackhole Path", type: "folder", hint: "Folder Radarr watches for imports" },
      { key: "auto_add", label: "Auto-add Movies", type: "checkbox", hint: "Automatically add missing movies to Radarr" },
    ],
  },
  {
    type: "renamer",
    label: "Renamer (Media Info)",
    icon: "📝",
    fields: [
      { 
        key: "format", 
        label: "Filename Format", 
        type: "text", 
        placeholder: "{title} ({year}) [{source}-{res} {codec}] [{langs}] {tmdb-{id}}",
      },
      { key: "use_mediainfo", label: "Use MediaInfo", type: "checkbox", hint: "Extract resolution and codec from file" },
    ],
  },
];

const GENERAL_SECTIONS = [
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
      { key: "plex_media_dir", label: "Složka pro filmy", type: "folder", hint: "Cílová složka pro filmy (v Docker kontejneru)" },
      { key: "tv_media_dir", label: "Složka pro seriály", type: "folder", hint: "Pokud prázdné, seriály se stahují do složky pro filmy" },
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
];

const SEARCH_SECTIONS = [
  {
    title: "Vyhledávání",
    icon: "🔍",
    fields: [
      { key: "min_relevance_score", label: "Minimální skóre relevance", type: "range", hint: "Soubory s nižším skóre se nezobrazí (0 = vše, 100 = pouze perfektní shoda)" },
    ],
  },
];

type SettingsTab = "obecne" | "vyhledavani" | "automatizace" | "zdroje";

const TABS: { key: SettingsTab; label: string }[] = [
  { key: "obecne", label: "Obecné" },
  { key: "vyhledavani", label: "Vyhledávání" },
  { key: "automatizace", label: "Automatizace" },
  { key: "zdroje", label: "Zdroje" },
];

export default function SettingsPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [integrations, setIntegrations] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editIntegrationType, setEditIntegrationType] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<number, boolean | null>>({});

  const [settings, setSettings] = useState<AppSettings>({});
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);

  const [activeTab, setActiveTab] = useState<SettingsTab>("obecne");
  const [allLanguages, setAllLanguages] = useState<LanguageOption[]>([]);
  const [browsingField, setBrowsingField] = useState<string | null>(null);

  const loadSources = useCallback(async () => {
    try { setSources(await getSources()); } finally { setLoading(false); }
  }, []);

  const loadIntegrations = useCallback(async () => {
    try { setIntegrations(await getIntegrations()); } catch (e) {}
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
    loadIntegrations();
    loadSettings();
    getLanguages().then(setAllLanguages).catch(() => {});
  }, [loadSources, loadIntegrations, loadSettings]);

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

  async function handleTest(id: number) {
    setTestResults((prev) => ({ ...prev, [id]: null }));
    try {
      const result = await testSource(id);
      setTestResults((prev) => ({ ...prev, [id]: result.ok }));
    } catch {
      setTestResults((prev) => ({ ...prev, [id]: false }));
    }
  }

  function renderSettingsSections(sections: typeof GENERAL_SECTIONS) {
    return sections.map((section) => (
      <div key={section.title} className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
        <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2"><span>{section.icon}</span>{section.title}</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {section.fields.map((field) => (
            <div key={field.key}>
              <label className="block text-xs text-zinc-500 mb-1">{field.label}</label>
              {field.type === "folder" ? (
                <div className="flex gap-2">
                  <input type="text" value={settings[field.key] || ""} onChange={(e) => handleSettingChange(field.key, e.target.value)}
                    className="flex-1 rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" />
                  <button type="button" onClick={() => setBrowsingField(field.key)} className="rounded bg-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-600 transition-colors">Procházet</button>
                </div>
              ) : field.type === "range" ? (
                <div className="flex items-center gap-3">
                  <input type="range" min="0" max="100" step="5" value={settings[field.key] || "0"}
                    onChange={(e) => handleSettingChange(field.key, e.target.value)}
                    className="flex-1 accent-violet-500" />
                  <span className="text-sm text-zinc-300 w-8 text-right">{settings[field.key] || "0"}</span>
                </div>
              ) : (
                <input type={field.type === "password" ? "password" : "text"} value={settings[field.key] || ""} onChange={(e) => handleSettingChange(field.key, e.target.value)}
                  className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" />
              )}
              {field.hint && <p className="text-[10px] text-zinc-600 mt-1">{field.hint}</p>}
            </div>
          ))}
        </div>
      </div>
    ));
  }

  function renderSaveButton() {
    return (
      <div className="flex justify-end gap-3">
        {settingsSaved && <span className="text-green-400 text-sm self-center">✓ Uloženo</span>}
        <button onClick={handleSettingsSave} disabled={settingsSaving || !settingsDirty}
          className={`rounded px-6 py-2 text-sm font-bold text-white transition-colors shadow-lg shadow-violet-900/20 ${
            settingsDirty ? "bg-violet-600 hover:bg-violet-500" : "bg-zinc-700 cursor-not-allowed"
          }`}>
          {settingsSaving ? "Ukládám..." : "Uložit vše"}
        </button>
      </div>
    );
  }

  return (
    <main className="flex flex-col items-center gap-8 px-4 py-12 max-w-4xl mx-auto">
      <div className="flex items-center gap-4 w-full">
        <Link href="/" className="text-zinc-500 hover:text-zinc-300 transition-colors text-sm">&larr; Hledat</Link>
        <h1 className="text-2xl font-bold text-zinc-100">Nastavení</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-zinc-900 rounded-lg p-1 w-full border border-zinc-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`flex-1 px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === t.key
                ? "bg-violet-600 text-white shadow"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* === Tab: Obecné === */}
      {activeTab === "obecne" && (
        <section className="w-full space-y-4">
          {settingsLoading ? <p className="text-zinc-500">Načítám...</p> : (
            <div className="space-y-4">
              {renderSettingsSections(GENERAL_SECTIONS)}
              {renderSaveButton()}
            </div>
          )}
        </section>
      )}

      {/* === Tab: Vyhledávání === */}
      {activeTab === "vyhledavani" && (
        <section className="w-full space-y-4">
          {settingsLoading ? <p className="text-zinc-500">Načítám...</p> : (
            <div className="space-y-4">
              {renderSettingsSections(SEARCH_SECTIONS)}

              {/* Languages */}
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-5 space-y-4">
                <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2"><span>🌍</span>Preferované jazyky</h3>
                <p className="text-[10px] text-zinc-600">Vyber jazyky pro detekci dabingu a TMDB metadata. Dropdown ve vyhledávání filtruje podle konkrétního jazyka.</p>
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
                            handleSettingChange("languages", next.join(","));
                          }}
                          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${
                            isSelected
                              ? "bg-violet-600/20 border-violet-500 text-violet-300"
                              : "bg-zinc-800 border-zinc-700 text-zinc-500 hover:border-zinc-600"
                          }`}
                        >
                          {FLAG[lang.code] || ""} {lang.label} <span className="opacity-60">{lang.name}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {renderSaveButton()}
            </div>
          )}
        </section>
      )}

      {/* === Tab: Automatizace === */}
      {activeTab === "automatizace" && (
        <section className="w-full space-y-4">
          {integrations.map((int) => (
            <div key={int.type} className="flex items-center gap-4 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3">
              <button onClick={async () => { await updateIntegration(int.type, { enabled: !int.enabled }); loadIntegrations(); }}
                className={`w-10 h-6 rounded-full relative transition-colors ${int.enabled ? "bg-violet-600" : "bg-zinc-700"}`}>
                <span className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${int.enabled ? "left-5" : "left-1"}`} />
              </button>
              <div className="flex-1 min-w-0">
                <span className="text-zinc-100 font-medium">{int.name}</span>
                <span className="ml-2 text-xs text-zinc-500 uppercase">{int.type}</span>
              </div>
              <button onClick={() => setEditIntegrationType(int.type)} className="text-xs text-zinc-400 hover:text-zinc-200 border border-zinc-700 px-3 py-1 rounded hover:bg-zinc-800 transition-colors">Upravit</button>
            </div>
          ))}
          {integrations.length === 0 && <p className="text-zinc-500 text-sm">Žádné integrace</p>}
        </section>
      )}

      {/* === Tab: Zdroje === */}
      {activeTab === "zdroje" && (
        <section className="w-full space-y-4">
          <div className="flex justify-end">
            <button onClick={() => setShowAdd(true)} className="rounded bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700 transition-colors">+ Přidat zdroj</button>
          </div>
          {sources.map((source) => (
            <div key={source.id} className="flex items-center gap-4 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3">
              <button onClick={async () => { await updateSource(source.id, { enabled: !source.enabled }); loadSources(); }}
                className={`w-10 h-6 rounded-full relative transition-colors ${source.enabled ? "bg-green-600" : "bg-zinc-700"}`}>
                <span className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${source.enabled ? "left-5" : "left-1"}`} />
              </button>
              <div className="flex-1 min-w-0">
                <span className="text-zinc-100 font-medium">{source.name}</span>
                <span className="ml-2 text-xs text-zinc-500 uppercase">{source.type}</span>
              </div>
              <div className="flex items-center gap-3">
                <button onClick={() => handleTest(source.id)} className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors">
                  {testResults[source.id] === null ? "..." : testResults[source.id] === true ? "OK" : testResults[source.id] === false ? "Chyba" : "Test"}
                </button>
                <button onClick={() => setEditId(source.id)} className="text-xs text-zinc-400 hover:text-zinc-200 border border-zinc-700 px-3 py-1 rounded hover:bg-zinc-800 transition-colors">Upravit</button>
                <button onClick={async () => { if (confirm("Opravdu smazat tento zdroj?")) { await deleteSource(source.id); await loadSources(); } }} className="text-xs text-red-500 hover:text-red-400 transition-colors">Smazat</button>
              </div>
            </div>
          ))}
          {sources.length === 0 && <p className="text-zinc-500 text-sm">Žádné zdroje — přidej WebShare, FastShare nebo Jackett</p>}
        </section>
      )}

      {showAdd && (
        <AddSourceModal
          onClose={() => setShowAdd(false)}
          onSave={async (data) => { await createSource(data); loadSources(); setShowAdd(false); }}
        />
      )}

      {editIntegrationType && (
        <EditIntegrationModal
          type={editIntegrationType}
          integration={integrations.find((i) => i.type === editIntegrationType)!}
          onClose={() => setEditIntegrationType(null)}
          onSave={async (config) => { await updateIntegration(editIntegrationType, { config }); await loadIntegrations(); setEditIntegrationType(null); }}
        />
      )}

      {editId !== null && (
        <EditSourceModal
          source={sources.find((s) => s.id === editId)!}
          onClose={() => setEditId(null)}
          onSave={async (data) => { await updateSource(editId, data); loadSources(); setEditId(null); }}
        />
      )}

      {browsingField && (
        <FolderBrowser initialPath={settings[browsingField] || "/"}
          onSelect={(path) => { handleSettingChange(browsingField, path); setBrowsingField(null); }}
          onCancel={() => setBrowsingField(null)} />
      )}
    </main>
  );
}

function AddSourceModal({ onClose, onSave }: { onClose: () => void, onSave: (data: any) => void }) {
  const [type, setType] = useState(SOURCE_TYPES[0].type);
  const [name, setName] = useState(SOURCE_TYPES[0].label);
  const [config, setConfig] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState<boolean | null | "loading">(null);
  const typeDef = SOURCE_TYPES.find((t) => t.type === type);

  async function handleTest() {
    setTesting("loading");
    try { const res = await testSourceConfig({ type, name, config }); setTesting(res.ok); } catch { setTesting(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 px-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
        <h2 className="text-xl font-bold text-zinc-100">Přidat zdroj</h2>
        <div className="mt-6 space-y-4">
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Type</label>
            <select value={type} onChange={(e) => { const t = e.target.value; setType(t); setName(SOURCE_TYPES.find((st) => st.type === t)?.label || ""); setConfig({}); }}
              className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none">
              {SOURCE_TYPES.map((t) => (<option key={t.type} value={t.type}>{t.label}</option>))}
            </select>
          </div>
          <div><label className="block text-xs text-zinc-500 mb-1">Name</label><input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" /></div>
          {typeDef?.fields.map((f) => (
            <div key={f.key}>
              <label className="block text-xs text-zinc-500 mb-1">{f.label}</label>
              <input type={f.type === "password" ? "password" : "text"} value={config[f.key] || ""} onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })}
                className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" />
            </div>
          ))}
        </div>
        <div className="mt-8 flex justify-end gap-3">
          <button onClick={handleTest} className="px-4 py-2 text-sm text-zinc-400 border border-zinc-800 rounded hover:bg-zinc-800 transition-colors">
            {testing === "loading" ? "Testuji..." : testing === true ? "OK" : testing === false ? "Chyba" : "Test spojení"}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">Zrušit</button>
          <button onClick={() => onSave({ type, name, config })} className="rounded bg-violet-600 px-6 py-2 text-sm font-bold text-white hover:bg-violet-500 transition-colors shadow-lg shadow-violet-900/20">Přidat</button>
        </div>
      </div>
    </div>
  );
}

function EditIntegrationModal({ type, integration, onClose, onSave }: { type: string, integration: Automation, onClose: () => void, onSave: (config: Record<string, string>) => void }) {
  const typeDef = INTEGRATION_TYPES.find((t) => t.type === type);
  const [config, setConfig] = useState(integration.config);
  const [options, setOptions] = useState<any>({});
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [browsingField, setBrowsingField] = useState<string | null>(null);

  const DEFAULT_FORMAT = "{title} ({year}) [{source}-{res} {codec}] [{langs}] {tmdb-{id}}";

  useEffect(() => {
    if (type === "radarr") {
      setLoadingOptions(true);
      getIntegrationOptions(type).then(data => { setOptions(data); setLoadingOptions(false); });
    }
    if (type === "renamer" && !config.format) { setConfig({ ...config, format: DEFAULT_FORMAT }); }
  }, [type, config.format]);

  const TAGS = [
    { tag: "{title}", desc: "Movie Title" }, { tag: "{year}", desc: "Release Year" },
    { tag: "{res}", desc: "Resolution" }, { tag: "{source}", desc: "Source (WEBDL...)" },
    { tag: "{codec}", desc: "Codec" }, { tag: "{langs}", desc: "Languages" },
    { tag: "{id}", desc: "TMDB ID" },
  ];

  const getPreview = () => {
    let p = config.format || DEFAULT_FORMAT;
    p = p.replace("{title}", "Avatar").replace("{year}", "2009").replace("{res}", "1080p").replace("{source}", "BluRay").replace("{codec}", "x264").replace("{langs}", "CS+EN").replace("{id}", "19995");
    return p + ".mkv";
  };

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 px-4 backdrop-blur-sm">
        <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
          <h2 className="text-xl font-bold text-zinc-100">{typeDef?.label} Settings</h2>
          <div className="mt-6 space-y-4">
            {typeDef?.fields.map((f: any) => (
              <div key={f.key}>
                <label className="block text-xs text-zinc-500 mb-1">{f.label}</label>
                {f.type === "select" ? (
                  <select value={config[f.key] || ""} onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })}
                    className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none">
                    <option value="">-- Vybrat --</option>
                    {f.option_key === "root_folders" && options.root_folders?.map((opt: any) => (<option key={opt.id} value={opt.path}>{opt.path}</option>))}
                    {f.option_key === "quality_profiles" && options.quality_profiles?.map((opt: any) => (<option key={opt.id} value={opt.id}>{opt.name}</option>))}
                  </select>
                ) : f.type === "checkbox" ? (
                  <div className="flex items-center gap-2 py-1"><input type="checkbox" checked={config[f.key] === "true"} onChange={(e) => setConfig({ ...config, [f.key]: e.target.checked ? "true" : "false" })} className="w-4 h-4 rounded border-zinc-700 bg-zinc-800 text-violet-600" /><span className="text-sm text-zinc-300">Zapnuto</span></div>
                ) : f.type === "folder" ? (
                  <div className="flex gap-2"><input type="text" value={config[f.key] || ""} onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })} className="flex-1 rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" /><button type="button" onClick={() => setBrowsingField(f.key)} className="rounded bg-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-600 transition-colors">Browse</button></div>
                ) : (
                  <input type={f.type === "password" ? "password" : "text"} value={config[f.key] || ""} onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })} className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" />
                )}
                {f.hint && <p className="text-[10px] text-zinc-600 mt-1">{f.hint}</p>}
              </div>
            ))}
            {type === "renamer" && (
              <div className="mt-4 space-y-4">
                <div className="rounded-lg bg-zinc-950 p-3 border border-zinc-800/50"><label className="text-[10px] uppercase font-bold text-zinc-600 block mb-2">Filename Preview</label><code className="text-xs text-violet-400 break-all">{getPreview()}</code></div>
                <div className="grid grid-cols-2 gap-2">{TAGS.map(t => (<div key={t.tag} className="flex flex-col gap-0.5"><span className="text-[10px] font-mono text-violet-300">{t.tag}</span><span className="text-[9px] text-zinc-500">{t.desc}</span></div>))}</div>
              </div>
            )}
            {loadingOptions && <p className="text-xs text-violet-400 animate-pulse">Fetching options from Radarr API...</p>}
          </div>
          <div className="mt-8 flex justify-end gap-3">
            <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">Zrušit</button>
            <button onClick={() => onSave(config)} className="rounded bg-violet-600 px-6 py-2 text-sm font-bold text-white hover:bg-violet-500 transition-colors shadow-lg shadow-violet-900/20">Uložit</button>
          </div>
        </div>
      </div>
      {browsingField && (
        <FolderBrowser initialPath={config[browsingField] || "/"}
          onSelect={(path) => { setConfig({ ...config, [browsingField]: path }); setBrowsingField(null); }}
          onCancel={() => setBrowsingField(null)} />
      )}
    </>
  );
}

function EditSourceModal({ source, onClose, onSave }: { source: Source, onClose: () => void, onSave: (data: any) => void }) {
  const typeDef = SOURCE_TYPES.find((t) => t.type === source.type);
  const [name, setName] = useState(source.name);
  const [config, setConfig] = useState(source.config);
  const [testing, setTesting] = useState<boolean | null | "loading">(null);

  async function handleTest() {
    setTesting("loading");
    try { const res = await testSourceConfig({ type: source.type, name, config }); setTesting(res.ok); } catch { setTesting(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 px-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
        <h2 className="text-xl font-bold text-zinc-100">Upravit {source.name}</h2>
        <div className="mt-6 space-y-4">
          <div><label className="block text-xs text-zinc-500 mb-1">Name</label><input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" /></div>
          {typeDef?.fields.map((f) => (
            <div key={f.key}>
              <label className="block text-xs text-zinc-500 mb-1">{f.label}</label>
              <input type={f.type === "password" ? "password" : "text"} value={config[f.key] || ""} onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })}
                className="w-full rounded bg-zinc-800 border border-zinc-700 px-3 py-2 text-zinc-100 text-sm focus:border-violet-500 outline-none" />
            </div>
          ))}
        </div>
        <div className="mt-8 flex justify-end gap-3">
          <button onClick={handleTest} className="px-4 py-2 text-sm text-zinc-400 border border-zinc-800 rounded hover:bg-zinc-800 transition-colors">
            {testing === "loading" ? "Testuji..." : testing === true ? "OK" : testing === false ? "Chyba" : "Test spojení"}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">Zrušit</button>
          <button onClick={() => onSave({ name, config })} className="rounded bg-violet-600 px-6 py-2 text-sm font-bold text-white shadow-lg shadow-violet-900/20">Uložit</button>
        </div>
      </div>
    </div>
  );
}
