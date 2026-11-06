# Lumina

A self-hosted library manager for movies and TV shows, built for Czech / Slovak households. It searches
direct-download storages (WebShare, FastShare) and torrent indexers (Prowlarr / Jackett), knows which file is
really the film or the episode you want and which dub it has, downloads it, and keeps a Plex library in order —
a replacement for the Radarr / Sonarr stack with a human in the loop.

![Stack](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Stack](https://img.shields.io/badge/Next.js_14-000?logo=nextdotjs&logoColor=white)
![Stack](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Stack](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Stack](https://img.shields.io/badge/Python_3.11+-3776AB?logo=python&logoColor=white)

> The user interface is in Czech. This repository is a fork of
> [lesnek/lumina](https://github.com/lesnek/lumina) that grew into a full library manager.

## What it does

**Search and pick**
- Search a film or show on TMDB (Wikidata as a fallback for titles TMDB does not know), or describe the plot
  when you forgot the name — an AI chat suggests titles.
- All sources searched at once. Each file is judged by rules first: is it this film (sequels, namesakes,
  other parts of a collection are recognised), which sound tracks and subtitles it really has (read from the
  source's MediaInfo, not guessed from the name), its quality score. AI only for the unclear ones.
- Episodes are matched by their **name**, not only by the number in the file name — uploaders number
  differently, TMDB has specials, split seasons and absolute anime numbering.
- Cinema recordings (CAM / TS, a dub recorded in a cinema) are recognised and never recommended; before a
  film's digital release every file is suspect.
- Quality profiles (resolution, codec, bitrate, sound languages) pick the file for you; you approve it.

**Download**
- Aria2 for direct downloads, qBittorrent for torrents, with a queue and a concurrency limit.
- Whole-show and whole-season torrent packs are planned before anything downloads: every file is matched to
  its episode (names, two-part episodes, specials, bonuses into Plex extras folders), episodes you already own
  are skipped, the first episodes download first and each one goes to the library as soon as it is complete.

**Library**
- Imports, renames (one naming engine for films and shows), detects versions and duplicates, reads MediaInfo,
  shows a quality overview with bulk actions, finds better versions.
- A library check for TV shows: unplaced or doubtful files, multi-episode files, manual episode mapping with
  an AI suggestion (reads the subtitles when the file has no name).
- Sound track languages can be set and are written into the file (MKV in place, MP4 by a checked copy).
- Plex integration: partial scans, path mapping, a migration mode that keeps Plex's watched state when files
  move.

**Automation (off by default)**
- "Chci" (want list): films and shows to get once a file suiting the profile appears.
- Per-show automation: new episodes, Czech / Slovak dubs, better quality — notify or download.
- A scheduler, notifications in the app.

**Audio and files**
- **Dub transfer**: moves a Czech / Slovak sound track from one release into another (different cut, speed,
  offset) — measured against a reference track, checked, with previews and manual fine-tuning. Works for
  films and whole seasons of episodes.
- **File changes**: Dolby Vision MKV → MP4 losslessly; shrinking files to a chosen quality (VMAF) or bitrate
  with a preview and a check. Encoding can run on the server or on your PC via the optional
  [worker](worker/) tray app.

**Other**
- Built-in browser player (HLS, original picture when the browser can play it, two-part episodes).
- Subtitles from OpenSubtitles.
- A separate mobile interface, users with permissions (including a child account that can only ask).

## Quick start

```bash
git clone https://github.com/shanislav/lumina.git
cd lumina
cp .env.example .env
docker compose up -d
```

Open <http://localhost:3000> — the setup wizard creates the first admin and guides you through API keys,
sources and download settings. Everything is stored in SQLite and editable in Settings; `.env` is only read
on the first run.

`docker-compose.yml` is a starting point: mount your movie and TV library folders into the backend and set
their paths in Settings → Library. Lumina expects one root for each (films in their own folders, shows as
`Show (year)/Season NN`).

### What you'll need

| Service | What for |
|---|---|
| TMDB API key | Search and metadata ([themoviedb.org](https://www.themoviedb.org/settings/api)) |
| WebShare / FastShare account | Direct-download sources |
| Prowlarr or Jackett + qBittorrent | Torrents |
| Groq and / or Gemini API key | Optional AI help (both have free tiers) |
| Plex | Optional — scans, path mapping, now playing |
| OpenSubtitles account | Optional — subtitles |

## Architecture

```
Next.js 14 frontend ──▶ FastAPI backend ──▶ SQLite
                          │
                          ├─ core/      shared logic: quality score, profiles, film / episode matching,
                          │             release names, MediaInfo, naming, events, offers search
                          ├─ modules/   one folder per feature (router + tables + tasks), found automatically
                          │             search, downloads, library, series, wanted, scheduler, notify,
                          │             plex, renamer, nfo, subtitles, audiosync, convert, player, auth …
                          ├─ sources/   source plugins (WebShare, FastShare, Prowlarr, Jackett)
                          └─ clients/   Aria2, qBittorrent, TMDB, Plex, Groq, Gemini, OpenSubtitles, Wikidata
```

Modules do not import each other; they share the database and talk through events
(`download.completed`, `library.movie_updated`, …). A new feature is a new module:
`backend/app/modules/<name>/__init__.py` with `module = Module(...)` — routers, migrations, event
subscriptions, permissions and background tasks. The registry finds it, nothing else to register.

Backend image: Python 3.11, uv, a pinned static ffmpeg, MediaInfo, mkvtoolnix.

## Development

```bash
cd backend && uv run pytest        # tests
cd frontend && npx tsc --noEmit    # type check
```

## License

MIT
