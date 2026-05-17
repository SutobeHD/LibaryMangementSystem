# Changelog

## [Unreleased]

- Added: Phase-1 Bearer-token authentication on 84 mutation endpoints.
  New `app/auth.py` self-generates session token at sidecar boot, captured
  by Tauri stdout reader + dev-middleware fallback. Frontend attaches
  `Authorization: Bearer` via bootstrap promise. New `safe_compare` helper.
  `SHUTDOWN_TOKEN` query-string scheme deleted (redundant under `require_session`).

## v1.0.0-beta — 2026-05-07

First public **beta**. Standalone DJ-library manager that competes with
Rekordbox/Serato while staying open and local-first.

### Workflows fully supported

1. **SoundCloud → Library → USB → Club**
   - SC OAuth login, playlist + likes browser
   - Per-playlist Download button — full pipeline with anonymous fallback
     when SC's v2 API rejects the token
   - Auto-analyse (BPM / Key / Beatgrid / Phrases / Auto-Hot-Cues / Auto-
     Memory-Cues / Waveform via librosa+scipy+numba)
   - Auto-import into library + auto-add to `SC_<playlist-name>`
   - USB-Sync to CDJ-3000 ready stick
2. **Local Files → Library → USB → Club**
   - Drag-drop / folder picker
   - Folder name becomes the playlist; every file (incl. duplicates) is
     bundled into one coherent playlist
   - Same full analyse + ANLZ + auto-add pipeline as SC
   - USB-Sync end-to-end

### Library

- **Standalone XML mode** — full feature parity with Live: create / rename /
  move / delete / duplicate / reorder, folders, smart playlists with
  Rekordbox-XML-spec conditions (BPM, Key, Genre, Rating, DateAdded, …),
  cues + beatgrid persisted on save
- **Live mode** — direct master.db via pyrekordbox
- DBWrapper routes every CRUD operation to the active mode without API
  duplication

### USB Export

- `PIONEER/rekordbox/exportLibrary.db` via rbox.OneLibrary
- `PIONEER/USBANLZ/<bucket>/<hash>/ANLZ0000.{DAT,EXT,2EX}` per track
- Audio copy under `Contents/<Artist>/<Title>`
- Cover artwork copy
- Mode-agnostic LibrarySource abstraction so Standalone-XML works for export
  the same as Live

### UI

- 4-card XML submode picker (New Empty / Standalone / Import / Defined Path)
- Import Manager with 7-stage live pipeline + per-stage timestamps
- Sticky import progress banner — visible on every screen, click to open
  manager
- Click-to-rate (5 stars) + Pioneer color-tag picker (9 colors) inline in
  the track table
- Tokenized search: `bpm:120-130 key:Am genre:techno year:2024 rating:>3`
- Right-click context menus on tracks (9 actions) and playlists (7 actions)
  — both via React portal + document-level capture-listener so they actually
  work in Tauri WebView2
- Edit-mode toggle removed — everything is always editable
- Playlist drag-reorder works (Tauri main-window `dragDropEnabled: false`)
- Player drag-seek with `track.TotalTime` fallback for chunked streams
- Pane height fills full window when no player is shown

### Tauri / Dev

- Debug build auto-spawns Python backend + Vite if their ports are free —
  direct exe launch behaves like `npm run tauri dev`
- Children killed on `RunEvent::Exit`

### SoundCloud

- Optional **Aggressive Download Mode** (hidden setting) — bypasses the
  default snipped-preview gate. Reveal by tapping the dot under "SoundCloud
  Sync" in Settings 5×. Use only for tracks you have a personal right to.

### Known limitations

- USB-Sync requires `pyrekordbox` (`rbox.OneLibrary`) — bundled with the
  release binary
- Manual Hot-Cue editing is post-MVP (auto-cues from analysis only for now)
- Master.db creation without an existing Rekordbox install is post-MVP

### Build & install

- Windows MSI + NSIS installers via `scripts/local-release.ps1`
- No code-signing — SmartScreen warning on first run is expected
- SHA256SUMS published with each release
