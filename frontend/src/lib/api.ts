const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Event fired when the API says the session is gone — AuthGate shows the sign-in form. */
export const UNAUTHORIZED_EVENT = "lumina:unauthorized";

/** fetch for the Lumina API: sends the session cookie, reports a lost session, and turns
 *  "not allowed" into an error with the backend's explanation. */
export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  // the session cookie goes with same-origin requests (default); the UI calls its own /api
  const res = await fetch(input, init);
  if (res.status === 401 && typeof window !== "undefined" && !input.includes("/api/auth/")) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (res.status === 403) {
    let detail = "Na tohle nemáš oprávnění";
    try {
      detail = (await res.clone().json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res;
}

// ── sign-in and users ──

export interface AuthUser {
  id: number;
  username: string;
  role: "admin" | "user";
  is_admin: boolean;
  permissions: string[];
}

export interface ManagedUser extends AuthUser {
  disabled: boolean;
  created_at: string;
  last_login: string | null;
}

export interface PermissionInfo {
  name: string;
  title: string;
  default: boolean;
}

export interface SessionInfo {
  id: string;
  current: boolean;
  remember: boolean;
  created_at: string;
  last_seen: string;
  expires_at: string;
  ip: string;
  user_agent: string;
}

async function authCall<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await apiFetch(`${API_BASE}/api/auth${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `Chyba ${res.status}`;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const getAuthStatus = () => authCall<{ setup_required: boolean; user: AuthUser | null }>("/status");
export const setupAdmin = (code: string, username: string, password: string, remember: boolean) =>
  authCall<{ user: AuthUser }>("/setup", "POST", { code, username, password, remember });
export const login = (username: string, password: string, remember: boolean) =>
  authCall<{ user: AuthUser }>("/login", "POST", { username, password, remember });
export const logout = () => authCall<{ ok: boolean }>("/logout", "POST");
export const changePassword = (current: string, next: string) =>
  authCall<{ ok: boolean }>("/password", "POST", { current, new: next });
export const getSessions = () => authCall<SessionInfo[]>("/sessions");
export const endSession = (id: string) => authCall<{ ok: boolean }>(`/sessions/${id}`, "DELETE");
export const endOtherSessions = () => authCall<{ ended: number }>("/sessions/end-others", "POST");
export const getPermissions = () => authCall<PermissionInfo[]>("/permissions");
export const getUsers = () => authCall<ManagedUser[]>("/users");
export const createUser = (u: { username: string; password: string; role: string; permissions?: string[] }) =>
  authCall<ManagedUser>("/users", "POST", u);
export const updateUser = (id: number, change: { role?: string; permissions?: string[]; disabled?: boolean; password?: string }) =>
  authCall<ManagedUser>(`/users/${id}`, "PATCH", change);
export const deleteUser = (id: number) => authCall<{ ok: boolean }>(`/users/${id}`, "DELETE");

export interface TMDBMovie {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: string;
  overview: string;
  poster_url: string | null;
  media_type?: "movie" | "tv";
  wikidata_id?: string | null;   // a film TMDB does not know (tmdb_id = 0)
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
  audio_langs: string[];
  subtitle_langs: string[];
  // evaluation (backend app/modules/search/evaluate.py)
  film: "yes" | "unsure" | "length" | "no";
  film_reasons: string[];
  quality_score: number;
  quality_summary: string;
  quality_parts: [string, number][];
  resolution: string;
  codec: string;
  bitrate: number;
  hdr: string;
  duration_s: number;
  audio: { lang: string; codec: string; channels: number }[];
  verified: boolean;
  lang_tier: number;
}

/** The film a file search was for — sent back with detail requests so files are re-evaluated with it. */
export interface MovieContext {
  titles: string[];
  year: number | null;
  runtime: number;
}

export interface SearchFilesResult {
  movie: MovieContext;
  prefer_local_audio: boolean;
  files: ScoredFile[];
}

/** Technical info a source knows about a file (WebShare file_info / FastShare file page). */
export interface FileDetails {
  duration_s: number;
  width: number;
  height: number;
  video_codec: string;
  bitrate: number;
  audio: { lang: string; codec: string; channels: number }[];
  subtitles: string[];
}

export async function getFileDetails(
  files: { source_id: number; ident: string; name: string; size: number }[],
  movie: MovieContext | null,
): Promise<Record<string, Partial<ScoredFile> | null>> {
  if (!files.length) return {};
  const res = await apiFetch(`${API_BASE}/api/search/details`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files, movie }),
  });
  if (!res.ok) return {};
  return res.json();
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
  const res = await apiFetch(`${API_BASE}/api/discover/trending${qs}`);
  if (!res.ok) throw new Error(`Trending failed: ${res.status}`);
  return res.json();
}

export async function getNowPlaying(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await apiFetch(`${API_BASE}/api/discover/now-playing${qs}`);
  if (!res.ok) throw new Error(`Now playing failed: ${res.status}`);
  return res.json();
}

export async function getRecentlyDigital(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await apiFetch(`${API_BASE}/api/discover/recently-digital${qs}`);
  if (!res.ok) throw new Error(`Recently digital failed: ${res.status}`);
  return res.json();
}

export async function getRecentlyDigitalTV(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await apiFetch(`${API_BASE}/api/discover/recently-digital-tv${qs}`);
  if (!res.ok) throw new Error(`Recently digital TV failed: ${res.status}`);
  return res.json();
}

export async function getPopular(language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams();
  if (language) params.set("language", language);
  const qs = params.toString() ? `?${params}` : "";
  const res = await apiFetch(`${API_BASE}/api/discover/popular${qs}`);
  if (!res.ok) throw new Error(`Popular failed: ${res.status}`);
  return res.json();
}

// --- Search & Download ---

export async function searchMovies(query: string, language?: string): Promise<TMDBMovie[]> {
  const params = new URLSearchParams({ query });
  if (language) params.set("language", language);
  const res = await apiFetch(`${API_BASE}/api/search/movies?${params}`);
  if (!res.ok) throw new Error(`Search failed: ${res.status}`);
  return res.json();
}

export async function searchFiles(
  query: string,
  language?: string,
  originalTitle?: string,
  tmdbId?: number,
  mediaType?: string,
  wikidataId?: string | null,
): Promise<SearchFilesResult> {
  const params = new URLSearchParams({ query });
  if (wikidataId) params.set("wikidata_id", wikidataId);
  if (language) params.set("language", language);
  if (originalTitle && originalTitle !== query) params.set("original_title", originalTitle);
  if (tmdbId) params.set("tmdb_id", String(tmdbId));
  if (mediaType) params.set("media_type", mediaType);
  const res = await apiFetch(`${API_BASE}/api/search/files?${params}`);
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
  libraryAction?: LibraryAction,
): Promise<{
  gid?: string;
  hash?: string;
  status: string;
  target_dir: string;
  source: string;
}> {
  const res = await apiFetch(`${API_BASE}/api/download`, {
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
      library_action: libraryAction,
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
  const res = await apiFetch(`${API_BASE}/api/downloads`);
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
  await apiFetch(`${API_BASE}/api/download/${identifier}${qs}`, { method: "DELETE" });
}

// --- Source Management ---

export async function getSources(): Promise<Source[]> {
  const res = await apiFetch(`${API_BASE}/api/sources`);
  if (!res.ok) throw new Error(`Failed to load sources: ${res.status}`);
  return res.json();
}

export async function createSource(data: SourceCreate): Promise<Source> {
  const res = await apiFetch(`${API_BASE}/api/sources`, {
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
  const res = await apiFetch(`${API_BASE}/api/sources/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update source: ${res.status}`);
  return res.json();
}

export async function deleteSource(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/sources/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete source: ${res.status}`);
}

export async function testSource(id: number): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`${API_BASE}/api/sources/${id}/test`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Test failed: ${res.status}`);
  return res.json();
}

export async function testSourceConfig(
  data: SourceCreate
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`${API_BASE}/api/sources/test`, {
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
  const res = await apiFetch(`${API_BASE}/api/settings/setup-status`);
  if (!res.ok) throw new Error(`Failed to check setup: ${res.status}`);
  return res.json();
}

export type AppSettings = Record<string, string>;

export async function getAppSettings(): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw new Error(`Failed to load settings: ${res.status}`);
  return res.json();
}

/** Score numbers (backend core/quality.py DEFAULT_WEIGHTS). */
export interface QualityWeights {
  res_base: Record<string, number>;
  good_mbps: Record<string, number>;
  excellent_mbps: Record<string, number>;
  efficiency: Record<string, number>;
  points: Record<string, number>;
}

export interface QualitySample {
  label: string;
  score: number;
  parts: [string, number][];
}

export async function getQualityWeights(): Promise<{ defaults: QualityWeights; current: QualityWeights }> {
  const res = await apiFetch(`${API_BASE}/api/settings/quality-weights`);
  if (!res.ok) throw new Error(`Failed to load quality weights: ${res.status}`);
  return res.json();
}

export async function previewQuality(weights: QualityWeights): Promise<QualitySample[]> {
  const res = await apiFetch(`${API_BASE}/api/settings/quality-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ weights }),
  });
  if (!res.ok) throw new Error(`Quality preview failed: ${res.status}`);
  return res.json();
}

export interface ModuleInfo {
  name: string;
  title: string;
  required: boolean;
  active: boolean;   // running now
  enabled: boolean;  // by the setting — differs from active until the backend restarts
}

export async function getModules(): Promise<ModuleInfo[]> {
  const res = await apiFetch(`${API_BASE}/api/modules`);
  if (!res.ok) throw new Error(`Failed to load modules: ${res.status}`);
  return res.json();
}

// --- Wanted films (module wanted) ---

export interface WantedBest {
  name?: string; source?: string; source_id?: number; ident?: string; size?: number; magnet_url?: string | null;
  quality_score?: number; quality_summary?: string; resolution?: string; codec?: string; hdr?: string;
  lang_tier?: number; audio_langs?: string[]; verified?: boolean; video_bitrate?: number;
}

export interface WantedItem {
  id: number;
  tmdb_id: number | null;
  wikidata_id: string;
  title: string;
  original_title: string;
  year: string;
  poster_url: string | null;
  profile_id: number | null;
  status: "wanted" | "found" | "downloading" | "done";
  matches: number;
  best: WantedBest;
  note: string;
  added_at: string;
  checked_at: string | null;
  done_at: string | null;
}

export interface SchedulerStatus {
  enabled: boolean;
  config: Record<string, string>;
  last_run: string;
  next_run: string | null;
  server_time: string;
}

export async function getScheduler(): Promise<SchedulerStatus> {
  const res = await apiFetch(`${API_BASE}/api/scheduler`);
  if (!res.ok) throw new Error(`Scheduler status failed: ${res.status}`);
  return res.json();
}

export async function runSchedulerNow(): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/scheduler/run`, { method: "POST" });
  if (!res.ok) throw new Error(`Spuštění selhalo: ${res.status}`);
}

export interface WantedJob { running: boolean; total: number; done: number; current: string; found: number; queued: number }

export async function getWanted(): Promise<WantedItem[]> {
  const res = await apiFetch(`${API_BASE}/api/wanted`);
  if (!res.ok) throw new Error(`Failed to load wanted: ${res.status}`);
  return res.json();
}

export async function addWanted(item: {
  tmdb_id: number | null; wikidata_id: string | null; title: string; original_title: string; year: string;
  poster_url: string | null; profile_id: number | null;
}): Promise<WantedItem> {
  const res = await apiFetch(`${API_BASE}/api/wanted`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Přidání selhalo: ${res.status}`);
  }
  return res.json();
}

export async function updateWanted(id: number, data: { profile_id?: number | null; note?: string; status?: string }): Promise<WantedItem> {
  const res = await apiFetch(`${API_BASE}/api/wanted/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
  return res.json();
}

export async function removeWanted(id: number): Promise<void> {
  await apiFetch(`${API_BASE}/api/wanted/${id}`, { method: "DELETE" });
}

export async function checkWanted(ids: number[] = []): Promise<WantedJob> {
  const res = await apiFetch(`${API_BASE}/api/wanted/check`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }),
  });
  if (!res.ok) throw new Error(`Kontrola selhala: ${res.status}`);
  return res.json();
}

export async function getWantedJob(): Promise<WantedJob> {
  const res = await apiFetch(`${API_BASE}/api/wanted/check/status`);
  if (!res.ok) throw new Error(`Status failed: ${res.status}`);
  return res.json();
}

/** Quality profile (backend core/profiles.py). */
export interface QualityProfile {
  id: number;
  name: string;
  is_default: boolean;
  min_resolution: string;
  max_resolution: string;
  audio_langs: string[];          // wanted audio languages ("cs", "sk", "en" …); empty = any
  audio_mode: "any" | "all";      // one of them is enough | every one of them
  codecs: string[];
  hdr: "any" | "require" | "forbid";
  max_size_gb: number;
  min_video_mbps: number;
  max_video_mbps: number;
  min_score: number;
  cutoff: number;
}

export async function getProfiles(): Promise<QualityProfile[]> {
  const res = await apiFetch(`${API_BASE}/api/settings/profiles`);
  if (!res.ok) throw new Error(`Failed to load profiles: ${res.status}`);
  return res.json();
}

export async function saveProfile(p: QualityProfile): Promise<QualityProfile> {
  const { id, name, is_default, ...config } = p;
  const res = await apiFetch(`${API_BASE}/api/settings/profiles${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, is_default, config }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Uložení selhalo: ${res.status}`);
  }
  return res.json();
}

export async function deleteProfile(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/settings/profiles/${id}`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Smazání selhalo: ${res.status}`);
  }
}

export async function updateAppSettings(data: AppSettings): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/api/settings`, {
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

export interface GroqModels {
  models: string[];
  default: string;
  error: string | null;
}

export async function getGroqModels(): Promise<GroqModels> {
  const res = await apiFetch(`${API_BASE}/api/settings/groq-models`);
  if (!res.ok) return { models: [], default: "", error: `HTTP ${res.status}` };
  return res.json();
}

export async function getLanguages(): Promise<LanguageOption[]> {
  const res = await apiFetch(`${API_BASE}/api/settings/languages`);
  if (!res.ok) throw new Error(`Failed to load languages: ${res.status}`);
  return res.json();
}

// --- Integrations ---

export interface Automation {
  id: number;
  type: string;
  name: string;
  enabled: boolean;
  config: Record<string, string>;
}

export async function getIntegrations(): Promise<Automation[]> {
  const res = await apiFetch(`${API_BASE}/api/integrations`);
  if (!res.ok) throw new Error(`Failed to load integrations: ${res.status}`);
  return res.json();
}

export interface PlexTestResult {
  ok: boolean;
  error?: string;
  sections: { title: string; type: string; locations: string[] }[];
  library_root?: string;
  plex_path?: string | null;
  section?: string | null;
}

export async function testPlex(url: string, token: string, path_map: string): Promise<PlexTestResult> {
  try {
    const res = await apiFetch(`${API_BASE}/api/plex/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, token, path_map }),
    });
    return await res.json();
  } catch (e) {
    return { ok: false, error: String(e), sections: [] };
  }
}

export async function updateIntegration(type: string, data: { enabled?: boolean; config?: Record<string, string> }): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/integrations/${type}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update integration: ${res.status}`);
}

// --- Library ---

export interface LibraryMovie {
  id: number;
  tmdb_id: number;
  title: string;
  original_title: string;
  year: string;
  poster_url: string | null;
  filename: string;
  file_size: number;
  quality: string;
  language: string;
  added_at: string;
  matched_by?: "nfo" | "filename" | "manual" | "auto";
  status: LibraryStatus;
  confidence: number;
  candidates: MatchCandidate[];
  media: MediaInfo;
  duration_s: number;
  file_path: string;
  quality_score: number;
  quality_summary: string;
  quality_parts: [string, number][];
  note: string;          // the user's words about this version
  preferred: boolean;    // the version to prefer (one per movie)
}

export type LibraryStatus = "matched" | "review" | "unmatched" | "manual";

export interface MatchCandidate {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: number | null;
  runtime: number;
  poster_url: string | null;
  score: number;
  reasons: string[];
}

export interface MediaInfo {
  duration_s?: number;
  width?: number;
  height?: number;
  video_codec?: string;
  hdr?: string;
  bitrate?: number;
  audio?: { lang: string; codec: string; channels: number }[];
  subtitles?: string[];
}

export interface ScanStatus {
  running: boolean;
  phase?: "movies" | "tv" | "done";
  total?: number;
  done?: number;
  current?: string;
  error?: string | null;
  stats?: { movies_found: number; matched: number; review: number; unmatched: number; skipped: number; removed: number; shows_found: number; episodes_matched: number };
}

export interface TMDBSearchResult {
  tmdb_id: number;
  title: string;
  year: string;
  poster_url: string | null;
}

export interface LibraryShow {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: string;
  poster_url: string | null;
  total_seasons: number;
  total_episodes: number;
  owned_episodes: number;
}

export interface LibraryEpisode {
  episode: number;
  title: string;
  air_date: string;
  has_file: boolean;
  filename: string;
  file_size: number;
  quality: string;
  language: string;
}

export interface LibrarySeason {
  season_number: number;
  episodes: LibraryEpisode[];
}

export interface LibraryShowDetail {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: string;
  poster_url: string | null;
  overview: string;
  total_seasons: number;
  total_episodes: number;
  seasons: LibrarySeason[];
}

export async function scanLibrary(force = false): Promise<ScanStatus & { started: boolean }> {
  const res = await apiFetch(`${API_BASE}/api/library/scan?force=${force}`, { method: "POST" });
  if (!res.ok) throw new Error(`Scan failed: ${res.status}`);
  return res.json();
}

export async function getScanStatus(): Promise<ScanStatus> {
  const res = await apiFetch(`${API_BASE}/api/library/scan/status`);
  if (!res.ok) throw new Error(`Scan status failed: ${res.status}`);
  return res.json();
}

export async function getLibrarySummary(): Promise<Partial<Record<LibraryStatus, number>>> {
  const res = await apiFetch(`${API_BASE}/api/library/movies/summary`);
  if (!res.ok) return {};
  return res.json();
}

export async function getLibraryMovies(): Promise<LibraryMovie[]> {
  const res = await apiFetch(`${API_BASE}/api/library/movies`);
  if (!res.ok) throw new Error(`Failed to load library movies: ${res.status}`);
  return res.json();
}

export async function getLibraryShows(): Promise<LibraryShow[]> {
  const res = await apiFetch(`${API_BASE}/api/library/shows`);
  if (!res.ok) throw new Error(`Failed to load library shows: ${res.status}`);
  return res.json();
}

export async function getShowDetail(tmdbId: number): Promise<LibraryShowDetail> {
  const res = await apiFetch(`${API_BASE}/api/library/shows/${tmdbId}`);
  if (!res.ok) throw new Error(`Failed to load show detail: ${res.status}`);
  return res.json();
}

export async function deleteLibraryMovie(id: number): Promise<void> {
  await apiFetch(`${API_BASE}/api/library/movies/${id}`, { method: "DELETE" });
}

export async function deleteLibraryShow(tmdbId: number): Promise<void> {
  await apiFetch(`${API_BASE}/api/library/shows/${tmdbId}`, { method: "DELETE" });
}

export async function searchTMDBForFix(movieId: number, query: string): Promise<TMDBSearchResult[]> {
  const res = await apiFetch(`${API_BASE}/api/library/movies/${movieId}/search-tmdb?query=${encodeURIComponent(query)}`);
  if (!res.ok) return [];
  return res.json();
}

// --- Library: better versions (background check) ---

export interface UpgradeCheck {
  owned_id: number | null;
  owned_score: number;
  status: "better" | "none" | "error" | "done";   // done = the film's profile cutoff is reached
  upgrades: number;
  best: { name?: string; source?: string; quality_score?: number; quality_summary?: string; verified?: boolean; size?: number };
  error: string;
  checked_at: string;
  note?: string;
}

/** Film-level settings in the library: quality profile and "watch for a better version". */
export interface FilmSettings { profile_id: number | null; watch_upgrades: boolean }

export async function getFilmSettings(): Promise<Record<string, FilmSettings>> {
  const res = await apiFetch(`${API_BASE}/api/library/films`);
  if (!res.ok) return {};
  return res.json();
}

export async function setFilmSettings(tmdbId: number, s: FilmSettings): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/library/films/${tmdbId}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
}

export interface UpgradeJob {
  running: boolean;
  total: number;
  done: number;
  current: string;
  found: number;
  queued: number;
}

export async function checkUpgrades(tmdbIds: number[]): Promise<UpgradeJob> {
  const res = await apiFetch(`${API_BASE}/api/library/upgrades/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tmdb_ids: tmdbIds }),
  });
  if (!res.ok) throw new Error(`Upgrade check failed: ${res.status}`);
  return res.json();
}

export async function getUpgradeJob(): Promise<UpgradeJob> {
  const res = await apiFetch(`${API_BASE}/api/library/upgrades/status`);
  if (!res.ok) throw new Error(`Upgrade status failed: ${res.status}`);
  return res.json();
}

export async function getUpgrades(): Promise<Record<string, UpgradeCheck>> {
  const res = await apiFetch(`${API_BASE}/api/library/upgrades`);
  if (!res.ok) return {};
  return res.json();
}

export async function updateVersion(movieId: number, data: { note?: string; preferred?: boolean }): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/library/movies/${movieId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Update failed: ${res.status}`);
}

/** Delete this version from DISK and the library — definitive. */
export async function deleteVersionFile(movieId: number): Promise<{ deleted: string[] }> {
  const res = await apiFetch(`${API_BASE}/api/library/movies/${movieId}/file`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Smazání selhalo: ${res.status}`);
  }
  return res.json();
}

export async function fixMovieMatch(movieId: number, tmdbId: number): Promise<void> {
  await apiFetch(`${API_BASE}/api/library/movies/${movieId}/fix`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tmdb_id: tmdbId }),
  });
}

// --- Library: owned versions ---

export interface OwnedVersion {
  id: number;
  filename: string;
  file_size: number;
  quality: string;
  language: string;
  duration_s: number;
  hdr: string;
  codec: string;
  quality_score?: number;
  quality_summary?: string;
  note?: string;
  preferred?: boolean;
}

/** What to do with an owned movie once a download finishes. */
export type LibraryAction = { mode: "version" } | { mode: "replace"; file_id: number };

export async function getOwned(tmdbIds: number[]): Promise<Record<string, OwnedVersion[]>> {
  const ids = tmdbIds.filter(Boolean);
  if (!ids.length) return {};
  const res = await apiFetch(`${API_BASE}/api/library/owned?tmdb_ids=${ids.join(",")}`);
  if (!res.ok) return {};
  return res.json();
}

export function versionLabel(v: OwnedVersion): string {
  const label = [v.quality !== "unknown" ? v.quality : "", v.codec, v.hdr, v.language.replaceAll(",", "+")].filter(Boolean).join(" ");
  return `${v.preferred ? "★ " : ""}${label}${v.note ? ` „${v.note}“` : ""}`;
}

// --- Library: fix names on disk ---

export interface OrganizeOp {
  kind: "video" | "sidecar" | "other";
  src: string;
  dst: string;
}

export interface OrganizePlan {
  movie_ids: number[];
  tmdb_id: number;
  title: string;
  year: number | null;
  folder: string;
  target_folder: string;
  ops: OrganizeOp[];
  conflicts: string[];
  remove_folder: boolean;
}

export interface OrganizeResult {
  batch_id: string | null;
  done: { title: string; ops: number }[];
  failed: { movie_id: number; error: string }[];
}

export async function getOrganizePlan(movieId: number): Promise<OrganizePlan> {
  const res = await apiFetch(`${API_BASE}/api/library/movies/${movieId}/organize`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function getOrganizePlanAll(): Promise<OrganizePlan[]> {
  const res = await apiFetch(`${API_BASE}/api/library/organize`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function applyOrganize(movieIds: number[]): Promise<OrganizeResult> {
  const res = await apiFetch(`${API_BASE}/api/library/organize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ movie_ids: movieIds }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function undoOrganize(batchId: string): Promise<{ undone: number }> {
  const res = await apiFetch(`${API_BASE}/api/library/operations/${batchId}/undo`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

// ── background work of all modules (the task button in the navigation) ──

export interface BackgroundTask {
  id: string;
  module: string;
  title: string;
  detail?: string;
  done?: number | null;
  total?: number | null;
  unit?: "bytes";
  running: boolean;
  error?: string | null;
  finished_at?: number;
  link?: string | null;
}

export async function getTasks(): Promise<BackgroundTask[]> {
  const res = await apiFetch(`${API_BASE}/api/tasks`);
  if (!res.ok) throw new Error(`Chyba ${res.status}`);
  return res.json();
}

// ── audio editor: the reference track, every track measured against it, edits ──

export interface AudioTrackInfo {
  index: number;
  language: string;
  title: string;
  codec: string;
  channels: number;
  bitrate?: number;
  profile?: string;
}

export type AudioVerdict = "constant" | "speed" | "cuts" | "no_match";

export interface AudioSyncJob {
  running: boolean;
  kind?: "map" | "apply";
  map_id?: number;
  title?: string;
  finished_at?: number;
  imported?: boolean | null;
  path?: string;
  phase?: string;
  done?: number;
  total?: number;
  current?: string;
  error?: string | null;
  report?: { added?: string[]; skipped?: { track: number; reason: string }[] };
}

async function audioSyncCall<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(`${API_BASE}/api/audiosync${path}`, init);
  if (!res.ok) {
    let detail = `Chyba ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const getAudioTracks = (movieId: number) =>
  audioSyncCall<{ id: number; filename: string; duration: number; audio: AudioTrackInfo[] }>(`/tracks/${movieId}`);
export const getAudioSyncJob = () => audioSyncCall<AudioSyncJob>("/job");

export interface AudioPreview { name: string; at: number; adjust_ms: number; saved_ms: number }
/** A track of another version on the reference picture, placed by measurement ``resultId`` (+ a trial shift). */
export const makeAudioPreview = (resultId: number, at: number, adjustMs: number, otherTrack?: number) =>
  audioSyncCall<AudioPreview>("/preview", jsonBody("POST", { result_id: resultId, at, adjust_ms: adjustMs, other_track: otherTrack ?? null }));
/** A track of the file as it is, on the file's own picture. */
export const makeTrackPreview = (movieId: number, track: number, at: number) =>
  audioSyncCall<AudioPreview>("/preview-track", jsonBody("POST", { movie_id: movieId, track, at }));
export const audioPreviewUrl = (name: string) => `${API_BASE}/api/audiosync/preview/${name}`;
export const setAudioAdjust = (resultId: number, adjustMs: number) =>
  audioSyncCall<{ id: number; adjust_ms: number }>(`/results/${resultId}`, jsonBody("PATCH", { adjust_ms: adjustMs }));

export interface AudioReference {
  original_language: string;
  chosen: { movie_id: number; track: number; verified: boolean; verified_by: string | null; updated_at: string } | null;
  defaults: Record<string, number>;          // version id → default reference track
}
export const getAudioReference = (tmdbId: number) => audioSyncCall<AudioReference>(`/reference/${tmdbId}`);
export const setAudioReference = (tmdbId: number, movieId: number, track: number, verified: boolean) =>
  audioSyncCall<AudioReference>(`/reference/${tmdbId}`, jsonBody("PUT", { movie_id: movieId, track, verified }));

export interface FilmMapVersion {
  id: number;
  filename: string;
  quality: string;
  file_size: number;
  audio: AudioTrackInfo[];
  alignment: {
    result_id: number; verdict: AudioVerdict; speed: number; offset: number;
    // each track measured against the reference: shifted by the version's timing + delta, or on its own (result_id)
    tracks?: Record<string, { delta: number; ok: boolean; result_id?: number; verdict?: AudioVerdict }>;
  } | null;
}

export interface TargetTrackCheck {
  ok: boolean;
  fixable: boolean;
  verdict: AudioVerdict;
  offset: number;
  speed: number;
  note?: string;
  pieces: { start: number; end: number; offset: number | null; slope?: number }[];
  result_id: number;
}

export interface FilmMapMember {
  version_id: number;
  track: number;
  codec: string;
  channels: number;
  bitrate?: number;
  title: string;
  language: string;
}

export interface FilmMap {
  id: number;
  created_at: string;
  stale: boolean;
  target_id: number;
  ref_track: number;
  duration: number;
  target_tracks?: Record<string, TargetTrackCheck>;
  selection?: Record<string, number[]> | null;     // measured tracks per version (null = all)
  versions: FilmMapVersion[];
  dubs: { id: number; lang: string; name: string; members: FilmMapMember[] }[];
}

export const startFilmMap = (tmdbId: number, targetId: number, refTrack: number, tracks: Record<number, number[]> | null) =>
  audioSyncCall<AudioSyncJob>("/map", jsonBody("POST", { tmdb_id: tmdbId, target_id: targetId, ref_track: refTrack, tracks }));
export const getFilmMap = (tmdbId: number) => audioSyncCall<FilmMap | null>(`/map/${tmdbId}`);
export const applyFilmMap = (mapId: number, picks: { version_id: number; track: number }[], fixTracks: number[],
                             dropTracks: number[], mode: "replace" | "version") =>
  audioSyncCall<AudioSyncJob>("/map/apply", jsonBody("POST", { map_id: mapId, picks, fix_tracks: fixTracks, drop_tracks: dropTracks, mode }));

// ── browser player ──

export interface PlayerInfo {
  id: number;
  title: string;
  year: string;
  duration: number;
  audio: AudioTrackInfo[];
  video: { codec: string; height: number; hdr: boolean; dv_profile: number | null; pix_fmt: string };
}

async function playerCall<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(`${API_BASE}/api/player${path}`, init);
  if (!res.ok) {
    let detail = `Chyba ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const getPlayerInfo = (movieId: number) => playerCall<PlayerInfo>(`/${movieId}/info`);
export type PlayerMode = "auto" | "original" | "transcode";

export const startPlayer = (movieId: number, at: number, audio: number, mode: PlayerMode,
                            caps: { hevc: boolean; dv5: boolean }) =>
  playerCall<{ session: string; start: number; audio: number; mode: "original" | "transcode"; reason: string }>(
    `/${movieId}/start`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ at, audio, mode, ...caps }),
    });
export const playerUrl = (session: string) => `${API_BASE}/api/player/s/${session}/index.m3u8`;
export const stopPlayer = (session: string) =>
  playerCall<{ ok: boolean }>(`/s/${session}`, { method: "DELETE" }).catch(() => ({ ok: false }));


// ── before a big rename: what Plex does on its own ──

export interface PlexMigrationCheck {
  configured: boolean;
  reachable?: boolean;
  error?: string;
  lumina_scans: boolean;
  settings: { id: string; title: string; on: boolean }[];
}

export async function getPlexMigrationCheck(): Promise<PlexMigrationCheck | null> {
  const res = await apiFetch(`${API_BASE}/api/plex/migration-check`);
  if (res.status === 404) return null;        // the plex module is off
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
