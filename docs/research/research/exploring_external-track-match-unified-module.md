---
slug: external-track-match-unified-module
title: Unified track-matching + fingerprint + adapter-registry module shared across remix-detector / extended-remix-finder / quality-upgrade-finder
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
tags: [architecture, shared-module, fuzzy-match, chromaprint, adapter-registry, cross-cutting]
related: [analysis-remix-detector, library-extended-remix-finder, library-quality-upgrade-finder]
---

# Unified track-matching + fingerprint + adapter-registry module shared across remix-detector / extended-remix-finder / quality-upgrade-finder

> **Caveman style.** Fragments, bullets. Drop articles/filler/hedges. No prose paragraphs. Respect per-section caps below.
> State = folder + filename prefix (not frontmatter). Lifecycle = audit trail. See `../README.md`.

## Lifecycle

- 2026-05-15 — `research/idea_` — scaffolded + initial design fill (cross-cutting from 3 sister docs)
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (caught load-bearing rapidfuzz cross-doc error AND flagged sister-docs for fixup; 8/11 OQ resolved-M1; 5 dated Findings with module-API design specifics)
- 2026-05-17 — research/exploring_ — evaluated_-ready rework loop (re-verified `SequenceMatcher` + `fingerprint.rs` + `backend.spec`; cross-doc taxonomy alignment pass across 4 sister-docs; added Rust-FP-via-IPC option for M1; added canonical `VersionTag.label` enum + classifier-input table; added pre-evaluated_ checklist with sign-off blockers)
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

---

## Problem

3 sister-docs converged on same shared module: fuzzy match + version-parse + fingerprint + adapter registry. Skip-cost: 3 forked fuzzy matchers drifting from 0.65, 3 taxonomies, 3 chromaprint wrappers, 3 adapter shapes. Design module shape up-front so sister features ship sequentially without re-architecting.

## Goals / Non-goals

**Goals** (each carries a testable metric for M1 acceptance)

- **Single fuzzy-matcher API.** Replace `SoundCloudSyncEngine._fuzzy_match_with_score` (`app/soundcloud_api.py:566`) with module-level pure function callable from any sister-feature. Metric: SC sync regression suite (`tests/test_soundcloud_*.py`) passes unchanged + 1 new sister-feature test imports same function with identical match output on shared fixture.
- **Version-tag taxonomy as importable artefact.** Cover `original | extended | radio | club | dub | instrumental | acapella | vip | remix | bootleg | edit | mashup` + year-edit + remixer-bearing parens. Metric: ≥95 % label-recall on a fixture of 200 real titles harvested from local library (15 per primary tag + edge cases per remix-detector regex catalogue).
- **Title-stem extractor.** Strip parenthetical/bracket suffixes, `feat.`/`ft.`/`featuring`/`with`, trailing-dash `- Extended Mix`, normalise casing/accents. Metric: deterministic round-trip — stem of `"Strobe (Radio Edit)" == stem of "Strobe - Extended Mix" == stem of "Strobe (Deadmau5 Club Mix)" == "strobe"` (all 12 regex shapes in extended-remix-finder Findings 2026-05-15 covered by ≥1 fixture).
- **Fingerprint API with PATH-detect.** Wrap `fpcalc` subprocess; degrade to `None` when binary missing. Metric: import + call succeeds on a machine without `fpcalc` (returns `FingerprintUnavailable`), succeeds with `fpcalc` on PATH (returns `(fingerprint:str, duration:float)`).
- **Adapter registry — plugin pattern.** All adapters (Discogs / Beatport / Bandcamp / Qobuz / YouTube / SoundCloud / AcoustID-MusicBrainz / local-HQ-folder) implement same `search(title, artist, duration_s) -> list[Candidate]` interface. Metric: M1 ships ≥1 real adapter (the SC extraction) + 1 mock adapter (`tests/fixtures/mock_adapter.py`) registered side-by-side, called by a shared dispatcher with identical kwargs.
- **Read-only module.** No `master.db` writes, no `_db_write_lock` acquisition, no rbox parsing. Metric: `grep -r "_db_write_lock\|rbox\|pyrekordbox" app/external_track_match*` returns empty on first commit.

**Non-goals**

- Per-feature UX / data-model / persistence (sister-doc concerns).
- Cross-platform popularity classifier (`idea_analysis-underground-mainstream-classifier`, different domain).
- Rekordbox metadata-migration logic (lives in `quality-upgrade-finder`).
- UI surface choices for any sister feature.
- Owning candidate-storage sidecar SQLite schema (each sister-doc owns its own table; this module returns transient `Candidate` objects only).
- Replacing the SC OAuth client / download path (`SoundCloudClient` stays where it is; only the matcher method migrates).

## Constraints

External facts bounding the solution. Each re-verified 2026-05-15.

- **Fuzzy matcher** — `SoundCloudSyncEngine._fuzzy_match_with_score` at `app/soundcloud_api.py:566`. Threshold `0.65` hardcoded at `app/soundcloud_api.py:583` (`if ratio > best_ratio and ratio >= 0.65`). **Correction vs sister-docs:** uses `difflib.SequenceMatcher(None, a, b).ratio()` (Python stdlib, imported at `app/soundcloud_api.py:16`), NOT `rapidfuzz token-set ratio`. `requirements.txt` does not list `rapidfuzz`. Sister-doc citations on this detail are wrong — flag in cross-doc fixup before promote.
- **Match short-circuit** — exact normalised-title match returns `(tid, 1.0)` immediately (line 580); otherwise loop returns `(best_match, round(best_ratio, 3))`. Artist is included only via the combined `"artist - title"` haystack — there is NO independent artist gate today (sister-doc `extended-remix-finder` Findings 2026-05-15 describes "rapidfuzz token-set ratio + artist gate" — that's also wrong). Module extraction is the right moment to add an explicit artist-gate parameter if sister features need stricter matching.
- **`SoundCloudSyncEngine` class** — `app/soundcloud_api.py:550`. Method is instance-bound but its body uses only the `local_tracks` argument + the instance-bound `_normalize_title` (line 558). `_normalize_title` is itself stateless (`return re.sub(r'[^\w\s]', '', title.lower().strip())`). Lift-to-pure-function is mechanical — no `self` access needed.
- **No `app/external_track_match.py` exists today** — verified via `Glob app/external_track_match*` → empty. Greenfield module path.
- **`fpcalc` (libchromaprint) NOT bundled today** — verified via `Grep backend.spec` for `fpcalc|chromaprint|acoustid|pyacoustid` → no matches. Bundling = Schicht-A dep-pinning decision per-platform (~3 MB binary × 3 OS). M1 = PATH-detect + skip if missing (per remix-detector Recommendation, M2-deferred).
- **rbox quarantine pattern (`app/anlz_safe.py`)** — rbox 0.1.5/0.1.7 panics on malformed content via `Option::unwrap()` and aborts the entire process (Windows `0xC0000409`). Quarantined via `ProcessPoolExecutor(max_workers=1)`. This module does NOT call rbox directly (matching is title-based; fingerprinting is `fpcalc` subprocess, not rbox) → no `ProcessPoolExecutor` inside the module itself. If a sister feature pulls canonical-original metadata from local Rekordbox after a match, that read goes through `SafeAnlzParser`, not through this module.
- **`_db_write_lock` lives at `app/database.py:22`** (verified: `_db_write_lock = threading.RLock()`). Sister-doc `library-quality-upgrade-finder.md` line 54 incorrectly references `app/main.py:138`; correction needed in that doc separately. Module is read-mostly — no `master.db` writes — so does not acquire the lock. Sister-doc persistence layers (sidecar SQLite each) acquire their own locks if needed.
- **`ALLOWED_AUDIO_ROOTS` sandboxing (`app/main.py:138-189`)** — applies only when the module opens a local audio file for fingerprinting. Path must pass `Path.is_relative_to(resolved_root)` before being handed to `fpcalc`. External URLs (SC/Bandcamp etc.) bypass this — they aren't filesystem reads.
- **Subprocess discipline** — `fpcalc` invocation must have explicit `timeout=` per coding-rules (FFmpeg pattern 30 s default; fpcalc at 120 s sample = ~0.3 s real, set 10 s ceiling). Log start/end/elapsed with `logger.info("fpcalc path=%s elapsed=%.3f", ...)`.
- **httpx pattern** — adapters that hit external APIs use `httpx.AsyncClient` with timeout + retry (per coding-rules; no `requests.get` in async paths). Hand-rolled httpx preferred over per-source SDKs (smaller dep surface, easier Schicht-A pinning).
- **External-source rate limits** (re-verified vs sister-doc Constraints):
  - **AcoustID** — 3 req/s per app key; `lookup?meta=recordings+releasegroups` batches multiple MBIDs per call (remix-detector Constraints).
  - **MusicBrainz** — 1 req/s per IP, requires `User-Agent: AppName/Version (contact)` header (remix-detector Constraints).
  - **Discogs** — 60 req/min authenticated, free; REST (extended-remix-finder Constraints).
  - **SoundCloud** — informal ~15k req/day per `client_id` from observation; back off on 429 (extended-remix-finder Constraints).
  - **YouTube Data API v3** — 10k units/day default, `search.list` = 100 units → ~100 searches/day per key (extended-remix-finder Constraints). Insufficient for batch.
  - Adapter registry must surface per-source `quota_remaining()` hint so callers can prioritise / throttle.
- **Schicht-A dep pinning** — any new dep (e.g. `python3-discogs-client`, `pyacoustid`, optional `rapidfuzz` upgrade) lands in `requirements.txt` as `==X.Y.Z` with CVE check. M1 introduces zero new deps if `SequenceMatcher` stays; `rapidfuzz` swap is a separate Schicht-A decision (faster, but new transitive).

## Open Questions

Status legend: **RESOLVED-M1** = answered for M1, locked. **DEFERRED** = M2/M3 decision, not blocking. **PARKED** = depends on first sister-feature lands, revisit then. **OPEN** = still needs decision before draftplan.

1. **Taxonomy strictness** — `Enum` (strict; unknown raises) vs `Literal[str]` (flexible; new tags = string literals without code change) vs `dataclass VersionTag(label, modifiers)` (rich; supports compound like "Extended Remix"). **RESOLVED-M1: `frozen dataclass VersionTag` with `label: Literal[...]` + `remixer: str | None` + `modifiers: tuple[str, ...]`** (see Findings 2026-05-15 module-API design). Compound = the dataclass shape, not the label vocabulary. Cross-doc sign-off on the `Literal` enumeration is the only remaining bit; tracked in checklist.
2. **`fpcalc` bundling** — bundle per-platform (`app/bin/fpcalc/{win,mac,linux}/fpcalc[.exe]`) up-front vs PATH-detect + skip if missing. **RESOLVED-M1: PATH-detect, no bundle.** Bundling decision (~3 MB × 3 OS, Schicht-A + `backend.spec` `binaries=[...]` + per-platform CI matrix) **DEFERRED to M2/M3** once fingerprinting moves from "nice-to-have" to "required for use-case (b)" per remix-detector.
3. **Fuzzy-match cache scope** — single shared LRU (deduplicates across features, ~30k entries × 3 features overlap) vs per-feature LRU (isolation, possible 3× memory). **RESOLVED-M1: single shared module-level `functools.lru_cache(maxsize=4096)`** on the pure stem-extractor + version-parser (cheap, no cross-feature semantics). Match-result caching is sister-feature concern (lives in their sidecar SQLite, not in-memory here).
4. **Adapter-registry runtime config** — `settings.json` toggle per source (user opts out of paid) vs hardcoded enabled list. **DEFERRED** — irrelevant until ≥2 adapters registered. M1 ships with hardcoded enable (only SC + mock). First paid adapter (Discogs in extended-remix-finder) introduces toggle.
5. **API surface shape** — function-only vs class-based vs hybrid. **RESOLVED-M1: hybrid (Option C in Options Considered)** — functions for stateless ops, module-level `ADAPTER_REGISTRY` dict + `register_adapter()` for plugin slot, no class wrapper. Migration path to class-based is mechanical if DI need emerges (sister-feature tests pass `adapters=...`).
6. **Module location** — flat `app/external_track_match.py` vs subpackage `app/track_match/`. **RESOLVED-M1: flat single file.** Promotion to subpackage gated on (a) ≥3 sister-features actually shipped (not just `idea_`), (b) ≥3 adapters implemented, (c) file >800 LOC OR single function >150 LOC. (Was OQ6; gating moved to Recommendation.)
7. **Logging / telemetry** — structured log line per fuzzy-match call (`logger.info("match score=%.3f source=%s ...")`) for hit-rate monitoring vs silent. **RESOLVED-M1: silent in matcher; one INFO line per scan-batch in caller.** 30k tracks × 3 features = 90k log lines is spam. Add per-call DEBUG behind `LOG_TRACK_MATCH=1` env-toggle for threshold calibration sessions.
8. **Test ownership boundary** — dedicated `tests/test_external_track_match.py` (regex catalogue, taxonomy round-trip, PATH-detect mock) vs sister-doc integration coverage only. **RESOLVED-M1: module owns its unit tests.** Regex catalogue + taxonomy + PATH-detect mock + stem-extractor round-trip live here. Sister-doc integration tests cover end-to-end adapter behaviour (e.g. SC search → match → score).
9. **Per-source fuzzy threshold tuning** — universal `0.65` (current SC value) vs per-source override (SC=0.65 dirty user titles, Discogs=0.80 canonical, YouTube=0.55 spam-loose, AcoustID=0.70). **DECISION-NEEDED** (M2). SC is dirty (user-uploaded); Discogs/Beatport `mix_name` is canonical-clean; YouTube titles lie. Per-source override is the right shape. M1 exposes `threshold` parameter (default 0.65) so caller picks at call-site; per-source constants table lands in module when 2nd adapter (Discogs) ships in M2, calibrated against a small per-source labelled fixture. Sub-question for extended-remix-finder's first calibration pass: does threshold also depend on source-of-truth confidence (Discogs ground-truth vs SC discovery use-case)?
10. **Adapter return-type stability** — concrete `Candidate` dataclass (importable, type-safe, sister features depend on it) vs `dict[str, Any]` (looser coupling). **RESOLVED-M1: `Candidate` frozen dataclass.** Fields: `source: str, source_id: str, title: str, artist: str, duration_s: float | None, version_tag: VersionTag | None, url: str | None, raw: dict`. `raw` escape-hatch holds source-specific payload (SC `permalink`, Discogs `release_id`, etc.). All adapters return `list[Candidate]`.
11. **Async vs sync API for adapters** — pure-sync (matcher does not need async, but adapter HTTP calls do) vs all-async (adapters are `async def search(...)`, matcher stays sync). **OPEN, deferred to draftplan.** Adapters are HTTP-bound = async wins; matcher is CPU-bound = sync is fine. Likely shape: `async def search(...) -> list[Candidate]` for adapters; sync `fuzzy_match`, `extract_title_stem`, `parse_version_tag`. Sister-features bridge via `asyncio.run()` or `await`.
12. **Rust-FP as secondary fingerprint source for sidecar paths** — expose Rust `fingerprint_track` via a new FastAPI route the frontend kicks (round-trip IPC) OR keep Rust-FP only for Tauri-direct-IPC consumers (e.g. quality-upgrade replace-modal called from frontend before backend swap). **RESOLVED-M1: Tauri-direct-IPC only for M1**, no FastAPI wrapping. Frontend `invoke('fingerprint_track')` → frontend forwards `Vec<u32>` to backend route that needs it (e.g. `POST /api/upgrade/confirm-replace`). Backend module exposes `accept_rust_fingerprint(track_id, fp: list[int])` setter; never calls Rust directly. Avoids inverse-IPC anti-pattern + keeps sidecar deployable headless (non-Tauri test contexts). See Findings 2026-05-17 "Rust-FP-via-IPC".
13. **`fpcalc` fingerprint reciprocal-call from Rust** — should `fingerprint.rs` ALSO be able to invoke `fpcalc` (Rust-side `Command::new("fpcalc")`) so AcoustID lookups can happen entirely Rust-side? **DEFERRED (M3-territory).** Would duplicate the Python `fingerprint()` wrapper; adds Rust dep on AcoustID HTTP. Defer until Rust audio path has its own reason to query AcoustID (none today). Sister-doc remix-detector's M3 (Python `app/fingerprint_acoustid.py` + AcoustID HTTP from sidecar) covers the use-case.

## Findings / Investigation

### 2026-05-15 — initial design landscape from sister-docs

**Cross-doc citation map** (what each sister-doc independently asks for):
- `idea_analysis-remix-detector.md` — Findings 2026-05-15-option-refinement: shares `_fuzzy_match_with_score` (0.65), title-stem extractor, version-tag taxonomy, chromaprint pipeline. Recommendation §M2 cites "sister-doc `idea_library-extended-remix-finder` should consume this module's variant labels rather than re-deriving them".
- `idea_library-extended-remix-finder.md` — Findings 2026-05-15-UX-refinement: "all three share `SoundCloudSyncEngine._fuzzy_match_with_score` (0.65), title-stem extractor, version-tag taxonomy, and (planned) chromaprint pipeline. Suggest unified `app/external_track_match.py` owning fuzzy + version-parse + fingerprint helpers, consumed by all three." Recommendation also calls for unified candidate-storage sidecar (out-of-scope here, but consistent with shared-module direction).
- `idea_library-quality-upgrade-finder.md` — Findings 2026-05-15-safety-refinement: "Recommend unified `app/external_track_match.py` module (match + fingerprint + adapter registry) consumed by all three. Avoids parallel partial implementations." Recommendation §cross-cutting: "extract `app/external_track_match.py` in Phase 1 (fuzzy + chromaprint + adapter registry) so sister-docs don't fork it."

**Converging design hints from sister-docs**:
- Version-tag taxonomy must be agreed once — remix-detector enumerated the patterns (year-edit, remixer-bearing, compound, bracket-variant, nested, semicolon-segmented, trailing-dash, multi-suffix, language variants DE/ES/PT/IT/EN). Extended-remix-finder added Extended-indicator tokens (`extended mix`, `extended version`, `club mix`, `long version`, `full version`, `12" mix`, `12" version`) + negative tokens (`radio edit`, `radio mix`, `short edit`, `clean edit`, `intro edit`, `single edit`). Quality-upgrade-finder needs the same taxonomy for same-edit detection (refuse-by-default when version-tag differs).
- Fuzzy threshold may need per-source tuning — SC titles dirtier (user-generated) than Discogs (canonical), Beatport `mix_name` field is structured. Universal 0.65 is the SC-calibrated value. Per-source override resolves OQ9.
- Chromaprint pipeline can be staged — M1 = match-only (title + version-tag), M2 = fingerprint added (catches untitled remixes per remix-detector; gates same-edit replacement per quality-upgrade-finder). PATH-detect with graceful skip lets M1 ship without bundling.
- Adapter registry shape — extended-remix-finder sketched `SourcePlugin` interface (`search`, `parse_version`, `quota_remaining`). Quality-upgrade-finder lists same set (Bandcamp, Beatport, SoundCloud Go+, Qobuz, local "HQ" folder). Remix-detector adds AcoustID/MusicBrainz as external relation source. All compatible with one registry pattern.

**Non-trivial decisions deferred to this doc**:
- Where the module lives (OQ6) — flat `app/external_track_match.py` vs sub-package `app/track_match/`. Promotion criteria gated on N sister features shipped (Option C → D in Options Considered).
- How the module is tested (OQ8) — own test file vs sister-coverage. Affects whether shared edge-cases (regex catalogue, taxonomy round-trip, PATH-detect mock) are tested once here or N times in sister tests.
- Who owns the fuzzy-match cache (OQ3) — single shared cache vs per-feature. Affects memory footprint and possible cross-feature cache poisoning.
- Whether to bundle `fpcalc` (OQ2) — Schicht-A dep-pinning decision crosses backend.spec, requirements.txt, and per-platform binary distribution. M1 PATH-detect avoids the decision; M2/M3 forces it.

**Existing SoundCloud-coupling friction**:
- `_fuzzy_match_with_score` is an instance method bound to `SoundCloudSyncEngine` (`app/soundcloud_api.py:566`). Two extraction paths: (a) lift to pure function `fuzzy_match_with_score(title, artist, local_tracks, threshold=0.65)` at module-level, sister-doc and SC engine both import; (b) keep method, add module-level free-function wrapper that delegates. Option (a) decouples cleanly; option (b) avoids touching SC code on day one. Resolvable in draftplan, not blocking idea_.

### 2026-05-15 — module-API: public function surface

Pure functions on `app/external_track_match.py` (signature shapes in prose):

- `normalize_title(title)` → lowercase + accent-fold + strip non-word punctuation. Lift of `SoundCloudSyncEngine._normalize_title` (line 558).
- `extract_title_stem(title, *, drop_features=True)` → strip tail parenthetical/bracket groups, optional `feat./ft.` clauses, trailing-dash `- Extended Mix`. Returns canonical root for grouping.
- `parse_version_tag(title)` → `VersionTag | None`. Regex catalogue from remix-detector Findings 2026-05-15.
- `fuzzy_match_with_score(query_title, query_artist, candidates, *, threshold=0.65)` → `(best_id_or_None, rounded_score)`. Module-level lift of `_fuzzy_match_with_score`. Same `SequenceMatcher` semantics + same exact-normalised-title short-circuit.

### 2026-05-15 — module-API: fingerprint + registry surface

- `fingerprint(audio_path, *, sample_seconds=120, timeout=10.0)` → `Fingerprint | FingerprintUnavailable`. Wraps `fpcalc` subprocess. **Always** validates `audio_path.is_relative_to(resolved_root)` against `ALLOWED_AUDIO_ROOTS` before subprocess. Logs `fpcalc path=... elapsed=...`.
- `is_fingerprinting_available()` → `bool`. Cached PATH-detect; callers short-circuit batch jobs without invoking subprocess.
- `register_adapter(name, plugin)` → mutates module-level `ADAPTER_REGISTRY`. Idempotent (re-registering replaces).
- `get_adapter(name)` → lookup; raises `AdapterNotRegistered`.
- `list_adapters()` → `list[str]`.

### 2026-05-15 — module-API: `SourcePlugin` Protocol + dataclasses

`SourcePlugin` = `typing.Protocol`, duck-typed (no class hierarchy):

- `async search(title, artist, duration_s=None, *, max_results=20)` → `list[Candidate]`. Empty list on no match; raises `AdapterError` subclass on transport failure.
- `parse_version(raw)` → `VersionTag | None`. Adapter-specific reverse lookup from source's native metadata field (Beatport `mix_name`, Discogs `tracklist[].title`, SC `title`).
- `quota_remaining()` → `int | None`. Best-effort hint; `None` = unknown. Used by orchestrators to throttle.
- `name: str` class attr = registry key.

### 2026-05-15 — module-API: concrete dataclasses + errors

Frozen, immutable, hashable where reasonable:

- `VersionTag(label: Literal[...], remixer: str|None, modifiers: tuple[str,...])`. `modifiers` carries year-edit/compound tokens (e.g. `("2024",)`, `("Extended",)`).
- `Candidate(source, source_id, title, artist, duration_s, version_tag, url, raw: dict)`. Returned from all `SourcePlugin.search`. `raw` escape-hatch holds source-specific payload (SC `permalink`, Discogs `release_id`).
- `Fingerprint(fpcalc_hash, duration_s)` + `FingerprintUnavailable` sentinel union (`BinaryMissing | Timeout | DecodeError`).
- `AdapterError` hierarchy: `AdapterNotRegistered`, `AdapterTransportError`, `AdapterQuotaExceeded`, `AdapterParseError`.

### 2026-05-17 — Rust-FP-via-IPC: alternate fingerprint source for M1?

Discovered by sister-doc `analysis-remix-detector` 2026-05-15: `src-tauri/src/audio/fingerprint.rs` (399 LOC) already ships a Chromaprint-style in-house fingerprint (Goertzel 32-band Mel × 128 ms windows → `Vec<u32>` hash words, Hamming similarity 0–1). Re-verified 2026-05-17. Tauri commands `fingerprint_track(path) -> Vec<u32>` and `fingerprint_batch(paths) -> HashMap<String, Vec<u32>>`. Decode via Symphonia (MP3/FLAC/WAV/AIFF/ALAC/M4A). 5-min cap, 11025 Hz, no network.

**Could it replace `fpcalc` as M1 source?**

- **NO for external lookup.** Algorithm is in-house — bit-incompatible with AcoustID (different hash shape, different SR, different windowing). Cannot query AcoustID with `Vec<u32>` from `fingerprint.rs`. Sister-doc remix-detector explicitly confirms this with two-tier plan.
- **YES for local-cluster pairwise compare.** `hamming_similarity(a, b) -> Option<f32>` answers "are these two local files the same recording?" — sufficient for quality-upgrade-finder safety-rule 2 (chromaprint-match ≥ 0.95 for replace-eligibility), sister-doc remix-detector M2 cluster pass.
- **Cost of IPC for the module:** Python `httpx` → FastAPI is wrong direction; Rust fingerprint is in Tauri main, not Python sidecar. Two routes available:
  - (a) **Tauri-context only:** UI side calls `invoke('fingerprint_track', {path})`, hands result to backend via a new route. Latency = decode + fingerprint + JSON marshall + HTTP. Acceptable for batch jobs, awkward for module-level "fingerprint this file" call.
  - (b) **Sidecar can't call Tauri.** Python sidecar is a subprocess of Tauri; no inverse IPC. If quality-upgrade replace path runs in sidecar (FastAPI route), it cannot reach Rust fingerprint without round-tripping through frontend.
- **Decision for M1 module API:** keep `fpcalc` PATH-detect as the **module-level** fingerprint source (called from sidecar Python code). Add **separate optional secondary source** `rust_fingerprint_via_ipc(path) -> RustFingerprint | RustFingerprintUnavailable` callable only when invoked from a route the frontend kicks. Two distinct return types (one for AcoustID-lookup, one for local-pairwise) prevent semantic confusion. Document the asymmetry in module README.
- **Sister-doc implications:** remix-detector M2 ("Rust-FP local cluster") already plans this — fits. Quality-upgrade safety-rule 2 has wiggle room: can accept `RustFingerprint`-similarity ≥ 0.95 as substitute when `fpcalc` missing AND when the candidate is also a local file (HQ-folder scenario). Update quality-upgrade Constraints in next round.

### 2026-05-17 — canonical `VersionTag.label` enum across 4 sister-docs

Cross-doc audit 2026-05-17 of all 4 docs (this + 3 sisters):

| Doc | Enum values listed | Order | Differs? |
|---|---|---|---|
| this (Goals line 33) | `original \| extended \| radio \| club \| dub \| instrumental \| acapella \| vip \| remix \| bootleg \| edit \| mashup` | 12 values | — |
| `analysis-remix-detector` Goals line 37 | `original \| extended \| radio \| club \| dub \| instrumental \| acapella \| remix \| edit \| bootleg \| vip \| mashup` | 12 values, `remix/edit/bootleg/vip` reordered | members identical, order differs |
| `library-extended-remix-finder` | no enum; lists **classifier-input tokens** as bands | classifier inputs, not labels | not a label conflict |
| `library-quality-upgrade-finder` | no enum; consumes shared module | — | not a label conflict |

**Members align across both label-defining docs.** Ordering harmless (set semantics, not list semantics). **Canonical proposal for `VersionTag.label`:**

```
Literal["original", "extended", "radio", "club", "dub",
        "instrumental", "acapella", "vip", "remix", "bootleg",
        "edit", "mashup"]
```

Ordering rule = stem-prefix first (original, extended, radio, club, dub, instrumental, acapella) then derivative-type (vip, remix, bootleg, edit, mashup). Mnemonic: "stem family before derivation family".

**Extended-finder classifier-input tokens (NOT labels)** — collapse-to-canonical mapping:

| Title token (case-insensitive) | Maps to `label` | Modifier captured |
|---|---|---|
| `extended mix`, `extended version`, `extended`, `long version`, `full version`, `12" mix`, `12" version` | `extended` | one of `("Extended","Long","Full","12\"")` |
| `club mix`, `club version` | `club` | `("Club",)` |
| `radio edit`, `radio mix`, `short edit`, `clean edit`, `intro edit`, `single edit` | `radio` | one of `("Radio","Short","Clean","Intro","Single")` |
| `dub mix`, `dub` | `dub` | `("Dub",)` |
| `instrumental`, `instrumental mix` | `instrumental` | `("Instrumental",)` |
| `acapella`, `a cappella`, `vocal mix` | `acapella` if instrumentless else `original` w/ `("Vocal",)` | source-aware |
| `original mix` (Beatport) | `original` | `("Original",)` |
| `original mix` (SoundCloud) | `radio` | `("Original",)` (source-aware override) |
| `vip`, `vip mix` | `vip` | `("VIP",)` |
| `(<artist>) remix`, `(<artist>) extended remix` | `remix` | remixer name + `("Remix",)` or `("Extended","Remix")` |
| `(<artist>) bootleg`, `(<artist>) flip`, `(<artist>) refix`, `(<artist>) rework` | `bootleg` | remixer + `("Bootleg"|"Flip"|"Refix"|"Rework",)` |
| `(<artist>) edit`, `(<year>) edit`, `(<year>) remaster(ed)` | `edit` | remixer-or-year + `("Edit"|"Remaster",)` |
| `(<artist>) mashup`, `vs.`-pair in title | `mashup` | remixer + `("Mashup",)` |

Source-aware bit (Beatport `original mix` = canonical extended cut; SoundCloud `original mix` = radio cut) lives in the **adapter's `parse_version(raw)` method** (per `SourcePlugin` Protocol Findings 2026-05-15), not the title-only parser. Title-only `parse_version_tag(title)` returns label without source context — adapter overrides per-source.

**Sign-off slot:** cross-doc fixup in next round will (a) re-order remix-detector Goals enum to match canonical for diff-grep readability, (b) add classifier-input table as Findings citation in extended-finder, (c) note in quality-upgrade Constraints that the enum lives here.

### 2026-05-17 — verification round: load-bearing facts still hold

Re-verified 2026-05-17 (each `Grep`/`Read` against current main):

- **`SoundCloudSyncEngine._fuzzy_match_with_score`** at `app/soundcloud_api.py:566` — still uses `SequenceMatcher(None, sc_combined, local_combined).ratio()` on line 582. Threshold `0.65` hardcoded on line 583. Exact-norm-title shortcut on line 579-580 returns `(tid, 1.0)`. Module-extraction target unchanged. Sister-doc rapidfuzz misreferences still need fixup.
- **`backend.spec` `fpcalc|chromaprint|acoustid|pyacoustid` grep**: zero hits. Bundling decision still pending. PATH-detect M1 plan holds.
- **`src-tauri/src/audio/fingerprint.rs`**: 399 LOC, ships Tauri commands `fingerprint_track` + `fingerprint_batch`. In-house Chromaprint-style. Confirms two-tier fingerprint plan (Rust local-cluster; `fpcalc` external-lookup) — see Findings 2026-05-17 "Rust-FP-via-IPC".
- **`_db_write_lock`** at `app/database.py:22` (verified by sister-doc quality-upgrade-finder 2026-05-15). Module read-only — no acquisition.

No verification has fallen out. All Constraints assertions remain true on main.

### 2026-05-17 — implementation-ready bar: test signatures + Options scoring + first-30-LoC pseudocode + git-diff prose

**Empirical re-verify (each Read against current `main` 2026-05-17):**

- `app/soundcloud_api.py:16` — `from difflib import SequenceMatcher`. STDLIB-only matcher confirmed.
- `app/soundcloud_api.py:550` — `class SoundCloudSyncEngine:`. Bound.
- `app/soundcloud_api.py:558-559` — `_normalize_title` body literally `return re.sub(r'[^\w\s]', '', title.lower().strip())`. Stateless, mechanical lift.
- `app/soundcloud_api.py:566` — `def _fuzzy_match_with_score(self, sc_title, sc_artist, local_tracks):` body lines 567-587.
- `app/soundcloud_api.py:579-580` — exact-norm-title shortcut `return tid, 1.0`. Confirmed.
- `app/soundcloud_api.py:582-583` — `ratio = SequenceMatcher(None, sc_combined, local_combined).ratio()` + `if ratio > best_ratio and ratio >= 0.65`. Confirmed.
- `app/soundcloud_api.py:587` — return shape `(best_match, round(best_ratio, 3))`. Confirmed.
- `app/database.py:22` — `_db_write_lock = threading.RLock()`. Confirmed.
- `app/database.py:26-40` — public `db_lock()` `@contextmanager`. Confirmed.
- `app/database.py:43-53` — private `_serialised` decorator. Confirmed.
- `Grep backend.spec` for `fpcalc|chromaprint|acoustid|pyacoustid` → zero matches. PATH-detect plan holds.
- `src-tauri/src/audio/fingerprint.rs:287-302` — `pub fn hamming_similarity(a: &[u32], b: &[u32]) -> Option<f32>`, `MIN_FP_LEN=4`. Confirmed.
- `src-tauri/src/audio/fingerprint.rs:320-334` — `pub async fn fingerprint_track(path: String) -> Result<Vec<u32>, String>`. **No `window` arg** — sidecar-callable IF Python could hit Tauri command surface (it can't). Confirmed signature.
- `src-tauri/src/audio/fingerprint.rs:343-398` — `pub async fn fingerprint_batch(paths: Vec<String>, window: tauri::Window) -> Result<HashMap<String, Vec<u32>>, String>`. `tauri::Window` injection ties batch-call to a frontend window — Python sidecar has no window handle. Confirmed.
- `Glob app/external_track_match*` → empty. Greenfield path.
- `tests/test_soundcloud_api.py:17-22` — import shape `from app.soundcloud_api import (AuthExpiredError, RateLimitError, SoundCloudSyncEngine, _sc_get)`. M1 must preserve this surface.

**EXACT test signatures for `tests/test_external_track_match.py` (M1 acceptance gate):**

Module is read-only + stateless except `ADAPTER_REGISTRY` dict. Tests use `pytest` (already wired) + `monkeypatch` (no new dev deps). Fixture file path `tests/fixtures/external_track_match/titles_corpus.yaml` (corpus = 200+ title cases; YAML loader via `PyYAML` — already pinned).

```python
# Pure functions
def test_normalize_title_lowercases_and_strips_punct():
    """normalize_title('Strobe!') == 'strobe' — mirrors _normalize_title at app/soundcloud_api.py:559."""

def test_normalize_title_handles_accents():
    """normalize_title('Pacífico') == 'pacifico' — NFD-fold (new vs current stdlib behaviour; gate)."""

def test_extract_title_stem_strips_paren_suffix():
    """extract_title_stem('Strobe (Radio Edit)') == 'strobe'."""

def test_extract_title_stem_strips_bracket_suffix():
    """extract_title_stem('Strobe [Extended Mix]') == 'strobe'."""

def test_extract_title_stem_strips_trailing_dash_variant():
    """extract_title_stem('Strobe - Extended Mix') == 'strobe'."""

def test_extract_title_stem_drops_feat_by_default():
    """extract_title_stem('Song feat. X') == 'song'; with drop_features=False → 'song feat. x'."""

def test_extract_title_stem_round_trip_three_shapes():
    """All four equivalent stems collapse to same root.
    extract_title_stem('Strobe (Radio Edit)') == extract_title_stem('Strobe - Extended Mix')
        == extract_title_stem('Strobe (Deadmau5 Club Mix)') == 'strobe'.
    """

@pytest.mark.parametrize("title,expected_label", [...])  # ≥200 cases from corpus
def test_parse_version_tag_label_recall(title, expected_label, labelled_titles):
    """≥95% label recall on 200-title corpus per Goals metric."""

def test_parse_version_tag_captures_remixer():
    """parse_version_tag('Strobe (Deadmau5 Remix)').remixer == 'Deadmau5'."""

def test_parse_version_tag_captures_year_modifier():
    """parse_version_tag('Strobe (2024 Edit)').modifiers == ('2024',)."""

def test_parse_version_tag_returns_none_on_no_suffix():
    """parse_version_tag('Strobe') is None."""

def test_parse_version_tag_canonical_label_set():
    """All parsed labels in canonical 12-member Literal set."""

# Fuzzy match equivalence vs current SC behaviour (regression-equivalence gate)
def test_fuzzy_match_with_score_equivalence_to_sc_baseline(sc_baseline_fixture):
    """For 50 (query, candidates) pairs harvested from SC sync logs, the
    new module-level fuzzy_match_with_score(...) returns IDENTICAL (tid, round_score)
    tuples as SoundCloudSyncEngine(db)._fuzzy_match_with_score(...).
    """

def test_fuzzy_match_with_score_exact_norm_title_returns_one_point_zero():
    """Mirrors short-circuit at app/soundcloud_api.py:579-580."""

def test_fuzzy_match_with_score_threshold_param_default_065():
    """Default threshold == 0.65 (matches hardcoded SC value); explicit override accepted."""

def test_fuzzy_match_with_score_no_match_returns_none_zero():
    """Empty candidates dict → (None, 0.0)."""

# Adapter registry
def test_register_adapter_idempotent():
    """Re-registering name=key replaces; no duplicate entry."""

def test_get_adapter_raises_when_missing():
    """get_adapter('nonexistent') raises AdapterNotRegistered."""

def test_list_adapters_returns_registered_names():
    """Order-independent membership check."""

@pytest.fixture(autouse=True)
def _reset_registry():
    """Teardown clears ADAPTER_REGISTRY so test order doesn't leak state."""

# Fingerprint PATH-detect (mock subprocess; no real fpcalc invocation in CI)
def test_is_fingerprinting_available_true_when_fpcalc_on_path(monkeypatch):
    """monkeypatch shutil.which('fpcalc') → '/usr/bin/fpcalc'; cached PATH-detect returns True."""

def test_is_fingerprinting_available_false_when_fpcalc_missing(monkeypatch):
    """monkeypatch shutil.which('fpcalc') → None; cached PATH-detect returns False."""

def test_fingerprint_returns_unavailable_when_fpcalc_missing(monkeypatch):
    """isinstance(fingerprint(path), FingerprintUnavailable.BinaryMissing) == True."""

def test_fingerprint_validates_audio_path_sandbox(monkeypatch, tmp_path):
    """path outside ALLOWED_AUDIO_ROOTS → raises ValueError BEFORE subprocess invocation."""

def test_fingerprint_respects_timeout_param(monkeypatch):
    """Mock subprocess.run records timeout kwarg; assert 10.0 default."""

def test_fingerprint_returns_fingerprint_dataclass_on_success(monkeypatch):
    """Mock fpcalc stdout returns valid output; result is Fingerprint(fpcalc_hash=..., duration_s=...)."""

# Module-purity gate (read-only invariant)
def test_module_has_no_db_writer_imports():
    """grep app/external_track_match.py for '_db_write_lock|pyrekordbox|rbox' → zero matches."""
```

Total = 22 tests + 1 autouse fixture. Coverage maps to Goals metrics: label-recall (`test_parse_version_tag_label_recall`), stem-extractor round-trip (`test_extract_title_stem_round_trip_three_shapes`), fuzzy-match equivalence (`test_fuzzy_match_with_score_equivalence_to_sc_baseline`), PATH-detect (3 `test_is_fingerprinting_*` + `test_fingerprint_returns_unavailable_*`), adapter registry (`test_register_adapter_idempotent` + `test_get_adapter_raises_when_missing`), read-only invariant (`test_module_has_no_db_writer_imports`).

**Quantified Options table (M1 viability scoring):**

Scoring rubric per criterion 1-5 (5=best). Sums + risk decide.

| Criterion | A flat-functions | B class-DI | C hybrid (fns + reg) | D subpackage |
|---|---:|---:|---:|---:|
| **Effort M1** (LoC, days; 5 = lowest) | 5 (~400 LoC, 1 d) | 3 (~700 LoC, 2 d) | 5 (~500 LoC, 1.5 d) | 2 (~900 LoC scaffold + ~500 LoC content, 3 d) |
| **Migration cost A→C or A→D later** (5 = cheapest) | 5 (in-place add registry dict) | 2 (back-out class wrapper) | 5 (already there) | 1 (already paid; reverse path absurd) |
| **Test friction** (5 = cleanest fixtures) | 5 (pure fns) | 3 (instance per test) | 4 (autouse reset_registry) | 4 (per-submodule isolated) |
| **Import latency for one-helper callers** (5 = fastest cold-import) | 5 (import 1 sym) | 3 (instantiate class) | 4 (registry init at import) | 4 (re-export hop) |
| **Adapter plugin slot quality** (5 = clean DI) | 1 (none on day 1) | 5 (constructor inject) | 4 (registry dict + `register_adapter`) | 5 (subpackage `adapters/`) |
| **Pre-M3 over-engineering** (5 = least premature) | 5 (zero scaffold) | 2 (class for 1-2 adapter case) | 4 (registry dict cheap) | 1 (4+ submodules with 1-2 used) |
| **Risk if N>3 sister-features land** (5 = scales best) | 2 (refactor to class or sub-package forced) | 4 (clean DI scales) | 4 (promotion gates fire, move to D) | 5 (already structured) |
| **TOTAL** (max 35) | **28** | **22** | **30** | **22** |

C wins by 2 points over A, 8 over B/D. Result reproduces Recommendation pick. Tie-break A vs C: C buys plugin slot for one extra day; A defers it but every sister-feature re-derives one. C dominates.

**Pseudocode for first ~30 LoC of M1 (`app/external_track_match.py` — Protocol shapes + dataclasses + adapter registry skeleton):**

Prose-form (no real code committed); shapes load-bearing for sister-doc consumers.

```
# app/external_track_match.py
# (Module header docstring lifts SC matcher to module-level. Read-only.
#  No master.db writes, no rbox imports.)

# Stdlib imports: dataclasses, typing.Protocol, typing.Literal,
#   typing.runtime_checkable, functools.lru_cache, re, difflib.SequenceMatcher,
#   shutil, subprocess, pathlib.Path, logging.

# Logger.

# Public canonical Literal alias:
#   VersionLabel = Literal["original","extended","radio","club","dub",
#                          "instrumental","acapella","vip","remix","bootleg",
#                          "edit","mashup"]

# @dataclass(frozen=True, slots=True)
# class VersionTag:
#     label: VersionLabel
#     remixer: str | None = None
#     modifiers: tuple[str, ...] = ()
# (Hashable via frozen + tuple; cheap eq/hash for fixture parametrize.)

# @dataclass(frozen=True, slots=True)
# class Candidate:
#     source: str         # adapter name (e.g. "soundcloud")
#     source_id: str      # adapter-native ID (e.g. SC track ID)
#     title: str
#     artist: str
#     duration_s: float | None
#     version_tag: VersionTag | None
#     url: str | None
#     raw: dict           # adapter-specific escape-hatch payload

# @dataclass(frozen=True, slots=True)
# class Fingerprint:
#     fpcalc_hash: str
#     duration_s: float

# Sentinel union for fingerprint failure (importable singletons):
#   class FingerprintUnavailable:
#       class BinaryMissing: ...
#       class Timeout: ...
#       class DecodeError: ...

# Error hierarchy:
#   class AdapterError(Exception): ...
#   class AdapterNotRegistered(AdapterError): ...
#   class AdapterTransportError(AdapterError): ...
#   class AdapterQuotaExceeded(AdapterError): ...
#   class AdapterParseError(AdapterError): ...

# @runtime_checkable
# class SourcePlugin(Protocol):
#     name: str
#     async def search(self, title: str, artist: str, duration_s: float | None = None,
#                      *, max_results: int = 20) -> list[Candidate]: ...
#     def parse_version(self, raw: dict) -> VersionTag | None: ...
#     def quota_remaining(self) -> int | None: ...

# Module-level registry singleton (mutated at adapter import / boot):
#   ADAPTER_REGISTRY: dict[str, SourcePlugin] = {}
#   def register_adapter(name: str, plugin: SourcePlugin) -> None:
#       ADAPTER_REGISTRY[name] = plugin   # idempotent replace
#   def get_adapter(name: str) -> SourcePlugin:
#       try: return ADAPTER_REGISTRY[name]
#       except KeyError: raise AdapterNotRegistered(name)
#   def list_adapters() -> list[str]:
#       return list(ADAPTER_REGISTRY.keys())
```

First ~30 LoC = imports + `VersionLabel` Literal + 3 frozen dataclasses + `FingerprintUnavailable` sentinel + 4 `AdapterError` subclasses + `SourcePlugin` Protocol + registry dict + 3 registry mutators. Pure-function ops (`normalize_title`, `extract_title_stem`, `parse_version_tag`, `fuzzy_match_with_score`, `fingerprint`, `is_fingerprinting_available`) follow after the skeleton. Total M1 file = ~400-500 LoC (well under 800 LoC cap before subpackage migration triggers).

**Git-diff prose (M1 commit-level surface):**

This doc does not commit code; the following prose describes what M1 commit will mutate.

- **NEW file** `app/external_track_match.py` — ~400-500 LoC. Public surface: `normalize_title`, `extract_title_stem`, `parse_version_tag`, `fuzzy_match_with_score`, `fingerprint`, `is_fingerprinting_available`, `register_adapter`, `get_adapter`, `list_adapters`, `VersionLabel`, `VersionTag`, `Candidate`, `Fingerprint`, `FingerprintUnavailable`, `SourcePlugin`, `AdapterError` hierarchy, `ADAPTER_REGISTRY`.
- **NEW file** `tests/test_external_track_match.py` — 22 tests per signatures above. Imports `pytest`, `monkeypatch` only. No new dev deps.
- **NEW file** `tests/fixtures/external_track_match/titles_corpus.yaml` — ≥200 hand-labelled `(title, expected_label, expected_remixer, expected_modifiers)` records harvested from `master.db` (owner manual labelling, ~3 hr work). PyYAML loader (already pinned).
- **MODIFIED** `app/soundcloud_api.py` — `_fuzzy_match_with_score(self, sc_title, sc_artist, local_tracks)` body at lines 566-587 replaced with single-line delegate `return external_track_match.fuzzy_match_with_score(sc_title, sc_artist, local_tracks)`. `_normalize_title` body at line 558-559 same delegate pattern. Import `from . import external_track_match as etm` added near line 16-19 area. Net diff: -22 LoC body, +2 LoC delegates, +1 LoC import. Behaviour-preserving — existing `tests/test_soundcloud_api.py` regression suite green.
- **MODIFIED** `docs/FILE_MAP.md` + `docs/MAP.md` + `docs/MAP_L2.md` — new entry for `app/external_track_match.py` via `python scripts/regen_maps.py`.
- **MODIFIED** `docs/backend-index.md` — no entry (module is internal, no FastAPI route surface in M1).
- **UNCHANGED** `requirements.txt` — zero new deps (`difflib`, `re`, `subprocess`, `pathlib`, `typing`, `dataclasses`, `functools`, `shutil`, `logging` all stdlib).
- **UNCHANGED** `backend.spec` — no new binaries (`fpcalc` PATH-detect M1).
- **UNCHANGED** `src-tauri/**` — Rust-FP-via-IPC is M2 territory per OQ12.

Commit message draft: `feat(backend): add app/external_track_match.py — extract SC fuzzy matcher + version-tag taxonomy + adapter registry as shared module (M1 of unified-module exploring_)`.

### 2026-05-15 — module-API: scope boundary + dep footprint

NOT in module (sister-feature concerns):

- Match-result persistence (sidecar SQLite per sister-doc).
- UI / API routes / Pydantic models for FastAPI.
- Confidence-score composition (`+0.2 if duration matches` etc. — per-feature business logic).
- Replacement / acceptance / dismissal lifecycle.
- rbox / `master.db` reads or writes.

M1 dep footprint: **zero new deps.** `difflib.SequenceMatcher`, `re`, `subprocess`, `pathlib`, `typing.Protocol`, `dataclasses.frozen`, `functools.lru_cache` = stdlib. `httpx` (already pinned for SC) covers adapter HTTP. `rapidfuzz` swap (5–10× faster) = separate Schicht-A draftplan decision.

## Options Considered

### Option A — Function-only flat module

- Sketch: top-level functions in `app/external_track_match.py`: `extract_title_stem(title) → str`, `parse_version_tag(title) → VersionTag`, `fuzzy_match(title, artist, candidates, threshold=0.65) → list[Match]`, `fingerprint(path) → str | None`. No class. Adapter registry as module-level `dict[str, Callable]` populated at import.
- Pros: lightweight, no instantiation overhead, easiest to test (pure functions), minimal import cost for sister-docs that only need one helper.
- Cons: harder to wire dependency injection later (e.g. swap adapter registry for mocking), module-global state for cache + registry is hidden, refactor cost when N>3 features need different config.
- Effort: S
- Risk: Low. Worst case: refactor to class-based when DI becomes painful — pure functions migrate to methods cleanly.

### Option B — Class-based with adapter registry

- Sketch: `TrackMatcher` class holds adapter registry, fuzzy threshold, cache instance. Sister features instantiate one (`matcher = TrackMatcher(adapters=[...], threshold=0.65)`) or grab a module-singleton (`get_default_matcher()`).
- Pros: clean DI for testing (inject mock adapters), explicit lifecycle, per-instance config (e.g. quality-upgrade wants strict 0.80, extended-finder wants 0.65).
- Cons: more boilerplate, instantiation overhead per call-site, sister-docs forced into instance pattern even for one-shot title-stem extraction.
- Effort: M
- Risk: Low-medium. Risk = over-engineering before need is proven.

### Option C — Hybrid (functions + registry singleton)

- Sketch: pure module-level functions for stateless ops (`extract_title_stem`, `parse_version_tag`, `fuzzy_match`). Module-level `ADAPTER_REGISTRY: dict[str, SourcePlugin]` mutated at boot (via `register_adapter(name, plugin)`). Cache as module-level LRU. No class.
- Pros: lowest friction (sister-docs call functions, register adapters at boot), supports per-source threshold via function arg, easy to migrate to Option B if DI need emerges. Minimal API surface change to migrate forward.
- Cons: module-global state (registry, cache) — must be careful in tests (fixture reset). Less explicit than class-based.
- Effort: S-M
- Risk: Low. Module-global state in tests = `pytest` fixture with `reset_registry()` teardown.

### Option D — Subpackage `app/track_match/` with submodules

- Sketch: `app/track_match/__init__.py` re-exports public API; `match.py` (fuzzy), `version_parse.py` (taxonomy + extractor), `fingerprint.py` (chromaprint wrapper), `adapters/` (one file per source: `discogs.py`, `beatport.py`, `bandcamp.py`, `qobuz.py`, `youtube.py`, `soundcloud.py`).
- Pros: cleanest separation, each submodule independently testable, adapters easy to add/remove, scales to 5+ sources without becoming a 2k-line file.
- Cons: heaviest scaffolding cost up-front, premature for M1 when only 1-2 adapters exist. Risk of empty/skeleton submodules sitting unused for months.
- Effort: M-L
- Risk: Low — but the cost is paid up front whether all subdirs get used or not.

## Recommendation

**Option C** (hybrid: functions + module-level registry singleton) **for M1.** Migrate to **Option D** (subpackage `app/track_match/`) when promotion gates fire.

Rationale: Option A defers adapter-registry decision (each sister-doc invents its own). Option B forces class-based instantiation before DI is actually needed. Option D scaffolds 4+ submodules when only 1-2 are populated on day one. Option C lands function-level API + registry mutation point — sister-docs call `register_adapter("discogs", ...)` at boot — without committing to package layout.

### Phased deliverables + gates

**M1 — core module landing (this doc's primary deliverable)**

- **Scope:** lift `_fuzzy_match_with_score` + `_normalize_title` from `SoundCloudSyncEngine` to module-level pure functions in `app/external_track_match.py`. Add `extract_title_stem`, `parse_version_tag`, `VersionTag` / `Candidate` / `Fingerprint` dataclasses, `SourcePlugin` Protocol, registry mutators, PATH-detect `fingerprint` wrapper. SC engine becomes the first real adapter (`SoundCloudAdapter`); mock-adapter ships in tests.
- **Deliverables:**
  - `app/external_track_match.py` (single file, M1 cap 800 LOC).
  - `tests/test_external_track_match.py` (regex catalogue ≥200 fixtures, taxonomy round-trip, PATH-detect mock, fuzzy-match equivalence vs current SC behaviour).
  - `app/soundcloud_api.py` updated: `_fuzzy_match_with_score` becomes a thin delegate to `external_track_match.fuzzy_match_with_score` (preserves call-site compatibility, can deprecate later).
  - Entry in `docs/FILE_MAP.md` + `docs/MAP.md` (regen).
- **Gates to land M1:**
  - SC sync regression suite green (`pytest tests/test_soundcloud_*.py`).
  - ≥95 % label-recall on 200-title taxonomy fixture.
  - `mypy app/external_track_match.py` clean (Schicht-A typed-API discipline).
  - `ruff` + `ruff format` clean.
  - Zero new deps in `requirements.txt`.
  - No `_db_write_lock` / `rbox` / `pyrekordbox` imports.
- **Gates to promote `inprogress_` → `implemented_`:**
  - Above M1 gates + ≥1 sister-feature's `inprogress_` PR consumes the module without re-deriving.

**M2 — first external adapter validates the registry**

- Triggers when sister-doc `library-extended-remix-finder` reaches `draftplan_` (the Discogs adapter is its primary external source).
- **Scope:** `DiscogsAdapter` implementation (hand-rolled httpx, 60 req/min throttle); per-source threshold override exposed (resolves OQ9); `settings.json` adapter-toggle wiring (resolves OQ4).
- **Deliverables:** adapter file under flat module (or sub-namespace if file >500 LOC); httpx pinning entry in `requirements.txt`; adapter-toggle Pydantic model.
- **Gates:** Discogs lookups respect 60/min budget (rate-limit test); adapter passes shared `SourcePlugin` contract test (mock fixture validates interface).

**M3 — fingerprinting goes from optional to bundled**

- Triggers when remix-detector's use-case (b) ("Bootleg — find canonical original") or quality-upgrade-finder's same-edit gate (rule 2: "Chromaprint match required") reaches `draftplan_`.
- **Scope:** bundle `fpcalc` per-platform under `app/bin/fpcalc/{win,mac,linux}/`; update `backend.spec` `binaries=[...]`; CI matrix per-OS verification; bundling Schicht-A entry (resolves OQ2 fully).
- **Deliverables:** binary files committed (LFS or `.gitattributes` rule); `backend.spec` change; CI workflow tweak; smoke test that bundled binary executes on each OS.
- **Gates:** desktop installer artefacts on Windows/macOS/Linux include `fpcalc`; first-run smoke test on fresh OS verifies binary executes; size budget +10 MB max across 3 OS.

**Subpackage migration (Option C → D) — separate gate, not in M1/M2/M3**

Fires when ALL true:

- ≥3 sister features actually shipping (`implemented_` state, not `idea_`).
- ≥3 adapters implemented (SC + Discogs + Local-HQ-folder at minimum).
- Module file >800 LOC OR single function >150 LOC OR adapter count >4.
- ≥2 sister features each contributing distinct test edge-cases — justifies submodule test-file split.

### Pre-promote-to-`exploring_` checklist

- [x] Goals each carry a testable metric.
- [x] Constraints re-verified (fuzzy matcher uses `SequenceMatcher` not rapidfuzz; `_db_write_lock` at `app/database.py:22`; `fpcalc` not bundled; rate-limits per sister-doc citations).
- [x] Open Questions resolved-or-parked: 8 RESOLVED-M1 (OQ1, 2, 3, 5, 6, 7, 8, 10), 1 DEFERRED to M2 (OQ4 adapter-toggle), 1 DEFERRED-DECISION-NEEDED at M2 (OQ9 per-source threshold tuning — SC dirtier than Discogs), 1 OPEN deferred to draftplan (OQ11 async-vs-sync adapter shape). Total = 11/11.
- [x] Findings include module-API design specifics (signature-level prose, not real code).
- [x] Options 4 differentiated sketches with effort + risk.
- [x] Recommendation phased M1/M2/M3 with deliverables + gates.

### Pre-promote-to-`evaluated_` checklist (added 2026-05-17)

- [x] Re-verify load-bearing facts on main (`SequenceMatcher`, `backend.spec`, `fingerprint.rs`, `_db_write_lock`). See Findings 2026-05-17 "verification round".
- [x] Coordinate `VersionTag.label` canonical enum across 4 sister-docs. See Findings 2026-05-17 "canonical enum" — member-set aligned, ordering proposed.
- [x] Document classifier-input → canonical-label collapse table (extended-finder's `extended mix`/`long version`/`12" mix` tokens all map to `extended`). See Findings 2026-05-17.
- [x] Rust-FP-via-IPC question answered (OQ12 RESOLVED-M1 Tauri-direct-IPC only).
- [x] Rust-side AcoustID lookup deferred (OQ13 DEFERRED M3).
- [x] Updated OQ tally: **13/13** (10 RESOLVED-M1, 2 DEFERRED, 1 OPEN-for-draftplan).
- [ ] **Sister-doc cross-doc fixups landed** (blocker for `evaluated_` promote):
  - [ ] `analysis-remix-detector`: re-order Goals enum to canonical order `(original, extended, radio, club, dub, instrumental, acapella, vip, remix, bootleg, edit, mashup)` for diff-grep alignment (members identical, low-risk edit).
  - [ ] `library-extended-remix-finder`: add cross-ref to this doc's classifier-input table in Constraints — `extended mix`/`long version`/`12" mix` collapse to `extended` label, not separate values.
  - [ ] `library-quality-upgrade-finder`: add note in Constraints that the canonical `VersionTag.label` lives here; quality-upgrade is read-only consumer.
  - [ ] `analysis-remix-detector` Constraints (line 66): correct sister-doc reference (still says "M1 consumes the unified-module pure-function wrapper") — point at this doc's Recommendation section, not generic.
  - [ ] All three sister-docs: confirm `Candidate` dataclass shape — already agreed in Findings 2026-05-15 "module-API: concrete dataclasses" but not explicitly signed-off in sister-doc text.
- [ ] **Quality-upgrade Constraints update** (blocker for `evaluated_`):
  - [ ] Note that Rust-FP-via-IPC can substitute `fpcalc` for safety-rule 2 when candidate is also a local file (HQ-folder scenario) — per Findings 2026-05-17 "Rust-FP-via-IPC" final bullet. Keeps replace-flow usable when `fpcalc` missing AND both files are local.
- [ ] Owner sign-off on Option C vs Option D split (M1 = flat file; subpackage migration gated). Already captured in Recommendation but never explicitly ack'd.

**Promote `exploring_` → `evaluated_`** only after sister-doc fixups land (PRs touching the 3 sister-docs only — this doc's design content is `evaluated_`-grade already). Owner ack on Recommendation completes promotion. NO code change required before `accepted_`.

---

## Implementation Plan

> Required from `implement/draftplan_` onward. Concrete enough that someone else executes without re-deriving.

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

- Code (existing):
  - `app/soundcloud_api.py:16` (`from difflib import SequenceMatcher`)
  - `app/soundcloud_api.py:550` (`SoundCloudSyncEngine`)
  - `app/soundcloud_api.py:558` (`_normalize_title`)
  - `app/soundcloud_api.py:566` (`_fuzzy_match_with_score`)
  - `app/soundcloud_api.py:580` (exact-match short-circuit returning `(tid, 1.0)`)
  - `app/soundcloud_api.py:582-583` (`SequenceMatcher` call + 0.65 threshold)
  - `app/database.py:22` (`_db_write_lock = threading.RLock()`)
  - `app/anlz_safe.py` (rbox quarantine pattern — referenced for module-non-coupling argument)
  - `app/main.py:138-189` (`ALLOWED_AUDIO_ROOTS` validator)
- External docs:
  - Chromaprint / fpcalc: https://acoustid.org/chromaprint
  - AcoustID API: https://acoustid.org/webservice
  - MusicBrainz rate limits: https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
  - Discogs API: https://www.discogs.com/developers
- Related research: `analysis-remix-detector`, `library-extended-remix-finder`, `library-quality-upgrade-finder`
