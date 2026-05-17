# app/ INDEX — Python Backend

> Module and endpoint map for the FastAPI backend. Update when adding/removing endpoints or modules.
> Last updated: 2026-05-17

---

## Entry Point & Server Config (`app/main.py`)

FastAPI app (~4000 lines). Security: CORS locked to localhost, `validate_audio_path()` enforces `ALLOWED_AUDIO_ROOTS` sandbox on all file I/O. **Phase-1 Bearer-token auth** (commits `1c7d410..f90f5f8` + `8498937`): every mutation route (POST/PUT/PATCH/DELETE) carries `dependencies=[Depends(require_session)]` from `app/auth.py`. Token is self-generated at sidecar boot (`secrets.token_urlsafe(32)`), printed once as `LMS_TOKEN=<value>` on stdout (captured + scrubbed by Tauri Rust supervisor), and persisted to `%APPDATA%/MusicLibraryManager/.session-token` for the browser-dev fallback. Clients must send `Authorization: Bearer <token>` on every gated request — see `app/auth.py:require_session`. 84/85 mutation routes gated; `POST /api/system/heartbeat` is the only intentional exception (unauth healthcheck). **Phase-1 rate-limit** (`app/rate_limit.py`, commit `e78fb24`): the 3 HIGH-tier routes (shutdown / restart / sc-auth-token) additionally carry `@rate_limit(steady=5.0, burst=10, key_mode="both")`.

Marker convention in the route tables below:
- `[AUTH]` — requires `Authorization: Bearer <SESSION_TOKEN>`. Missing/wrong → 401.
- `[RL]` — gated by `@rate_limit` in addition to `[AUTH]`. Excess → 429 with `Retry-After` header.
- Unmarked rows are open (read-only or heartbeat).

### Library Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/library/tracks` | All tracks with full metadata (XML or live DB depending on mode) |
| GET | `/api/playlist/{pid}/tracks` | Tracks in a specific playlist |
| GET | `/api/track/{tid}` | Single track full detail |
| GET | `/api/track/{tid}/cues` | Hot cues + memory cues for a track |
| GET | `/api/track/{tid}/beatgrid` | Beatgrid data for a track |
| POST | `/api/track/{tid}` `[AUTH]` | Update track metadata fields |
| DELETE | `/api/track/{tid}` `[AUTH]` | Delete a track |
| POST | `/api/track/delete` `[AUTH]` | Delete track by body param |
| POST | `/api/track/cues/save` `[AUTH]` | Save cue points for a track |
| POST | `/api/track/grid/save` `[AUTH]` | Save beatgrid for a track |
| PATCH | `/api/tracks/batch` `[AUTH]` | Batch update metadata on multiple tracks |
| POST | `/api/tracks/move` `[AUTH]` | Move tracks between playlists |
| GET | `/api/artists` | All artists (normalized) |
| GET | `/api/artist/{aid}/tracks` | Tracks by artist |
| GET | `/api/genres` | All genres |
| GET | `/api/labels` | All labels |
| GET | `/api/albums` | All albums |
| GET | `/api/label/{aid}/tracks` | Tracks by label |
| GET | `/api/album/{aid}/tracks` | Tracks by album |
| GET | `/api/artwork` | Serve artwork image for a track (path param) |

### Playlist Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/playlists/tree` | Full playlist tree (nested folders + playlists) |
| POST | `/api/playlists/create` `[AUTH]` | Create new playlist |
| POST | `/api/playlists/rename` `[AUTH]` | Rename playlist |
| POST | `/api/playlists/move` `[AUTH]` | Move playlist in tree |
| POST | `/api/playlists/delete` `[AUTH]` | Delete playlist |
| POST | `/api/playlists/add-track` `[AUTH]` | Add track to playlist |
| POST | `/api/playlists/remove-track` `[AUTH]` | Remove track from playlist |
| POST | `/api/playlists/reorder` `[AUTH]` | Reorder tracks within playlist |
| POST | `/api/playlists/smart/create` `[AUTH]` | Create smart-playlist with rules |
| POST | `/api/playlists/smart/update` `[AUTH]` | Update smart-playlist rules |
| GET | `/api/playlists/smart/{pid}/evaluate` | Re-evaluate smart-playlist against current library |
| POST | `/api/playlists/folder/create` `[AUTH]` | Create folder node in playlist tree |

### Audio Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream` | Stream audio file (range-request, sandboxed, query param `path`) |
| GET | `/api/audio/stream` | Alias for stream endpoint |
| GET | `/api/audio/waveform` | Multiband waveform data (pps param controls points-per-second) |
| POST | `/api/audio/analyze` `[AUTH]` | Start async BPM/key analysis → returns `{ task_id }` |
| GET | `/api/audio/analyze/{task_id}` | Poll analysis result: `{ status, bpm, key, confidence }` |
| POST | `/api/audio/slice` `[AUTH]` | Slice audio file at beat positions |
| POST | `/api/audio/render` `[AUTH]` | Render audio with FFmpeg (format conversion, export) |
| POST | `/api/audio/import` `[AUTH]` | Import a local audio file into the library |
| POST | `/api/track/{tid}/analyze` `[AUTH]` | Full analysis pipeline on single track |
| POST | `/api/track/{tid}/analyze-full` `[AUTH]` | Analyze + write ANLZ files + update master.db in one call. Body: `{ force: bool }`. Requires live DB mode, Rekordbox must be closed |
| POST | `/api/library/analyze-batch` `[AUTH]` | Batch analyze multiple tracks; NDJSON streaming progress. Body: `{ track_ids: [int], force: bool }` |
| GET | `/api/library/analyze-status` | Report analysis engine capabilities + count of unanalyzed tracks |

### Project (DAW / RBEP) Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects` | List `.rbep` project files |
| POST | `/api/projects/save` `[AUTH]` | Save non-destructive edit project to `.rbep` |
| GET | `/api/projects/{name}` | Load and parse a specific `.rbep` project |
| GET | `/api/projects/rbep/list` | List available `.rbep` files |
| GET | `/api/projects/rbep/{name}` | Get parsed content of a `.rbep` project |

### Library Management Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/library/status` | Library load state, mode (XML/live), path |
| POST | `/api/library/mode` `[AUTH]` | Switch between XML and live (SQLite) mode |
| POST | `/api/library/load` `[AUTH]` | Load a Rekordbox XML file into memory |
| POST | `/api/library/unload` `[AUTH]` | Unload current library |
| POST | `/api/library/new` `[AUTH]` | Create a new empty library |
| POST | `/api/library/sync` `[AUTH]` | Persist current state (XML mode writes rekordbox.xml; live mode is a no-op) |
| POST | `/api/library/smart-playlists` `[AUTH]` | Generate smart playlists from rules |
| POST | `/api/library/scan-folder` `[AUTH]` | Background scan of a directory; auto-imports audio files not yet in library. Body: `{ path: str }` |
| POST | `/api/library/import-paths` `[AUTH]` | Bulk-import a list of explicit file paths |
| POST | `/api/library/clean-titles` `[AUTH]` | Clean track title strings (remove tags, fix encoding) |
| GET | `/api/library/folder-watcher/status` | Report active folder-watcher state |
| POST | `/api/library/folder-watcher/add` `[AUTH]` | Register a folder for auto-watch + auto-import |
| POST | `/api/library/folder-watcher/remove` `[AUTH]` | Stop watching a folder |
| GET | `/api/import/tasks` | List pending/completed import tasks |
| POST | `/api/import/tasks/clear` `[AUTH]` | Clear completed import tasks |
| POST | `/api/debug/load_xml` `[AUTH]` | Debug endpoint: reload XML from disk |

### Rekordbox XML Endpoints
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/xml/clean` `[AUTH]` | Clean Rekordbox XML (strip tags, fix encoding, normalize) |
| POST | `/api/rekordbox/export` `[AUTH]` | Export selected tracks to Rekordbox-compatible XML |
| POST | `/api/rekordbox/import` `[AUTH]` | Import tracks from a Rekordbox XML export |
| POST | `/api/file/write` `[AUTH]` | Write raw XML content to a file (sandboxed) |
| POST | `/api/file/reveal` `[AUTH]` | Open OS file-manager at the given sandboxed path |

### Insights Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/insights/low_quality` | Tracks below bitrate/quality threshold |
| GET | `/api/insights/no_artwork` | Tracks missing embedded artwork |
| GET | `/api/insights/lost` | Tracks whose file paths no longer exist on disk |

### Tools Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tools/duplicates` | Find duplicate tracks (title + artist matching) |
| POST | `/api/tools/duplicates/merge` `[AUTH]` | Merge a set of duplicate tracks |
| POST | `/api/tools/duplicates/merge-all` `[AUTH]` | Auto-merge all detected duplicates |
| POST | `/api/tools/rename` `[AUTH]` | Rename tracks (not supported in XML-only mode) |
| POST | `/api/tools/batch-comment` `[AUTH]` | Batch update comments on multiple tracks |
| POST | `/api/metadata/merge` `[AUTH]` | Merge metadata from one track into another |

### SoundCloud Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/soundcloud/playlists` | Fetch SC playlists for authenticated user |
| GET | `/api/soundcloud/me` | Get authenticated SC user profile |
| POST | `/api/soundcloud/auth-token` `[AUTH]` `[RL]` | Store SC OAuth token (called after Tauri OAuth flow). Rate-limited: 5 req/min steady, burst 10, `key_mode="both"`. |
| GET | `/api/soundcloud/settings` | Get SC-specific settings (target folder, etc.) |
| PUT | `/api/soundcloud/settings` `[AUTH]` | Update SC settings |
| POST | `/api/soundcloud/preview-matches` `[AUTH]` | Preview library matches for SC playlist tracks (dry run) |
| POST | `/api/soundcloud/sync` `[AUTH]` | Run SC playlist sync (match + mark) |
| POST | `/api/soundcloud/sync-all` `[AUTH]` | Sync all SC playlists at once |
| POST | `/api/soundcloud/merge` `[AUTH]` | Merge SC playlist into local playlist |
| POST | `/api/soundcloud/download` `[AUTH]` | Start legal download: body `ScDownloadRequest`. Accepts URL or pre-resolved track data. Rejects if `downloadable=false`. Uses official SC API only. Returns `{task_id}`. |
| POST | `/api/soundcloud/download-playlist` `[AUTH]` | Bulk-download every track in an SC playlist |
| GET | `/api/soundcloud/tasks` | List all active download tasks |
| GET | `/api/soundcloud/task/{task_id}` | Poll status of a download task |
| GET | `/api/soundcloud/history` | Paginated analysis history log. Params: `limit`, `offset`, `status`, `device_id`, `search`, `this_device_only` |
| GET | `/api/soundcloud/history/stats` | Aggregate stats: total/analyzed/failed counts, device count, date range |
| GET | `/api/soundcloud/check/{sc_track_id}` | O(1) dedup check — returns `{already_downloaded: bool}` |
| DELETE | `/api/soundcloud/history/{sc_track_id}` `[AUTH]` | Remove registry entry (to allow re-download). Does NOT delete the file. |
| POST | `/api/artist/soundcloud` `[AUTH]` | Associate artist with SC profile |

### USB Sync Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/usb/devices` | Scan drives for Rekordbox USB libraries |
| GET | `/api/usb/profiles` | List all sync profiles |
| POST | `/api/usb/profiles` `[AUTH]` | Save/update a sync profile |
| DELETE | `/api/usb/profiles/{device_id}` `[AUTH]` | Delete a sync profile |
| POST | `/api/usb/profiles/prune` `[AUTH]` | Drop sync profiles for devices no longer present |
| GET | `/api/usb/{device_id}/contents` | List tracks/playlists on USB device |
| GET | `/api/usb/diff/{device_id}` | Calculate diff: what would change in a sync |
| POST | `/api/usb/sync` `[AUTH]` | Run USB sync for specific playlists (long-running, no timeout — can take minutes for large libraries) |
| POST | `/api/usb/sync/all` `[AUTH]` | Sync entire local library to USB |
| POST | `/api/usb/eject` `[AUTH]` | Eject a USB device |
| POST | `/api/usb/reset` `[AUTH]` | Reset USB library to factory state |
| POST | `/api/usb/initialize` `[AUTH]` | Initialize a drive as a Rekordbox USB |
| POST | `/api/usb/rename` `[AUTH]` | Rename a USB device profile |
| GET | `/api/usb/settings` | Get USB sync global settings |
| POST | `/api/usb/settings` `[AUTH]` | Update USB sync global settings |
| POST | `/api/usb/history` `[AUTH]` | Append a sync-history entry |
| GET | `/api/usb/mysettings/schema` | Hardware-settings field schema (dropdowns/enums) |
| GET | `/api/usb/mysettings/{device_id}` | Read CDJ/DJM settings from a USB device |
| POST | `/api/usb/mysettings` `[AUTH]` | Write CDJ/DJM settings to a USB device |
| POST | `/api/usb/format/preview` `[AUTH]` | Dry-run preview of a USB format operation |
| POST | `/api/usb/format/confirm` `[AUTH]` | Execute a USB format operation |

### System Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Load user settings (JSON) |
| POST | `/api/settings` `[AUTH]` | Save user settings |
| POST | `/api/system/heartbeat` | Keep-alive ping (called on startup). **Intentional Phase-1 exception** — the only unauth'd mutation endpoint. No body, no token leak (the old `SHUTDOWN_TOKEN` scheme was deleted in commit `7dfdef5`). |
| POST | `/api/system/shutdown` `[AUTH]` `[RL]` | Graceful server shutdown. Rate-limited: 5 req/min steady, burst 10, `key_mode="both"` (per-IP + per-bearer). |
| POST | `/api/system/restart` `[AUTH]` `[RL]` | Restart the backend process. Rate-limited: 5 req/min steady, burst 10, `key_mode="both"`. |
| POST | `/api/system/select_db` `[AUTH]` | Open file dialog to select Rekordbox DB |

---

## Core Config (`app/config.py`)

| Variable | Value | Description |
|----------|-------|-------------|
| `REKORDBOX_ROOT` | `%APPDATA%/Pioneer/rekordbox` | Pioneer Rekordbox database directory |
| `DB_FILENAME` | `master.db` | Rekordbox SQLite database filename |
| `FFMPEG_BIN` | `ffmpeg` | FFmpeg binary (must be in PATH) |
| `EXPORT_DIR` | `./exports` | Audio export output |
| `LOG_DIR` | `./logs` | Application log files |
| `TEMP_DIR` | `./temp_uploads` | Temporary file processing |
| `MUSIC_DIR` | `./music` | Default music library root |
| `DB_KEY` | env: `REKORDBOX_DB_KEY` | Rekordbox SQLite encryption key |

`validate_audio_path(path_str)` in `main.py` — validates any file path against `ALLOWED_AUDIO_ROOTS` before I/O. Raises `ValueError` on sandbox violation.

---

## Business Logic Modules (`app/services.py`)

All classes are instantiated on import; most methods are instance methods unless noted.

| Class | Key Methods | Purpose |
|-------|------------|---------|
| `XMLProcessor` | `clean_xml(path)`, `create_artist_playlists()`, `create_label_playlists()` | Clean/transform Rekordbox XML (strip tags, normalize, create derived playlists) |
| `SystemGuard` | `is_rekordbox_running() → bool` | Check if Rekordbox process is running before writes |
| `AudioEngine` | `convert(src, dst, format)`, `render(params)`, `slice(path, positions)` | FFmpeg wrapper — format conversion, render exports, slice at beat positions |
| `FileManager` | `copy(src, dst)`, `delete(path)`, `compress(path)` | File I/O operations (copy/delete/compress) |
| `LibraryTools` | `extract_metadata(path)`, `batch_update(ids, fields)` | Track metadata extraction, batch metadata operations |
| `SettingsManager` | `load() → dict`, `save(settings)` | User preferences persistence (JSON file) |
| `MetadataManager` | `read(path)`, `write(path, fields)` | Read/write embedded audio metadata tags |
| `BeatAnalyzer` | `analyze(path) → {bpm, key, confidence}` | BPM/key analysis wrapper (delegates to `AnalysisEngine`) |
| `ImportManager` | `import_file(path)`, `import_url(url)` | Import tracks from filesystem or URL into library |
| `ProjectManager` | `save(name, state)`, `load(name) → state`, `list() → [names]`, `delete(name)` | `.rbep` non-destructive edit project CRUD |

---

## Database Modules

### `app/database.py` — `RekordboxXMLDB`
XML-based library (default mode). In-memory dict cache for fast lookups. Also contains `RekordboxDB` for direct SQLite via rbox.

**Concurrency**: the module-level `_db_write_lock` (a `threading.RLock`) serialises every mutating method on `RekordboxDB` against concurrent FastAPI request threads. Each mutation is wrapped at module load via a `@_serialised` decorator pass over `RekordboxDB` (see the loop just above `db = RekordboxDB()`). Routes that need to chain several mutations atomically should use the `db_lock()` context manager (exposed from this module) instead of calling individual methods in sequence — RLock is reentrant so nested acquires from within an already-wrapped method are safe.

| Method | Returns | Description |
|--------|---------|-------------|
| `load_xml(path)` | None | Parse XML into memory cache |
| `get_tracks(filters?)` | `[Track]` | All tracks, optionally filtered |
| `get_playlists()` | `[Playlist]` | All playlists with track ID lists |
| `get_playlist_tracks(pid)` | `[Track]` | Tracks in a specific playlist |
| `get_track_details(tid)` | `Track` | Single track by ID |
| `get_all_labels()` / `get_all_albums()` | `[str]` | Label/album lists for filtering |
| `get_tracks_by_artist/label/album(id)` | `[Track]` | Filter tracks by grouping |
| `add_track(track_data)` | `str` (new ID) | Add track to XML library |
| `delete_track(tid)` | `bool` | Remove track from XML |
| `save_xml()` | None | Write current state back to XML file |
| `save_track_cues(tid, cues)` | `bool` | Persist cue points |
| `save_track_beatgrid(tid, grid)` | `bool` | Persist beatgrid |
| `get_track_cues(tid)` | `[Cue]` | Read stored cues |
| `get_track_beatgrid(tid)` | `[Beat]` | Read stored beatgrid |
| `update_tracks_metadata(ids, fields)` | `bool` | Batch metadata update |

### `app/live_database.py` — `LiveRekordboxDB`
Direct SQLite access (live mode). Thread-safe via locks. Auto-backup on write.

| Method | Description |
|--------|-------------|
| `get_tracks()` | Query `master.db` directly |
| `update_track(id, fields)` | Write to SQLite (**requires Rekordbox to be closed**) |
| `get_analysis_writer()` | Lazy-create and return `AnalysisDBWriter` instance bound to this DB |
| `get_unanalyzed_track_ids()` | Return list of track IDs with BPM=0 (not yet analyzed) |
| `_load_beatgrids_from_anlz()` | Batch-loads PQTZ beatgrids via `SafeAnlzParser.load_all_beatgrids` (subprocess-isolated, bisecting). Runs on a daemon thread spawned by `_start_beatgrid_background_load` after `loaded=True` so a slow / panicking rbox call cannot block library init |
| `_start_beatgrid_background_load()` | Spawns the `anlz-beatgrid-loader` daemon thread that runs `_load_beatgrids_from_anlz` out of band |

### `app/anlz_safe.py` — `SafeAnlzParser`
Process-isolated wrapper around `rbox.MasterDb` + `rbox.Anlz`. Defends
against known panics in `rbox` 0.1.5 (`masterdb/database.rs:1162`
`unwrap()` on `None`) that abort the whole Python process on malformed
ANLZ rows. The panic site is in `MasterDb` itself, so even a plain
`get_content_anlz_paths(tid)` call can crash the backend — the
subprocess therefore handles **the entire iteration**, not just
`rbox.Anlz()`.

| Layer | Purpose |
|-------|---------|
| `_validate_anlz_header(path)` | Fast pre-check: file size ≥ 28 bytes + `PMAI` magic. Rejects truncated / non-ANLZ files before rbox sees them. |
| `ProcessPoolExecutor(max_workers=1)` | All rbox calls run in a subprocess. A panic kills only the worker; parent respawns it (`BrokenExecutor` handler). |
| Batch + bisect | `load_all_beatgrids` chunks tracks (default 500/chunk). On worker crash the chunk is bisected to identify and blacklist the offending track id (~log₂N restarts). |
| `_bad_ids` cache | Track ids that panic / time out / fail header check are skipped for the rest of the session. |
| `PER_CHUNK_TIMEOUT_S = 60.0` | Hung worker is killed and respawned; that chunk is dropped (no bisect — bisecting a hang would just hang again). |
| `MAX_PANICS_PER_RUN = 32` | Hard ceiling on subprocess restarts to protect against pathological DBs where every other row panics. |

| Method | Returns | Description |
|--------|---------|-------------|
| `load_all_beatgrids(db_path, track_ids, chunk_size=500)` | `dict[str, list[dict]]` | Batch-load PQTZ for every requested track id. Worker opens its own MasterDb. Used by `LiveRekordboxDB._load_beatgrids_from_anlz`. |
| `parse_pqtz(track_id, dat_path)` | `list[dict] \| None` | Single-file PQTZ extract for ad-hoc lookups; `None` on any failure. |
| `stats` | `dict` | `{bad_ids, panics, worker_alive}` for diagnostics. |
| `shutdown()` | None | Tear down the worker (idempotent). |

**Load flow**: `LiveRekordboxDB.load()` finishes synchronous init
(metadata, tracks, playlists, cues), marks `loaded=True`, and **then**
spawns a daemon thread (`_start_beatgrid_background_load`) that calls
`_load_beatgrids_from_anlz` → `SafeAnlzParser.load_all_beatgrids`. The
UI sees tracks immediately; beatgrids fill in within seconds without
ever blocking the load path.

---

## Analysis Engine (`app/analysis_engine.py`) — `AnalysisEngine`

Async DSP pipeline using `ProcessPoolExecutor`. All class methods (no instance needed).

| Method | Returns | Description |
|--------|---------|-------------|
| `AnalysisEngine.submit(task_id, file_path)` | `{ task_id, status: "pending" }` | Queue a track for analysis in background pool |
| `AnalysisEngine.get_status(task_id)` | `{ status, bpm, key, confidence, beatgrid }` | Poll task result. status: `pending|done|error` |
| `AnalysisEngine.analyze_sync(file_path)` | `{ bpm, key, confidence, beatgrid, anlz }` | Synchronous analysis (blocks; use for scripting only) |
| `run_full_analysis(file_path)` | full result dict | Top-level free function — single call returns everything needed for ANLZ writing |

Algorithms: madmom RNN beat tracking (librosa fallback), essentia KeyExtractor (K-S/Temperley ensemble fallback), 3-band Butterworth crossover (200Hz/2500Hz) for waveform generation.

Waveform outputs: PWAV (400 mono preview), PWV2 (100 tiny), PWV3 (detail@150fps), PWV4 (1200×6 color preview), PWV5 (color detail u16), PWV6 (1200×[lo,mi,hi] 3-band preview), PWV7 (N×[lo,mi,hi] 3-band detail@150fps).

---

## ANLZ Writer (`app/anlz_writer.py`)

Writes Rekordbox-compatible binary ANLZ files (.DAT, .EXT, .2EX). All output is validated by rbox.

| Function | Description |
|----------|-------------|
| `write_anlz_files(anlz_dir, track_path, analysis_result)` | Main entry point — writes all 3 files from `run_full_analysis()` output. Returns `{"dat": path, "ext": path, "2ex": path}` |
| `build_dat(track_path, beats, pvbr, pwav, pwv2)` | Build .DAT bytes: PPTH + PVBR + PQTZ + PWAV + PWV2 + PCOB×2 |
| `build_ext(track_path, beats, pwv3, pwv5, pwv4)` | Build .EXT bytes: PPTH + PWV3 + PCOB×2 + PCO2×2 + PWV5 + PWV4 |
| `build_2ex(track_path, pwv7, pwv6)` | Build .2EX bytes: PPTH + PWV7 + PWV6 + PWVC |

**Tag formats** (all big-endian, rbox-validated):
- PQTZ entries: `[beat_number(u16), tempo×100(u16), time_ms(u32)]`
- PCOB: 24-byte header with `0xFFFFFFFF` sentinel
- PCO2: `list_type` is u32 (not u16)
- PWV5: header field +20 must be `0x00960305`
- PWV6: hdr_len=20, `entry_size(u32)=3` before `entry_count`
- PWV7: hdr_len=24, `entry_size(u32)=3`, data is `[lo, mid, hi]` per entry

---

## Analysis-to-DB Orchestrator (`app/analysis_db_writer.py`) — `AnalysisDBWriter`

Ties analysis → ANLZ files → master.db in a single pipeline. **Requires live DB mode and Rekordbox closed.**

| Method | Description |
|--------|-------------|
| `analyze_and_save(track_id, force=False)` | Full pipeline for single track: analyze → write ANLZ → update BPM/key/analysed in master.db |
| `analyze_batch(track_ids, force=False)` | Generator — yields `{track_id, status, progress, bpm, key, error}` dicts as each track completes |
| `get_unanalyzed_tracks()` | Returns list of track dicts with `bpm=0` or missing ANLZ files |

---

## SoundCloud (`app/soundcloud_api.py`)

| Class / Exception | Purpose |
|------------------|---------|
| `SoundCloudPlaylistAPI` | SC unofficial v2 API client: `fetch_playlists(token)`, `fetch_likes(token)`, `get_me(token)`. Handles dynamic `client_id` scraping, Cloudflare fallback, exponential backoff on 429, full pagination |
| `SoundCloudSyncEngine` | `match_to_library(sc_tracks, local_tracks) → [MatchResult]` — fuzzy title/artist matching (jaro-winkler), duration tiebreaker, confidence scoring |
| `AuthExpiredError` | Raised when SC token is expired → frontend should trigger re-auth via `login_to_soundcloud` Tauri command |
| `RateLimitError` | Raised on 429 → includes `retry_after` seconds |

---

## Download Registry (`app/download_registry.py`)

SQLite-based deduplication + analysis history log. DB at `{MUSIC_DIR}/download_registry.db`.

| Function | Returns | Description |
|----------|---------|-------------|
| `init_registry()` | None | Create schema (call once on startup) |
| `is_already_downloaded(sc_track_id)` | bool | O(1) dedup check by SC track ID |
| `find_by_hash(sha256)` | `dict \| None` | Content-based dedup by SHA-256 |
| `register_download(**kwargs)` | bool | UPSERT download record (idempotent) |
| `update_analysis(**kwargs)` | bool | Store BPM/key results, mark 'analyzed' |
| `mark_failed(sc_track_id, error)` | None | Mark as permanently failed |
| `delete_entry(sc_track_id)` | bool | Remove entry (allows re-download) |
| `get_history(**filters)` | `[dict]` | Paginated history, newest-first |
| `get_stats()` | dict | Aggregate counts + date range |
| `get_current_device_id()` | str | Stable UUID for this machine |
| `compute_sha256(path)` | `str \| None` | Stream-hash a file for content dedup |

Device ID: stored as `{MUSIC_DIR}/.rb_device_id`. Not hardware-tied.

---

## SoundCloud Downloader (`app/soundcloud_downloader.py`)

Dedup-aware downloader with two-stage acquisition — same legal surface as the
SC web player itself.

**Acquisition order:**
1. **Official `/tracks/{id}/download`** (preferred) — used when `downloadable=True`,
   returns the creator's original upload.
2. **Fallback: v2 `media.transcodings[]`** — signed CDN stream same as web-player:
   - `progressive` → direct HTTP stream (MP3)
   - `hls` → ffmpeg `-c copy` mux to `.m4a` (no re-encode)

**Legal boundaries** (enforced in `_resolve_stream_via_transcodings`):
- Skip `snipped: true` transcodings (= 30s paywall previews)
- Skip on 401/403 from transcoding-signing (no access)
- Skip when `transcodings[]` empty (private/removed)
- Only use `hq` (Go+) when SC itself returns it — never probe for paid quality
- HLS mux uses `-c copy` (repack only, no re-encode, no DRM touched)

**Download flow:**
1. Dedup gate: reject if `is_already_downloaded(sc_track_id)` → True
2. Register as 'downloading' in registry (blocks parallel duplicates)
3. Resolve source URL (official → transcodings fallback)
4. Download by protocol (progressive=`_stream_file_to_temp`, hls=`_download_hls_to_temp`)
5. Move to `SoundCloud/{Artist}/{Title}.ext`
6. SHA-256 hash → content dedup check
7. Register as 'downloaded'
8. Background thread: analyze (BPM/key) → import → auto-sort into SC playlist

| Method / Function | Description |
|-------------------|-------------|
| `download_track(**kwargs)` | Start download; returns task_id immediately |
| `get_task_status(task_id)` | Returns task dict or None |
| `cleanup_processes()` | atexit hook (no-op; no subprocesses held) |
| `_resolve_official_download_url(id, token)` | v1 `/tracks/{id}/download` resolver |
| `_resolve_stream_via_transcodings(id, token)` | v2 transcodings[] resolver with legal gates |
| `_stream_file_to_temp(url, token)` | Progressive HTTP download to temp |
| `_download_hls_to_temp(m3u8, token, mime)` | HLS segment download + ffmpeg `-c copy` mux |

---

## USB Manager (`app/usb_manager.py`)

| Class | Key Methods | Purpose |
|-------|------------|---------|
| `UsbDetector` | `scan() → [Device]`, `is_rekordbox_usb(drive)`, `initialize_usb(drive)` | Detect and validate Rekordbox USB drives, initialize new drives |
| `UsbProfileManager` | `get_profiles()`, `get_profile(id)`, `save_profile(p)`, `delete_profile(id)`, `get_settings()`, `save_settings(s)`, `get_usb_contents(id)` | Persistent sync profile CRUD (which playlists → which drive) |
| `UsbSyncEngine` | `calculate_diff()`, `sync_collection(profile)`, `sync_playlists(profile, ids)`, `sync_metadata(profile)`, `_sync_library_legacy(profile, ids)` | Incremental sync with lock-file concurrency, diff calculation, XML export w/ control-char sanitization |

**`UsbSyncEngine` notes**:
- Drive letter normalization: bare `"E:"` → `"E:\\"` to ensure absolute paths in file URLs
- XML safety: `_xml_safe()` strips ASCII control chars (0x00-0x1F except tab/LF/CR) from Rekordbox metadata before writing — prevents ElementTree from writing unparseable XML
- Filename safety: `_clean_filename()` strips trailing dots/spaces and rejects Windows reserved names (CON/PRN/AUX/NUL/COM1-9/LPT1-9). Without this, Windows silently drops trailing dots at mkdir time and later writes to the requested path fail with ENOENT.
- Copy-error classification: only real disconnects — `winerror` in `(21, 31, 1167, 3)` OR `self.usb_root.exists()` returning False — abort the batch. Plain ENOENT on a single bad path is logged and skipped so one bad track doesn't kill a 3000-track sync.
- Uses file lock (`.rbep_sync_lock`) to prevent concurrent syncs
- All sync methods are generators: yield `{ stage, message, progress }` events; `stage="complete"` or `stage="error"` on finish

---

## Other Modules

| Module | Class / Function | Purpose |
|--------|-----------------|---------|
| `app/rbep_parser.py` | `RbepSerializer` | Parse + serialize `.rbep` XML: volume envelopes, hot cues, memory cues, beatgrids, tempo maps. Entry points: `parse_project(path)`, `serialize(state, path)` |
| `app/rekordbox_export.py` | `export_track_to_xml(analysis_result)` | Convert `AnalysisEngine` output → Rekordbox XML `TRACK` element with `TEMPO` nodes and `POSITION_MARK` cues |
| `app/rekordbox_bridge.py` | `export_selection(track_ids, dest_path)`, `import_xml(src_path)` | High-level bridge: export selected tracks → XML; import from XML |
| `app/xml_generator.py` | `RekordboxXML` | Generate valid `DJ_PLAYLISTS` XML: `add_track(track)`, `add_playlist(name, tracks)`, `write(path)` |
| `app/sidecar.py` | `SidecarStorage` | Persist artist metadata (SC links, custom fields) in `app_data.json`. Methods: `get(artist_id)`, `set(artist_id, data)` |
| `app/batch_worker.py` | CLI tool | Batch track comment/tag update via rbox `MasterDb`. Run directly: `python -m app.batch_worker --help` |

---

## Response Envelope (all endpoints)

```python
# Success
{"status": "ok", "data": ...}

# Error
{"status": "error", "message": "Human-readable description", "code": "ERROR_CODE"}
```

**Error codes**:
| Code | Meaning |
|------|---------|
| `REKORDBOX_RUNNING` | Can't write — Rekordbox is open |
| `PATH_SANDBOX_VIOLATION` | Path outside `ALLOWED_AUDIO_ROOTS` |
| `AUTH_EXPIRED` | SoundCloud auth token expired |
| `RATE_LIMITED` | SC API rate limit (429) |
| `FILE_NOT_FOUND` | Audio file missing from disk |
| `ANALYSIS_PENDING` | Task still running |
| `ANALYSIS_FAILED` | Analysis task failed |
| `USB_LOCKED` | Another sync is in progress |


---

## USB Play-Count Sync (`app/playcount_sync.py`)

New module for three-way play-count diffing between PC and USB.

| Function | Signature | Notes |
|----------|-----------|-------|
| `load_usb_sync_meta` | `(usb_root: str) -> dict` | Reads `PIONEER/RB_EDITOR_SYNC.json` |
| `save_usb_sync_meta` | `(usb_root: str, meta: dict) -> None` | Atomic write via .tmp |
| `diff_playcounts` | `(pc_tracks, usb_tracks, last_sync_ts) -> dict` | Returns `{auto, conflicts}` |
| `resolve_playcounts` | `(resolutions, pc_db_path, usb_xml_path) -> dict` | Commits to DB + XML |
| `read_usb_xml_playcounts` | `(usb_xml_path: str) -> list[dict]` | Parses Rekordbox XML |

**API endpoints added to `app/main.py`**:
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/usb/playcount/diff` | Diff PC vs USB play counts |
| POST | `/api/usb/playcount/resolve` `[AUTH]` | Commit resolved play counts |

---

## Phrase & Auto-Cue Generator (`app/phrase_generator.py`)

| Function | Signature | Notes |
|----------|-----------|-------|
| `extract_beats_from_db` | `(track_id: int, db_path: str) -> list[float]` | Reads rbox beatgrid |
| `detect_first_downbeat` | `(audio_path: str, beats: list[float]) -> float` | librosa energy analysis |
| `generate_phrase_cues` | `(beats, phrase_length=16) -> list[dict]` | phrase_start / bar_start cues |
| `commit_cues_to_db` | `(track_id, cues, db_path) -> None` | Writes hot cues A–H via rbox |

**API endpoints added to `app/main.py`**:
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/phrase/generate` `[AUTH]` | Generate phrase cues from beat grid |
| POST | `/api/phrase/commit` `[AUTH]` | Write hot cues to Rekordbox DB |

---

## Acoustic Duplicate Finder (in `app/main.py`)

Background job system with Python fallback fingerprinting (librosa PCM / MD5).

**API endpoints added to `app/main.py`**:
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/duplicates/scan` `[AUTH]` | Start background fingerprint scan, returns `job_id` |
| GET | `/api/duplicates/results` | Poll scan status/results |
| POST | `/api/duplicates/merge` `[AUTH]` | Merge duplicate tracks in library |

---

## MyTags Endpoints

Per-track tag taxonomy (orthogonal to genres). Stored in `master.db.djmdMyTag` + `djmdSongMyTag`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/mytags` | List all tag definitions |
| POST | `/api/mytags` `[AUTH]` | Create new tag definition |
| DELETE | `/api/mytags/{tag_id}` `[AUTH]` | Delete tag definition + cascade unlink |
| GET | `/api/track/{tid}/mytags` | Tags applied to a single track |
| POST | `/api/track/{tid}/mytags` `[AUTH]` | Replace the tag set on a track |

---

## Insights Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/insights/low_quality` | Tracks below bitrate/quality threshold |
| GET | `/api/insights/no_artwork` | Tracks missing embedded artwork |
| GET | `/api/insights/lost` | Tracks whose file paths no longer exist on disk |

---

## Authentication (`app/auth.py`)

Phase-1 Bearer-token gate for every mutation route. Token rotates only on sidecar process restart.

| Symbol | Returns | Description |
|--------|---------|-------------|
| `SESSION_TOKEN` | `str` | Boot-time token (`secrets.token_urlsafe(32)`). Empty in worker subprocesses (rate-limit pool, anlz worker) — only the main process generates + emits the banner. |
| `require_session(authorization)` | `None` | FastAPI dependency. Parses `Authorization: Bearer <token>`, compares against `SESSION_TOKEN` via `safe_compare`. Raises `HTTPException(401, "Unauthorized")` on any failure (missing header, wrong scheme, length mismatch, value mismatch). **Never logs the token at any level.** |
| `_token_file_path()` | `Path` | Resolves `<user_data_dir>/MusicLibraryManager/.session-token` cross-platform via `platformdirs`. Browser-dev path reads from this; Tauri reads from stdout instead. |
| `_write_token_file(token)` | `Path` | Persist token to user-data dir; best-effort `chmod 0o600` on POSIX. |
| `_emit_boot_banner(token)` | `None` | Write `LMS_TOKEN=<value>\n` to stdout once at import time. Tauri Rust supervisor's stdout reader matches the prefix, stores the value in a `Mutex<String>`, and drops the line from the forwarded log stream. |

Wire-protocol: every mutation route declares `dependencies=[Depends(require_session)]`. The frontend's axios interceptor (`frontend/src/api/api.js`) blocks on a bootstrap promise that fetches the token via Tauri IPC (`get_session_token`) or the Vite dev-middleware (`GET /dev-token`), then attaches `Authorization: Bearer <token>` to every request.

---

## Constant-Time Compare (`app/security_compare.py`)

Hardened wrapper around `secrets.compare_digest` for token / HMAC equality.

| Symbol | Returns | Description |
|--------|---------|-------------|
| `safe_compare(presented, expected)` | `bool` | Constant-time equality. Returns `False` (never raises) for non-`(str\|bytes)` inputs, non-ASCII `str`, mixed `str`/`bytes`, length mismatch — the five fragility cases from the codebase audit (see `docs/research/research/evaluated_security-secrets-compare-digest-codebase-audit.md`). Trust direction is part of the public contract: `presented` is untrusted (request-side), `expected` is canonical (server-side). |

---

## Rate Limiting (`app/rate_limit.py`)

Phase-1 deliverable from `docs/research/research/evaluated_security-rate-limit-design.md`. Module shipped and applied to the 3 HIGH-tier routes per the Recommendation block.

| Symbol | Returns | Description |
|--------|---------|-------------|
| `TokenBucket(steady_per_min, burst)` | instance | Single-key bucket with monotonic-clock refill. `take()` → `(allowed, retry_after_s)`. |
| `BucketStore` | instance | Process-wide TTL'd dict keyed by `make_key()` output. Lazy purge (60s sweep interval, 600s idle TTL). RLock-guarded mutation. |
| `make_key(request, mode)` | `str` | Derive bucket-key from request. Loopback IPs (`127.0.0.1`, `::1`) short-circuit to `"__whitelist__"` sentinel — Phase-1 sidecar binds to loopback so every real request bypasses the limiter today. `mode` ∈ `{"ip", "bearer", "both"}`. |
| `rate_limit(steady, burst, key_mode)` | decorator | Wraps an async FastAPI handler with a per-key gate. On miss raises `HTTPException(429, {"error": "rate_limited", "retry_after_s": int})` plus a `Retry-After` header. |
| `_store` | `BucketStore` | Module-level singleton; exposed for tests to reset between cases. |

**Currently applied to** (all `steady=5.0, burst=10, key_mode="both"`):
- `POST /api/system/shutdown`
- `POST /api/system/restart`
- `POST /api/soundcloud/auth-token`

Decorator order: `@app.post(...)` outermost → `@rate_limit(...)` inner → `Depends(require_session)` via `dependencies=` kwarg on the `@app.post`, so `require_session` raises 401 before the bucket is decremented (verified by `tests/test_rate_limit.py::test_auth_before_ratelimit`). Remaining MEDIUM/LOW-tier wiring is Phase-2 work.
