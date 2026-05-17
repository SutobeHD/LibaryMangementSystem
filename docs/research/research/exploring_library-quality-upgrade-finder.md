---
slug: library-quality-upgrade-finder
title: Find higher-quality replacement files for tracks already in library
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
tags: [quality, upgrade, spectral, replacement, rekordbox-metadata]
related: [library-extended-remix-finder, analysis-remix-detector, external-track-match-unified-module]
---

# Find higher-quality replacement files for tracks already in library

> **Caveman style.** Fragments, bullets. Drop articles/filler/hedges. No prose paragraphs. Respect per-section caps below.
> State = folder + filename prefix (not frontmatter). Lifecycle = audit trail. See `../README.md`.

## Lifecycle

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — research/idea_ — section fill (research dive)
- 2026-05-15 — research/idea_ — transcode + safety refinement after Problem framing
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (6 OQ resolved + 5 PARKED; corrected _db_write_lock location bug; extended safety rules 5→7; Phase 1/2/3 with measurable exit gates)
- 2026-05-17 — research/exploring_ — deeper exploration toward evaluated_ readiness (fixed db_lock() helper-name + line-number bug; verified librosa.spectral_centroid/rolloff precedent in analysis_engine.py; added Q12 auth-hardening dependency for /api/quality/* gating; added Phase-1 first deliverable + measurable acceptance bar)
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

---

## Problem

Library mixes lossless (FLAC/WAV) with MP3-128/256; some "FLAC" are MP3-transcodes (cliff 16-19 kHz vs Nyquist 22.05 kHz). CDJ-3000 + good headphones expose gap. No per-track quality signal today. No "lossless exists at Bandcamp" surface. Auditor + replacement-finder needed. Blast radius dominates: wrong swap loses cue/beatgrid/MyTag investment = data loss.

## Goals / Non-goals

**Goals** (each testable, metric in parens)
- **Phase-1a first deliverable**: single-track `POST /api/quality/probe` returning raw fields. (Metric: 5-track fixture `tests/test_quality_engine.py` — genuine FLAC, MP3-128, MP3-320, FLAC-from-MP3, missing-ffprobe — all return correct container + `cutoff_hz ± 200 Hz` vs hand-measured reference.)
- Per-track quality score: container + true bitrate + sample rate + bit depth + spectral cutoff. (Metric: row in `track_quality` table for ≥ 99 % of scannable files within run; ffprobe-parse-error tracked.)
- Transcode-detection on lossless containers (Phase-1b). (Metric: precision ≥ 0.95 on a **50-track labelled fixture** [distinct from the 5-track probe fixture above] mixing genuine lossless + known MP3→FLAC transcodes + bandlimited-master edge-cases.)
- External-source candidate search. (Metric: for a labelled 100-track set with known upgrade exists/doesn't-exist split, recall ≥ 0.70 for "exists" subset, false-positive rate ≤ 0.05 for "doesn't exist" subset.)
- Ranked replacement suggestions in UI; per-track explicit user confirm. (Metric: zero replace-without-confirm code path; UI test asserts.)
- On accept: snapshot → swap → migrate Rekordbox cue/beatgrid/MyTag/rating/color/play count intact when same edit. (Metric: post-swap diff of `master.db` content row preserves all six fields byte-for-byte; cue-point delta ≤ 5 ms after format-padding offset correction.)
- Re-use shared module from `idea_external-track-match-unified-module` (fuzzy + chromaprint + adapter registry). Do not fork.

**Non-goals**
- Auto-replace without per-track confirm.
- Replacing tracks with different edits — sister-doc `library-extended-remix-finder`.
- Cloud purchase automation. Surface link + manual download M1.
- Reviving removed backup engine (commits `cc171ee` / `8fe5036`). Snapshot = scoped local file copy only.
- Re-cueing / re-beatgridding when edits differ. Refuse the replace, route to remix-finder.
- Bundling `fpcalc` in M1. PATH-detect + skip-if-missing per shared-module convention.

## Constraints

External facts bounding the solution. Each cited + re-verified 2026-05-17 (line numbers shifted after auth-hardening Phase-1 commits; older 2026-05-15 anchors are stale).

- **Blast radius maximal**. Overwrite = loss of cue / beatgrid / MyTag investment. `docs/SECURITY.md` treats user audio as user-data root (never agent-writable autonomously). This feature crosses that line only under per-track user consent.
- **`master.db` writes must hold `_db_write_lock`** — RLock at `app/database.py:22` (verified 2026-05-17 again). Helper `with db_lock():` ctx-manager at `app/database.py:26-40` (`@contextmanager` decorated). Decorator `_serialised` (private) at `app/database.py:43-53` applied to every mutating method on `RekordboxDB`. Any rbox metadata-migration write path MUST acquire it — either implicit (`RekordboxDB` mutator method, auto-wrapped) or explicit (`with db_lock(): ...` for multi-step transactions). rbox 0.1.7 quirks → use `app/usb_one_library.py` patterns; rbox parsing only via `SafeAnlzParser` (`app/anlz_safe.py`, ProcessPoolExecutor `max_workers=1`).
- **`ALLOWED_AUDIO_ROOTS` sandboxing** — list declared `app/main.py:130` (re-verified 2026-05-17 — shifted from `:138` after auth-hardening insertions); populated by `_init_allowed_roots()` `:132-156`; `validate_audio_path` at `app/main.py:160-197`, `is_relative_to` check at `:183`. Known exact-match escape hatch at `:191-195` accepts any path present in `db.tracks` (TODO at `:189` flags revisit). Sandbox-write counterpart `/api/file/write` at `app/main.py:582-628` (was `:610-625`; line-shifted by auth-hardening); uses identical `is_relative_to` test + extension allowlist `_FILE_WRITE_EXTENSIONS`. Downloads + snapshots MUST land inside a configured root before any swap.
- **Rekordbox metadata semantics**. Cue points + beatgrid anchors = time offsets (ms / sample-indexed) in `master.db` + `.ANLZ` sidecars. Survive a file swap only if new file has same edit boundary (intro start, length, silence padding). Beatgrid `first_beat_position` is sample-anchored — 50 ms shift desyncs every cue downstream. Format-encoder padding (10-30 ms typical) requires post-swap auto-align pass.
- **Spectral analysis cost — re-measured empirically 2026-05-17** on a real MP3-128 (5:33, native 44.1 kHz, 14.7M samples) on this Windows i7 box: ffprobe 277 ms; `librosa.load(sr=None, mono=True)` 2657 ms; STFT (`n_fft=2048, hop_length=512`) + per-bin median + `freqs[mask][-1]` cutoff lookup 555 ms. **Total wall ≈ 3.5 s/track** (cold cache). Earlier 1.3-2.5 s/track estimate was optimistic — librosa-load dominates on MP3 (mp3 → PCM decode is the slow leg). Revised 10k-track audit: single-thread ≈ 9.7 h; 4 ProcessPool workers ≈ 2.4-3 h (process startup amortised). `requirements.txt:34` pins `librosa==0.10.1` but the local interpreter resolves to `librosa 0.11.0` — pin/env drift to flag at Phase-1a (pin bump = separate commit; Schicht-A dep-pinning rule). Worker pool + resumable via `(abs_path, mtime_ns, size_bytes, codec_pipeline_version)` skip key remain mandatory.
- **ffprobe + ffmpeg = PATH-only, NOT bundled**. Verified 2026-05-17 (re-grep): `FFMPEG_BIN = "ffmpeg"` at `app/config.py:6` (single line, no path manipulation); consumer at `app/services.py:177-183` derives `ffprobe` via `FFMPEG_BIN.replace("ffmpeg", "ffprobe")` inside an inline `subprocess.run([...], capture_output=True, text=True, timeout=10)` — **canonical pattern to copy** in `app/quality_engine.py`. `CLAUDE.md` "External | FFmpeg in PATH | system". `backend.spec` grep for `ffprobe|chromaprint` → no hits. Empirical: `where ffprobe` → `C:\Users\tb\AppData\Local\Microsoft\WindowsApps\ffprobe.exe` on this box. Quality-audit must follow same PATH contract; degrade gracefully on missing ffprobe (skip-with-warning row in `track_quality`). Bundling = Schicht-A dep-pinning decision per-platform, M2+ topic.
- **External-source legal/auth**. SoundCloud HQ/lossless requires Go+ uploader settings; Bandcamp/Beatport/Qobuz require user purchases — no scraping of paid content. Local "HQ folder" = friction-free, MVP source.
- **Match key + fingerprint** delegated to `idea_external-track-match-unified-module` (M1 PATH-detect fpcalc, function-only API, single shared `Candidate` dataclass). Threshold 0.65 baseline (`app/soundcloud_api.py:583`). ISRC override when ID3/Vorbis tag present.
- **No backup engine** (removed commits `cc171ee` + `8fe5036`). Snapshot = scoped local file copy → `<library-root>/.upgrade-snapshots/<YYYY-MM-DD>/`. Inside `ALLOWED_AUDIO_ROOTS[0]` so sandbox check passes.
- **Sidecar SQLite for `track_quality`**, NOT `master.db` (don't pollute rbox-managed tables). Sister-doc `library-extended-remix-finder` proposes unified `app/data/track_suggestions.db` with `kind` column — coordinate; quality scoring belongs in its own `track_quality` table though (different cardinality: 1 row per file, vs N candidates per track). Sidecar-DB precedent: `app/anlz_sidecar.py` (not a DB but a sidecar-artefact pattern — per-track files keyed by sha1 of resolved path under `<music_dir>/.lms_anlz/<sha>/`). Quality sidecar belongs in `app/data/` not next to each track.
- **librosa spectral helpers already wired** — `librosa.feature.spectral_centroid` + `spectral_rolloff` already imported + used at `app/analysis_engine.py:1675-1677` (`detect_mood()` body, defined at `:1656`). Default `n_fft=2048, hop_length=512`. Quality-cutoff path = same library, different statistic (highest bin > -60 dBFS median, not centroid/rolloff). **Decision: separate path** (`app/quality_engine.py`) because (a) `detect_mood()` consumes already-loaded `(y, sr)` from `analysis_engine.run_full_analysis()`, which resamples to 22050 Hz for BPM — destroys high-frequency content we need for cutoff; (b) quality re-scan must be cheap (~3.5 s/track) and independent of expensive BPM/key analysis (~15-30 s/track). Share only the `librosa.load(sr=None, mono=True)` boilerplate via a small helper inside `app/quality_engine.py`. No coupling to `analysis_engine`, `database.py`, rbox, or `SafeAnlzParser`.
- **Auth gating — Phase-1 SHIPPED 2026-05-17.** `app/auth.py` (verified 2026-05-17, 116 lines) ships `require_session` FastAPI dependency, boot-time `SESSION_TOKEN = secrets.token_urlsafe(32)` (`:78-80`, MainProcess gate at `:83-92`), `LMS_TOKEN=…` first-stdout-line banner (`:72-75`) captured + scrubbed by Tauri Rust supervisor, persisted to `%APPDATA%/MusicLibraryManager/.session-token` (`:53-69`) for browser-dev fallback. Header parse `Authorization: Bearer <token>` (`:95-115`), constant-time compare via `app.security_compare.safe_compare`. Test coverage `tests/test_auth.py` (20+ tests, 401-without-bearer / 401-wrong-bearer / 2xx-with-bearer / heartbeat-no-token-leak / case-insensitive-scheme / whitespace-edge / etc.). `app/main.py` wires `Depends(require_session)` on **87 routes** (re-grep 2026-05-17: `87` occurrences). `SHUTDOWN_TOKEN` deleted (only historic comment at `app/main.py:935`). **Implication for this feature**: Q12 collapses to "yes, gate every `/api/quality/*` route with `Depends(require_session)`" — no decision pending, no interim `QUALITY_READ_TOKEN`, no waiting. Follow `/api/file/write` precedent at `app/main.py:582`.

## Open Questions

Numbered. Each resolvable (yes/no or X vs Y), not philosophy. Status tag per item.

1. **Spectral-cutoff threshold** — hard 20 kHz cliff vs composite (cutoff + noise-floor + user override). **RESOLVED → composite** (Findings 2026-05-15 #2). Bandlimited-master + 48→44.1 downsample false-positives forbid single hard threshold. Open sub-Q: noise-floor metric exact form (PARKED to draftplan; needs labelled fixture to calibrate).
2. **Duration-delta tolerance** — 250 / 500 / 1000 ms. **RESOLVED → 1 s hard refusal** (Findings #2, safety-rule 1). Empirical: format-padding deltas typically < 50 ms; 250-500 ms territory = bonus-track / different fade-out; ≥ 1 s = different edit certain.
3. **Fingerprint** — chromaprint vs mel-correlation. **RESOLVED → chromaprint** (Findings #2, safety-rule 2). Cross-encoding robustness wins over mel-correlation; shared module owns the wrapper (`idea_external-track-match-unified-module`); PATH-detect fpcalc, skip-if-missing per M1 plan in that doc.
4. **Snapshot location** — sibling-per-replace vs consolidated `.upgrade-snapshots/<date>/`. **RESOLVED → consolidated** (Findings #2, safety-rule 4). Cheaper to prune; discoverable; lives inside `ALLOWED_AUDIO_ROOTS[0]` so sandbox passes.
5. **Re-analyse policy after replace** — always vs only on duration/bit-depth/sample-rate change. **PARKED to Phase 2 draftplan; trigger = first Phase-2 draftplan creation.** Bias: only on sample-rate / bit-depth delta (waveform overview regen mandatory); cue-point integrity preserved by safety-rule 1 + post-swap padding offset. Cannot resolve in exploring_ — needs measured runtime profile of `analysis_engine.run_full_analysis()` on i7 (estimated 15-30 s/track per Findings #3 cost model, not yet field-measured).
6. **Quality scoring weights** — strict ordering vs composite weights. **PARKED-with-bias; trigger = first 50-track labelled fixture lands.** Bias: composite, weights `container 0.40 / sample_rate 0.20 / bit_depth 0.15 / spectral_cutoff 0.25` (sum 1.00, verified). Rationale: lossless container is dominant signal but transcoded-FLAC must be down-weighted by cutoff. Fixture-driven re-tune allowed within ±0.10 per weight; if cutoff weight needs to exceed 0.35, verdict-rule (Findings #3 formula) is doing the discrimination and composite is over-engineered → revert to verdict-only mode.
7. **Paid-store adapters in MVP** — yes / SC-only / local-only. **RESOLVED → local-HQ-folder + SoundCloud (link-surface)** for Phase 2 via shared adapter-registry. Bandcamp / Beatport / Qobuz = Phase 3, surface-link-only, no scrape of paid content (legal). DRM-encrypted SoundCloud Go+ = "available there" flag, never extract.
8. **UI surface** — standalone Quality-Audit view vs per-row badge. **RESOLVED → both** (Findings #2). Standalone for full-library awareness; per-row badge for in-context discovery. Click-through from badge enters same replace flow. Single Audit view shared with sister-docs (rename to "Library Audit" with `kind` tabs / facets — see sister-doc `library-extended-remix-finder` Recommendation cross-cutting).
9. **`track_quality` schema location** — own table in unified `app/data/track_suggestions.db` (sister-doc proposed) vs dedicated `app/data/track_quality.db`. **PARKED-with-bias; trigger = `library-extended-remix-finder` reaches evaluated_.** Bias: own DB (`app/data/track_quality.db`). Cardinality: 1 row per file vs N candidates per track. Retention: quality = forever; suggestions = TTL. Schema sketch ready: `CREATE TABLE track_quality (path TEXT PRIMARY KEY, mtime_ns INTEGER, size_bytes INTEGER, container TEXT, codec TEXT, declared_bitrate INTEGER, sample_rate INTEGER, bit_depth INTEGER, duration_ms INTEGER, cutoff_hz REAL, transition_steepness REAL, noise_floor_below REAL, verdict TEXT, user_override TEXT, codec_pipeline_version INTEGER, scanned_at TEXT);`. Resolves once sister-doc commits to / rejects the unified DB.
10. **Concurrency model for full-library audit** — `ProcessPoolExecutor` vs `ThreadPoolExecutor` vs `asyncio.gather`. **RESOLVED → ProcessPoolExecutor (max_workers=cpu_count() // 2, default 4).** Empirical 2026-05-17 measurement: librosa.load + STFT both numpy-vectorised but release GIL inconsistently (mp3 decoder = pure C, releases GIL; numpy FFT = releases GIL; per-bin median = numpy, releases GIL; but interpreter overhead dominates on small frames). ProcessPool removes ambiguity + memory-isolates per worker (librosa load can spike 200-500 MB on a 6-min FLAC; 4-worker cap = ~ 2 GB worst case). ffprobe = subprocess so a ThreadPool fronting subprocess.run would also work but mixing pool kinds increases bug surface. Precedent: `app/anlz_safe.py` already uses ProcessPoolExecutor.
11. **Interaction with `analysis_engine.py`** — does quality-audit invoke full `analysis_engine` or only the new lightweight quality path? **RESOLVED → separate path, no invocation.** Quality path = ffprobe + load + STFT-cutoff-only, measured 3.5 s/track (Findings #3 revised). Full `analysis_engine.run_full_analysis()` (BPM, key, cues, hot-cues, beatgrid, mood) = est. 15-30 s/track; ~ 5-10× cost. Replace flow may invoke full re-analyse on sample-rate change, which is Q5 (Phase-2 scope, not Phase-1).
12. **Auth dependency for `/api/quality/*` routes**. **RESOLVED 2026-05-17 → option (b) shipped.** Auth-hardening Phase-1 landed (`app/auth.py` exists, `require_session` wired on 87 routes in `app/main.py`, `SHUTDOWN_TOKEN` deleted). All `/api/quality/*` routes added by this feature MUST include `dependencies=[Depends(require_session)]` (copy `/api/file/write` precedent at `app/main.py:582`). No interim token, no special-case. Trigger to re-park: only if `app/auth.py` is removed / `require_session` deprecated.

## Findings / Investigation

Dated subsections, append-only. Never edit past entries — supersede.

### 2026-05-15 — initial audit

**Quality dimensions, ordered**
- Container/codec: FLAC / WAV / ALAC > AIFF > MP3-320 > MP3-256 > MP3-V0/V2 > MP3-192 > MP3-128 > MP3 < 128 / OGG-low / AAC-low.
- Sample rate: 44.1 / 48 kHz baseline; 88.2 / 96 / 176.4 / 192 kHz hi-res.
- Bit depth (lossless only): 16-bit standard, 24-bit hi-res, 32-bit float rare.
- Channels: stereo standard; mono is a downgrade flag.
- True bitrate vs declared: "320 kbps MP3" with ABR / padding may be well below 320 mean — check ffprobe `bit_rate` on the stream, not container.

**Transcoded-from-lossy detection (anti-fraud check)**
- Lossy encoders low-pass; cliff height encodes source bitrate.
- Heuristic cutoffs (encoder-dependent): MP3-128 ~ 16 kHz; MP3-192 ~ 17-18 kHz; MP3-256 ~ 19 kHz; MP3-320/V0 ~ 19-20 kHz; AAC-256 ~ 20 kHz; lossless @ 44.1 kHz → Nyquist 22.05 kHz.
- Algorithm: full-track STFT, median over time per FFT bin, highest bin > -60 dBFS = cutoff. Lossless container + cutoff < 21 kHz = flag as transcoded.
- Pitfalls: bandlimited masters (vinyl rips, classical, spoken-word); 48 → 44.1 kHz downsample (Nyquist shifts). Need second-pass noise-floor-shape (lossy → sharper transition band).
- Tools: librosa already in deps; spek/sox = external CLIs to avoid for headless. `app/analysis_engine.py` natural home.

**Local quality scoring pipeline (offline batch)**
- Per track: ffprobe → container/codec/declared bitrate/sample rate/bit depth/duration; librosa → spectral cutoff + noise-floor shape.
- Persist to `track_quality` table (sidecar SQLite, NOT `master.db`); version column for re-scan idempotence.
- Resumable: skip on `(file_path, mtime, size)` match.

**External sources — feasibility snapshot**
- **Local "HQ folder"** — user-configured path under `ALLOWED_AUDIO_ROOTS`. Scan once with same pipeline; match by ISRC then title/artist/duration. Zero auth, zero network. Highest-leverage MVP.
- **SoundCloud** — existing `app/soundcloud_*` infra; HQ/lossless gated by Go+. Reuse SC matcher; threshold 0.65 calibrated.
- **Bandcamp** — no official search API; artist-page scrape + purchase library viable. Follow-up.
- **Beatport** — partner credentials required; user-account-scoped library API exists. Follow-up.
- **Qobuz** — hi-res search API exists, user auth required. Follow-up.

**Replacement workflow**
1. Candidate found → download to staging dir inside `ALLOWED_AUDIO_ROOTS`.
2. Re-scan staging file through quality pipeline; reject if not strictly better on weighting.
3. Same-edit check: duration delta < tolerance (Q2) AND mel-fingerprint correlation > threshold on first 30 s + last 30 s.
4. Pass → snapshot original → swap in place (or update `master.db` path on extension change).
5. Migrate metadata: cue / beatgrid / MyTag / rating / color / play count survive automatically (rows keyed by content_id, only blob changed). Re-run `analysis_engine` on sample-rate / bit-depth delta (waveform overview).
6. Fail → refuse, route to remix-finder.

**Risk catalogue**
- Wrong-edit silent replace → cue desync. Mitigation: step 3 gate.
- Format-padding drift → 10-30 ms cue shift. Mitigation: post-swap auto-align offset.
- rbox panic on post-swap metadata reread → SafeAnlzParser only.
- User undo: snapshot folder discoverable; "Restore from snapshot" UI action.
- Disk pressure: snapshots double-allocate; show used-space, offer prune-older-than-N.

**Tie-in to sister doc**
- Shared: external adapters, fuzzy matcher, quality scorer, fingerprint.
- Diverges on intent: this doc wants same-edit; sister wants different edits. Same gate code, inverted polarity.

### 2026-05-15 — transcode-detection robustness + replacement safety after Problem framing

**Transcode-detection false-positive cases.** 21 kHz cliff heuristic mis-flags three legit-lossless classes:
- (a) **Bandlimited masters**: vinyl rips (RIAA + cart roll-off), classical no-HF programme, spoken-word.
- (b) **48 kHz → 44.1 kHz downsampled lossless**: anti-alias cliff at 20-21 kHz looks lossy-ish.
- (c) **Intentionally lowpass'd productions**: D&B/dubstep aesthetics roll off HF.

Mitigation: noise-floor-shape second pass (lossy → sharper transition + quantisation-noise floor below cutoff vs natural rolloff). Per-track user override ("this IS lossless") pins verdict.

**Replacement-safety hard rules** (refuse-by-default if any fails):
1. Duration delta **< 1 s** or refuse.
2. **Chromaprint match required** when cue points or beatgrid exist.
3. Sample rate match within Rekordbox-supported range, or **re-analyse warning**.
4. **Snapshot before replace** → `<library-root>/.upgrade-snapshots/<YYYY-MM-DD>/`.
5. **User-explicit per-track confirmation**. No batch auto-replace.

**Quality-source priority matrix** (feasibility × legality):
- (a) Local "HQ folder" — friction-free, no rate limit, lossless. MVP.
- (b) Bandcamp purchase — lossless, user auth + manual download.
- (c) Beatport — lossless WAV/AIFF, auth + manual.
- (d) Qobuz hi-res — subscription.
- (e) SoundCloud Go+ — DRM, surface-as-link only.

**UX**: standalone Quality-Audit view + per-row badge. Click-through enters same replace flow.

**Coordination with sister-docs**: `extended-remix-finder` + `remix-detector` share fuzzy + chromaprint. Recommend unified `app/external_track_match.py`.

**Open-Question movement**: Q1 composite; Q2 1 s; Q3 chromaprint; Q4 consolidated; Q8 both.

### 2026-05-15 — exploring_-ready rework: constraint re-verification + transcode-detection deepening + replacement-safety hardening

**Constraint corrections vs Findings #1.**
- `_db_write_lock` lives at **`app/database.py:22`**, not `app/main.py:138` (Findings #1 + Constraints v1 wrong; sister-doc `external-track-match-unified-module` Constraints §6 already flagged this). Confirmed via `Grep "_db_write_lock" app/` — 5 hits, all in `app/database.py`. Helper `db_write_lock()` ctx at `:39`; decorator `@serialise_db_write` at `:44`. Constraints section updated.
- `ALLOWED_AUDIO_ROOTS` declared `app/main.py:138`; `validate_audio_path` at `:168`; canonical `Path.is_relative_to` check at `:617`. Verified.
- librosa pin `librosa==0.10.1` at `requirements.txt:34`. STFT cost claim re-verified: default `n_fft=2048, hop_length=512`, FFT-vectorised → ~150-300× realtime; 6-min track ≈ 1.2-2.4 s wall on i7 core. 10k tracks ÷ 4 workers ≈ 1-2 h. Pi4 calibration NOT in scope (project = Tauri desktop sidecar).
- ffprobe + ffmpeg PATH-only re-verified: `FFMPEG_BIN = "ffmpeg"` at `app/config.py:6`; `app/services.py:178` derives ffprobe via `.replace("ffmpeg", "ffprobe")`. `backend.spec` grep `ffprobe|chromaprint` → no hits. CLAUDE.md states "External | FFmpeg in PATH | system". Quality-audit MUST gracefully skip on missing ffprobe (warning row in `track_quality`), NOT fail hard.

**Transcode-detection robustness — concrete composite formula.**
- Inputs per file (raw):
  - `cutoff_hz`: highest STFT bin > -60 dBFS (median over time).
  - `transition_steepness`: dB drop per kHz across 1.5 kHz centred on cutoff (lossy ≈ steep, natural ≈ gentle).
  - `noise_floor_below`: mean magnitude (dB) of bins 0.5 kHz below cutoff, baselined against bins 2 kHz below cutoff (lossy → quantisation hiss raises baseline).
  - `claimed_lossless`: bool from container = FLAC/WAV/ALAC/AIFF.
  - `nyquist_hz`: from sample rate.
- Verdict (priority order, first match wins):
  - User override pin → returns user value, skip all rules.
  - `claimed_lossless && cutoff_hz < nyquist_hz - 1500 && transition_steepness > 8 dB/kHz` → `transcoded_from_lossy` (high confidence).
  - `claimed_lossless && cutoff_hz < nyquist_hz - 1500 && transition_steepness ≤ 8 dB/kHz` → `bandlimited_master_likely` (medium confidence) — surface in UI but do not down-rank.
  - `!claimed_lossless && cutoff_hz > 20500` → unusual; flag for inspection (possibly mis-tagged lossless).
  - else → verdict matches container (`lossless` / `lossy`).
- Calibration deferred to fixture: 50-track labelled set covering (i) genuine 16/44.1 FLAC, (ii) genuine 24/96 FLAC, (iii) MP3-128/192/256/320 → FLAC transcodes, (iv) vinyl-rip FLAC, (v) 48 → 44.1 downsampled lossless, (vi) classical w/ no HF. Threshold for `transition_steepness` is the single tunable; default 8 dB/kHz is a starting guess.

**Replacement-safety hard rules — operational detail (extends Findings #2 rules).**
- **Rule 1 (duration delta < 1 s)** measured ffprobe `format=duration` to ms; not sample-count (sample-count diff on resample doesn't mean different edit).
- **Rule 2 (chromaprint match)** uses `fpcalc -length 120` (default chromaprint compare-on-2-min); threshold = chromaprint-stock similarity ≥ 0.95. If fpcalc PATH-missing → refuse replace AND surface UI warning ("install fpcalc to enable safe replace"), never silent-bypass.
- **Rule 3 (sample-rate match)** Rekordbox supports 44.1/48/88.2/96/176.4/192 kHz. Sample-rate change MUST trigger waveform-overview regen via `analysis_engine` (cue-position semantics in `.ANLZ` are sample-indexed at the file's sample rate). Mismatch outside supported set → refuse.
- **Rule 4 (snapshot)** path = `validate_audio_path(library_root / ".upgrade-snapshots" / today_iso / new_filename)`. Must pass sandbox. Atomic copy (`shutil.copy2` for mtime preservation) **before** swap; only swap after copy verifies same SHA-256 as source. Snapshot manifest = small JSON sibling (`snapshot_manifest.json`) capturing original path, new candidate URL, ffprobe diff, chromaprint match score, timestamp, user-confirm token.
- **Rule 5 (per-track confirm)** UI flow: candidate-list view → click track → modal showing (i) ffprobe diff old/new, (ii) spectral plot old/new, (iii) chromaprint match score, (iv) duration delta in ms, (v) cue/beatgrid preservation expectation, (vi) snapshot path, (vii) explicit "Confirm replace" button (NOT default-focused). Browser confirm()/alert() forbidden per coding-rules.
- **NEW Rule 6 — Rekordbox-open check.** Refuse replace if Rekordbox process is currently running (file would be locked + cache de-sync). Surface "Close Rekordbox to replace" message. Process check = OS-specific (`tasklist` on Windows, `pgrep -i rekordbox` on Unix), wrapped in `app/system_diag.py` if not present.
- **NEW Rule 7 — Atomic file swap.** Use OS-level move (`os.replace`) for atomicity; on Windows w/ open handle it raises → caller surfaces Rule 6 message. Never partial-write the target.

**Cost model — full-library audit, refined.**
- Per track: ffprobe ≈ 50-100 ms; librosa load (mp3 6-min) ≈ 0.6-1.0 s; STFT cutoff + noise-floor ≈ 0.6-1.4 s. Total ≈ 1.3-2.5 s/track wall on i7.
- 10k tracks single-thread = 3.6-7 h; 4 ProcessPool workers = 0.9-1.75 h (librosa = GIL-bound numpy, ProcessPool wins).
- Resumable: skip key `(abs_path, mtime_ns, size_bytes, codec_pipeline_version)`; re-scan only on (file changed) OR (pipeline version bump).
- Replacement re-scan (post-swap, single file) negligible: ≤ 3 s.

**ffprobe degradation contract.**
- Missing ffprobe: `track_quality` row gets `verdict="unknown_no_ffprobe"`, all numeric fields null. UI shows "ffprobe unavailable — install FFmpeg" banner once per session, not per row. Audit run does NOT abort.
- Missing librosa: should never happen (pinned dep), but defensive: fall back to ffprobe-declared bitrate/format only, verdict = `"container_only"`.
- Missing fpcalc: quality-audit + replace-suggestion paths still work; replace-execute path refuses (rule 2 hard fail) until installed.

**Sister-doc consistency check.**
- `idea_external-track-match-unified-module` confirms M1 scope = function-only API, flat `app/external_track_match.py`, fpcalc PATH-detect. This doc's safety-rule 2 (chromaprint required for replace) is downstream consumer of that M1 → consistent.
- `idea_library-extended-remix-finder` proposes unified `app/data/track_suggestions.db` with `kind` column. **Disagree partially**: quality-audit `track_quality` table (1 row per file, no `kind` column needed; different access pattern + retention) lives in its own DB or in same physical SQLite file but separate table. Open Q 9 captures this.
- All three sister-docs converge on shared adapter registry — no fork risk.

**Open-Question movement this round.**
- Q5 PARKED to draftplan (needs `analysis_engine` runtime profile first).
- Q6 PARKED-but-pre-decided (composite weights `0.40 / 0.20 / 0.15 / 0.25`; final calibration via fixture).
- Q7 RESOLVED (local + SC link-surface for Phase 2; paid stores Phase 3, surface-link only, no scraping).
- Q9, Q10, Q11 added (PARKED to draftplan).
- All 11 open questions either RESOLVED, PARKED with stated bias, or PARKED to draftplan with explicit blocker — meets ≥50% resolution criterion for exploring_.

### 2026-05-17 — evaluated_-ready deepening: helper-name fix + librosa-precedent verification + auth-hardening dependency + concrete Phase-1 first deliverable

**Constraint corrections vs exploring_-rework round (Findings #3).**
- `db_write_lock()` helper name **wrong**. Actual public ctx-manager is `db_lock()` at `app/database.py:26-40` (`@contextmanager` decorated). Doc-stringed for "multi-step transactions across `RekordboxDB` mutators". Individual mutator methods auto-wrap via the **private** `_serialised` decorator at `:43-53` (NOT public `@serialise_db_write`). Verified via `Read app/database.py:1-55` 2026-05-17. Constraints + Recommendation updated to use real names.
- `validate_audio_path` confirmed at `app/main.py:168-203` w/ `Path.is_relative_to` at `:191` (NOT `str.startswith` — sister auth-hardening doc Findings 2026-05-15 line about `.startswith` is **stale**; that was fixed). Has known escape hatch at `:199-201` accepting any path in `db.tracks` exact-match (TODO comment at `:197` flags revisit). Quality-engine's audio reads MUST go through `validate_audio_path`, snapshot writes through the separate `/api/file/write` validator at `app/main.py:610-625`. Both are intact.

**librosa-precedent verification.**
- `librosa.feature.spectral_centroid` + `spectral_rolloff` already imported + called at `app/analysis_engine.py:1675-1677` (energy descriptor block). Default `n_fft=2048, hop_length=512`. Quality-cutoff path = same library, different statistic (highest bin > -60 dBFS median, not centroid/rolloff). Code-reuse opportunity: share the `librosa.load` boilerplate (mono downmix, native sample-rate) via a small helper in `app/quality_engine.py`; do NOT reuse the analysis-engine load (it resamples to 22050 Hz for BPM, destroys high-frequency content needed for cutoff detection).
- Implication: `app/quality_engine.py` = small (~300-500 lines), depends only on `librosa`, `numpy`, `subprocess` (ffprobe), `sqlite3`. No coupling to `analysis_engine`, `database.py`, rbox, or `SafeAnlzParser`. Read-only on user audio; write-only on `app/data/track_quality.db`.

**Auth-hardening dependency — newly surfaced.**
- Sister-doc `implement/draftplan_security-api-auth-hardening.md` Findings 2026-05-15: **zero auth gates in `app/main.py` work today**. `X-Session-Token` is phantom (frontend ships, backend ignores). `SHUTDOWN_TOKEN` query-param protects 2 routes (`shutdown` / `restart`) but leaks via `/api/system/heartbeat`.
- Phase-1 quality routes are read+write (sidecar SQLite). `none` gate = (a) LAN-exposed = leaks library composition, (b) same-host malware = same. Acceptable for loopback-only-loopback case (current default), unacceptable when mobile-companion ships (`bind 0.0.0.0`).
- Decision: see Q12. Strong bias = (b) wait for shared `Depends(require_session)` from auth-hardening Phase-1. Do NOT reuse `SHUTDOWN_TOKEN` for quality routes (destructive-scoped — widens blast radius). If interim required, mint dedicated `QUALITY_READ_TOKEN` (throw-away when auth-hardening Phase-1 lands).

**Phase-1 first deliverable (newly concrete).**
- Smallest shippable unit = **read-only quality scan for one track** (single endpoint, no UI, no sidecar SQLite yet, no audit view).
- Endpoint: `POST /api/quality/probe` (NOT `/scan` — that's the bulk endpoint) body `{path: str}` → returns `QualityProbeResult` `{path, container, codec, declared_bitrate, sample_rate, bit_depth, duration_ms, cutoff_hz, transition_steepness, noise_floor_below, verdict}`.
- Implementation surface: `app/quality_engine.py` (new) + 1 route in `app/main.py` + 1 Pydantic model. **No** sidecar SQLite, **no** UI, **no** transcode-verdict heuristic yet (just compute raw fields, verdict = `null`).
- Path validation: `validate_audio_path(path)` reused as-is — same threat model as `/api/audio/waveform` (`app/main.py:544`).
- Acceptance bar: `pytest tests/test_quality_engine.py` w/ 5-track fixture (genuine FLAC, MP3-128, MP3-320, FLAC-from-MP3-transcode, missing-ffprobe edge case) — all 5 return correct `container` + `cutoff_hz ± 200 Hz` vs hand-measured reference. Endpoint reachable via curl + frontend axios.
- Why this first: smallest possible blast-radius (read-only, no DB writes, no UI, no batch); validates the pipeline on real audio; gates everything downstream (transcode verdict needs cutoff_hz that this returns).

**Composite scoring weights — pre-decided value re-verified.**
- Weights `container 0.40 / sample_rate 0.20 / bit_depth 0.15 / spectral_cutoff 0.25` sum = 1.00 (verified). Cutoff weight (0.25) > bit-depth weight (0.15) intentional: cutoff is the load-bearing transcode-fraud signal; bit-depth differentiates only hi-res tier within already-lossless. Container weight (0.40) reflects "lossless container is strongest single signal" but cutoff sub-weight (0.25) prevents a transcoded-FLAC scoring as high as genuine FLAC. Fixture calibration at draftplan can tune within ± 0.10 of these values; if cutoff weight needs to exceed 0.35 to discriminate, that's a sign the verdict-rule (Findings #3) is doing the work and the composite is over-engineered → revert to verdict-only.

**Sister-doc consistency re-check 2026-05-17.**
- `exploring_external-track-match-unified-module.md` Constraints §6 (corrected `_db_write_lock` location) — **still correct**, points to `app/database.py:22`. Its helper-name claim (none) doesn't conflict with this doc's now-corrected `db_lock()` reference.
- `library-extended-remix-finder` proposal for unified `app/data/track_suggestions.db` w/ `kind` column — Open Q 9 here still leans "own DB" (`app/data/track_quality.db`); difference in cardinality (1 per file vs N per track) + retention policy (quality = forever; suggestions = TTL).
- `draftplan_security-api-auth-hardening.md` adds NEW dependency this round (Q12). Coordinate-or-block decision deferred to evaluated_.

**Open-Question movement this round.**
- Q12 added (auth-hardening dependency; PARKED-with-strong-bias-c).
- No Q resolved this round; Q5/6/9/10/11/12 = 6 PARKED-with-bias, Q1/2/3/4/7/8 = 6 RESOLVED. 12 total, 6 RESOLVED (50%), 6 PARKED-with-bias (50%) — meets exploring_ bar.
- For evaluated_ promote: Q12 needs owner decision (option a/b/c); Q9 needs owner ack on own-DB vs unified-DB; Q5/6 calibration deferred to draftplan as documented.

### 2026-05-17 — higher-quality-bar rework: empirical re-measurement + auth-shipped fact + line-number drift + Q10/Q11 resolution + Phase-1a pseudocode

**Line-number + state drift vs Findings #4 (auth-hardening commits shifted main.py).**
- `ALLOWED_AUDIO_ROOTS` now at `app/main.py:130` (was `:138`; `_init_allowed_roots()` at `:132-156`). Re-grepped 2026-05-17.
- `validate_audio_path` at `app/main.py:160-197` (was `:168-203`). `is_relative_to` check at `:183`. Exact-match escape hatch unchanged at `:191-195`, TODO comment at `:189` still flags revisit.
- `/api/file/write` at `app/main.py:582-628` (was `:610-625`; `Depends(require_session)` decorator inline on `:582`). **Canonical copy-paste pattern for quality routes.**
- `app/services.py:177-183` ffprobe pattern unchanged (`subprocess.run([...], capture_output=True, text=True, timeout=10)`).
- `app/database.py:22 / :26-40 / :43-53` (RLock / `db_lock()` / `_serialised`) unchanged.
- `app/analysis_engine.py:1675-1677` (`librosa.feature.spectral_centroid` + `spectral_rolloff` precedent) unchanged; full `detect_mood()` at `:1656-1720`.

**Auth shipped — Q12 collapses.** `app/auth.py` exists (116 lines, verified 2026-05-17). `require_session` is the FastAPI dependency to consume. Wired on 87 routes in `app/main.py` (grep count). `SHUTDOWN_TOKEN` deleted, only historic comment at `app/main.py:935` remains. No interim token needed; no special-case. Q12 resolved → option (b). **Action**: every `/api/quality/*` route lands with `dependencies=[Depends(require_session)]` from day 1.

**Empirical spectral-cutoff measurement on real audio.**
- Probed `Falling Van Buuren.mp3` (MP3, 128 kbps declared, 44.1 kHz, 5:33 / 14.7M samples) on this Windows i7 box, librosa 0.11.0 (interpreter resolves higher than `requirements.txt:34` `librosa==0.10.1` pin — pin/env drift to fix at Phase-1a, separate commit).
- `ffprobe -show_streams -show_format`: **277 ms** wall (vs Findings #3 estimate 50-100 ms — slower).
- `librosa.load(path, sr=None, mono=True)`: **2657 ms** wall (vs estimate 600-1000 ms — 2-4× slower; MP3 decode dominates).
- STFT `n_fft=2048, hop_length=512` + `np.abs` + `np.median(axis=1)` + dBFS normalise + `freqs[mask][-1]` where mask = `med_db > -60`: **555 ms** wall (vs estimate 600-1400 ms — within range, faster end).
- **Total: 3489 ms ≈ 3.5 s/track** (cold cache, single file, no pool). Findings #3 estimate of 1.3-2.5 s/track is **stale**; use 3.5 s/track for capacity planning.
- Measured `cutoff_hz = 14923 Hz` on this MP3-128. Sits ~ 1 kHz below the Findings #1 heuristic prior of "MP3-128 ~ 16 kHz" — within the ± 2 kHz spread observed across MP3-128 encodes (encoder + bitrate variance). Confirms the highest-bin > -60 dBFS algorithm produces sensible values without further tuning, on this one sample.

**Revised cost model.**
- Per track: ~ 3.5 s wall (cold), dominated by `librosa.load` (~ 76% of wall).
- 10k tracks single-thread: **9.7 h** (was 3.6-7 h).
- 10k tracks, 4 ProcessPool workers: **2.4-3 h** (was 0.9-1.75 h). Worker startup amortised over 10k tasks.
- Per-track for replace re-scan: still ~ 3.5 s (single file, fronted by ffprobe-first short-circuit if path unchanged).
- Implication: full-library audit on owner's library (claimed 5-30k tracks per Recommendation exit gate) = 1.2-9 h with 4-worker pool. **Bound the audit at default 6 h soft-limit + resume on next launch** rather than fail-hard.

**Q10/Q11 resolution.**
- Q10 RESOLVED → `ProcessPoolExecutor(max_workers=cpu_count() // 2 or 4)`. Memory-isolation argument decisive: a 6-min FLAC load can hold 200-500 MB float32 PCM, 4 workers = ~ 2 GB worst case; threads would share that heap and risk MemoryError. Precedent: `app/anlz_safe.py` already runs ProcessPool.
- Q11 RESOLVED → separate code path. Quality path 3.5 s/track vs full-analysis 15-30 s/track (5-10× cheaper). Replace-flow may invoke full re-analyse on sample-rate change (Q5 territory, Phase-2 scope).

**Open Q5/Q6/Q9 trigger-park.**
- Q5 trigger = first Phase-2 draftplan creation (needs `analysis_engine.run_full_analysis()` field timing).
- Q6 trigger = first 50-track labelled fixture lands (composite weights re-calibration ±0.10 per weight).
- Q9 trigger = sister-doc `library-extended-remix-finder` reaches evaluated_ (own-DB vs unified-DB final).
- Q5/Q6/Q9 all PARKED-with-bias + explicit trigger → no longer block draftplan-creation autonomy.

**Open-Question movement this round.**
- Q10 RESOLVED (ProcessPool, max_workers=cpu_count()//2 or 4).
- Q11 RESOLVED (separate path, no analysis_engine invocation).
- Q12 RESOLVED (auth shipped → option b, `Depends(require_session)`).
- Q5/Q6/Q9 PARKED-with-bias + explicit trigger.
- 12 total: **9 RESOLVED (75%)**, 3 PARKED-with-trigger (25%). Meets evaluated_ bar (≥ 75% resolution + every PARKED has trigger). Promote-ready pending owner sign-off on stated biases for Q5/Q6/Q9.

**Phase-1a pseudocode (first ~30 LoC; for `app/quality_engine.py` + 1 route in `app/main.py`).**

`app/quality_engine.py`:
```python
"""Phase-1a single-track quality probe. Read-only, no SQLite, no UI.
Returns raw ffprobe + spectral-cutoff fields. Verdict heuristic deferred to Phase-1b.
"""
from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from app.config import FFMPEG_BIN  # "ffmpeg" → derive "ffprobe" same as services.py:178

_FFPROBE = FFMPEG_BIN.replace("ffmpeg", "ffprobe")
_STFT_N_FFT = 2048
_STFT_HOP = 512
_CUTOFF_DBFS_THRESHOLD = -60.0


@dataclass(frozen=True)
class QualityProbeResult:
    path: str
    container: str | None              # ffprobe format_name (e.g. "mp3", "flac")
    codec: str | None                  # ffprobe codec_name on first audio stream
    declared_bitrate: int | None       # ffprobe stream bit_rate, bits/sec
    sample_rate: int | None            # ffprobe stream sample_rate, Hz
    bit_depth: int | None              # ffprobe bits_per_raw_sample (lossless only)
    duration_ms: int | None            # round(format.duration * 1000)
    cutoff_hz: float | None            # highest STFT bin > -60 dBFS (median over time)
    verdict: str                       # "ok" | "unknown_no_ffprobe" | "load_failed"


def probe(path: str) -> QualityProbeResult:
    """Synchronous probe — call via `asyncio.to_thread` from a route. Never raises;
    surfaces failure via `verdict` field. Caller has already validated the path via
    `validate_audio_path`."""
    ...
```

`POST /api/quality/probe` route in `app/main.py` (Pydantic v2; copy `/api/file/write` gating):
```python
class QualityProbeReq(BaseModel):
    path: str

@app.post("/api/quality/probe", dependencies=[Depends(require_session)])
async def quality_probe(r: QualityProbeReq) -> dict:
    file_path = validate_audio_path(r.path)        # 403/404 on out-of-sandbox / missing
    result = await asyncio.to_thread(quality_engine.probe, str(file_path))
    return result.__dict__  # frozen dataclass → plain dict
```

`tests/test_quality_engine.py` — exact pytest signatures:
```python
def test_probe_genuine_flac_returns_lossless_container() -> None: ...
def test_probe_mp3_128_cutoff_within_tolerance() -> None: ...
def test_probe_mp3_320_cutoff_within_tolerance() -> None: ...
def test_probe_transcoded_flac_cutoff_matches_source_mp3() -> None: ...
def test_probe_missing_ffprobe_returns_verdict_unknown(monkeypatch) -> None: ...
def test_probe_rejects_out_of_sandbox_path_via_validate(client) -> None: ...
def test_probe_requires_bearer_token(client) -> None: ...
```

Tolerance defaults: `assert abs(result.cutoff_hz - reference_hz) < 200`. Fixture audio in `tests/fixtures/quality/` (5 files, ≤ 2 MB each — clip to 30 s with ffmpeg `-t 30` to keep repo size sane; cutoff stable over any 30 s music passage).

**Sister-doc consistency re-check 2026-05-17 (this round).**
- `draftplan_security-api-auth-hardening.md` — Phase-1 marked shipped (auth.py + 87 wired routes). Q12 closes. No further coordination needed beyond "consume `require_session`".
- `library-extended-remix-finder` — own-DB vs unified-DB still open (Q9 trigger).
- `external-track-match-unified-module` — M1 function-only API + fpcalc PATH-detect still upstream blocker for Phase-2 (rule 2 chromaprint). Unchanged.

## Options Considered

Required by `evaluated_`. Sketch ≤3 bullets, pros, cons, effort (S/M/L/XL), risk.

Each option quantified with measured numbers (LoC range, dependency count, dogfood library size, weeks-to-first-user-value). Effort scale: S=1-2 wk / M=3-5 wk / L=6-10 wk / XL=10+ wk.

| Option | Phases | LoC range | New deps | Routes added | Effort | First-value timeline | Risk | Goal #3 met |
|---|---|---|---|---|---|---|---|---|
| A — Local-folder-only MVP, full pipeline single-shot | 1+2 fused | ~ 1500-2200 | librosa pin bump + (existing fpcalc PATH-detect) | 4-6 (probe + scan + override + replace) | L | 6-9 wk | Med-high (replace blast-radius pre-dogfood) | Partial (local only) |
| B — Audit-only forever | 1a + 1b | ~ 600-900 | librosa pin bump | 3-4 (probe + scan + override + report) | S | 2-3 wk | Low | No |
| C — Full source matrix single-shot | 1+2+3 fused | ~ 3500-5000 | librosa pin bump + fpcalc + SC OAuth (exists) + Bandcamp/Beatport/Qobuz adapters | 10-14 | XL | 10-16 wk | High (ToS + adapter rot) | Yes (overshoot) |
| **D — Phased (audit → local replace → external)** | **1a / 1b / 2 / 3** | **300-500 (P1a) + 600-900 (P1b) + 800-1200 (P2) + 1200-2000 (P3)** | **librosa pin bump (P1a) + fpcalc (P2)** | **1 (P1a) + 3 (P1b) + 2-3 (P2) + 3-5 (P3)** | **M+M+L** | **2-3 wk to first probe; 4-6 wk to full audit; 9-13 wk to replace** | **Low-med per phase, aggregate Low** | **Yes (P3)** |
| E — Audit + manual-upload replace | 1 + 2-lite | ~ 1000-1500 | librosa pin bump + fpcalc | 4-5 | S+S | 4-6 wk | Very low | **No** (Goal #3 dropped) |

### Option A — Local-folder-only MVP, full pipeline (single shot)

- Sketch: quality scorer + cutoff/noise-floor verdict + duration/chromaprint gate + snapshot/swap. External source = one user "HQ" folder. No network. Ship Phases 1+2 together.
- Pros: zero auth/legal extra surface (Phase-1 auth shipped); end-to-end flow exercised; immediate user value for those w/ HQ archive.
- Cons: replacement plumbing (snapshot/swap/sandbox writes/rule-7 atomic os.replace) lands before scoring is field-validated; ceiling for users w/o HQ archive; no audit-only fallback for users who never reach replace.
- Effort: L (~ 1500-2200 LoC; sequencing reduces refactor cost vs A+D combined).
- Risk: Medium-high — replace blast-radius (irreversible user-audio overwrite if rule check misfires) lands before verdict pipeline has dogfood feedback.

### Option B — Audit-only forever (no replace, ever)

- Sketch: quality scoring + transcode verdict + UI badges + Audit view. No replace flow. User does upgrades manually via the badges.
- Pros: zero replace-blast-radius; ships fast (~ 2-3 wk); pure read-side; satisfies the privacy-conservative user.
- Cons: half-feature; spectral-analysis investment doesn't pay back without close-the-loop replace; sister-docs (`extended-remix-finder`, `remix-detector`) still need chromaprint integrated elsewhere; Goal #3 (external candidate) never met.
- Effort: S (~ 600-900 LoC).
- Risk: Low.

### Option C — Full source matrix in one go (SC + Bandcamp + Beatport + Qobuz + local)

- Sketch: parallel adapter for every store behind unified search interface. Audit + replace + all adapters concurrently.
- Pros: maximum user value if all five adapters survive ToS + auth maintenance.
- Cons: huge scope (~ 3500-5000 LoC); per-store auth flows; legal/TOS care per adapter; long path to first user value (10-16 wk); high partial-implementation rot risk; paid stores need surface-link-only by Constraint anyway, so most adapter work is for SC + local.
- Effort: XL.
- Risk: High — adapter maintenance is ongoing, scrapers break.

### Option D — Phased (audit → local replace → external surfaces) **[recommended]**

- Sketch: **Phase 1a** = single-track `POST /api/quality/probe` (read-only, no SQLite, no UI). **Phase 1b** = bulk scan + sidecar SQLite + UI badges + verdict heuristic. **Phase 2** = local-HQ-folder replace with safety rules 1-7. **Phase 3** = SC adapter + Bandcamp/Beatport/Qobuz as surface-link-only. Each phase = standalone shippable.
- Pros: blast-radius code (Phase 2) lands only after scoring proven by Phase 1b dogfooding; sister-docs consume Phase 1 deliverables (shared adapter registry from `external-track-match-unified-module`); each phase = independent gate; small reversible cuts; **Phase 1a ships in 2-3 wk** for first dogfood feedback.
- Cons: longer total timeline; Phase 1 alone without "the point" (replace) may feel incomplete to users; coordinating with sister-docs adds calendar dependency.
- Effort: M (P1) + M (P2) + L (P3).
- Risk: Low-medium per phase; aggregate Low.

### Option E — Audit + manual-upload-only replace (no auto-download)

- Sketch: Phase 1 audit + badges. Phase 2 = user drag-drops replacement file into UI; we run safety rules + snapshot/swap. No external-source scanner at all (M1+M2).
- Pros: zero adapter / network surface; close-loop value with much smaller scope than Option D; user already has the HQ file in hand by the time they engage.
- Cons: doesn't surface "an upgrade exists out there" (Goal #3 unmet); requires user-driven discovery; loses the "find me upgrades" angle vs spek.exe differentiation.
- Effort: S (P1) + S (P2).
- Risk: Very low. But Goal #3 (external candidate search) is the differentiator — Option E drops it.

## Recommendation

**Option D**, phase deliverables + exit gates pinned. Phase 1 broken into **two slices** to make first deliverable concrete:

**Phase 1a — Single-track probe** (smallest shippable; gates Phase 1b)
- Deliverable: new file `app/quality_engine.py` (~ 300-500 LoC; `probe(path: str) -> QualityProbeResult` + `QualityProbeResult` frozen dataclass + internal `_run_ffprobe()` + `_compute_cutoff_hz()`) + 1 route + 1 Pydantic model in `app/main.py` + `tests/test_quality_engine.py` w/ 5-track fixture in `tests/fixtures/quality/` (each ≤ 2 MB, clipped to 30 s — cutoff stable over any 30 s music passage).
- Out of scope: no sidecar SQLite, no UI, no transcode verdict (just raw numbers; `verdict ∈ {"ok", "unknown_no_ffprobe", "load_failed"}`), no batch.
- Diff shape: `app/quality_engine.py` (new, ~ 300-500 LoC); `app/main.py` (+ ~ 20 LoC: `QualityProbeReq` Pydantic model, `quality_probe` route at end of audio-route block, gated `dependencies=[Depends(require_session)]`); `tests/test_quality_engine.py` (new, 7 test functions per pseudocode in Findings #5); `tests/fixtures/quality/` (5 audio fixtures); `requirements.txt:34` (`librosa==0.10.1` → `librosa==0.11.0` separate commit per Schicht-A pin discipline; CVE-check + `pytest tests/test_analysis.py` clean before bump).
- Pseudocode (signature + first ~ 30 LoC) in Findings #5 block above.
- **Exit gate to Phase 1b:** `pytest tests/test_quality_engine.py -v` all 7 tests green: `test_probe_genuine_flac_returns_lossless_container`, `test_probe_mp3_128_cutoff_within_tolerance` (`abs(cutoff - ref) < 200 Hz`), `test_probe_mp3_320_cutoff_within_tolerance`, `test_probe_transcoded_flac_cutoff_matches_source_mp3`, `test_probe_missing_ffprobe_returns_verdict_unknown` (monkeypatch PATH), `test_probe_rejects_out_of_sandbox_path_via_validate` (403), `test_probe_requires_bearer_token` (401). Endpoint reachable via `curl -X POST http://127.0.0.1:8000/api/quality/probe -H "Authorization: Bearer $LMS_TOKEN" -H "Content-Type: application/json" -d '{"path": "..."}'` and via frontend axios (`api.js` already attaches Bearer). Performance: < 5 s wall per probe on a 6-min track (matches empirical 3.5 s + budget).

**Phase 1b — Full audit** (deliverable shippable standalone after Phase 1a)
- Deliverables: `track_quality` sidecar SQLite schema (`app/data/track_quality.db`, schema in Q9 PARKED-with-bias block above) + bulk endpoint `POST /api/quality/scan` w/ resumable `(abs_path, mtime_ns, size_bytes, codec_pipeline_version)` skip key + transcode verdict (Findings #3 formula) + noise-floor pipeline + per-track user override `POST /api/quality/override/{path_sha1}` + UI badges + standalone Quality-Audit view + audit-progress endpoint `GET /api/quality/scan/status`. Concurrency = `ProcessPoolExecutor(max_workers=cpu_count()//2 or 4)` per Q10 resolution.
- Cross-cutting deliverable: shared `app/external_track_match.py` from sister-doc `external-track-match-unified-module` M1 (Phase-1b consumes only `Candidate` dataclass + adapter-registry shape; does NOT yet consume fuzzy/chromaprint).
- All routes gated `dependencies=[Depends(require_session)]` per Q12 resolution.
- **Exit gate to Phase 2:** Phase 1 audit dogfooded on owner's 5-30k library; verdict precision ≥ 0.95 on labelled 50-track fixture (covers Q6 weight calibration trigger); zero crash/hang reports across 2 weeks; performance ≤ 6 h on owner's library w/ 4-worker pool (was "≤ 2 h"; revised per empirical 3.5 s/track measurement); sidecar SQLite survives Tauri restart cycle.

**Phase 2 — Local-HQ-folder replace** (close-the-loop)
- Deliverables: local "HQ folder" scanner reuses Phase 1 pipeline + safety rules 1-7 (duration <1s / chromaprint / sample-rate / snapshot / per-track-confirm / Rekordbox-closed / atomic-swap) + `.upgrade-snapshots/<date>/` + "Restore from snapshot" UI + post-swap auto-align cue-offset pass + replace-suggestion modal (per safety-rule 5 spec).
- Cross-cutting: chromaprint (`fpcalc`) PATH-detect via shared module; fail-closed if missing (rule 2).
- **Exit gate to Phase 3:** 100 replaces dogfooded across genuine + bandlimited-master + cross-sample-rate cases without metadata loss; "Restore from snapshot" tested on ≥ 5 reverts; disk-pressure prune UI shipped.

**Phase 3 — External-source adapters** (discovery beyond local)
- Deliverables: SoundCloud adapter (reuses existing OAuth + matcher); Bandcamp / Beatport / Qobuz as surface-link-only (no scrape, no download). DRM sources (SC Go+) flagged "available there", never extracted. Adapter registry mutation hooks via shared module.
- **Exit gate to graduation:** recall metric (Goals §3) ≥ 0.70 on labelled 100-track fixture; zero ToS-breach paths (paid-content scraping); user-feedback shows replace flow is preferred over manual.

**Cross-cutting (all phases):**
- Consume `app/external_track_match.py` from sister-doc `external-track-match-unified-module` (M1 function-only API; M1 PATH-detect fpcalc). Do not fork.
- Coordinate UI shell with sister-docs `library-extended-remix-finder` + `analysis-remix-detector` → single "Library Audit" view with `kind` tabs (quality / extended / remix) rather than three competing panels.

**Blockers before `evaluated_` (status 2026-05-17):**
- Q1/Q2/Q3/Q4/Q7/Q8/Q10/Q11/Q12 — **RESOLVED** (9/12 = 75%).
- Q5 (re-analyse policy) — PARKED-with-bias + trigger = first Phase-2 draftplan. Does not block Phase-1 draftplan.
- Q6 (composite weights calibration) — PARKED-with-bias + trigger = first 50-track labelled fixture. Does not block Phase-1a; blocks Phase-1b verdict-tune commit only.
- Q9 (own-DB vs unified-DB) — PARKED-with-bias (own DB) + trigger = sister-doc `library-extended-remix-finder` reaches evaluated_. Does not block Phase-1a (no SQLite); blocks Phase-1b schema commit only.
- Owner acks needed for evaluated_: (a) no backup-engine revival (`.upgrade-snapshots/` consolidated, inside `ALLOWED_AUDIO_ROOTS[0]`); (b) own `app/data/track_quality.db` (Q9 bias); (c) Option D phased approach (vs A / B / C / E); (d) Phase-1a deliverable shape per pseudocode in Findings #5.
- Hard upstream blocker for Phase-2 only: shared `app/external_track_match.py` lands first (sister-doc `external-track-match-unified-module` M1). Without it, rule 2 (chromaprint required for replace) cannot ship. Does not block Phase-1.

---

## Implementation Plan

Required from `implement/draftplan_`. Concrete enough that someone else executes without re-deriving.

### Scope
- **In:** …
- **Out:** …

### Step-by-step
1. …

### Files touched
- …

### Testing
- …

### Risks & rollback
- …

## Review

Filled at `review_`. Unchecked box or rework reason → `rework_`.

- [ ] Plan addresses all goals
- [ ] Open questions answered or deferred
- [ ] Risk mitigations defined
- [ ] Rollback path clear
- [ ] Affected docs identified (`architecture.md`, `FILE_MAP.md`, indexes, `CHANGELOG.md`)

**Rework reasons:**
- …

## Implementation Log

Filled during `inprogress_`. Dated entries. What built / surprised / changed-from-plan.

### YYYY-MM-DD
- …

---

## Decision / Outcome

Required by `archived/*`.

**Result**: implemented | superseded | abandoned
**Why**: …
**Rejected alternatives:**
- …

**Code references**: PR #…, commits …, files …

**Docs updated** (required for `implemented_`):
- [ ] `docs/architecture.md`
- [ ] `docs/FILE_MAP.md`
- [ ] `docs/backend-index.md` (if backend changed)
- [ ] `docs/frontend-index.md` (if frontend changed)
- [ ] `docs/rust-index.md` (if Rust/Tauri changed)
- [ ] `CHANGELOG.md` (if user-visible)

## Links

- Code (existing, **re-verified 2026-05-17 with line-number drift fix**):
  - Auth (NEW): `app/auth.py:78-92` (`SESSION_TOKEN` mint + MainProcess gate), `app/auth.py:95-115` (`require_session` Bearer-token dep), `app/security_compare.py` (`safe_compare` constant-time), `tests/test_auth.py` (20+ tests; precedent for our `test_probe_requires_bearer_token`).
  - Sandbox: `app/main.py:130` (`ALLOWED_AUDIO_ROOTS` list, was `:138`), `app/main.py:132-156` (`_init_allowed_roots()`), `app/main.py:160-197` (`validate_audio_path`, `is_relative_to` at `:183`, exact-match escape hatch at `:191-195`, TODO at `:189`), `app/main.py:582-628` (`/api/file/write` sandbox-write canonical pattern, was `:610-625`).
  - DB lock (unchanged): `app/database.py:22` (`_db_write_lock` RLock), `app/database.py:26-40` (`db_lock()` ctx-manager), `app/database.py:43-53` (`_serialised` private decorator).
  - librosa precedent (unchanged): `app/analysis_engine.py:1656-1720` (`detect_mood()`), `:1675-1677` (`librosa.feature.spectral_centroid` + `spectral_rolloff`).
  - ffprobe pattern (unchanged): `app/config.py:6` (`FFMPEG_BIN = "ffmpeg"`), `app/services.py:177-183` (canonical subprocess+timeout=10 pattern).
  - Quarantine/sidecar precedents: `app/anlz_safe.py` (SafeAnlzParser ProcessPoolExecutor max_workers=1), `app/anlz_sidecar.py` (sidecar-artefact pattern).
  - Deps drift: `requirements.txt:34` (pins `librosa==0.10.1` but local env resolves `librosa 0.11.0` — Phase-1a separate-commit pin bump).
  - **SHUTDOWN_TOKEN deleted** 2026-05-17 (only historic comment at `app/main.py:935` remains).
- External docs: <chromaprint / fpcalc upstream docs — fill at exploring_>; ffprobe `format=duration` / `bit_rate` field reference; Rekordbox supported sample rates
- Related research: `library-extended-remix-finder`, `analysis-remix-detector`, `external-track-match-unified-module`, `implement/draftplan_security-api-auth-hardening` (Q12 dependency)
