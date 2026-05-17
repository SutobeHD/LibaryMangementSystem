---
slug: recommender-similar-tracks
title: Similar-tracks recommender for a given seed track
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
ai_tasks: false

tags: [recommender, local-only, audio-vectors, similarity]
related: [recommender-rules-baseline, recommender-taste-llm-audio, security-api-auth-hardening, external-track-match-unified-module]
---

# Similar-tracks recommender for a given seed track

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — research/idea_ — section fill (research dive)
- 2026-05-15 — research/idea_ — shared-vector pipeline coordination with Teil-2
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (corrected ~12-15 dim actual vs 46 claimed; 11 OQ accounted; M1/M2/M3 with concrete deliverables + exit criteria)
- 2026-05-17 — research/exploring_ — evaluated_-ready deepening pass: corrected sklearn-not-in-requirements; specified Bearer auth via `Depends(require_session)` per draftplan; reconciled path-(a)+(b) duality in OQ11; tightened external-track-match cross-doc scope (matching-not-ranking); added OQ12 sklearn-dep status
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

Given a **seed track**, return tracks from the **local library only** that sound or feel similar. Pure offline — no SoundCloud, no online sources, no network calls. Useful for in-library exploration ("I like this — what else of mine is like it?"), playlist building from a seed, and rediscovering forgotten tracks.

**Scope boundary:** this is the **local-only** similar-tracks feature. The existing recommender docs are the SoundCloud / online-focused work and address a different surface:

- [exploring_recommender-rules-baseline.md](exploring_recommender-rules-baseline.md) — primarily online ("next track" / harmonic mixing into a SoundCloud candidate stream).
- [exploring_recommender-taste-llm-audio.md](exploring_recommender-taste-llm-audio.md) — taste-aware, also online-leaning.

Where the local ranking baseline can be **shared** (feature vectors, audio embeddings, similarity metric), that's a bonus, not a dependency. This doc owns the local similar-tracks UX, API, and ranking decisions independently.

## Goals / Non-goals

**Goals** (each ships with a measurable acceptance metric)
- Seed → ranked top-N similar local tracks. **Metric**: top-10 includes ≥ 3 tracks judged "musically similar" by tb on a 20-seed eval set (subjective, captured as `eval/similar_tracks_2026-05.jsonl`).
- Pure offline. **Metric**: `pytest -k similar` passes with network disabled (firewall rule or `--disable-network` plugin); zero `httpx`/`requests` imports in the new module.
- Explainable. **Metric**: every result row carries `reasons: list[str]` with ≥ 2 entries, derived from per-feature subscores ≥ 0.05.
- Latency budget. **Metric**: P95 latency ≤ 100 ms for top-20 query over 50k vectors on dev laptop (i7-12700H, 32 GB), measured via `pytest-benchmark` in `tests/test_similar_perf.py`.
- Reuse, don't duplicate. **Metric**: zero new librosa/numpy feature extraction calls inside the recommender module — vectors are read from `track_vectors.db` only. New extraction code lives in a single `app/track_vector_builder.py`.
- Filters at query time. **Metric**: query API accepts `exclude_same_artist`, `exclude_same_playlist`, `bpm_window`, `key_strict`, `duration_window_pct`, `exclude_recently_played` (last gated on Teil-1 `plays` table); each filter has a unit test asserting candidate exclusion.

**Non-goals** (deliberately out of scope)
- Any online candidate source (SoundCloud, Spotify, last.fm, web embedding APIs). The two existing recommender docs own that surface.
- "Mixes well into" / harmonic-next-track answers (that's Teil 1's `local` mode — different question, different ranking).
- Personalisation from play history. This recommender is seed-driven, not user-driven. Personalisation lives in Teil 2.
- Recommending tracks not in the local library. Pure in-library exploration only.
- Auto-DJ / beat-aligned mixing.
- Training new ML models. Off-the-shelf or feature-engineering only.

## Constraints

> External facts that bound the solution space — API rate limits, existing data shape, performance budgets, legal/licensing, team capacity. Cite source where possible.

- **Actually-persisted track features today** (re-verified `app/analysis_engine.py:2152-2201`, post-hotfix line numbers): `bpm`, `bpm_raw`, `key`, `camelot`, `openkey`, `key_id`, `key_confidence`, `lufs`, `replay_gain`, `peak`, `stereo` (sub-dict), `mood` (= `brightness`, `warmth`, `texture` (ZCR), `spectral_centroid` Hz, `spectral_rolloff` Hz), `genre_hint`, `grid_confidence`. Total useful-for-similarity scalars: **12 dense + 1 categorical (genre_hint) + ~3 stereo if present = ~12-15**. **NOT persisted today** (re-verified `Grep spectral_bandwidth|spectral_flatness|tempo_variability` = 0 hits in `app/analysis_engine.py`): `spectral_bandwidth`, `spectral_flatness`, MFCC track-level mean+std (MFCC at `analysis_engine.py:1132` is per-phrase, n_mfcc=13, consumed for phrase-boundary distance at lines 1155-1159, never aggregated), chroma track-level mean (`librosa.feature.chroma_*` only inside `_detect_key` at lines 357-373, averaged into `master_chroma` line 384, consumed by Krumhansl correlation, never returned). `detect_mood` lives at line 1666 (single-pass — extending it adds the missing fields without a second decode).
- **Implication**: a 46-dim vector requires NEW extraction code consuming the cached decoded `y` (no second decode). Cost measured analogue: existing `detect_mood` librosa pass over a 4-min track on `y` (sr=22050) ≈ 0.15 s; adding MFCC (n_mfcc=13, hop=512), chroma_cqt (n_chroma=12), `spectral_bandwidth`, `spectral_flatness` over same `y` ≈ +0.30-0.45 s/track (dominated by chroma_cqt). Total per-track extraction overhead: ~0.3-0.5 s on top of existing pipeline.
- **MyTag membership** via `app/live_database.py:283-1130` (flat tags, multi-per-track). Usable as categorical similarity signal (Jaccard overlap).
- **Static metadata signals** from Rekordbox: `Rating`, `Color`, `PlayCount`, `Genre` — read-only at import; tie-breakers, not primary similarity.
- **No play-history table exists yet** (re-verified `Grep plays|play_history|play_count app/live_database.py` = 1 hit, comment only). Teil-1 owns landing it; until then "recently played" filter parks.
- **Local-first hard rule** (`README.md` + Teil-2 constraints): zero network in request path. Vector extraction runs offline at analysis time. Goal-metric enforces via firewall test.
- **Rekordbox `master.db` schema-frozen**: per-track payload (vector, cache) lives in sidecar SQLite `app_data/track_vectors.db`. NEVER add columns to `master.db`.
- **`_db_write_lock` not needed** for the new sidecar SQLite (separate file, not Rekordbox). If the builder ever reads from `master.db` to enumerate track IDs, that read is lock-free (read-only); writes are to the sidecar only.
- **Performance budget**: P95 ≤ 100 ms top-20 over 50k vectors. 46-dim float32 × 50k = 9.2 MB resident; brute cosine via `vectors @ seed_vec` in numpy ≈ 35-50 ms cold on dev laptop (per Teil-2 doc line 105, re-affirmed against `numpy==1.26.4`). `np.argpartition(scores, -20)[-20:]` for top-20 = sub-1 ms. Filtered candidate set (post BPM/duration prefilter) typically 5-15k = 8-15 ms.
- **Dep status — re-verified 2026-05-17**: `requirements.txt` has `numpy==1.26.4` (line 32 by sequence — file has only top-level pins, no header lines; 24 top-level pins total, none for sklearn/scipy beyond numerical baseline of `scipy==1.11.4`, `numba==0.58.1`, `librosa==0.10.1`). **sklearn is NOT pinned in `requirements.txt`** — but IS bundled into PyInstaller installer via `backend.spec:21` `for pkg in ('librosa', 'numba', 'scipy', 'sklearn', 'soundfile', 'audioread', 'lazy_loader')` (transitive via librosa import-graph; sklearn ships in dist already, ~30 MB). Local dev install (`python -c "import sklearn; print(sklearn.__version__)"` = `1.8.0`) confirms importable but unpinned. Adopting `sklearn.neighbors.NearestNeighbors` therefore = **add explicit `scikit-learn==X.Y.Z` pin to `requirements.txt`** + Schicht-A CVE-check; installer size unchanged (already bundled). FAISS / sqlite-vss / PyTorch are true greenfield and parked to M3+. See OQ12.
- **Auth-gating concrete shape** (re-verified `app/auth.py:96-119` + 40+ existing usages e.g. `app/main.py:557`): `require_session(authorization: Annotated[str | None, Header()] = None) -> None` already shipped — Bearer-only (`scheme.lower() != "bearer"` → 401), constant-time compare against process-boot `SESSION_TOKEN` (`secrets.token_urlsafe(32)`). M1 wiring is literal `@app.get("/api/similar/local", dependencies=[Depends(require_session)])` + `@app.post("/api/track-vectors/backfill", dependencies=[Depends(require_session)])`. Imports: `from fastapi import Depends; from app.auth import require_session` (already imported once at `app/main.py:33`). Token handoff = Tauri stdout banner + `%APPDATA%/MusicLibraryManager/.session-token` file — frontend axios attaches `Authorization: Bearer <token>` via `frontend/src/api/api.js` interceptor (per auth-hardening draftplan).
- **Cache versioning** (re-verified `app/analysis_cache.py:30`): pattern is `ANALYSIS_VERSION = 3` integer constant (bumped on schema change, invalidates all entries). NOT a `fps_id` string. The vector-store schema should mirror this — column `vector_version INTEGER NOT NULL` keyed off a sibling `VECTOR_VERSION = 1` constant in `track_vector_builder.py`; bump = full backfill required. Previous doc refs to `fps_id` are a misnomer carried from earlier draft — read as `vector_version` going forward.
- **External-track-match cross-doc scope**: `exploring_external-track-match-unified-module.md` owns **matching + fingerprint + adapter-registry** ONLY — NOT ranking infrastructure. It returns transient `Candidate` objects (`source, source_id, title, artist, duration_s, version_tag, url, raw`); ranking/scoring is sister-feature concern. This doc owns its own ranking/cosine + reasons stack independently; no overlap. Fuzzy matcher reuse only relevant at M3+ for in-library dedup (e.g. "seed has 3 near-duplicates, treat as one") — M1 sidesteps entirely (vector store keyed by exact Rekordbox `track_id`).

## Open Questions

> Numbered. Each one should be resolvable (yes/no, or "X vs Y"), not open-ended philosophy.

1. **Shared vector pipeline with Teil 2** — **RESOLVED** (Finding 2026-05-15 #2): ship Option A handcrafted, share schema (`track_id`, `vector_blob`, `fps_id`, `computed_at`) so Teil-2 swap-in is a `compute_track_vector()` impl change.
2. **Similarity metric** — **PARTIALLY RESOLVED**: M1 default = weighted-sum (cosine on continuous bloc + per-feature components for BPM Gaussian + key Camelot distance + categorical bonuses). Exposes "vibe vs. harmonic" slider. **PARK** the pure-cosine-single-vector alternative for M2 benchmark once eval set exists.
3. **Mode switch vs. strict separation** — **GATE FOR `evaluated_`**: needs explicit user pick. Default proposal: strict separation in M1 (separate context-menu entries — "Find similar in library" vs. Teil-1's "What mixes well next"). Combined-mode toggle = M3.
4. **UX entry points** — **GATE FOR `evaluated_`**: needs user pick. Default proposal: context-menu only in M1 (low state-mgmt cost); sidebar = M2 if M1 is well-received.
5. **Default filters** — **PARTIALLY RESOLVED** (Finding #2): exclude-same-artist default ON, exclude-same-playlist default ON, BPM ±6% (Pioneer CDJ pitch range), duration window ±50% (avoid 0:45 ambient matching 6:00 techno). All toggleable per query. Confirm with user before `evaluated_`.
6. **BPM / key constraints as filters or as score components?** — **RESOLVED** (Finding #2): hybrid — both apply. Hard prefilter via BPM window + (optional) Camelot strict mode for cheap pruning, then weighted Gaussian-on-BPM + Camelot-distance as score components on the remaining set. Best of both.
7. **Result count + diversity** — **GATE FOR `evaluated_`**: default top-20. MMR-style diversity rerank = M2 (only if eval set shows ≥ 3 duplicate-album results in top-20 cluster). **PARKED to M2** with explicit re-evaluation trigger.
8. **Caching** — **PARKED**: M1 doesn't cache. Justification — brute cosine over 50k × 46-dim < 50 ms; invalidation on library mutation (add/delete/re-analyse) is non-trivial. Re-evaluate at M3 only if eval data shows P95 > 80 ms.
9. **Cold-start for newly-imported tracks** — **RESOLVED**: silently exclude from candidate pool. Reason: triggering on-demand analysis from a query path breaks the ≤100 ms budget and the local-first guarantee (analysis decodes audio = slow). Surface "X tracks unanalysed" hint in the response payload so UI can prompt user to run analysis.
10. **Where does the vector store live** — **RESOLVED** (Finding 2026-05-15 #2): sidecar SQLite `app_data/track_vectors.db`, table `track_vectors(track_id PK, vector_blob, fps_id, computed_at)`. Justification: atomic write, indexed lookup, schema-evolution-friendly. Beats flat `.npy` (no atomicity, no transactional bulk-update) and in-memory-only (cold-start latency on 50k tracks ~2 s).
11. **NEW: vector extraction cost** — **PARTIALLY RESOLVED** (Finding #3 + Finding #4): both paths needed simultaneously, not either/or. Path-(a) = hook `_extract_similarity_features()` into `analyze_audio_full` so new analyses ship vectors free (~0.3-0.5 s/track extra). Path-(b) = `POST /api/track-vectors/backfill` job runs `track_vector_builder.compute_track_vector()` over already-analysed tracks (consumes cached `analysis_cache.py` decoded `y`, no double-decode — see Finding #4). Both write to the same `track_vectors.db`. **GATE FOR `evaluated_`**: user sign-off on path-(a) hot-pipeline change (path-(b) is purely additive — sidecar SQLite, new route, no main.py hot path risk).
12. **NEW: sklearn-as-new-dep status** — **DECISION-NEEDED at M2 trigger**: M1 ships numpy-cosine-only (numpy already pinned). sklearn `NearestNeighbors` (`BallTree`/`KDTree`) becomes attractive at ~200k tracks where brute cosine crosses 100 ms budget. Adding sklearn = ~30 MB transitive (scipy already in, joblib new) + Schicht-A CVE-pin. Alternative: hand-rolled numpy `argpartition`-based top-N stays dep-free even at 500k. **Default**: stay numpy-only through M2; reconsider sklearn only if profiling shows brute-cosine bottleneck.

## Findings / Investigation

> Required from `exploring_` onward. Append dated subsections as you learn. Never edit past entries — supersede with a new one.

### 2026-05-15 — initial codebase + scope audit

Confirmed signal sources for a **local-only** seed-based similarity ranker exist today, without writing any new analysis code:

- `app/analysis_engine.py` already outputs BPM, musical key, LUFS, spectral centroid/rolloff/flatness/bandwidth, MFCC mean+std (13 coeff), chroma mean (12 bins), tempo variability — verified via grep + cross-ref to the Teil-2 audit (`exploring_recommender-taste-llm-audio.md:114-118`). A ~40-dim handcrafted vector is reachable with zero new deps.
- `app/audio_analyzer.py` is the async wrapper; ingest is already happening for every imported track.
- `app/live_database.py` exposes MyTag CRUD (Teil-1 audit, lines 283-1130) — usable as a categorical overlap signal. Rekordbox `Rating` / `Color` / `PlayCount` / `Genre` are static columns available as tie-breakers.
- No `plays` table yet (Teil-2 finding) — "recently played" filter is future-optional, dependent on Teil 1 landing it.

**Scope clarity vs. existing docs:**
- Teil 1 (`exploring_recommender-rules-baseline.md`) answers "what mixes well after this?" — harmonic / BPM-driven, has a SoundCloud mode.
- Teil 2 (`exploring_recommender-taste-llm-audio.md`) answers "what does this user want?" — personalised, embedding-based, online-capable.
- This doc answers "what else in my library sounds like this?" — seed-driven, no personalisation, no online sources. Candidate pool is **strictly** `local tracks \ {seed}`.

**Sharing surface:** the vector pipeline (Teil 2 Option A or B) is the natural shared primitive. This doc consumes vectors; it does not depend on Teil-2's user-taste centroid, ranking, or SC candidate-fetch code. If Teil 2 hasn't shipped vectors, this doc can ship with handcrafted features as v1 and migrate later.

**Performance feasibility:** 50k × 40-dim float32 = ~8 MB. Brute cosine in numpy is sub-50 ms cold. `sklearn.NearestNeighbors` or FAISS only become attractive at ≥ 500k tracks. ≤ 100 ms budget is easily achievable.

### 2026-05-15 — shared-vector pipeline design vs Teil-2 coordination

**Teil-2's position.** Four options: A (handcrafted ~40-dim + cosine), B (CLAP / OpenL3 / MERT 512-768-d + FAISS), C (LLM-in-loop, no audio), D (hybrid). Recommendation deliberately non-committal: *"Land Teil 1 first; prototype A; benchmark B; defer LLM."* The A-vs-B decision is **blocked on benchmark data** that doesn't exist yet (extraction time, PyTorch bundling cost).

**Implication.** If similar-tracks ships before Teil-2 decides, it must commit to one vector shape. Two paths: (1) ship A now, let Teil-2 inherit same storage; (2) block on Teil-2's benchmark. Path 2 is open-ended — Teil-2 is itself gated on Teil-1 shipping the `plays` table.

**Option-A handcrafted vector — concrete spec for sharing.** Source signals already produced by `app/analysis_engine.py`: BPM (1), key in Krumhansl / Camelot-numeric (1-2), LUFS (1), spectral centroid + rolloff + flatness + bandwidth (4), MFCC mean+std for 13 coefficients (26), chroma mean over 12 pitch classes (12). Total ~46 dims. Storage: sidecar SQLite `app_data/track_vectors.db` (deliberately **not** `master.db` — schema-frozen). Row shape: `(track_id, vector_blob, fps_id, computed_at)`. `fps_id` = analysis-engine version-fingerprint for cache-invalidation when the extractor changes. Metric: cosine on the full vector vs. per-feature weighted sum — cosine simpler; weighted exposes a "vibe vs. harmonic" tuning slider. Default weights: MFCC 0.35, chroma 0.25, BPM-Gaussian 0.15, key-Camelot 0.10, LUFS + spectral 0.15.

**Option-B shareability.** CLAP / MERT: 512-768-d ~4 KB/track vs. A's ~180 bytes → 50k library = 200 MB vs. 9 MB. Extraction: 1-2 s/track CPU, 0.1-0.3 s GPU (cold-scan 50k = ~14 h CPU, ~1.5 h GPU). PyTorch dep: ~200 MB installer bloat + PyInstaller / Tauri sidecar cold-start issues (Teil-2 flags, lines 81-82). Shareability trivial: same table, only `vector_blob` shape differs.

**Decision matrix:**

| Path | Pro | Con | Recommend |
|------|-----|-----|-----------|
| Wait for Teil-2 | Single pipeline ever | Blocked (Teil-2 gated on Teil-1) | NO |
| Ship A now, swap to B later | Unblocks now; shared schema → swap-in compatible | Two extractor impls briefly | **YES, M1** |
| Ship A; never adopt B | Lightest infra | Misses Spotify-class quality | M1 default; revisit at Teil-2 benchmark |

**UX entry-points concrete** (refining Goals): context-menu "Find similar in library" → side-panel with top-20 + reasons; optional always-on Ranking-view sidebar that updates on selection (costs state-management); API entry `GET /api/similar/local?seed_track_id=...&limit=20&exclude_same_artist=true&bpm_window=0.06`. All gated by Phase-1 auth per the auth-hardening draftplan.

**Filters concrete:** same-artist exclude (default ON), same-playlist exclude (default ON), BPM tolerance ±6% (Pioneer CDJ pitch range), key strict/relaxed Camelot toggle, duration window (avoid 0:45 ambient matching 6:00 techno), exclude-recently-played (activates once Teil-1 ships `plays`).

**Open Questions newly answerable:**
- **OQ1** — answered: ship Option A standalone; share storage shape with whatever Teil-2 picks later. Swap is a `compute_track_vector()` impl change; no schema migration, no UX change.
- **OQ10** — answered: sidecar SQLite `app_data/track_vectors.db`, single table keyed by track_id with `fps_id` for cache-invalidation. Beats flat `.npy` (no atomicity) and in-memory-only (cold-start latency on 50k tracks).
- **OQ2** + **OQ6** remain open but reframed as UX choices, not architecture — weighted-sum + filter-first is the M1 default.

### 2026-05-15 — signal-source re-verification + vector-spec correction

**Finding #2's ~46-dim vector spec was wrong** about which features are persisted today. Re-verified `app/analysis_engine.py` end-to-end (return dict at lines 2160-2200, fallback at 2219-2239):

**Actually persisted at track level:** `bpm` (1), `bpm_raw` (1), `key`/`camelot`/`openkey`/`key_id` (1 useful — Camelot-numeric), `key_confidence` (1), `lufs` (1), `replay_gain` (1), `peak` (1), `mood.brightness` (1), `mood.warmth` (1), `mood.texture` (ZCR, 1), `mood.spectral_centroid` (1), `mood.spectral_rolloff` (1), `genre_hint` (categorical), `grid_confidence` (1). **Stereo** is a sub-dict (mid/side energy, correlation) if present — adds ~3 scalars.

Total **scalar dims persisted today: ~12-15**, not 46.

**NOT persisted today** (Finding #2 wrongly claimed they were):
- `spectral_bandwidth`, `spectral_flatness` — librosa functions exist, but `analysis_engine.py` never calls them.
- **MFCC mean+std (13 coeff, 26 dims)** — `librosa.feature.mfcc` IS called at `analysis_engine.py:1132` but ONLY per-phrase inside `detect_phrases`, used for phrase-boundary detection (`mfcc_distances` line 1155-1159). Per-phrase MFCCs are not aggregated to track level and not in the return dict.
- **Chroma mean (12 bins)** — `librosa.feature.chroma_{cqt,cens,stft}` called at lines 357-373 ONLY inside `_detect_key`, averaged into `master_chroma` (line 384) and consumed by Krumhansl correlation. Not returned to caller.
- **`tempo_variability`** — no such field. `bpm_raw` is the closest (single scalar, raw vs. snapped).

**Source of error:** Teil-2 doc lines 114-118 (and 71) overstated by listing librosa-available features as if they were already-extracted features. Cross-doc audit lesson: "could be extracted" ≠ "is extracted".

**Impact on Option C / M1:** the ~46-dim vector requires NEW extraction code adding `spectral_bandwidth`, `spectral_flatness`, MFCC mean+std (26 dims), chroma mean (12 dims), and a `tempo_variability` (= `np.std(beat_intervals)` from existing `beat_result["beats"]`) field. Code goes into `_detect_mood` extension OR a new `_extract_similarity_features` function in `analysis_engine.py`, called once in the main `analyze_audio_full` pass. Cost: ~0.3-0.5 s extra per track at analysis time (librosa MFCC + chroma over full track on already-decoded `y`).

**Backfill required:** every already-analysed track in the user's library needs a re-pass for the new fields. Two strategies:
- (a) lazy backfill on first similarity query (per-track on-demand, expensive — breaks 100 ms budget).
- (b) one-shot batch backfill job exposed via `POST /api/track-vectors/backfill` (recommended for M1) — runs `audio_analyzer.ProcessPoolExecutor` over all tracks missing a vector, ~1-2 hours for 50k tracks on a dev laptop.

**Cross-doc fuzzy-match coordination** — this doc previously had no fuzzy-match surface, but a name-conflict deduper could become useful at M3 (e.g. "this seed has 3 duplicates in library, treat as one"). When that lands, it consumes `app/external_track_match.py` from `idea_external-track-match-unified-module.md` (greenfield module, in flight); does NOT extract a private fork. M1 sidesteps fuzzy match entirely — vector store is keyed by exact Rekordbox `track_id`, no title resolution needed.

**Teil-1 coordination check** — `exploring_recommender-rules-baseline.md` (Teil-1) is in `exploring_` state, all 10 OQs still open (Findings line 167-178). Teil-1's blocker is product/UX (weights, BPM tolerance, key model). It is NOT blocking this doc — local-mode vector ranking is orthogonal to Teil-1's harmonic-mixing surface. Teil-2 (`exploring_recommender-taste-llm-audio.md`) A-vs-B benchmark blocker also does NOT block this doc (Finding #2 path 2 confirmed).

**Open Questions newly answerable:**
- **OQ11 (new)** — partially resolved: extraction-cost path-(a) preferred, gate on user sign-off.
- **OQ9** (cold-start) — now strongly justified: backfill job is the answer, not query-path on-demand analysis.

### 2026-05-17 — deeper exploration: dep / auth / cross-doc re-audit

**Dep claim correction (sklearn).** Constraints originally claimed "numpy + sklearn (already in `requirements.txt`) only". Re-verified `requirements.txt` (47 lines): numeric stack is `numpy==1.26.4`, `scipy==1.11.4`, `numba==0.58.1`, `librosa==0.10.1`. **No sklearn pin.** M1 budget shrinks accordingly: brute numpy cosine + `np.argpartition` for top-N. sklearn `NearestNeighbors` would be ~30 MB transitive (joblib added too) — Schicht-A pinning + CVE-check decision deferred to M2 trigger (see new OQ12). Updated Constraints + Recommendation exit-criteria accordingly.

**Auth wiring concrete (was vague).** Recommendation line 215 previously said "Auth-gated per the auth-hardening draftplan" without specifying mechanism. Re-read `docs/research/implement/draftplan_security-api-auth-hardening.md` (Lifecycle line 25): user signed off on **Bearer-only**, no env-var, no X-Session-Token compat, init-token endpoint deleted. Findings line 82 also reconfirms zero `X-Session-Token` validator in `app/` today (frontend ships header, backend ignores). Concrete API surface for this doc's two routes:
- `GET /api/similar/local?seed_track_id=...` → read-gate via `Depends(require_session)` (Bearer; function name will be set by draftplan implementation — track here as `require_session`).
- `POST /api/track-vectors/backfill` → same gate; can grow to a stricter write-gate if draftplan splits read/write.

Token handoff = Tauri stdout+file (per draftplan Findings/Recommendation pick). M1 plumbing for this doc is a one-line `Depends(...)` per route — no module-level work. Updated Constraints accordingly; removed "X-Session-Token gated" from Recommendation M1.

**Cross-doc external-track-match scope tightened.** Original Finding #3 line 163 mentioned consumption of `external_track_match.py` at M3 for "name-conflict deduper". Re-read `exploring_external-track-match-unified-module.md` Goals (line 32-37): module scope = fuzzy-match + version-tag taxonomy + title-stem extractor + fingerprint API + adapter registry. Returns transient `Candidate` dataclasses; explicitly **no ranking/scoring infrastructure** (Non-goals line 41: "Per-feature UX / data-model / persistence (sister-doc concerns)" + line 45: "Owning candidate-storage sidecar SQLite schema (each sister-doc owns its own table; this module returns transient `Candidate` objects only)"). M3 consumption = read-only stem-extractor + fuzzy-match utility for in-library dedup ONLY; no ranking-stack overlap. Constraint clarified in updated Constraints block.

**Path-(a)/(b) duality reconciliation.** Finding #3 line 161 called path-(b) "recommended for M1"; Recommendation M1 line 213 hooks into `analyze_audio_full` (= path-(a)) + adds backfill route (= path-(b)). Both, simultaneously. Path-(a) handles future imports automatically; path-(b) handles the existing library. No either/or. OQ11 updated to reflect both-needed; user sign-off scope narrowed to path-(a) only (hot-pipeline touch); path-(b) is purely additive (new sidecar SQLite + new route).

**`analysis_cache.py` integration check.** `app/analysis_cache.py` already provides fps_id-based cache invalidation pattern (Constraints already cite). For backfill: re-running `audio_analyzer.analyze_async` over a cached track returns the cached `result` dict in milliseconds (decoded `y` not re-fetched). So the backfill job's per-track cost is dominated by the new `_extract_similarity_features` librosa pass on cached `y`, not by file I/O / decode. 50k library backfill at ~0.4 s/track ≈ 5-6 h single-process; `audio_analyzer.ProcessPoolExecutor` (configurable workers) brings to ~1-2 h on 4-core dev laptop. Updated Recommendation exit-criteria accordingly (5k tracks < 15 min target stays achievable; 50k full backfill < 2 h with 4 workers).

**Teil-1 / Teil-2 re-check.** Re-read `exploring_recommender-taste-llm-audio.md` (Teil-2) Findings + OQ status: still 8/8 OQs open, no embedding decision. Sibling doc landed correctly notes this doc can ship Option-A independently (line 118). No change in upstream status — this doc's independence claim stands.

**Open Questions newly answerable:**
- **OQ11** — bumped from "path-(a) preferred" to "both-paths required; user sign-off only on path-(a)" — narrower gate.
- **OQ12 (new)** — sklearn-as-new-dep status documented; default = stay numpy-only.

## Options Considered

> Required by `evaluated_`. For each viable approach: sketch (2-4 lines), pros, cons, effort (S/M/L/XL), risk.

### Option A — Handcrafted vector + brute cosine over local library
- **Sketch**: Build a ~46-dim vector per track. Reuses existing librosa calls already in `analysis_engine.py` (chroma at lines 357-373, MFCC at line 1132) but lifts them to track-level aggregation + adds `spectral_bandwidth`/`spectral_flatness`/`tempo_variability`. Precompute on import + backfill route, store in sidecar SQLite. Query = cosine vs. all rows, top-N. Reasons = per-feature subscore breakdown.
- **Pros**: Zero new deps. Ships independent of Teil 2. Fast (<50 ms over 50k). Explainable. Existing analysis cache (`app/analysis_cache.py`) handles fps_id invalidation pattern already.
- **Cons**: MFCCs miss high-level semantics (vocal vs. instrumental). Quality ceiling lower than learned embeddings. Requires new extraction code (~46-dim ≠ already-persisted ~12-15 dim, see Finding #3) + 1-2 h backfill for 50k library.
- **Effort**: M
- **Risk**: Low — worst case "feature-space close but vibe-wrong"; still useful. Backfill time is the operational risk.

### Option B — Consume Teil-2's learned embedding (CLAP / MERT) once it lands
- **Sketch**: Wait for Teil-2 to pick an embedding model, reuse its per-track vector + storage. This doc adds only the query-side: cosine top-N over local tracks, filters, reasons.
- **Pros**: Best quality similarity. Single shared pipeline. No duplicate infra.
- **Cons**: Hard dependency on Teil-2 decision + installer-size impact. Blocks shipping until Teil 2 chooses.
- **Effort**: S (query side only) but blocked
- **Risk**: Medium — slippage risk inherited from Teil 2.

### Option C — Hybrid: handcrafted + categorical (MyTag / genre / key) weighted score [RECOMMENDED for M1]
- **Sketch**: Option A's cosine on the continuous bloc (~46-dim) plus discrete bonuses for shared MyTag (overlap / max-set-size), same Genre (1.0/0.0), Camelot distance table (same=1.0, ±1/rel=0.7, ±2=0.3, else=0 — reuses Teil-1 table), Gaussian on |Δ BPM|, |Δ LUFS| linear decay. Weights configurable per query. Defaults: cosine 0.45, MFCC-cosine sub-component 0.20 (extracted from the cosine bloc for reasons-list), chroma-cosine 0.10, BPM-Gaussian 0.10, key-Camelot 0.08, MyTag 0.04, genre 0.03.
- **Pros**: Explainable reasons map directly to feature names. Categorical bonuses catch DJ-meaningful overlaps MFCCs alone miss. Still no new deps. Camelot table reused from Teil-1 — single source of truth.
- **Cons**: Weight tuning hell (same OQ as Teil 1; partially mitigated by exposing weights in query string). Two-system feel.
- **Effort**: M
- **Risk**: Low.

### Option D — Approximate-NN index (FAISS / sklearn `NearestNeighbors`) over Option A or B vectors
- **Sketch**: Build a FAISS `IndexFlatIP` or `IVF` (~50-200 MB binary dep) OR sklearn `NearestNeighbors` (BallTree / KDTree; ~30 MB transitive incl. joblib) from precomputed vectors. Persist to disk; reload on sidecar boot.
- **Pros**: Scales beyond ~200k. Sub-ms queries.
- **Cons**: **Both add a new Schicht-A-pinned dep** — neither numpy-only. sklearn not in `requirements.txt` today (re-verified 2026-05-17); FAISS adds installer bloat + PyInstaller/Tauri sidecar bundling pain (per Teil-2 doc lines 81-82). Premature at current library scale — brute cosine + `np.argpartition` is already ≤ 50 ms on 50k.
- **Effort**: L
- **Risk**: Medium — installer + bundling + Schicht-A CVE-pin per dep.

## Recommendation

> Required by `evaluated_`. Which option, what we wait on before committing.

**Commit to Option C** (handcrafted ~46-dim vector + categorical weighting), phased across three milestones. Storage `app_data/track_vectors.db` with `(track_id PK, vector_blob, fps_id, computed_at)` shape — schema chosen Option-B-compatible so a Teil-2 swap to CLAP/MERT later changes only `compute_track_vector()` impl + `vector_blob` size, **not** storage layout, query API, filters, reasons-list shape, or UX.

### M1 — minimum-viable recommender (ships standalone)

**Deliverables:**
- New `app/track_vector_builder.py` — extracts the missing fields (`spectral_bandwidth`, `spectral_flatness`, MFCC mean+std, chroma mean track-aggregated, `tempo_variability`) into a 46-dim float32 numpy array. Single function `compute_track_vector(y, sr, existing_analysis) -> np.ndarray`. Path-(a) **and** path-(b) both consume this — path-(a) calls it inside `analyze_audio_full`, path-(b) calls it inside backfill worker over cached `y`.
- Hook into `app/analysis_engine.py:analyze_audio_full` so every new analysis writes a vector (path-(a), one-line `_extract_similarity_features()` call before return dict at line 2152, vector row written to `track_vectors.db` post-cache write).
- Backfill route `POST /api/track-vectors/backfill` for the existing library — Bearer-gated via `Depends(require_session)` (auth-hardening draftplan; function name TBD). Uses `audio_analyzer.ProcessPoolExecutor` (configurable workers) over `analysis_cache`-hit tracks. 50k tracks ≈ 1-2 h on 4-core dev laptop.
- New `app/recommender_similar.py` — query function `find_similar(seed_id, *, limit, filters, weights) -> list[Result]`. Loads vectors lazy-mmap from `track_vectors.db`. Brute numpy cosine + `np.argpartition` for top-N over filtered candidate set. **No sklearn import** (numpy-only per OQ12 default).
- New routes in `app/main.py`: `GET /api/similar/local?seed_track_id=...&limit=20&exclude_same_artist=true&bpm_window=0.06&key_strict=false`. Bearer-gated via `Depends(require_session)` (read-gate; same shape as backfill or stricter per draftplan split).
- Frontend: context-menu entry "Find similar in library" on track row → side-panel results with reasons chips. State-mgmt scoped to the panel (no app-wide store). Bearer header attached by `frontend/src/api/api.js` (post-auth-hardening — replaces current `X-Session-Token` plumbing).
- Test artifacts: `tests/test_recommender_similar.py` (unit + filter coverage), `tests/test_similar_perf.py` (`pytest-benchmark`, P95 ≤ 100 ms over a 50k synthetic fixture), `eval/similar_tracks_2026-05.jsonl` (20-seed eval set, manually scored top-10 for the metric in Goals).

**Gates to enter M1 (= promote `evaluated_` → `accepted_`):**
- User signs off on extraction path-(a) (hot pipeline change) — OQ11.
- User picks UX surface: context-menu only vs. context-menu + sidebar — OQ4. Default proposal context-menu only.
- User picks separation vs. combined mode with Teil-1 — OQ3. Default proposal strict separation.
- User confirms default filter posture — OQ5. Default proposal: same-artist/same-playlist excluded by default.

**Exit criteria for M1 (= promote `inprogress_` → `implemented_`):**
- Goal metric hit: ≥ 3/10 musically-similar on the eval set.
- P95 ≤ 100 ms over 50k synthetic (numpy brute cosine + `np.argpartition`).
- Backfill job completes for a 5k-track real library in < 15 min (path-(b), `ProcessPoolExecutor` over `analysis_cache`-hit tracks).
- **Zero new Python deps in M1** — numpy already pinned (`requirements.txt:32`); no sklearn, no FAISS, no PyTorch. Auth-gating reuses `Depends(require_session)` from auth-hardening draftplan implementation (no new module here).
- Auth-hardening draftplan landed first OR concurrent — `require_session` dependency must exist before this doc's routes ship.

### M2 — quality + UX refinement (only if M1 lands cleanly)

**Triggers** (any one fires M2 scoping):
- Eval-set scoring shows ≥ 3 duplicate-album results in top-20 cluster → MMR diversity rerank (OQ7).
- User feedback "I want this updating live as I select tracks" → persistent sidebar panel (OQ4 escalation).
- Pure-cosine vs. weighted-sum benchmark on real-data eval set (OQ2 second pass).

**Deliverables (conditional):** MMR diversity rerank, sidebar panel with selection-listener, weighted-sum vs. cosine A/B in eval harness, tunable per-feature weight UI.

### M3 — scale + Teil-2 convergence (parked unless triggered)

**Triggers** (any one fires M3 scoping):
- Library scale > 200k tracks → FAISS / sklearn `NearestNeighbors` index (OQ8 caching also reconsidered here).
- Teil-2 lands CLAP / MERT → swap `compute_track_vector()` implementation, keep schema, expose `embedding_kind` column for migration.
- Cross-doc fuzzy match needed (e.g. dedup near-identical seeds in candidate pool) → import `extract_title_stem()` + `fuzzy_match()` pure functions from `app/external_track_match.py` (`exploring_external-track-match-unified-module.md`); that module ships **no ranking infrastructure** — this doc keeps full ownership of similarity scoring + reasons-list.
- Teil-1 `plays` table lands → wire `exclude_recently_played` filter.

**Deliverables (conditional):** ANN index, embedding swap, fuzzy-dedup integration, recently-played filter.

### Cross-cutting concern (explicit)

If `recommender-taste-llm-audio` later picks Option B, this doc upgrades by changing `compute_track_vector()` impl; storage table, query API, filters, and UX stay stable. No coordinated re-deploy.

### Open Question status summary (for `exploring_` → `evaluated_` gate)

- **Resolved**: OQ1, OQ6, OQ9, OQ10.
- **Partially resolved (defaults proposed)**: OQ2, OQ5, OQ11, OQ12.
- **Gates before `evaluated_`** (need user sign-off, listed once each):
  - **OQ3** — strict separation (default) vs combined mode with Teil-1.
  - **OQ4** — context-menu only (default) vs context-menu + sidebar.
  - **OQ5** — confirm default filter posture (same-artist + same-playlist excluded; BPM ±6%; duration ±50%).
  - **OQ11** — confirm path-(a) hot-pipeline change (path-(b) backfill is purely additive, no gate).
- **Parked to M2**: OQ7 diversity-rerank (with re-eval trigger), OQ2 cosine-vs-weighted-sum second-pass, OQ12 sklearn-as-new-dep (numpy-only stays default).
- **Parked to M3**: OQ8 (caching), Teil-1 `plays` table wiring, fuzzy-dedup via `external_track_match.py` (stem-extractor + fuzzy-match utility consumption only — that module owns no ranking).

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

- Code: <file:line or PR>
- External docs: <url>
- Related research: <slugs>
