# ARCHITECTURE.md — LibraryManagementSystem

> Quick-reference architecture map. Read `docs/FILE_MAP.md` first for file-level navigation, then this file for data flows and system design.
> Last updated: 2026-04-06

---

## System Overview

Three-tier desktop application built with Tauri 2.x:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Tauri Desktop Window                          │
│  ┌──────────────────────────────┐  ┌──────────────────────────┐ │
│  │   React Frontend (Vite)      │  │   Rust Audio Engine       │ │
│  │   localhost:5173             │  │   (CPAL + Symphonia)      │ │
│  │                              │  │                           │ │
│  │  ← HTTP/REST → FastAPI       │  │  ← Tauri IPC commands →   │ │
│  └──────────────────────────────┘  └──────────────────────────┘ │
│                    ↓ HTTP (localhost only)                        │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │           Python FastAPI Backend (Uvicorn)                    ││
│  │           localhost:8000                                      ││
│  └──────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## Directory Map

```
RB_Editor_Pro/
├── frontend/               # React + TypeScript frontend (Vite 7.x)
│   └── src/
│       ├── api/            # Axios HTTP client
│       ├── audio/          # DAW state machine + audio logic
│       ├── components/     # Reusable UI components (TODO: move views here)
│       ├── daw/            # DAW editor view (main editor)
│       ├── editor/         # Non-destructive editor components
│       └── *.jsx           # Top-level views (lazy-loaded tabs)
│
├── app/                    # Python FastAPI backend
│   ├── main.py             # FastAPI app, all route definitions (50+ routes)
│   ├── config.py           # Paths: Rekordbox root, dirs, FFmpeg
│   ├── services.py         # Core business logic classes
│   ├── database.py         # RekordboxXMLDB — XML-based library
│   ├── live_database.py    # LiveRekordboxDB — SQLite direct access
│   ├── audio_analyzer.py   # Async BPM/key analysis (librosa)
│   ├── analysis_engine.py  # Advanced frequency/beatgrid detection
│   ├── soundcloud_api.py   # SC unofficial API + sync engine
│   ├── soundcloud_downloader.py  # Track download + metadata
│   ├── usb_manager.py      # USB detection, sync engine, profiles
│   ├── rekordbox_export.py # Export to Rekordbox XML format
│   ├── rekordbox_bridge.py # Import/export bridge
│   ├── rbep_parser.py      # Parse/serialize .rbep project files
│   ├── xml_generator.py    # Generate DJ_PLAYLISTS XML
│   ├── batch_worker.py     # Async batch processing
│   └── sidecar.py          # Backend process management
│
├── src-tauri/              # Rust + Tauri desktop wrapper
│   └── src/
│       ├── main.rs         # App init, splashscreen, OAuth commands
│       ├── soundcloud_client.rs  # OAuth PKCE flow + callback server
│       └── audio/
│           ├── mod.rs      # Module exports
│           ├── engine.rs   # AudioEngine — file loading (Symphonia)
│           ├── playback.rs # PlaybackEngine — CPAL output
│           ├── commands.rs # Tauri IPC commands (load_audio, get_3band_waveform)
│           ├── analysis.rs # FFT waveform + BPM/key detection (RustFFT)
│           ├── export.rs   # Render audio to WAV/MP3
│           └── metadata.rs # ID3/FLAC/ALAC tag r/w (lofty)
│
├── .claude/
│   ├── agents/             # AI agent definitions (director, frontend, backend, rust, qa)
│   └── docs/               # Central index and architecture docs
│       ├── architecture.md # This file — system overview and data flows
│       ├── frontend-index.md # React component and module index
│       ├── backend-index.md  # FastAPI endpoint and module index
│       └── rust-index.md     # Rust/Tauri command and module index
│
├── docs/
│   ├── PROJECT_OVERVIEW.md
│   ├── FILE_MAP.md            # master file navigation
│   ├── architecture.md        # this file
│   ├── {frontend,backend,rust}-index.md  # symbol/endpoint indexes
│   └── research/              # open research topics, persistent across chats
│       ├── README.md          # how the research-log system works
│       ├── _TEMPLATE.md       # copy to start a new topic
│       ├── _INDEX.md          # living list of topics + status
│       └── <slug>.md          # one file per topic
├── scripts/                # Dev/build utility scripts
├── CLAUDE.md               # Claude Code configuration (this project's AI guide)
├── PROJECT_WIKI.md         # Detailed feature documentation
├── package.json            # Root: npm run dev:full, tauri commands
├── requirements.txt        # Python deps (FastAPI, librosa, sqlalchemy, keyring)
└── docker-compose.yml      # Local dev containers (backend + postgres)
```

---

## Key Data Flows

### 1. Library Loading
```
User opens app
  → Frontend: GET /api/library/tracks
  → app/database.py: RekordboxXMLDB.get_tracks()
    → Parse Pioneer Rekordbox XML (or SQLite in Live Mode)
  → Returns: [{id, title, artist, bpm, key, path, ...}]
  → Frontend: TrackTable.jsx renders sortable list
```

### 2. Audio Playback (Native)
```
User clicks track
  → Frontend: invoke('load_audio', {path})    [Tauri IPC]
  → src-tauri/audio/commands.rs: load_audio()
  → engine.rs: AudioEngine::load() — memory-mapped file
  → playback.rs: PlaybackEngine::play() — CPAL output stream
  → Emits progress events back to Frontend
```

### 3. Waveform Analysis
```
Track loaded
  → Frontend: invoke('get_3band_waveform', {path})
  → src-tauri/audio/analysis.rs: RustFFT computation
  → Returns: {low[], mid[], high[], peaks[]}    (3-band frequency data)
  → Frontend: DawTimeline.jsx / TimelineCanvas.jsx renders waveform
```

### 4. BPM/Key Analysis (Background)
```
Track imported
  → Frontend: POST /api/audio/analyze {path}
  → app/audio_analyzer.py: AudioAnalyzer.analyze_async()
    → ProcessPoolExecutor → librosa.beat.beat_track()
  → Frontend polls GET /api/audio/analyze/{task_id}
  → Result stored, displayed in TrackTable
```

### 5. Non-Destructive Editing (DAW)
```
User edits cues/beatgrid/envelope
  → DawState.js: dawReducer() — pure state update
  → DawTimeline.jsx: canvas re-renders (no file write)
  → User saves → POST /api/projects/save
  → app/services.py: ProjectManager.save()
    → rbep_parser.py: RbepSerializer — writes .rbep overlay file
  → Source audio file NEVER modified
```

### 6. SoundCloud Sync
```
User clicks "Connect SoundCloud"
  → Frontend: invoke('login_to_soundcloud')    [Tauri IPC]
  → src-tauri/main.rs: login_to_soundcloud()
  → soundcloud_client.rs: PKCE OAuth flow
    → get_auth_url() → open browser
    → wait_for_callback() → one-shot local HTTP server
    → exchange_code_for_token() → returns token string
  → Frontend receives token → POST /api/soundcloud/auth-token
  → app/main.py stores token in session

User loads SC playlists
  → Frontend: GET /api/soundcloud/playlists
  → app/soundcloud_api.py: SoundCloudPlaylistAPI.fetch_playlists()

User previews matches
  → Frontend: POST /api/soundcloud/preview-matches
  → SoundCloudSyncEngine.match_to_library() → fuzzy title/artist matching
  → Matches shown in SoundCloudSyncView.jsx inspector panel

User confirms sync + download
  → POST /api/soundcloud/sync (marks matches in library)
  → POST /api/soundcloud/download (triggers yt-dlp download per track)
  → Frontend polls GET /api/soundcloud/task/{task_id} for progress
```

### 7. USB Sync
```
User clicks sync to USB
  → Frontend: GET /api/usb/detect
  → app/usb_manager.py: UsbDetector.scan_drives()
  → User selects drive + playlists
  → POST /api/usb/sync {drive, playlists, profile}
  → UsbSyncEngine.run_sync()
    → Lock file prevents concurrent syncs
    → Incremental diff — only copies changed tracks
    → Updates USB Rekordbox library DB
  → Progress streamed back via SSE or polling
```

The library DB (``master.db``) is not backed up by this app — Rekordbox
itself maintains versioned copies in its install directory. If a user
needs to revert in-app edits, they restore from Rekordbox.

---

## Security Architecture

| Layer | Mechanism |
|-------|-----------|
| API auth | `Authorization: Bearer <token>` on all 84 mutation routes (`require_session` dep in `app/auth.py`). Heartbeat ungated. Phase-1 details below. |
| CORS | Locked to `localhost` + `tauri://localhost` origins only |
| File access | `ALLOWED_AUDIO_ROOTS` sandbox — all paths validated via `Path.is_relative_to(resolved_root)` before I/O |
| Secrets | SoundCloud tokens stored in OS keyring; sidecar session token in `%APPDATA%/MusicLibraryManager/.session-token` (user-data dir, never in repo) |
| Client ID | SC client ID scraped dynamically or from `.env` |
| Tauri | `capabilities/main.json` — minimal permission grants; sidecar stdout reader captures + scrubs `LMS_TOKEN=` banner before log-forwarding |
| SQL | SQLAlchemy ORM or parameterized queries — no string interpolation |

### 8. Bearer Auth Bootstrap (Phase 1 — landed 2026-05-17)

Source of truth: `app/auth.py`. Draftplan: `docs/research/implement/draftplan_security-api-auth-hardening.md`.

```
[Python sidecar boot]
  app/auth.py at import time:
    SESSION_TOKEN = secrets.token_urlsafe(32)
    sys.stdout.write("LMS_TOKEN=<value>\n")           ← first stdout line
    %APPDATA%/MusicLibraryManager/.session-token      ← persisted (user-data dir)
        │
        ├── Tauri prod (shell.sidecar("rb-backend"))
        │     src-tauri/src/main.rs:472 CommandEvent::Stdout
        │     → capture_token_line() strips "LMS_TOKEN=" prefix
        │     → stores in tauri::State<SessionToken> (Arc<Mutex<String>>)
        │     → DROPS the line (never forwarded to log::info!)
        │
        ├── Tauri dev (spawn_dev_backend → spawn_child)
        │     src-tauri/src/main.rs:138 same logic on BufReader::lines()
        │
        └── Browser dev (`npm run dev:full`)
              frontend/vite.config.js dev-middleware
              → on vite startup: reads %APPDATA%/.../.session-token
              → re-exposes content at GET /dev-token

[React frontend bootstrap]
  frontend/src/api/api.js (top-level bootstrap promise):
    if (window.__TAURI_INTERNALS__) {
        token = await invoke('get_session_token')  ← retry loop on "token-not-ready"
    } else {
        token = await fetch('/dev-token').text()
    }
    setSessionToken(token)
    [if both fail → toast + authStore._authBootstrapFailed = true]

[Every HTTP request thereafter]
  axios interceptor:
    config.headers['Authorization'] = `Bearer ${getSessionToken()}`
        │
        ▼
  FastAPI route with dependencies=[Depends(require_session)]:
    app/auth.py require_session():
        split scheme on whitespace, lowercase compare to "bearer"
        strip + reject empty / control-char / wrong-length
        secrets.compare_digest(candidate, SESSION_TOKEN)
        on mismatch: HTTPException(401, "Unauthorized")
```

**Token rotation:** only on sidecar process restart. Tauri spawns one sidecar per app lifetime; force-rotate = `POST /api/system/restart`. Phase 2 paired-device tokens have separate per-device lifecycle.

---

## Performance Characteristics

| Operation | Mechanism | Latency |
|-----------|-----------|---------|
| Library load (10k tracks) | In-memory XML cache | ~200ms first load, <5ms cached |
| Waveform render | Rust FFT (RustFFT) | ~50ms for 5min track |
| BPM analysis | Python ProcessPool (librosa) | ~2-8s per track (background) |
| Audio playback start | Memory-mapped file + CPAL | <50ms |
| USB sync (1000 tracks) | Incremental diff | minutes (I/O bound) |
| Backup commit | Compressed JSON diff | <500ms for typical change |

---

## External Dependencies (Key)

| Tool | Purpose | Location |
|------|---------|----------|
| FFmpeg | Audio conversion, export | `app/config.py` → binary path |
| Pioneer Rekordbox | Library database (read/write) | `REKORDBOX_ROOT` in config |
| SoundCloud API (v2) | Unofficial API, no official SDK | `app/soundcloud_api.py` |
| librosa | Audio analysis (BPM, key) | Python, runs in ProcessPool |
| CPAL | Cross-platform audio output | Rust crate |
| Symphonia | Audio decoding (MP3, FLAC, WAV) | Rust crate |
| RustFFT | FFT computation for waveform | Rust crate |
| lofty | Audio metadata (ID3, FLAC tags) | Rust crate |
