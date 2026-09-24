const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface TMDBMovie {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: string;
  overview: string;
  poster_url: string | null;
  media_type?: "movie" | "tv";
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
  const res = await fetch(`${API_BASE}/api/search/details`, {
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

export async function searchFiles(
  query: string,
  language?: string,
  originalTitle?: string,
  tmdbId?: number,
  mediaType?: string,
): Promise<SearchFilesResult> {
  const params = new URLSearchParams({ query });
  if (language) params.set("language", language);
  if (originalTitle && originalTitle !== query) params.set("original_title", originalTitle);
  if (tmdbId) params.set("tmdb_id", String(tmdbId));
  if (mediaType) params.set("media_type", mediaType);
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
  libraryAction?: LibraryAction,
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

export interface GroqModels {
  models: string[];
  default: string;
  error: string | null;
}

export async function getGroqModels(): Promise<GroqModels> {
  const res = await fetch(`${API_BASE}/api/settings/groq-models`);
  if (!res.ok) return { models: [], default: "", error: `HTTP ${res.status}` };
  return res.json();
}

export async function getLanguages(): Promise<LanguageOption[]> {
  const res = await fetch(`${API_BASE}/api/settings/languages`);
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
  const res = await fetch(`${API_BASE}/api/integrations`);
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
    const res = await fetch(`${API_BASE}/api/plex/test`, {
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
  const res = await fetch(`${API_BASE}/api/integrations/${type}`, {
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
  const res = await fetch(`${API_BASE}/api/library/scan?force=${force}`, { method: "POST" });
  if (!res.ok) throw new Error(`Scan failed: ${res.status}`);
  return res.json();
}

export async function getScanStatus(): Promise<ScanStatus> {
  const res = await fetch(`${API_BASE}/api/library/scan/status`);
  if (!res.ok) throw new Error(`Scan status failed: ${res.status}`);
  return res.json();
}

export async function getLibrarySummary(): Promise<Partial<Record<LibraryStatus, number>>> {
  const res = await fetch(`${API_BASE}/api/library/movies/summary`);
  if (!res.ok) return {};
  return res.json();
}

export async function getLibraryMovies(): Promise<LibraryMovie[]> {
  const res = await fetch(`${API_BASE}/api/library/movies`);
  if (!res.ok) throw new Error(`Failed to load library movies: ${res.status}`);
  return res.json();
}

export async function getLibraryShows(): Promise<LibraryShow[]> {
  const res = await fetch(`${API_BASE}/api/library/shows`);
  if (!res.ok) throw new Error(`Failed to load library shows: ${res.status}`);
  return res.json();
}

export async function getShowDetail(tmdbId: number): Promise<LibraryShowDetail> {
  const res = await fetch(`${API_BASE}/api/library/shows/${tmdbId}`);
  if (!res.ok) throw new Error(`Failed to load show detail: ${res.status}`);
  return res.json();
}

export async function deleteLibraryMovie(id: number): Promise<void> {
  await fetch(`${API_BASE}/api/library/movies/${id}`, { method: "DELETE" });
}

export async function deleteLibraryShow(tmdbId: number): Promise<void> {
  await fetch(`${API_BASE}/api/library/shows/${tmdbId}`, { method: "DELETE" });
}

export async function searchTMDBForFix(movieId: number, query: string): Promise<TMDBSearchResult[]> {
  const res = await fetch(`${API_BASE}/api/library/movies/${movieId}/search-tmdb?query=${encodeURIComponent(query)}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fixMovieMatch(movieId: number, tmdbId: number): Promise<void> {
  await fetch(`${API_BASE}/api/library/movies/${movieId}/fix`, {
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
}

/** What to do with an owned movie once a download finishes. */
export type LibraryAction = { mode: "version" } | { mode: "replace"; file_id: number };

export async function getOwned(tmdbIds: number[]): Promise<Record<string, OwnedVersion[]>> {
  const ids = tmdbIds.filter(Boolean);
  if (!ids.length) return {};
  const res = await fetch(`${API_BASE}/api/library/owned?tmdb_ids=${ids.join(",")}`);
  if (!res.ok) return {};
  return res.json();
}

export function versionLabel(v: OwnedVersion): string {
  return [v.quality !== "unknown" ? v.quality : "", v.codec, v.hdr, v.language.replaceAll(",", "+")].filter(Boolean).join(" ");
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
  const res = await fetch(`${API_BASE}/api/library/movies/${movieId}/organize`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function getOrganizePlanAll(): Promise<OrganizePlan[]> {
  const res = await fetch(`${API_BASE}/api/library/organize`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function applyOrganize(movieIds: number[]): Promise<OrganizeResult> {
  const res = await fetch(`${API_BASE}/api/library/organize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ movie_ids: movieIds }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function undoOrganize(batchId: string): Promise<{ undone: number }> {
  const res = await fetch(`${API_BASE}/api/library/operations/${batchId}/undo`, { method: "POST" });
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
