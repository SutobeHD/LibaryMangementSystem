---
slug: analysis-remix-detector
title: Detect remix / edit / bootleg variants of a track
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
tags: [variants, taxonomy, chromaprint, fuzzy-match, sidecar-db]
related: [library-extended-remix-finder, library-quality-upgrade-finder, external-track-match-unified-module, metadata-name-fixer]
ai_tasks: false
---

# Detect remix / edit / bootleg variants of a track

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — research/idea_ — section fill (research dive)
- 2026-05-15 — research/idea_ — option refinement after Problem framing
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (discovered existing src-tauri/src/audio/fingerprint.rs; corrected rapidfuzz→SequenceMatcher; M1/M2/M3 with 200-track fixture)
- 2026-05-17 — research/exploring_ — deep exploration pass toward evaluated_-ready: Rust IPC bridging path resolved (Python-side via `_fingerprint_python_fallback` already in `app/main.py:3746`, Rust path requires Tauri window context — design implication); fixture infra gap surfaced (`tests/fixtures/` does not exist — must scaffold); pyacoustid 1.3.0 MIT licence + maintainer (sampsyo) verified via PyPI; cross-doc enum alignment confirmed (12 values both docs); 1 new OQ added on Python↔Rust fingerprint bridging
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

DJ libraries accumulate many tracks that are **variants of the same original** — Radio Edit, Extended Mix, "Some Artist Remix", VIP, Bootleg, Dub, Acapella, Instrumental, Club Mix. Today these sit as independent library rows with **no relationship signal**, making it hard to (a) know whether a usable version is already present, (b) pick the right variant for a set context, (c) dedupe or group intelligently in the Library/Ranking UI. This doc designs the detector that **classifies each track's variant type** and **links related variants** — both within the local library and against external metadata sources where the canonical original may not be local yet.

## Goals / Non-goals

**Goals** (each with measurable target)
- Classify each track with variant label from fixed enum `(original, extended, radio, club, dub, instrumental, acapella, vip, remix, bootleg, edit, mashup)` — **canonical order from sister-doc `external-track-match-unified-module` Findings 2026-05-17 line 177**, "stem family before derivation family". Target: ≥ 95 % precision on labelled fixture of 200 tracks drawn from real library; recall ≥ 80 % (untagged "Original Mix" assumed when no tail-parenthetical found).
- Detect two local tracks as versions of same work via shared `(normalised_root, primary_artist)` key. Target: ≥ 90 % precision on hand-graded variant clusters; per-cluster size ≤ 12 (UI grouping cap).
- Per-relation confidence score in `[0.0, 1.0]`. Buckets: ≥ 0.75 auto-group, 0.5–0.74 suggestion, < 0.5 hidden. Calibration done against 200-track fixture (≈ 80 clusters) before promote to `evaluated_`. See Findings #3 for fixture composition.
- M1 title-only pass: full 30 k-track scan completes < 5 s on dev hardware (cold cache, single thread). M2 fingerprint pass: ≤ 0.5 s/track wall, ≤ 3 req/s AcoustID budget honoured.
- Persist relations as track-to-track edges in sidecar `app_data/variants.db` (see Findings 2026-05-15 #2). UI grouping = downstream JOIN, not this module's concern.
- Identify canonical original for a remix when present locally (parent edge); else mark `parent_track_id=NULL` + `source=external-mb` if M2 lookup resolves.

**Non-goals** (deliberately out of scope)
- Finding *missing* extended/remix versions to acquire — `idea_library-extended-remix-finder`.
- Auto-correcting track titles — `idea_metadata-name-fixer`.
- Higher-bitrate replacement of same edit — `idea_library-quality-upgrade-finder`.
- Owning the fuzzy matcher / chromaprint wrapper / adapter registry — `idea_external-track-match-unified-module` (this doc consumes that API).
- Full music-recognition over arbitrary audio (Shazam-class); we only resolve tracks already in library or directly looked-up.
- Cover-detection across genres (e.g. orchestral cover of pop song).
- Writing variant labels back to Rekordbox `master.db` / ID3 tags (read-only consumer; mutation is metadata-name-fixer's blast-radius).

## Constraints

External facts bounding the solution. Each cited.

- **Local-first** — title-only pass must work offline. External lookups (AcoustID, MusicBrainz, Discogs) opt-in, throttled, cacheable. Cite: `.claude/rules/coding-rules.md` "Secrets & paths" + `docs/SECURITY.md` (offline operation is a Schicht-A invariant).
- **AcoustID free tier** — 3 req/s per app key; bulk `lookup?meta=recordings+releasegroups` returns MB IDs in one call. Cite: <https://acoustid.org/webservice>. `pyacoustid` wrapper exposes `lookup(api_key, fingerprint, duration, meta=...)` + `match(api_key, path, parse=True)`; latter shells out to `fpcalc` internally. Not currently in `requirements.txt` (re-verified 2026-05-17: `python -c "import pyacoustid"` → `ModuleNotFoundError: No module named 'pyacoustid'`).
- **`fpcalc` (libchromaprint) NOT bundled today** — verified `Grep backend.spec` → no matches. Sister-doc `idea_external-track-match-unified-module` Constraints line 50 confirms same. Bundling = ~3 MB × 3 OS PyInstaller decision (Schicht-A dep-pinning crosses backend.spec, requirements.txt, per-platform binaries). M1 = PATH-detect + degrade to title-only if missing; M2 PATH-detect kept; M3 bundle decision.
- **Existing Rust fingerprint pipeline already in tree** — `src-tauri/src/audio/fingerprint.rs` (**398 LOC**, re-verified 2026-05-17) ships a Chromaprint-style in-house fingerprint (32-band Mel × Goertzel, Hamming similarity ≥ `MIN_FP_LEN=4` words at `fingerprint.rs:48`, fingerprint = `Vec<u32>`, ~128 ms frames @ 11025 Hz mono, 5-min cap). Public API (re-Greped 2026-05-17): `pub async fn fingerprint_track(path: String) -> Result<Vec<u32>, String>` at `fingerprint.rs:321`; `pub async fn fingerprint_batch(paths: Vec<String>, window: tauri::Window) -> Result<HashMap<String, Vec<u32>>, String>` at `fingerprint.rs:344` (key = input path); `pub fn hamming_similarity(a: &[u32], b: &[u32]) -> Option<f32>` at `fingerprint.rs:287`. Tauri commands registered at `src-tauri/src/main.rs:454-455`. Offline, no `fpcalc` shellout. NOT bit-compatible with AcoustID — cannot query AcoustID with Rust fingerprints. Two-tier fingerprint design needed (see Findings #3): Rust = local-cluster only; Python `fpcalc` = external lookup.
- **Rust↔Python bridging — Tauri-window-only** — `fingerprint_batch` requires `tauri::Window` for progress events (`fingerprint.rs:344-346`; injected by Tauri runtime, not constructable from sidecar). Sidecar Python (`app/main.py`) cannot invoke directly; no Tauri window context in sidecar process. Existing duplicate-finder workaround at `app/main.py:3730` (`_fingerprint_python_fallback`, **re-verified 2026-05-17 post-Phase-1 auth shift**; doc-side numbers in prior pass were stale by +16 lines): librosa decode → MD5 of first 30 s PCM (catches re-encodes, not remixes). Same Hamming-similarity logic re-implemented pure-Python at `app/main.py:3783` (`hamming_sim_py` operating on `list[int]` from Rust). Implication for M2: either (a) Tauri frontend calls `fingerprint_batch`, posts results to backend `POST /api/variants/fingerprints/ingest` endpoint, OR (b) Python sidecar reuses MD5 fallback only (loses remix-detection — useless for OQ-N#11), OR (c) extract Rust fingerprinter into a `cdylib` callable from Python via `pyo3`. (a) is the chosen path — see Findings #4 + OQ-N#11.
- **MusicBrainz** — 1 req/s per IP, requires User-Agent header `app/version (contact)`. Recording-recording relation types of interest: `remix`, `edited version`, `mashes up`, `samples`, `cover recording`. Cite: <https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting>.
- **Discogs** — 60 req/min authenticated, less reliable taxonomy but covers white-labels MB lacks. OAuth token in `.env`; not currently wired. Marker for M2/M3 plugin slot.
- **Library size target** — 5 k–30 k tracks (per sister-doc `library-extended-remix-finder` Constraints line 57). Title-pass < 5 s cold scan. Fingerprint pass batched as background job, never blocks `analysis_engine.py`.
- **Dirty titles** — nested parens, mixed brackets, semicolons, emoji, non-Latin scripts (Cyrillic, Japanese), missing parens around `feat.`, trailing-dash `- Extended Mix`, multi-suffix `(...) (...)`. Catalogue in Findings #1.
- **No `master.db` writes** — feature is read-mostly. `_db_write_lock` lives at `app/database.py:22` (RLock; re-Greped 2026-05-17 — context manager at `database.py:27`, decorator at `database.py:44`). NOT `app/main.py` — sister-doc `external-track-match-unified-module` Constraints corrects same misref. Relations live in sidecar `app_data/variants.db` (see Findings #2); only sidecar opener acquires its own lock (`_variants_db_write_lock` proposed in M1 pseudocode below).
- **Fuzzy matcher** — `SoundCloudSyncEngine._fuzzy_match_with_score(sc_title, sc_artist, local_tracks)` at `app/soundcloud_api.py:566` (**re-Greped 2026-05-17**), threshold `0.65` hardcoded at line `583`, callers at `563` + `726`. Implementation: `difflib.SequenceMatcher(None, sc_combined, local_combined).ratio()` (import at `soundcloud_api.py:16`) over `"artist - title"` combined haystacks (NOT `rapidfuzz token-set ratio` — sister-doc `extended-remix-finder` Findings #1 misstates this; corrected in `external-track-match-unified-module` Constraints line 51). **No independent artist gate** — artist contribution is via combined string only. M1 consumes the unified-module pure-function wrapper; if this doc needs strict artist-match (e.g. for cross-artist remix detection where root collides), the artist-gate flag goes on the unified-module API, not this module.
- **Schicht-A dep pinning** — any new dep (`pyacoustid`, `python3-discogs-client`, `musicbrainzngs`) lands as `==X.Y.Z` in `requirements.txt` with CVE check + `pytest` green. Cite: `.claude/rules/coding-rules.md`.
- **rbox quarantine** (`app/anlz_safe.py`) — rbox 0.1.5/0.1.7 panics; this feature does NOT call rbox (read-only on already-loaded `master.db` rows + sidecar writes). No `ProcessPoolExecutor` needed inside the module.

## Open Questions

Numbered. Each resolvable (yes/no or X vs Y).

1. **Storage** — RESOLVED. Sidecar `app_data/variants.db` (see Findings #2). `master.db` rejected: Rekordbox schema-validation risk, `_db_write_lock` contention, no recovery story when rbox bumps schema. Sidecar SQLite scales, decouples lifecycle, no rbox interaction. OQ1 resolved.
2. **Canonical-original picker** — RESOLVED. Order: (a) presence of `Original Mix` / unsuffixed token in title → score +0.3, (b) earliest release date if available → +0.2, (c) shortest normalised title in cluster → +0.1, (d) user-pinned `is_canonical=1` flag overrides all. Tiebreak: track with lowest `master.db` `ID` (deterministic). Implementation in M1.
3. **Confidence floor for UI surfacing** — RESOLVED. ≥ 0.75 auto-group, 0.5–0.74 suggestion, < 0.5 hidden. Configurable via `settings.json` `variant_detector.confidence_floor` (default 0.5). See Recommendation.
4. **Title normalisation order** — RESOLVED in Findings #2: strip `feat./ft./featuring/with` first, then extract tail parenthetical, then normalise root casing + accents (NFC). Collab markers (`&`, `vs.`, `x`) stay in root as canonical artist credit.
5. **Fingerprint pass scope** — RESOLVED. Opt-in globally via `settings.json` `variant_detector.fingerprint_enabled` (default `false` in M1, `true` once M2 ships + `fpcalc` PATH-detected). Per-folder override is YAGNI — deferred until ≥ 1 user requests.
6. **Mashup multi-parent representation** — RESOLVED in Findings #2: multiple rows in `track_variants` sharing `track_id` with distinct `parent_track_id`. No separate `mashup_of` table. M1 emits single best parent; M2 fingerprint pass can emit second parent.
7. **AcoustID match without MBID** — RESOLVED in Findings #2: accept bare fingerprint cluster as `source=acoustid-cluster`, confidence 0.6; upgrade to 0.95 if MBID later attaches (background job re-queries).
8. **Re-run invalidation policy** — PARKED. Decide in draftplan once at-import hook lands — touches `analysis_engine.py` lifecycle. Default M1: explicit "rescan variants" button; auto-invalidate only on title/artist mutation by `metadata-name-fixer` (event hook contract TBD in that sister-doc).
9. **UI confidence representation** — PARKED. Out-of-scope for this module (read-only data producer; UI is downstream consumer). Decide in UI-grouping sister-doc when one exists. Sidecar schema exposes raw float — any UI form derivable.
10. **Variant taxonomy enum vs free-tagged** — RESOLVED. Fixed `Enum` (12 values per Goals). Unknown variants fall through to `remix` (catch-all). Justification: sister-doc `external-track-match-unified-module` OQ1 calls for same — taxonomy is shared module's contract. **Cross-doc enum cross-check (2026-05-17)**: both docs list identical 12-value set `{original, extended, radio, club, dub, instrumental, acapella, vip, remix, bootleg, edit, mashup}`. Sister-doc OQ1 resolved as `frozen dataclass VersionTag(label: Literal[...], remixer: str | None, modifiers: tuple[str, ...])`. This doc consumes `VersionTag.label` directly as `variant_label` column in `track_variants`. No taxonomy drift.
11. **Rust fingerprint Python bridging** — RESOLVED (M2). Frontend (Tauri context) invokes `fingerprint_batch(paths)` → receives `HashMap<String, Vec<u32>>` → POSTs to new backend `POST /api/variants/fingerprints/ingest` (Pydantic `{path: list[int]}` body) → backend persists into `track_variants.source='rust-fp-cluster'` rows + runs cluster job. Rejected: (b) MD5-only fallback (loses remix-detection signal), (c) `pyo3` cdylib (Schicht-A binary-bundling crosses 3 OS + sidecar packaging, ROI poor when frontend bridge suffices). Existing pattern reused: `_run_duplicate_scan` at `app/main.py:3844` already orchestrates background fingerprint job + result storage in `_dup_jobs`; M2 wires a sister endpoint that ingests pre-computed fingerprints instead of computing them sidecar-side.

## Findings / Investigation

### 2026-05-15 — initial audit

**Title-pattern catalogue.** The dominant signal is the parenthetical/bracketed suffix. A two-pass parser is needed: (1) find the outermost balanced `()` or `[]` group at the tail, (2) tokenise its contents. Patterns observed in real DJ libraries:

- Pure variant: `(Original Mix)`, `(Extended Mix)`, `(Radio Edit)`, `(Club Mix)`, `(Dub)`, `(Dub Mix)`, `(Instrumental)`, `(Acapella)`, `(VIP)`, `[VIP Mix]`, `(<Year> Edit)` e.g. `(2024 Edit)`.
- Remixer-bearing: `(<Artist> Remix)`, `(<Artist> Bootleg)`, `(<Artist> Edit)`, `(<Artist> Rework)`, `(<Artist> Flip)`, `(<Artist> Refix)`, `(<Artist> Mashup)`.
- Compound: `(<Artist> Extended Remix)`, `(<Artist> Club Mix)`, `(<Artist> Dub Mix)`, `(<Artist> Instrumental Remix)`.
- Featuring/credits (not variants — must not be misread as a remix): `feat. X`, `ft. X`, `featuring X`, `with X`, `& X`, `vs. X`, `x X`.
- Edge cases: nested `((Original Mix) Extended)`, mixed brackets `[Extended Mix]`, semicolon-separated `(Original Mix; Remastered 2020)`, multiple suffixes `(<Artist> Remix) (Extended)`, emoji in artist name, non-Latin scripts (Cyrillic, Japanese), missing parens (`- Extended Mix` after a dash).

**Audio fingerprinting.** Chromaprint via `fpcalc` binary outputs ~120-char compressed fingerprint + duration; runs ~0.3 s per track at default 120 s sample. AcoustID matches fingerprints to MBID clusters. Two tracks with the same MBID-recording = same recording; two MBIDs in the same MBID-work = same composition (i.e. remix relation in MB sense). Self-contained mode = fingerprint-similarity only (Hamming distance on the integer arrays) — works for "is this the same recording" but not for "is this a remix of that".

**External relations.** MusicBrainz `recording-recording` relation types of interest: `remix`, `edited version`, `mashes up`, `samples`, `cover recording`. Discogs has `Remix`, `Edit`, `Bootleg`, `Mashup` as release-level credits, less reliable but covers white-labels MB doesn't have.

**Confidence tiers.** Title-pattern + same normalised root title = 0.5–0.7. Add same artist on root = 0.75. Add fingerprint-cluster match = 0.9. Add MB `remix of` edge = 0.95.

### 2026-05-15 — option-refinement after Problem framing

**Use-case prioritisation.** The Problem implies four flows, not all need external sources:
- (a) *"Do I already have an Extended of this Radio Edit?"* — within-library, title-pass. M1.
- (b) *"This Bootleg — find canonical original"* — within first, external fallback. M1 within, M2 external.
- (c) *"Group all 4 versions of Track X in the UI"* — within-library grouping by `(normalised_root, primary_artist)`. M1.
- (d) *"Warn me when I import a 5th version I might not need"* — at-import classification hook (read-only badge, non-blocking). M1.

Flows (a), (c), (d) are 100% local — justifying M1 before fingerprinting.

**Title-pattern catalogue — concrete regex shapes** (anchored at title tail, case-insensitive):
- Pure variant: `\((Original|Extended|Radio|Club|Dub|Instrumental|Acapella|VIP)(\s+(Mix|Edit|Version|Cut))?\)$`
- Year-edit: `\((19|20)\d{2}\s+(Edit|Remaster(ed)?|Version)\)$`
- Remixer-bearing: `\(([^()]+?)\s+(Remix|Bootleg|Edit|Rework|Flip|Refix|Mashup|Dub)\)$`
- Compound: `\(([^()]+?)\s+(Extended|Club|Dub|Instrumental)\s+(Remix|Mix)\)$`
- Bracket variant: `\[([^\[\]]+?)\s+(Remix|VIP|Mix|Edit)\]$`
- Nested: `\(\(([^()]+)\)\s+([^()]+)\)$`
- Semicolon-segmented: `\(([^;()]+);\s*([^()]+)\)$`
- Trailing-dash (no parens): `\s[-–—]\s(Extended|Radio|Club|Dub|Instrumental|Acapella|VIP|Original)(\s+(Mix|Edit|Version))?$`
- Multi-suffix: `\(([^()]+)\)\s*\(([^()]+)\)$`
- Featuring (NOT variant — strip pre-classification): `\s+(feat\.?|ft\.?|featuring|with)\s+`
- Collab markers (preserve in artist field): `\s+(&|vs\.?|x)\s+` (`x` ambiguous — require whitespace+capital)
- Language variants: `\((Remix|Mix)\s+(von|de|por|di|by)\s+([^()]+)\)$` (DE/ES/PT/IT/EN)

**External-source dependency-impact.** `fpcalc` is not currently bundled (`backend.spec` no reference). Three options: (1) bundle ~3 MB per-platform binaries under `app/bin/fpcalc/`; (2) PATH-detect, degrade to title-only if missing; (3) separate optional installer. (2) is lowest-risk M2 entry. MusicBrainz 1 req/s × 50k cold scan = ~14 h sequential — batch via AcoustID `lookup?meta=recordings+releasegroups` (MB IDs in one call), persist indefinitely. Cache key = `(fpcalc_fingerprint, duration_rounded)`.

**Output-data-model.** `master.db` is Rekordbox-managed — custom tables risk schema-validation rejection. Preferred: sidecar `app_data/variants.db` with `track_variants(track_id, variant_type, parent_track_id, confidence, source, computed_at)` keyed on the same `track_id`. Decouples relation lifecycle from Rekordbox writes, no `_db_write_lock` contention. UI grouping JOINs across both DBs (confirm pattern against `app/database.py`). Resolves OQ1; OQ6 (mashup multi-parent) maps to multiple rows sharing `track_id` with distinct `parent_track_id`.

OQ4 (normalisation order) **resolvable per regex catalogue**: strip `feat./ft./featuring/with` first, extract tail parenthetical, normalise root casing/accents — collab markers (`&`, `vs.`, `x`) stay in root as part of canonical artist credit. OQ7 (AcoustID without MBID) **resolvable per cache-key design**: accept bare fingerprint cluster as `source=acoustid-cluster` low-confidence (0.6) relation, upgrade to 0.95 if MBID later attaches.

### 2026-05-15 — exploring_-ready cross-verification

**Existing Rust fingerprint pipeline — major shift to M2 design.** Discovered `src-tauri/src/audio/fingerprint.rs` (399 LOC) already ships a Chromaprint-style in-house fingerprint: Symphonia decode → 11.025 kHz mono → 32-band Goertzel Mel × 128 ms windows → `u32` hash words, Hamming similarity. Tauri commands `fingerprint_track(path)` and `fingerprint_batch(paths)` already exposed (per `docs/rust-index.md`). **Not bit-compatible with AcoustID** (different algorithm, different output shape) — cannot lookup MB IDs from these. Implication: two-tier fingerprint plan in M2. (a) Use Rust fingerprint for local-cluster ("are these two local tracks the same recording / cover / remix-pair?") — already wired, no dep. (b) Use `fpcalc` + AcoustID for canonical-original / MB-relation lookup (M2 opt-in). Each answers a different question; both useful.

**Sister-doc coordination locked.** `idea_external-track-match-unified-module` owns: fuzzy matcher extraction (`SoundCloudSyncEngine._fuzzy_match_with_score` → pure function), title-stem extractor, version-tag taxonomy enum, `fpcalc` PATH-detect wrapper, adapter registry. This doc consumes that API; does NOT fork it. Variant-detector module = `app/version_classifier.py` (per Recommendation §M1), depends on `app/external_track_match.py` (per sister-doc Recommendation §Option C). Sister-doc `library-extended-remix-finder` reads `track_variants.variant_label` from this doc's sidecar rather than re-deriving — closes the duplicate-effort loop.

**`pyacoustid` Python wrapper specifics** (decision-relevant for M2 dep). API: `acoustid.lookup(apikey, fingerprint, duration, meta=['recordings', 'releasegroups'])` returns parsed match dicts; `acoustid.match(apikey, path, parse=True)` shells out to `fpcalc` internally + lookup in one call. Latest stable 1.3.0 (Apr 2024). Depends on `audioread` (already pinned via librosa stack — `backend.spec` collects `audioread`). MIT licence, single maintainer (sampsyo), low CVE history. For M2 PATH-detect path: import-time `shutil.which('fpcalc')` check; if None → skip module load + log warning; if found → wire `acoustid.match`. Defer pinning decision to M2 draftplan.

**`variants.db` schema sketch** (locks sidecar shape so sister-docs can JOIN):
```
CREATE TABLE track_variants (
  track_id INTEGER NOT NULL,           -- Rekordbox DjmdContent.ID
  variant_label TEXT NOT NULL,         -- enum value, see Goals
  normalised_root TEXT NOT NULL,       -- post feat-strip + NFC + casefold
  remixer TEXT,                        -- parsed from tail-parenthetical, nullable
  parent_track_id INTEGER,             -- canonical original, NULL = is-canonical OR unknown
  confidence REAL NOT NULL,            -- [0.0, 1.0]
  source TEXT NOT NULL,                -- 'title-regex' | 'rust-fp-cluster' | 'acoustid-cluster' | 'mb-relation'
  computed_at TEXT NOT NULL,           -- ISO-8601
  is_canonical INTEGER DEFAULT 0,      -- user-pinned override (OQ2)
  PRIMARY KEY (track_id, source, parent_track_id)
);
CREATE INDEX idx_variants_root ON track_variants(normalised_root);
CREATE INDEX idx_variants_parent ON track_variants(parent_track_id);
```
Composite PK allows multiple rows per track (mashup multi-parent per OQ6). `source` enum tracks provenance for confidence upgrade-on-rerun. Sister-doc `library-extended-remix-finder` joins `variant_label='radio'` rows against its `extended_candidates.db`.

**Fixture corpus for precision/recall targets** (Goals). 200-track labelled set needed before promote `evaluated_` → `accepted_`. Compose: 80 within-library variant clusters (Original/Extended/Radio/Remix variations) drawn from existing user library; 60 single-variant tracks (no cluster expected — negative cases); 40 dirty-title edge cases (nested parens, language variants, non-Latin, missing parens). Hand-label by owner; check into `tests/fixtures/variant_detector/` as YAML. Sample fixture format: `{track_id: 1234, expected_label: 'extended', expected_root: 'strobe', expected_remixer: null, expected_parent: 1233}`. Test harness asserts ≥ 95 % label precision and ≥ 80 % recall on this corpus.

### 2026-05-17 — Rust IPC + fixture infra + dep verification

**Rust pipeline API surface (re-read `src-tauri/src/audio/fingerprint.rs`).** Two `#[tauri::command]` exports: `fingerprint_track(path: String) -> Result<Vec<u32>, String>` (lines 320-334) + `fingerprint_batch(paths: Vec<String>, window: tauri::Window) -> Result<HashMap<String, Vec<u32>>, String>` (lines 343-398). Batch emits `"fingerprint_progress"` event per file. One public free function: `hamming_similarity(a: &[u32], b: &[u32]) -> Option<f32>` (lines 287-302), `MIN_FP_LEN=4`. Registered at `src-tauri/src/main.rs:454-455`. **Constraint:** `fingerprint_batch` signature `tauri::Window` argument = Tauri injects automatically; cannot be called from Python sidecar (no window handle). Resolves how M2 bridges (OQ-N#11 above): frontend orchestrates, backend ingests.

**Backend already has shadow implementation.** `app/main.py:3746-3841` (`_fingerprint_python_fallback`, `_group_duplicates`, `hamming_sim_py`). Used by `POST /api/duplicates/scan` (background job at line 3844, `_run_duplicate_scan`). Pattern reusable: M2's `POST /api/variants/fingerprints/ingest` mirrors `_run_duplicate_scan` shape but receives pre-computed `Vec<u32>` from frontend instead of computing MD5 sidecar-side. Cluster grouping logic = re-use `_group_duplicates(similarity_threshold=0.85)`; only persistence layer differs (`track_variants` rows, not `_dup_jobs` in-memory).

**Fixture infra — gap.** `tests/fixtures/` does **not** exist (verified `Glob tests/fixtures/**` empty). All existing tests use inline fixtures (`tests/conftest.py` `auth_token` autouse; `tests/test_pdb_structure.py` reference-binary check); 11 test files total. M1 must scaffold `tests/fixtures/variant_detector/labelled_corpus.yaml` from scratch + add YAML loader (`PyYAML` already pinned in `requirements.txt` — verify before M1). Add `conftest.py`-level fixture `def labelled_variants() -> list[dict]: ...` to keep test file lean.

**`pyacoustid` verified for M3.** PyPI: 1.3.1 latest (Apr 2024+ patch), 1.3.0 still installable. License: **MIT** (verified via `PKG-INFO` + `LICENSE`). Author: Adrian Sampson (sampsyo, also `beets` maintainer). Summary: "bindings for Chromaprint acoustic fingerprinting and the Acoustid API". Depends on `audioread` (already in `backend.spec` via librosa stack). Pin choice for M3: `pyacoustid==1.3.1` (newer patch).

**Cross-doc enum alignment confirmed (variant-tag taxonomy).** Re-read sister-doc `exploring_external-track-match-unified-module.md` Goals + Findings 2026-05-15. Both docs list identical 12-value set `{original, extended, radio, club, dub, instrumental, acapella, vip, remix, bootleg, edit, mashup}` (order differs but set-equal). Sister-doc Recommendation §M1 ships `VersionTag.label: Literal[...]`; this doc's `track_variants.variant_label` column = `VersionTag.label.value`. Sister-doc adds `remixer: str | None` + `modifiers: tuple[str, ...]` carrying year-edit / compound tokens — this doc's `track_variants.remixer` column persists `VersionTag.remixer`; `modifiers` not persisted (transient — derivable from re-parsing title if needed). No drift.

## Options Considered

### Option A — Title-only, in-process

- Sketch: Regex catalogue + title normaliser in `app/version_classifier.py`, consumes `app/external_track_match.py:extract_title_stem` + `parse_version_tag` (per sister-doc Option C). Run as background task after `analysis_engine`. Emit per-track `variant_label`, `normalised_root`, `remixer` → `app_data/variants.db`. Group by `(normalised_root, primary_artist)`.
- Pros: Zero network, fast (target < 5 s for 30 k tracks cold), no binary deps, deterministic, easy fixture-test, ships M1 standalone.
- Cons: Misses untitled remixes ("Track 04"), can't pick canonical original from external sources, fragile to dirty titles. Expected ceiling: ~85 % cluster precision (per Findings #1 confidence tier 0.5–0.75).
- Effort: S
- Risk: Low. Failure mode = missed relations (false negative), not wrong relations (no false positive at ≥ 0.75 floor).

### Option B — Title + Rust-fingerprint local cluster (no network)

- Sketch: Option A regex pass + Rust `fingerprint_batch` (already in tree, `src-tauri/src/audio/fingerprint.rs`) for local clustering. Tauri command emits `Vec<u32>`; Python receives via existing IPC. Hamming-similarity cluster threshold (e.g. ≥ 0.85) flags same-recording pairs even when titles disagree (catches "Track 04" + "Hidden Bonus" cases). Persist clusters to sidecar with `source='rust-fp-cluster'`. Still no external network.
- Pros: Still offline. No new dep (Rust pipeline ships). Catches untitled / mislabelled variants Option A misses. Upgrade-in-place over Option A (no schema change).
- Cons: Rust IPC adds latency (per-track ~0.5–1 s decode + fingerprint per existing `fingerprint.rs` constants). Hamming threshold needs calibration on fixture. Cannot pick canonical original (no MB IDs).
- Effort: M
- Risk: Low-medium. Risk = threshold mis-calibration causing wrong clusters; mitigated by ≥ 0.85 floor + fixture sweep.

### Option C — Title + Rust-fingerprint + opt-in Chromaprint/AcoustID

- Sketch: Layered. Option A always on. Option B added when Rust pipeline available (Tauri context). Optional outer tier: opt-in `fpcalc` PATH-detect + AcoustID bulk lookup + MB `remix of` ingestion, upgrades existing relations rather than replacing. Each source writes its own row in `track_variants` (composite PK per `source`); UI picks max-confidence per cluster. **Recommended.**
- Pros: Useful on day one (M1 title-only), degrades gracefully (no `fpcalc` → still works), user controls network cost. Canonical-original picker fully resolved when M3 ships. Confidence converges upward over time without re-architecting.
- Cons: Three code paths (title / Rust-fp / AcoustID). Merge layer needs clear precedence rule (resolved: highest-confidence row per `(track_id, parent_track_id)`).
- Effort: L (M1=S, M2=M, M3=M).
- Risk: Medium. Merge-precedence is the main complexity; covered by composite-PK schema in Findings #3.

### Option D — External-only, ignore titles

- Sketch: Skip title parsing, rely entirely on AcoustID + MB for relations.
- Pros: Bypasses messy title problem; uses curated ground truth.
- Cons: Useless offline; useless for bootlegs MB doesn't have (~30 % of underground DJ catalogue per Findings #1); slow first run (1 req/s MB × 30 k = ~8.3 h); over-trusts noisy external dataset; violates local-first invariant (Constraints).
- Effort: M
- Risk: High. Breaks Schicht-A local-first principle. **Rejected.**

### Option E — At-import classification hook only (no full-library scan)

- Sketch: Run classifier inline during `analysis_engine.py` import path. Per-track only at the moment of import. Skip retroactive scan; existing library stays unlabelled until manually re-analysed.
- Pros: Lowest implementation cost. No background job. No batch infrastructure.
- Cons: Doesn't satisfy use-case (a) "do I already have an Extended of this Radio Edit?" — needs labels on existing library. Doesn't satisfy use-case (c) "group all 4 versions in UI" — same issue. Only covers (d) "warn me on 5th-version import". Sub-MVP. **Rejected as standalone.** Folded into Option C as a sub-component.
- Effort: XS
- Risk: Low — but coverage gap forces user to re-analyse entire library to benefit.

## Recommendation

**Option C (hybrid layered), three milestones with explicit deliverables + measurable gates.**

### M1 — Title-only classifier (no network, no binary deps)

- **Deliverables:** `app/version_classifier.py` (consumes `app/external_track_match.py` per sister-doc); sidecar `app_data/variants.db` schema per Findings #3; CRUD helper module; CLI script `scripts/scan_variants.py`; at-import hook in `analysis_engine.py` (read-only badge, non-blocking); **new** `tests/fixtures/` directory (does not exist today — verified empty 2026-05-17, see Findings #4); `tests/fixtures/variant_detector/labelled_corpus.yaml` (200 tracks); `tests/conftest.py` fixture loader `def labelled_variants()`.
- **Covers:** use-cases (a) "do I have an Extended?", (c) "group versions in UI", (d) "warn on 5th-version import". Excludes (b) external canonical-original lookup.
- **Gates before M2:** (i) ≥ 95 % label precision on fixture, (ii) ≥ 80 % recall, (iii) full 30 k-track scan < 5 s wall on dev hardware, (iv) zero `master.db` writes verified by `_db_write_lock` instrumentation, (v) doc-syncer entry in `docs/backend-index.md` + `docs/FILE_MAP.md`.
- **Sister-doc dependency:** blocked on `external-track-match-unified-module` reaching `accepted_` (extracted fuzzy matcher + taxonomy enum). Coordinate via that doc's Open Questions.

### M2 — Rust-fingerprint local clustering (no network, no new dep)

- **Deliverables:** frontend orchestrator calls existing `fingerprint_batch` Tauri command (no Rust changes); new backend route `POST /api/variants/fingerprints/ingest` (Pydantic body `{fingerprints: dict[str, list[int]]}`) writes pre-computed fingerprints into staging table + triggers cluster job; cluster job re-uses `_group_duplicates(similarity_threshold)` shape from `app/main.py:3777` against the variant-detector staging rows; Hamming-similarity threshold calibration sweep on fixture (sweep 0.75–0.95 in 0.02 steps, pick precision-recall sweet spot); writes `track_variants` rows with `source='rust-fp-cluster'`, confidence 0.85 (above auto-group floor); batch size N=500 per Tauri invocation (frontend chunks).
- **Covers:** untitled / mislabelled variant pairs Option A misses ("Track 04" + "Hidden Bonus" same recording).
- **Gates before M3:** (i) Hamming threshold validated on fixture, (ii) batch latency ≤ 1 s/track average measured end-to-end (frontend→Rust→backend→cluster), (iii) zero new dep entries in `requirements.txt` / `Cargo.toml`, (iv) cluster precision ≥ 95 % (false-positive cluster is worse than false-negative — merging two unrelated tracks into one cluster confuses UI), (v) backend route `X-Session-Token`-gated per `coding-rules.md` (system-endpoint discipline).

### M3 — Opt-in Chromaprint + AcoustID + MusicBrainz (external)

- **Deliverables:** `pyacoustid==1.3.1` pinned in `requirements.txt` (latest patch verified 2026-05-17, MIT licence, maintainer sampsyo/Adrian Sampson, depends on `audioread` already pinned via librosa); `app/fingerprint_acoustid.py` with `shutil.which('fpcalc')` PATH-detect + import-time degrade; `app/musicbrainz_relations.py` querying `recording-recording` relations; background job batches via AcoustID `meta=recordings+releasegroups`; cache key `(fpcalc_fingerprint, duration_rounded)` in `variants.db`; `settings.json` `variant_detector.external_enabled` toggle (default `false`).
- **Covers:** use-case (b) "find canonical original for this Bootleg". Upgrades existing relations in-place via composite-PK schema.
- **Gates before promote `archived/implemented_`:** (i) AcoustID 3 req/s budget honoured (rate-limit test), (ii) MB User-Agent header sent (audit log line per request), (iii) graceful degrade verified by deleting `fpcalc` from PATH + running scan (zero crashes), (iv) `fpcalc` bundling decision documented (PATH-detect kept OR ~3 MB binaries added per-platform), (v) `_TEMPLATE.md` Decision/Outcome checkboxes complete.

### Confidence calibration (locked)

Cluster keying is **string-equality on normalised tuple**, NOT fuzzy ratio. Fuzzy matching from `external-track-match-unified-module` is used only when this module looks up external candidates (M3), not for within-library clustering. Normalisation rules per OQ4 (feat-strip → tail-parens → NFC + casefold).

- Title-regex + exact `(normalised_root, normalised_primary_artist)` match → 0.75 (auto-group).
- Title-regex + root-only match (artist mismatch — possibly cross-artist remix) → 0.55 (suggestion).
- Rust-fingerprint cluster (Hamming ≥ 0.85) → 0.85 (auto-group, mid-tier).
- AcoustID cluster (no MBID) → 0.6 (suggestion; upgraded to 0.95 once MBID attaches).
- MB `remix of` / `edited version` relation → 0.95 (auto-group, high confidence).
- Composite-PK schema lets each source write independently; UI picks `MAX(confidence) GROUP BY (track_id, parent_track_id)`.

### Pre-`exploring_` checks

- Owner confirms sister-doc `external-track-match-unified-module` taxonomy enum + `Candidate` dataclass shape before M1 begins (blocks compile dependency).
- Owner confirms 200-track fixture corpus available before promote `evaluated_` → `accepted_` (blocks precision/recall gate).
- Owner confirms `fpcalc` packaging decision (PATH-detect vs bundle) before M3 leaves `accepted_` (blocks Schicht-A dep-pinning).

---

## Implementation Plan

> Required from `implement/draftplan_` onward. Concrete enough that someone else could execute it without re-deriving the design.

### Scope
- **In:** …
- **Out (deliberately):** …

### Step-by-step
1. …
2. …

### Files touched (expected)
- …

### Testing approach
- …

### Risks & rollback
- …

## Review

> Filled by reviewer at `review_`. If any box is unchecked or rework reasons are listed, the doc moves to `rework_`.

- [ ] Plan addresses all goals
- [ ] Open questions answered or explicitly deferred
- [ ] Risk mitigations defined
- [ ] Rollback path clear
- [ ] Affected docs identified (`architecture.md`, `FILE_MAP.md`, indexes, `CHANGELOG.md`)

**Rework reasons** (only if applicable):
- …

## Implementation Log

> Filled during `inprogress_`. What got built, what surprised us, what changed from the plan. Dated entries.

### YYYY-MM-DD
- …

---

## Decision / Outcome

> Required by `archived/*`. Final state of the topic.

**Result**: `implemented` | `superseded` | `abandoned`
**Why**: …
**Rejected alternatives** (one line each):
- …

**Code references**: PR #…, commits …, files …

**Docs updated** (required for `implemented_` graduation):
- [ ] `docs/architecture.md`
- [ ] `docs/FILE_MAP.md`
- [ ] `docs/backend-index.md` (if backend changed)
- [ ] `docs/frontend-index.md` (if frontend changed)
- [ ] `docs/rust-index.md` (if Rust/Tauri changed)
- [ ] `CHANGELOG.md` (if user-visible)

## Links

- Code (existing, M1 will consume): `app/soundcloud_api.py:566` (`_fuzzy_match_with_score`), `app/soundcloud_api.py:583` (threshold 0.65), `app/database.py:22` (`_db_write_lock`), `app/anlz_safe.py` (rbox quarantine — not used here, noted for completeness), `src-tauri/src/audio/fingerprint.rs:320-334` (`fingerprint_track`), `src-tauri/src/audio/fingerprint.rs:343-398` (`fingerprint_batch`), `src-tauri/src/audio/fingerprint.rs:287-302` (`hamming_similarity`), `src-tauri/src/main.rs:454-455` (Tauri command registration), `app/main.py:3746` (`_fingerprint_python_fallback` — MD5 shadow), `app/main.py:3777` (`_group_duplicates` — reusable cluster shape for M2), `app/main.py:3844` (`_run_duplicate_scan` — pattern for M2 background job), `backend.spec` (verified no `fpcalc` reference)
- External docs: <https://acoustid.org/webservice>, <https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting>, <https://pypi.org/project/pyacoustid/>
- Related research: `external-track-match-unified-module` (owns fuzzy + fingerprint + adapter registry — blocks M1), `library-extended-remix-finder` (consumes `track_variants.variant_label` from this doc's sidecar), `library-quality-upgrade-finder` (same-edit detection reads variant labels here), `metadata-name-fixer` (mutation event hook for OQ8 cache-invalidation, contract TBD)
