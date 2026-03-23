const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface TMDBMovie {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: string;
  overview: string;
  poster_url: string | null;
}

export interface ScoredFile {
  ident: string;
  name: string;
  size: number;
  quality: string;
  is_dubbed: boolean;
  relevance_score: number;
  source: string;
  source_id: number;
  magnet_url: string | null;
  seeders: number | null;
}

export interface Source {
  id: number;
  type: string;
  name: string;
  enabled: boolean;
  config: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface SourceCreate {
  type: string;
  name: string;
  enabled?: boolean;
  config: Record<string, string>;
}

// --- Discover ---

export async function getTrending(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await fetch(`${API_BASE}/api/discover/trending${qs}`);
  if (!res.ok) throw new Error(`Trending failed: ${res.status}`);
  return res.json();
}

export async function getNowPlaying(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await fetch(`${API_BASE}/api/discover/now-playing${qs}`);
  if (!res.ok) throw new Error(`Now playing failed: ${res.status}`);
  return res.json();
}

export async function getRecentlyDigital(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await fetch(`${API_BASE}/api/discover/recently-digital${qs}`);
  if (!res.ok) throw new Error(`Recently digital failed: ${res.status}`);
  return res.json();
}

export async function getRecentlyDigitalTV(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await fetch(`${API_BASE}/api/discover/recently-digital-tv${qs}`);
  if (!res.ok) throw new Error(`Recently digital TV failed: ${res.status}`);
  return res.json();
}

export async function getPopular(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await fetch(`${API_BASE}/api/discover/popular${qs}`);
  if (!res.ok) throw new Error(`Popular failed: ${res.status}`);
  return res.json();
}

// --- Search & Download ---

export async function searchMovies(query: string, language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams({ query });
  if (language) params.set("language", language);
  const res = await fetch(`${API_BASE}/api/search/movies?${params}`);
  if (!res.ok) throw new Error(`Search failed: ${res.status}`);
  return res.json();
}

export async function searchFiles(query: string, language?: string): Promise<ScoredFile[]> {
  const params = new URLSearchParams({ query });
  if (language) params.set("language", language);
  const res = await fetch(`${API_BASE}/api/search/files?${params}`);
  if (!res.ok) throw new Error(`File search failed: ${res.status}`);
  return res.json();
}

export async function startDownload(
  file: ScoredFile,
  targetFolder?: string,
  tmdb_id?: number,
  title?: string,
  year?: number,
  contentType: "movie" | "tv" = "movie",
): Promise<{
  gid?: string;
  hash?: string;
  status: string;
  target_dir: string;
  source: string;
}> {
  const res = await fetch(`${API_BASE}/api/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      file_ident: file.ident,
      source: file.source,
      source_id: file.source_id,
      target_folder: targetFolder,
      tmdb_id: tmdb_id,
      title: title,
      year: year,
      magnet_url: file.magnet_url,
      content_type: contentType,
    }),
  });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  return res.json();
}

export interface DownloadItem {
  gid?: string;
  hash?: string;
  status: string;
  total_length: number;
  completed_length: number;
  download_speed: number;
  filename: string;
  backend: "aria2" | "qbittorrent";
  progress?: number;
  source_label?: string;
}

export async function getDownloads(): Promise<DownloadItem[]> {
  const res = await fetch(`${API_BASE}/api/downloads`);
  if (!res.ok) throw new Error(`Failed to load downloads: ${res.status}`);
  const data = await res.json();
  return data.downloads;
}

export async function removeDownload(
  identifier: string,
  backend: "aria2" | "qbittorrent" = "aria2",
  active: boolean = false,
): Promise<void> {
  const params = new URLSearchParams();
  if (backend === "qbittorrent") params.set("backend", "qbittorrent");
  if (active) params.set("active", "true");
  const qs = params.toString() ? `?${params}` : "";
  await fetch(`${API_BASE}/api/download/${identifier}${qs}`, { method: "DELETE" });
}

// --- Source Management ---

export async function getSources(): Promise<Source[]> {
  const res = await fetch(`${API_BASE}/api/sources`);
  if (!res.ok) throw new Error(`Failed to load sources: ${res.status}`);
  return res.json();
}

export async function createSource(data: SourceCreate): Promise<Source> {
  const res = await fetch(`${API_BASE}/api/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to create source: ${res.status}`);
  return res.json();
}

export async function updateSource(
  id: number,
  data: { name?: string; enabled?: boolean; config?: Record<string, string> }
): Promise<Source> {
  const res = await fetch(`${API_BASE}/api/sources/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update source: ${res.status}`);
  return res.json();
}

export async function deleteSource(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sources/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete source: ${res.status}`);
}

export async function testSource(id: number): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${API_BASE}/api/sources/${id}/test`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Test failed: ${res.status}`);
  return res.json();
}

export async function testSourceConfig(
  data: SourceCreate
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${API_BASE}/api/sources/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Test failed: ${res.status}`);
  return res.json();
}

// --- App Settings ---

export interface SetupStatus {
  complete: boolean;
  missing: string[];
}

export async function getSetupStatus(): Promise<SetupStatus> {
  const res = await fetch(`${API_BASE}/api/settings/setup-status`);
  if (!res.ok) throw new Error(`Failed to check setup: ${res.status}`);
  return res.json();
}

export type AppSettings = Record<string, string>;

export async function getAppSettings(): Promise<AppSettings> {
  const res = await fetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw new Error(`Failed to load settings: ${res.status}`);
  return res.json();
}

export async function updateAppSettings(data: AppSettings): Promise<AppSettings> {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update settings: ${res.status}`);
  return res.json();
}

// --- Languages ---

export interface LanguageOption {
  code: string;
  name: string;
  label: string;
  enabled: boolean;
}

export async function getLanguages(): Promise<LanguageOption[]> {
  const res = await fetch(`${API_BASE}/api/settings/languages`);
  if (!res.ok) throw new Error(`Failed to load languages: ${res.status}`);
  return res.json();
}

// --- Integrations ---

export interface Automation {
  id: number;
  type: "radarr" | "renamer";
  name: string;
  enabled: boolean;
  config: Record<string, string>;
}

export async function getIntegrations(): Promise<Automation[]> {
  const res = await fetch(`${API_BASE}/api/integrations`);
  if (!res.ok) throw new Error(`Failed to load integrations: ${res.status}`);
  return res.json();
}

export async function getIntegrationOptions(type: string): Promise<any> {
  const res = await fetch(`${API_BASE}/api/integrations/${type}/options`);
  return res.json();
}

export async function updateIntegration(type: string, data: { enabled?: boolean; config?: Record<string, string> }): Promise<void> {
  const res = await fetch(`${API_BASE}/api/integrations/${type}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update integration: ${res.status}`);
}

// --- Duplicates ---

export interface DuplicateFile {
  id: number;
  path: string;
  filename: string;
  size: number;
  quality: string;
  language: string;
  modified_at: string;
}

export interface DuplicateGroup {
  title: string;
  normalized_title: string;
  year: string;
  count: number;
  files: DuplicateFile[];
}

export async function scanDuplicates(): Promise<{ scanned: number; media_dir: string }> {
  const res = await fetch(`${API_BASE}/api/duplicates/scan`, { method: "POST" });
  if (!res.ok) throw new Error(`Scan failed: ${res.status}`);
  return res.json();
}

export async function aiScanDuplicates(): Promise<{ scanned: number; ai_groups: number; media_dir: string }> {
  const res = await fetch(`${API_BASE}/api/duplicates/ai-scan`, { method: "POST" });
  if (!res.ok) throw new Error(`AI scan failed: ${res.status}`);
  return res.json();
}

export async function getDuplicates(mode: "simple" | "ai" = "simple"): Promise<{ groups: DuplicateGroup[]; total_groups: number; mode: string }> {
  const res = await fetch(`${API_BASE}/api/duplicates?mode=${mode}`);
  if (!res.ok) throw new Error(`Failed to load duplicates: ${res.status}`);
  return res.json();
}

export async function deleteDuplicateFile(fileId: number): Promise<{ ok: boolean; deleted: string }> {
  const res = await fetch(`${API_BASE}/api/duplicates/file/${fileId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
  return res.json();
}

// --- Utilities ---

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
