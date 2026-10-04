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
  original_language?: string;    // "en", "hi" … (Objevit can leave Indian films out)
  in_library?: boolean;
  wanted?: WantedMark | null;    // already on the wanted list (a show: its automation looks for episodes)
}

export interface WantedMark { status: string; added_by: string; added_at: string }

/** The film on the wanted list (not done yet), or null. */
export async function getWantedOf(tmdbId: number, wikidataId?: string | null): Promise<WantedMark | null> {
  const q = tmdbId ? `tmdb_id=${tmdbId}` : `wikidata_id=${encodeURIComponent(wikidataId ?? "")}`;
  const res = await apiFetch(`${API_BASE}/api/wanted/of?${q}`);
  if (!res.ok) return null;
  return res.json();
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
  name_ok?: boolean;        // TV: the episode's own name in the file is (true) / is not (false) the wanted one
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
  pack?: boolean;            // TV: a season / show pack holding the wanted episode
  // a cinema recording (backend core/cinema): video = a cinema picture (junk), audio = its CZ/SK sound recorded
  // in a cinema (cinema_langs, not in audio_langs), likely / suspect = the film is not out digitally yet
  cinema?: "" | "video" | "audio" | "likely" | "suspect";
  cinema_reason?: string;
  cinema_langs?: string[];
}

/** The film a file search was for — sent back with detail requests so files are re-evaluated with it. */
export interface MovieContext {
  titles: string[];
  year: number | null;
  runtime: number;
  episode?: { season: number; episode: number } | null;   // a TV episode instead of a film
  pre_digital?: boolean;     // not out digitally yet: every file is most likely a cinema recording
  releases?: { theatrical?: string; digital?: string };
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

export interface Suggestion extends TMDBMovie {
  person?: string;          // found through this person (actor / director)
}

/** As-you-type suggestions (films, shows, the best-known films of a person). */
export async function suggest(q: string, signal?: AbortSignal): Promise<Suggestion[]> {
  const res = await apiFetch(`${API_BASE}/api/search/suggest?q=${encodeURIComponent(q)}`, { signal });
  if (!res.ok) return [];
  return res.json();
}

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
  episode?: { season: number; episode: number; torrent?: boolean },
): Promise<SearchFilesResult> {
  const params = new URLSearchParams({ query });
  if (episode) {
    params.set("season", String(episode.season));
    params.set("episode", String(episode.episode));
    if (episode.torrent === false) params.set("torrent", "false");
  }
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
  queued?: number;            // over the limit of concurrent downloads: waits in the queue
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
      file_name: file.name,
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
  // what Lumina knows of a download it started
  tmdb_id?: number | null;
  film?: string;
  requested_by?: string;      // a user name, "Chci (plánovač)" …
  created_at?: string;
  mode?: string;              // "version" | "replace" | ""
  content_type?: string;
  season?: number | null;     // a TV episode
  episode?: number | null;
  queue_id?: number;          // waiting for a free slot (status "queued")
  queue_pos?: number;
  id?: string;                // a finished one (history)
  finished_at?: string;
  history?: boolean;
}

export interface DownloadList {
  downloads: DownloadItem[];  // running, then the queue
  history: DownloadItem[];    // finished, the newest first
  history_total: number;
}

export async function getDownloads(historyLimit = 10): Promise<DownloadList> {
  const res = await apiFetch(`${API_BASE}/api/downloads?limit=${historyLimit}`);
  if (!res.ok) throw new Error(`Failed to load downloads: ${res.status}`);
  const data = await res.json();
  return { downloads: data.downloads, history: (data.history ?? []).map((d: DownloadItem) => ({ ...d, history: true })),
           history_total: data.history_total ?? 0 };
}

export async function removeDownload(
  identifier: string,
  backend: "aria2" | "qbittorrent" | "queue" | "history" = "aria2",
  active: boolean = false,
): Promise<void> {
  const params = new URLSearchParams();
  if (backend !== "aria2") params.set("backend", backend);
  if (active) params.set("active", "true");
  const qs = params.toString() ? `?${params}` : "";
  await apiFetch(`${API_BASE}/api/download/${identifier}${qs}`, { method: "DELETE" });
}

/** "Zastavit vše": empty the queue, stop background checks; cancelRunning also cancels running downloads. */
export async function stopAllDownloads(cancelRunning: boolean): Promise<{ dropped: number; cancelled: number }> {
  const res = await apiFetch(`${API_BASE}/api/downloads/stop-all`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cancel_running: cancelRunning }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Skip the queue: start a waiting download right away. */
export async function startQueuedNow(queueId: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/download/queue/${queueId}/start`, { method: "POST" });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
}

// --- Source Management ---

export interface FriendCopy {
  owner: string;
  server: string;
  resolution: string;
  audio: string[];        // audio languages (ISO 639-1), [] = not known yet
  online: boolean;        // the friend's server answered at the last check
  seen_at: string | null;
  url: string;            // opens the film in Plex
}

/** Friends who have the film in a Plex library shared with you ([] when the plex module is off). */
export async function getFriendCopies(tmdbId: number): Promise<FriendCopy[]> {
  const res = await apiFetch(`${API_BASE}/api/plex/friends/movie?tmdb_id=${tmdbId}`);
  return res.ok ? res.json() : [];
}

export interface MovieInfo {
  genres: string[];
  runtime: number;
  rating: number;
  votes: number;
  directors: string[];
  cast: string[];
  imdb_url: string | null;
  csfd_url: string | null;
  csfd_exact: boolean;      // the film's own ČSFD page (via Wikidata), else a ČSFD search
}

export async function getMovieInfo(m: { tmdb_id?: number | null; wikidata_id?: string | null; title: string; year?: string }): Promise<MovieInfo | null> {
  const q = new URLSearchParams({ tmdb_id: String(m.tmdb_id || 0), wikidata_id: m.wikidata_id || "", title: m.title, year: m.year || "" });
  const res = await apiFetch(`${API_BASE}/api/search/movie-info?${q}`);
  return res.ok ? res.json() : null;
}

export interface ProfilePick {
  profile: string;
  key: string | null;              // "<source_id>:<ident>" of the offer the profile would take now
  suitable: number;
  reasons: [string, number][];     // why the others do not suit (most common first)
}

/** What a quality profile would download now from these offers (the rule "Chci" uses). */
export async function pickForProfile(profileId: number | null, files: ScoredFile[]): Promise<ProfilePick> {
  const res = await apiFetch(`${API_BASE}/api/search/pick`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: profileId, files }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** The sources switched on (no settings needed) */
export async function getActiveSources(): Promise<{ type: string; name: string }[]> {
  const res = await apiFetch(`${API_BASE}/api/sources/active`);
  if (!res.ok) return [];
  return res.json();
}

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
  waiting?: string;          // "čeká na digitální vydání (10. 11. 2026)" — only cinema recordings exist yet
  added_at: string;
  added_by?: string;
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
  min_mbps: number;          // overall bitrate (picture + sound)
  max_mbps: number;
  min_score: number;
  cutoff: number;
  kind: "movie" | "tv";      // for films / for TV shows — each kind has its own default
}

export interface ScoreRange {
  possible: boolean;
  reason?: string;
  min?: number;
  max?: number;
  min_example?: string;
  max_example?: string;
  size_2h_gb: [number, number | null];
  size_gb?: [number, number | null];     // of a 2-hour film / a 45-minute episode (kind)
  length_label?: string;                 // "film 2 h" | "díl 45 min"
}

/** What score a file the (edited) profile lets through can get, and how big a 2-hour film is. */
export async function getScoreRange(p: QualityProfile): Promise<ScoreRange> {
  const { id, name, is_default, ...config } = p;
  const res = await apiFetch(`${API_BASE}/api/settings/profiles/score-range`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, is_default, config }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Profiles for films (default), for TV shows or all of them. */
export async function getProfiles(kind: "movie" | "tv" | "all" = "movie"): Promise<QualityProfile[]> {
  const res = await apiFetch(`${API_BASE}/api/settings/profiles?kind=${kind}`);
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

export async function getGeminiModels(): Promise<GroqModels> {
  const res = await apiFetch(`${API_BASE}/api/settings/gemini-models`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
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
  suggested_map?: string | null;
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
  phase?: "movies" | "tv" | "tv_media" | "done";
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
  status: "better" | "none" | "error" | "done" | "downloading";   // done = the film's profile cutoff is reached
  upgrades: number;
  best: { name?: string; source?: string; quality_score?: number; quality_summary?: string; verified?: boolean; size?: number };
  error: string;
  checked_at: string;
  note?: string;
}

/** Film-level settings in the library: quality profile and "watch for a better version". */
export interface FilmSettings {
  profile_id: number | null;
  watch_upgrades: boolean;
  on_better?: string;        // '' = as the scheduler says | notify | version | replace
  upgrade_once?: boolean;    // stop watching once a better version is in the library
}

export interface BulkFilmSettings {
  tmdb_ids: number[];
  keep_profile: boolean;
  profile_id: number | null;
  watch_upgrades: boolean;
  on_better: string;
  upgrade_once: boolean;
  check_now: boolean;
}

export async function setFilmSettingsBulk(s: BulkFilmSettings): Promise<{ saved: number; job: UpgradeJob | null }> {
  const res = await apiFetch(`${API_BASE}/api/library/films/bulk`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
  return res.json();
}

export interface WatchedFilm {
  tmdb_id: number;
  title: string;
  year: string;
  poster_url: string | null;
  profile_id: number | null;
  on_better: string;
  upgrade_once: boolean;
  owned: { id: number; quality: string; score: number; language: string; size: number };
  check: UpgradeCheck | null;
}

export async function getWatched(): Promise<WatchedFilm[]> {
  const res = await apiFetch(`${API_BASE}/api/library/watched`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Download the better version the last check found: another version, or replacing the owned one. */
export async function downloadUpgrade(tmdbId: number, mode: "version" | "replace"): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/library/upgrades/${tmdbId}/download`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
}

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
export type LibraryAction = { mode: "version" } | { mode: "replace"; file_id: number }
  | { mode: "episode"; season: number; episode: number; replace?: boolean };

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

/** stage "names": only new file names, files stay in their folders (the first half of a rename with Plex) */
export async function applyOrganize(movieIds: number[], stage: "all" | "names" = "all"): Promise<OrganizeResult> {
  const res = await apiFetch(`${API_BASE}/api/library/organize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ movie_ids: movieIds, stage }),
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
  default?: boolean;
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
export interface PlannedTrack {
  key: string;
  origin: "keep" | "fix" | "add";
  track: number;
  name: string;
  renamed: boolean;
  custom: boolean;
  from: string | null;
  reencoded: boolean;
  default: boolean;
  language: string;
  set_language: string | null;      // a language tag written into the file (read from the title, or chosen)
  language_from_title: boolean;
  codec: string;
  channels: number;
  bitrate: number;
}
export interface FilmMapEdit {
  picks: { version_id: number; track: number }[];
  fixTracks: number[];
  dropTracks: number[];
  defaultKey: string | null;
  names: Record<string, string>;
  languages: Record<string, string>;
}
const editBody = (mapId: number, e: FilmMapEdit) =>
  ({ map_id: mapId, picks: e.picks, fix_tracks: e.fixTracks, drop_tracks: e.dropTracks, default_key: e.defaultKey, names: e.names, languages: e.languages });
/** How the result would look: order, names written into the file, origin, default track. */
export const planFilmMap = (mapId: number, e: FilmMapEdit) =>
  audioSyncCall<{ tracks: PlannedTrack[]; default: string; reference_dropped: boolean }>("/map/plan", jsonBody("POST", editBody(mapId, e)));
export const applyFilmMap = (mapId: number, e: FilmMapEdit, mode: "replace" | "version") =>
  audioSyncCall<AudioSyncJob>("/map/apply", jsonBody("POST", { ...editBody(mapId, e), mode }));

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

/** What the player plays: a movie version (library_movies id) or an episode (library_episodes id). */
export type MediaKind = "movie" | "episode";
const playerPath = (id: number, kind: MediaKind) => (kind === "episode" ? `/episode/${id}` : `/${id}`);

export const getPlayerInfo = (movieId: number, kind: MediaKind = "movie") => playerCall<PlayerInfo>(`${playerPath(movieId, kind)}/info`);
export type PlayerMode = "auto" | "original" | "transcode";

export const startPlayer = (movieId: number, at: number, audio: number, mode: PlayerMode,
                            caps: { hevc: boolean; dv5: boolean }, kind: MediaKind = "movie") =>
  playerCall<{ session: string; start: number; audio: number; mode: "original" | "transcode"; reason: string }>(
    `${playerPath(movieId, kind)}/start`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ at, audio, mode, ...caps }),
    });
export const playerUrl = (session: string) => `${API_BASE}/api/player/s/${session}/index.m3u8`;
export const stopPlayer = (session: string) =>
  playerCall<{ ok: boolean }>(`/s/${session}`, { method: "DELETE" }).catch(() => ({ ok: false }));


// ── before a big rename: what Plex does on its own ──

export interface PlexMovieBrief { rating_key: string; title: string; year: number | null; files?: string[] }

export interface PlexMigrationReport {
  checked_at: number;
  moved: number;
  unchanged: number;
  new: PlexMovieBrief[];
  renewed?: PlexMovieBrief[];   // Plex gave a new id, watched state and date added carried over
  missing: PlexMovieBrief[];
  readded: { title: string; year: number | null; old_key: string; new_key: string; watched: boolean; added_at: number | null }[];
}

export type PlexKind = "movie" | "show";

export interface PlexMigration {
  configured: boolean;
  error?: string;
  active: boolean;
  kind?: PlexKind;
  other_running?: PlexKind | null;     // a migration of the other library runs (one at a time)
  section?: string;
  settings?: { id: string; title: string; on: boolean; was_on?: boolean }[];
  started_at?: string;
  movies?: number;
  report?: PlexMigrationReport | null;
  job: { running?: boolean; phase?: string; error?: string; finished_at?: number };
}

/** null = the plex module is off */
export async function getPlexMigration(kind: PlexKind = "movie"): Promise<PlexMigration | null> {
  const res = await apiFetch(`${API_BASE}/api/plex/migration?kind=${kind}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function plexMigrationPost(path: string, body?: object): Promise<any> {
  const res = await apiFetch(`${API_BASE}/api/plex/migration/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export const startPlexMigration = (kind: PlexKind = "movie"): Promise<PlexMigration> => plexMigrationPost("start", { kind });

/** One Plex scan + check, waiting until it is done. */
export async function checkPlexMigrationAndWait(kind: PlexKind = "movie"): Promise<PlexMigration | null> {
  await plexMigrationPost("check");
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    const state = await getPlexMigration(kind);
    if (!state?.job?.running) {
      if (state?.job?.error) throw new Error(`Kontrola Plexu selhala: ${state.job.error}`);
      return state;
    }
  }
}
export const checkPlexMigration = (): Promise<{ started: boolean }> => plexMigrationPost("check");
export const finishPlexMigration = (emptyTrash: boolean, repair: boolean): Promise<{ repaired: number; emptied: boolean }> =>
  plexMigrationPost("finish", { empty_trash: emptyTrash, repair });


// --- Subtitles (module subtitles, OpenSubtitles.com) ---

export interface SubtitleStatus {
  embedded: { lang: string; forced: boolean; title: string; codec: string }[];
  external: { file: string; lang: string; forced: boolean; syncing?: boolean; sync?: SubtitleSync | null }[];
  spoken_languages: string[];
  needs_forced: boolean;
  has_forced: boolean;
  local_langs: string[];
  configured: boolean;
}

export interface SubtitleSync {
  ok: boolean;
  changed?: boolean;
  reason?: string;
  scale_name?: string;
  shift?: number;
  parts?: number[];          // shift per third (s) — or, method "začátky vět", the share of starts that meet speech
  method?: string;           // "překryv s řečí" | "začátky vět"
  edge_hits?: string;
  cut_warning?: boolean;
  synced_at?: string;
}

export async function syncSubtitle(movieId: number, file: string, kind: MediaKind = "movie"): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/subtitles/${kind}/${movieId}/sync`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file }),
  });
  if (!res.ok) throw await errorOf(res);
}

export interface SubtitleResult {
  file_id: number;
  file_name: string;
  language: string;
  release: string;
  downloads: number;
  forced: boolean;
  hearing_impaired: boolean;
  fps: number;
  hash_match: boolean;
  machine: boolean;
  uploader: string;
  uploaded: string;
}

async function errorOf(res: Response): Promise<Error> {
  return new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
}

export async function getSubtitleStatus(movieId: number, kind: MediaKind = "movie"): Promise<SubtitleStatus> {
  const res = await apiFetch(`${API_BASE}/api/subtitles/${kind}/${movieId}`);
  if (!res.ok) throw await errorOf(res);
  return res.json();
}

export async function searchSubtitles(movieId: number, forced: boolean, kind: MediaKind = "movie"): Promise<{ results: SubtitleResult[]; video_fps: number }> {
  const res = await apiFetch(`${API_BASE}/api/subtitles/${kind}/${movieId}/search?forced=${forced}`);
  if (!res.ok) throw await errorOf(res);
  return res.json();
}

export async function downloadSubtitle(movieId: number, body: { file_id: number; language: string; forced: boolean; fps: number; replace: boolean },
                                       kind: MediaKind = "movie"):
    Promise<{ file: string; note: string; remaining: number | null }> {
  const res = await apiFetch(`${API_BASE}/api/subtitles/${kind}/${movieId}/download`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw await errorOf(res);
  return res.json();
}

export async function testOpenSubtitles(): Promise<{ api_key: boolean; account: boolean | null }> {
  const res = await apiFetch(`${API_BASE}/api/subtitles/test`, { method: "POST" });
  if (!res.ok) throw await errorOf(res);
  return res.json();
}


// ── TV shows (backend modules/series) ──

export type SeriesLangMode = "local_or_temp" | "local_only" | "original";

export type SeriesAutoMode = "off" | "notify" | "download";
export type SeriesAutoFrom = "next" | "all";

export interface SeriesSettingValues {
  profile_id: number | null;
  lang_mode: SeriesLangMode | null;
  torrent: boolean | null;
  auto_new: SeriesAutoMode | null;    // new episodes: nothing / show what was found / download
  auto_from: SeriesAutoFrom | null;   // "new" = after the last owned episode / every missing one
  auto_dub: SeriesAutoMode | null;    // the CZ/SK dub of episodes owned in English
  auto_upgrade: SeriesAutoMode | null; // a better version of episodes below the show's quality profile
}

export type SeriesEffectiveSettings = SeriesSettingValues & {
  lang_mode: SeriesLangMode; torrent: boolean; auto_new: SeriesAutoMode; auto_from: SeriesAutoFrom; auto_dub: SeriesAutoMode;
  auto_upgrade: SeriesAutoMode;
};

export interface SeriesAutoRecord {
  tmdb_id: number; season: number; episode: number; kind: "new" | "dub" | "upgrade";
  status: "found" | "downloading" | "dismissed"; updated_at: string;
  row: { name: string; source: string; size: number; resolution?: string; quality_summary?: string; audio_langs?: string[]; quality_score?: number };
}

export interface SeriesAutoCheck { checked_at: string; wanted: number; found: number; downloading: number; note: string }

export interface SeriesAutoJob { running: boolean; total: number; done: number; current: string; found: number; downloading: number; queued: number }

export interface SeriesAutoShow {
  tmdb_id: number; title: string; year: string; poster_url: string | null; in_library: boolean;
  own: SeriesSettingValues; effective: SeriesEffectiveSettings;
  owned: number; foreign: number;           // owned episodes / of them without CZ/SK sound
  checked: SeriesAutoCheck | null; found: SeriesAutoRecord[];
  quality: SeriesQualityStats | null;      // the owned episodes by their MediaInfo (null: none owned)
}

export interface SeriesQualityStats {
  size: number; known: number;             // all owned episodes' size / how many have MediaInfo
  res: Record<string, number>;             // "2160p" | "1080p" | "720p" | "SD" → episodes
  codec: Record<string, number>;
  hdr: number; weak: number; below: number;  // with HDR / score under 50 / not meeting the show's profile
  min_score: number | null; avg_score: number | null;
}

export interface SeriesAutoOverview {
  shows: SeriesAutoShow[]; defaults: SeriesEffectiveSettings; job: SeriesAutoJob;
  scheduler: { enabled: boolean; series: boolean; time: string; last_run: string };
}

async function seriesJson<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await apiFetch(`${API_BASE}/api/series${path}`, body === undefined ? { method } : {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export const getSeriesAutomation = () => seriesJson<SeriesAutoOverview>("/automation/overview");
export const saveSeriesAutomationBulk = (tmdbIds: number[], values: Partial<SeriesSettingValues>) =>
  seriesJson<{ saved: number }>("/automation/bulk", "PUT", { tmdb_ids: tmdbIds, values });
export const runSeriesAutomation = (tmdbIds: number[] = []) => seriesJson<SeriesAutoJob>("/automation/run", "POST", { tmdb_ids: tmdbIds });
export const getShowAutomation = (tmdbId: number) =>
  seriesJson<{ records: SeriesAutoRecord[]; checked: SeriesAutoCheck | null; job: SeriesAutoJob }>(`/${tmdbId}/automation`);
export const downloadAutoFound = (tmdbId: number, keys: [number, number, string][]) =>
  seriesJson<{ started: number; errors: string[] }>(`/${tmdbId}/automation/download`, "POST", { keys });
export const dismissAutoFound = (tmdbId: number, keys: [number, number, string][]) =>
  seriesJson<{ ok: boolean }>(`/${tmdbId}/automation/dismiss`, "POST", { keys });

export interface SeriesEpisodeFile {
  id?: number;               // library_episodes id (the episode's window)
  filename: string;
  file_path: string;
  size: number;
  quality: string;
  languages: string[];
}

export interface SeriesEpisode {
  episode: number;
  name: string;
  air_date: string;
  runtime: number;
  overview: string;
  no_dub?: boolean;         // the user marked it: a dub was never made
  state: "owned" | "temp" | "unknown" | "missing" | "upcoming";
  file: SeriesEpisodeFile | null;
}

export interface SeriesSeason {
  season_number: number;
  episode_count: number;
  name: string;
  air_date: string;
  poster_url: string | null;
  episodes: SeriesEpisode[];
  counts: { owned: number; temp: number; unknown: number; missing: number; upcoming: number };
}

export interface SeriesDetail {
  show: {
    tmdb_id: number; title: string; original_title: string; year: number | null; first_air_date: string;
    status: string; overview: string; poster_url: string | null; networks: string[]; genres: string[];
    rating: number; episode_runtime: number; titles: string[];
    last_episode: { season: number; episode: number; air_date: string; name: string } | null;
    next_episode: { season: number; episode: number; air_date: string; name: string } | null;
  };
  settings: { own: SeriesSettingValues; effective: SeriesEffectiveSettings; defaults: SeriesEffectiveSettings };
  profile: { id: number; name: string };
  seasons: SeriesSeason[];
  totals: { owned: number; temp: number; unknown: number; missing: number; upcoming: number };
  local_langs: string[];
  in_library: boolean;
}

export async function getSeries(tmdbId: number, fresh = false): Promise<SeriesDetail> {
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}${fresh ? "?fresh=true" : ""}`);
  if (!res.ok) throw new Error(`Seriál se nenačetl: ${res.status}`);
  return res.json();
}

/** Only the keys to change; null = back to the default. */
export async function saveSeriesSettings(tmdbId: number, values: Partial<SeriesSettingValues>): Promise<SeriesDetail["settings"]> {
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
  return res.json();
}

export async function getSeriesDefaults(): Promise<SeriesSettingValues> {
  const res = await apiFetch(`${API_BASE}/api/series/defaults/settings`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function saveSeriesDefaults(values: Partial<SeriesSettingValues>): Promise<SeriesSettingValues> {
  const res = await apiFetch(`${API_BASE}/api/series/defaults/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
  return res.json();
}

export interface SeasonSet {
  key: string;
  label: string;
  episodes: Record<string, ScoredFile>;   // episode number → file of this release
  covered: number[];                      // wanted episodes it has
  coverage: number;
  score: number;
  resolution: string;
  langs: string[];
  local: boolean;                         // CZ/SK sound
  size: number;
  sources: string[];
}

export interface SeasonOffers {
  season: number;
  wanted: number[];
  sets: SeasonSet[];
  packs: ScoredFile[];
  plan: { episode: number; set: string; row: ScoredFile }[];
  movie: MovieContext | null;
}

export async function getSeasonOffers(tmdbId: number, season: number, episodes?: number[]): Promise<SeasonOffers> {
  const q = episodes?.length ? `?episodes=${episodes.join(",")}` : "";
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}/season/${season}/offers${q}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Hledání selhalo: ${res.status}`);
  }
  return res.json();
}

export async function downloadSeason(tmdbId: number, season: number, items: { episode: number; row: ScoredFile }[],
                                     replaceOwned = true): Promise<{ started: number; errors: string[] }> {
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}/season/${season}/download`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, replace_owned: replaceOwned }),
  });
  if (!res.ok) throw new Error(`Stažení selhalo: ${res.status}`);
  return res.json();
}

export interface ShowPack extends ScoredFile {
  seasons: number[];        // the seasons the name says it holds ([] = the whole show)
  complete: boolean;        // "komplet" / "complete", or no seasons named
  covers: number;           // how many of TMDB's seasons it holds
}

export interface ShowPacks {
  seasons: number[];        // TMDB's seasons (no specials)
  packs: ShowPack[];
}

export async function getShowPacks(tmdbId: number): Promise<ShowPacks> {
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}/packs`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Hledání selhalo: ${res.status}`);
  }
  return res.json();
}

export async function downloadShowPack(tmdbId: number, row: ShowPack, replaceOwned: boolean): Promise<{ started: boolean }> {
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}/pack/download`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ row, replace_owned: replaceOwned }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Stažení selhalo: ${res.status}`);
  }
  return res.json();
}

// ── TV library inventory (backend modules/library/tv_inventory) ──

export interface TvInventoryProblem {
  file: string;
  season: number | null;
  episodes: number[];
  status: string;
  note: string;
}

export interface TvInventoryFolder {
  folder: string;
  tmdb_id: number | null;
  title?: string | null;
  year?: string | null;
  source: "plex" | "lumina" | "user" | "";
  lumina_tmdb_id?: number | null;
  lumina_title?: string;
  plex_tmdb_id?: number | null;
  plex_title?: string;
  files: number;
  counts: Record<string, number>;
  problems: TvInventoryProblem[];
  disagree: boolean;
}

export interface TvInventory {
  folders: TvInventoryFolder[];
  total: Record<string, number>;
  labels: Record<string, string>;
  scanned_at: string | null;
}

export async function getTvInventory(): Promise<TvInventory> {
  const res = await apiFetch(`${API_BASE}/api/library/tv/inventory`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── TV renamer (backend modules/library/organize_tv) ──

export interface TvOrganizeOp { kind: "video" | "sidecar" | "extra" | "other"; src: string; dst: string }

export interface TvOrganizePlan {
  folder: string;
  tmdb_id: number;
  title: string;
  year: number | null;
  numbering: "files" | "tmdb";
  source_folder: string;
  target_folder: string;
  kinds: Record<string, number>;
  ops: TvOrganizeOp[];
  conflicts: string[];
  blocked: string;
  skipped: { file: string; why: string }[];
  tips: string[];
  media_missing: number;
  renumbered: number;       // episodes Plex will show under another number (new items, state given back)
  suggested: boolean;       // numbering by names proposed (files named as other episodes), not chosen yet
  sure_names: number;
  renumber: { file: string; from: string; to: string; title: string }[];
  unsure: { file: string; why: string }[];
}

export interface TvOrganizeResult {
  batch_id: string | null;
  done: { title: string; folder: string; ops: number; new_folder: string }[];
  failed: { folder: string; error: string }[];
}

async function jsonOrError(res: Response) {
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function getTvOrganizePlans(folder?: string): Promise<TvOrganizePlan[]> {
  const q = folder !== undefined ? `?folder=${encodeURIComponent(folder)}` : "";
  const data = await jsonOrError(await apiFetch(`${API_BASE}/api/library/tv/organize${q}`));
  return folder !== undefined ? [data] : data;
}

/** stage "names": only new file names, files stay in their folders (the first half of a rename with Plex) */
export async function applyTvOrganize(folders: string[], stage: "all" | "names" = "all"): Promise<TvOrganizeResult> {
  return jsonOrError(await apiFetch(`${API_BASE}/api/library/tv/organize`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folders, stage }),
  }));
}

/** How a show's files are numbered: the user's (files/Plex) or TMDB's. */
export async function setTvNumbering(tmdbId: number, numbering: "files" | "tmdb"): Promise<void> {
  await jsonOrError(await apiFetch(`${API_BASE}/api/library/tv/naming`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tmdb_id: tmdbId, numbering }),
  }));
}

/** Which show a folder of the TV library is (null = let the scan decide again). */
export async function setTvOverride(folder: string, tmdbId: number | null): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/library/tv/override`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, tmdb_id: tmdbId }),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
}


// ── One episode of the library (its window on the show page) ──

export interface EpisodeVersion {
  file_path: string;
  filename: string;
  size: number;
  media: { width?: number; height?: number; video_codec?: string; hdr?: string; bitrate?: number; duration_s?: number;
           audio?: { lang?: string; codec?: string; channels?: number }[]; subtitles?: string[] };
  current: boolean;
}

export interface EpisodeDetail {
  id: number;
  show_tmdb_id: number;
  show_title: string | null;
  season: number;
  episode: number;
  episode_title: string;
  air_date: string;
  file_path: string;
  no_dub?: boolean;         // the user marked it: a Czech dub was never made
  versions: EpisodeVersion[];
}

export async function getEpisodeDetail(id: number): Promise<EpisodeDetail> {
  const res = await apiFetch(`${API_BASE}/api/library/episodes/${id}`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function deleteEpisodeFile(id: number, filePath: string): Promise<{ deleted: string[] }> {
  const res = await apiFetch(`${API_BASE}/api/library/episodes/${id}/delete-file`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file_path: filePath }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

// ── Which episode each file is (backend library: tv/folder, tv/episode-override, tv/ai-map) ──

export interface TvFolderFile {
  file: string;                 // relative to the TV library
  name: string;
  own: string;                  // the episode's name in the file's first name
  season: number | null;
  episode: number | null;
  status: string;
  note: string;
  manual: boolean;              // the user's word
  plex: [number, number, string] | null;
  duration: number;
}

export interface TvCatalogEpisode { season: number; episode: number; cs: string; en: string; runtime: number; air: string }

export interface TvFolderDetail {
  folder: string;
  tmdb_id: number;
  groq: boolean;
  files: TvFolderFile[];
  episodes: TvCatalogEpisode[];
}

export interface TvAiSuggestion {
  file: string;
  season: number;
  episode: number;
  confidence: number;
  title: string;
  agrees: boolean;              // Lumina's own rules say the same
  rules: string;                // Lumina's rules surely say another episode ("S05E03")
  same: boolean;                // the number it has now
  warning?: string;
  heard?: boolean;          // the file has no name: the AI read its subtitles
}

export async function getTvFolder(folder: string): Promise<TvFolderDetail> {
  const res = await apiFetch(`${API_BASE}/api/library/tv/folder?folder=${encodeURIComponent(folder)}`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

/** The user's word on which episode a file is (season/episode null = no word, the scan decides). */
export async function setAudioLanguage(ids: number[], lang: string, track?: number, paths: string[] = []):
    Promise<{ done: { id: number | null; written: boolean; languages: string; tracks: number }[]; errors: string[] }> {
  const res = await apiFetch(`${API_BASE}/api/library/tv/audio-language`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, paths, lang, track: track ?? null }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Uložení selhalo: ${res.status}`);
  }
  return res.json();
}

export async function setNoDub(tmdbId: number, season: number, episode: number, on: boolean): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/series/${tmdbId}/episode/${season}/${episode}/no-dub`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }),
  });
  if (!res.ok) throw new Error(`Uložení selhalo: ${res.status}`);
}

export async function setEpisodeOverride(file: string, season: number | null, episode: number | null, note = ""): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/library/tv/episode-override`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file, season, episode, note }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
}

export async function suggestEpisodesAi(folder: string, season: number | null, files?: string[]): Promise<{ suggestions: TvAiSuggestion[]; asked: number }> {
  const res = await apiFetch(`${API_BASE}/api/library/tv/ai-map`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, season, files }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

// „Neznám název“ — the user describes a film / show, Groq guesses, TMDB finds them
export interface DescribeTurn { role: "user" | "assistant"; content: string }
export interface DescribeHit extends TMDBMovie { why: string }
/** what is left of the Groq key's own limits (admin only): requests today, tokens this minute */
export interface GroqLeft { requests_left: number | null; requests_limit: number | null; tokens_left: number | null; tokens_limit: number | null }
/** Gemini sends no "what is left": Lumina counts its calls of Google's day */
export interface GeminiUsed { calls: number; searches: number; exhausted: boolean; no_search?: boolean; model?: string; out?: string[] }
export interface AiQuotas { groq?: Partial<GroqLeft>; gemini?: GeminiUsed }
/** left: questions left today, null = not limited (admin); ai: the AIs' own limits (admin only) */
export interface DescribeStatus { enabled: boolean; left: number | null; daily: number; ai: AiQuotas }
export interface DescribeAnswer {
  results: DescribeHit[]; ask: string; left: number | null; ai: AiQuotas; guessed: string[];
  by: string;            // which AI answered ("Gemini" / "Groq")
  searched: boolean;     // Gemini searched Google for it
}

export async function getDescribeStatus(): Promise<DescribeStatus> {
  const res = await apiFetch(`${API_BASE}/api/search/describe`);
  if (!res.ok) return { enabled: false, left: 0, daily: 0, ai: {} };
  return res.json();
}

export async function describeTitle(talk: DescribeTurn[]): Promise<DescribeAnswer> {
  const res = await apiFetch(`${API_BASE}/api/search/describe`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ talk }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

// ── notifications (backend modules/notify) ──

export interface NotificationItem {
  id: number; created_at: string; kind: string; level: "info" | "ok" | "warn" | "error";
  title: string; body: string; link: string; new: boolean;
}

export async function getNotifications(): Promise<{ items: NotificationItem[]; unread: number }> {
  const res = await apiFetch(`${API_BASE}/api/notifications`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function markNotificationsSeen(lastId: number): Promise<void> {
  await apiFetch(`${API_BASE}/api/notifications/seen`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ last_id: lastId }),
  });
}

// ── Plex: what plays right now (the phone's "Sleduji") ──

export interface NowPlaying {
  kind: "movie" | "episode"; title: string; year?: number | null; show: string; season: number | null; episode: number | null;
  state: string; player: string; progress: number | null;
  id: number | null;          // the library film / episode (null: Lumina does not know the file)
  tmdb_id: number | null;     // the film's / the show's
  user: string;               // the Plex account watching it
  mine: boolean;              // the server owner (first in the list)
}

export async function getPlexPlaying(): Promise<{ configured: boolean; items: NowPlaying[]; error?: string }> {
  const res = await apiFetch(`${API_BASE}/api/plex/now-playing`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
