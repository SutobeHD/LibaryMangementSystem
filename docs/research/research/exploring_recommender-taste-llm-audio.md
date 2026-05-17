---
slug: recommender-taste-llm-audio
title: Taste-aware audio recommender (Teil 2 of the recommender split)
owner: unassigned
created: 2026-05-11
last_updated: 2026-05-17
tags: [recommender, ml, audio-analysis, soundcloud, embeddings, llm]
related: [recommender-rules-baseline, recommender-similar-tracks]
ai_tasks: false
---

# Taste-aware audio recommender (Teil 2)

> **State**: derived from filename + folder. See `## Lifecycle` for transition history.

## Lifecycle

- 2026-05-11 — `research/idea_` — created as Teil 2 of recommender split (gated by Teil 1 landing first)
- 2026-05-11 — `research/exploring_` — options A-D outlined; pending audio-embedding benchmark and Teil 1 baseline
- 2026-05-15 — research/exploring_ — scope clarification re: new local-only sibling doc
- 2026-05-15 — research/exploring_ — deeper exploration pass (toward evaluated_ readiness)
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

## Problem

The "Teil 1" recommender (see [recommender-rules-baseline.md](exploring_recommender-rules-baseline.md)) is a deterministic rule engine: given a seed track, return tracks whose **measurable features** (BPM, key, energy, genre tag, duration) are compatible. It works without any user history and answers the question *"what mixes well with this?"*.

It does **not** answer *"what does this user actually want next?"*. A taste-aware recommender needs to:

1. Build a representation of the user's preferences from observed behaviour (plays, skips, likes, tag patterns, dwell time, manual playlist groupings).
2. Compare candidate tracks to that representation using something richer than 5 hand-crafted features — ideally a learned audio embedding that captures timbre, production style, instrumentation, mood beyond what BPM/Key/Energy can express.
3. Combine 1 + 2 to rank candidates from the local library and/or SoundCloud.

This doc collects options and constraints so we can pick a direction later, possibly with a different model (Claude 5.x, GPT-5, local Llama, etc.) and re-evaluated audio embedding tools.

## Goals / Non-goals

**Goals** (each with measurable acceptance metric)
- Personalised "play next" / "build a set" ranking. **Metric**: NDCG@10 on a 30-seed eval set (manually scored 0/1/2 per candidate) beats Teil-1 baseline by ≥ 0.15 absolute. Eval set captured as `eval/taste_recommender_2026-05.jsonl`.
- Audio-level similarity beyond metadata. **Metric**: ≥ 4/10 audio-judged-similar in top-10 for 10 seeds where Teil-1 returns ≤ 2/10 (genre-tag-mismatched but musically aligned cases).
- Dual-mode candidate ranking. **Metric**: same `find_taste_ranked(seed_or_context, source) -> list[Result]` signature for both `source="local"` (filter from `track_vectors.db`) and `source="soundcloud"` (consume Teil-1 candidate stream + same vector store). One code path, two candidate generators.
- Local-first. **Metric**: P95 ranking latency ≤ 200 ms for top-50 over 50k local vectors on dev laptop (i7-12700H, 32 GB) — Option-A handcrafted vector. Option-B target ≤ 400 ms (extra dim cost).
- Explainable. **Metric**: every result row carries `reasons: list[str]` ≥ 2 entries — at minimum one feature-level ("BPM 122 ±2 your cluster mean") + one history-level ("matches 5 of last 20 played-to-end tracks").
- Reuse shared infra. **Metric**: zero new vector-storage tables; consumes `app_data/track_vectors.db` from sister doc (sibling owns the schema).

**Non-goals**
- Beating Spotify cold-start. Single-user library-bounded.
- Real-time training. Nightly / on-demand batch updates fine.
- Generating audio. Rank existing only.
- Replacing Teil 1. Teil 1 stays — fallback when taste data thin; canonical harmonic-mixing answer.
- Owning the vector storage schema. Sibling [recommender-similar-tracks](exploring_recommender-similar-tracks.md) owns `app_data/track_vectors.db` shape; this doc adds a `user_taste_vectors` table only.
- Multi-user / collaborative filtering. App has no shared backend; deferred to a separate opt-in cloud doc.

## Constraints

- **Local-first hard rule** (`README.md`): cloud inference opt-in per-call only. Inference on typical DJ laptop (RAM ≤ 32 GB, GPU optional). Excludes Option C (LLM-in-loop) as default path.
- **Stack**: Python 3.10+ FastAPI, Rust CPAL/Symphonia. PyTorch dep inflates installer (~200 MB wheels) + PyInstaller `backend.spec` `collect_all` cost — `backend.spec:21` currently bundles only `librosa, numba, scipy, sklearn, soundfile, audioread, lazy_loader`. Adding `torch` requires explicit cost/benefit (M2 benchmark gate, see Recommendation).
- **Persisted track-level scalars TODAY** (re-verified 2026-05-17 — `app/analysis_engine.py` 2239 LOC; return dict at 2152-2201, fallback at 2219-2240). Useful for similarity: `bpm`, `bpm_raw`, `key_id` (Camelot-numeric), `key_confidence`, `lufs`, `replay_gain`, `peak`, `grid_confidence`, `mood.{brightness,warmth,texture,spectral_centroid,spectral_rolloff}`, `genre_hint` (categorical), `stereo` sub-dict (~3 if present). Total **~12-15 dense + 1 categorical**. **NOT persisted today** (`Grep spectral_bandwidth|spectral_flatness|tempo_variability` in `app/analysis_engine.py` = 0 hits, re-verified 2026-05-17): MFCC track-aggregated (only per-phrase at line 1132, n_mfcc=13, consumed at 1155-1159 for phrase distance, never returned); chroma track-aggregated (only inside `_detect_key` 357-373, averaged at 384, consumed by Krumhansl correlation, never returned); no `tempo_variability` field anywhere. `detect_mood` lives at line **1656** (sister doc previously said 1666 — off by 10; corrected here).
- **Vector-extraction code is sister-doc-owned**. Sister doc M1 owns `app/track_vector_builder.py` (Option A ~46-dim with new extraction); this doc consumes the same `app_data/track_vectors.db` table. Schema (re-decided 2026-05-17, corrects 2 prior cites): `(track_id INTEGER PK, vector_blob BLOB, vector_version INTEGER, computed_at TIMESTAMP)`. Column name `vector_version` (NOT `fps_id` — `Grep fps_id` in `app/` = 0 hits, term was carried over from earlier draft); mirrors `analysis_cache.ANALYSIS_VERSION = 3` integer-bump pattern (`app/analysis_cache.py:30, 63`). No duplicate extractor.
- **Audio re-decode avoided**: `app/analysis_cache.py` validates by `(file mtime, size, ANALYSIS_VERSION)` (lines 63-74), gzipped per-file result. Backfill path-(b) re-runs `audio_analyzer.analyze_async` over cached tracks → returns cached `result` in ms; new per-track librosa pass for vector extraction over decoded `y` is the only cost.
- **No play-history table yet** (`Grep plays|play_history|play_count app/live_database.py` = 1 hit, line 197, a `PlayCount` read of `dj_play_count`; **no writeable `plays` table**, re-verified 2026-05-17). Teil-1 owns landing the writable `plays` table. Without it: taste signal = Rekordbox `Rating`, `PlayCount` (static at import), `Color`, MyTag membership, playlist co-occurrence, file `mtime` proxy.
- **SoundCloud rate limits**: `app/soundcloud_api.py` exponential backoff + 0.3 s polite spacing. Fetch budget per recommendation ≤ 1 call (`/tracks/{id}/related` or `/stations/track:{id}` → ~20-50 candidates), then rank locally. Multi-call per-recommendation is non-starter.
- **Library scale**: target ~1k–50k local tracks. Brute cosine over 50k × 46-dim ≤ 50 ms (sister Finding); 50k × 512-d ≤ 200 ms uncached. FAISS / sklearn ANN only attractive at ≥ 200k.
- **Rekordbox `master.db` schema-frozen**: never add taste columns there. New `user_taste_vectors` table lives in sidecar SQLite (likely same `app_data/track_vectors.db` file with separate table, or a new `app_data/taste.db`).
- **`_db_write_lock` not needed** for the new sidecar SQLite. If we ever read `master.db` (e.g., to enumerate track IDs for backfill), that read is lock-free.

## Open Questions

1. **Audio embedding source** — **DECIDED**: M1 = Option A (sister-doc handcrafted 46-dim). M2 benchmark gates Option B swap-in (criteria table in Recommendation).
2. **Taste representation** — **DECIDED**: M1 ships **centroid only** (single recency-weighted vec). KMeans cluster centroids = M2 (k=3, only triggered if eval set shows ≥ 4/30 seeds where single-centroid misses an obvious cluster of taste).
3. **Role of LLM** — **DECIDED**: explanation layer only, never in ranking loop. Cached on `(seed_id, candidate_id, profile_hash)`.
4. **Storage** — **DECIDED** (corrected vs prior cite): sidecar SQLite `app_data/track_vectors.db`. Sister-owned `track_vectors(track_id INTEGER PK, vector_blob BLOB, vector_version INTEGER, computed_at TIMESTAMP)`. This doc's sibling: `user_taste_vectors(profile_id TEXT, kind TEXT, vector_blob BLOB, n_source_tracks INTEGER, computed_at TIMESTAMP, PRIMARY KEY(profile_id, kind))`. `kind ∈ {"centroid", "cluster_0", "cluster_1", "cluster_2"}`. Column **`vector_version`** (NOT `fps_id` — that was a misnomer; `Grep fps_id` in `app/` = 0 hits).
5. **Cold-start** — **DECIDED**: weighted Rekordbox seed — `Rating==5` weight 1.5, `Rating==4` weight 1.0, MyTag-overlap-with-recent-listens weight 0.5, others 0. Min 8 source tracks else emit `taste_cold=true` hint + fall back to Teil-1.
6. **Negative signals** — **TRIGGER-PARKED to post-Teil-1 `plays` table**: default thresholds skip<15s = −1.0, 15-60s = −0.3, >60s = 0.0; revise if eval-set false-positive > 20%. M3 owns per-context (mid-set vs solo).
7. **SoundCloud candidate set** — **DECIDED**: M1 = `GET /tracks/{id}/related` only, single call per request. `/stations/track:{id}` fallback in M2 when `/related` returns < 10. Stream feed + followed-user uploads = M3.
8. **Privacy / collaborative filtering** — **DECIDED**: out, single-user. Non-goal.
9. **Embedding extraction blocker** — **DECIDED**: M1 = no extraction here (sister owns vector). M2 = swap if 6-criterion benchmark passes.
10. **Taste-profile recompute cadence** — **DECIDED**: nightly batch (cron-equivalent triggered by sidecar `tasks.py` if present, else `scripts/cron_taste_refresh.py`) + on-demand `POST /api/taste/refresh`. Event-driven incremental = M3.
11. **Per-feature weight tuning** — **TRIGGER-PARKED to M2**: defaults `cosine=0.55, bpm=0.15, key=0.10, lufs=0.10, mytag=0.05, recency=0.05`. Re-tune only if eval NDCG@10 < baseline+0.15.
12. **NEW — Storage column name** — **DECIDED**: `vector_version INTEGER` (integer bump mirroring `ANALYSIS_VERSION = 3` at `app/analysis_cache.py:30`; `vector_version=1` for M1).
13. **NEW — LLM API key UX surface** — **TRIGGER-PARKED to M3**: separate doc when "explain" feature ships. Not in M1 scope.

## Options Considered

> Each option scored on six axes: **Impl cost** (S/M/L/XL), **Vector dim**, **Installer delta** (MB added vs. current `backend.spec` payload), **Per-track extraction latency** (CPU, no GPU assumed), **Query latency P95** (top-50 over 50k vectors), **Quality ceiling** (subjective).

**Scannable summary:**

| Option | Impl | Dim | Inst Δ | Extract/track | Query P95 | Quality | M1 verdict |
|---|---|---|---|---|---|---|---|
| **A** Handcrafted + cosine + centroid | M | 46 | 0 MB | 0.3-0.5 s (sister) | ≤ 100 ms | medium | **YES** |
| **B** Pre-trained embedding | L | 200-768 | +200-500 MB | 1-4 s CPU | ≤ 300 ms | high | M2-gated |
| **C** LLM-in-ranking-loop | S | n/a | 0 (HTTP) | n/a | 1.5-3 s | medium | REJECTED |
| **D** A/B + LLM explain-only | S over A/B | inherits | inherits | inherits + 1-3 s for explain | inherits | best UX | **YES** (long-term) |


### Option A — Handcrafted vector + cosine + recency-weighted user centroid [RECOMMENDED for M1]
- **Sketch**: Consume sister doc's `~46-dim` vector from `app_data/track_vectors.db` (no duplicate extraction code). User taste vector = time-decayed centroid of {liked, rated≥4, played-to-end}. Rank candidates by cosine to user vector; add per-feature bonuses (BPM Gaussian, Camelot distance, MyTag overlap). Re-compute taste vector nightly + on-demand.
- **Impl cost**: M | **Dim**: 46 (float32 = 184 B/track) | **Installer delta**: 0 MB (reuses sister) | **Extraction**: 0.3-0.5 s/track (sister-owned; runs once at analysis) | **Query P95**: ≤ 100 ms uncached | **Quality**: medium (MFCCs miss vocal/instrumental distinction)
- **Pros**: Zero new deps. Ships immediately after sister M1 lands. Explainable per-feature reasons. Backfill already addressed by sister.
- **Cons**: Quality ceiling capped at handcrafted-feature expressiveness; no semantic "deep dubby" vs "deep ambient" distinction unless MFCC happens to separate them.
- **Risk**: Low.

### Option B — Pre-trained learned audio embedding (CLAP / MERT / OpenL3) [M2 GATED ON BENCHMARK]
- **Sketch**: Run each track once through a pre-trained model, store embedding in same `track_vectors.db` schema (`vector_blob` just bigger; `vector_version` bumped to 2/3/4 per embedding kind: 2=CLAP, 3=MERT, 4=OpenL3). User taste = same centroid approach over the learned-embedding space. CLAP variant unlocks text queries.

Sub-variants (concrete `2026-05-17` PyPI status, re-verified `pip index versions`):

| Model | Repo / pkg | Latest pkg ver | Dim | Approx weights | CPU extract/track | GPU extract | torch req | Notes |
|---|---|---|---|---|---|---|---|---|
| **CLAP (LAION)** | `laion-clap` | **1.1.7** (2024-Q4) | 512 | ~600 MB | 1-3 s | 0.1-0.3 s | yes | Text-query support. Audio + text dual encoder. Active maintenance. |
| **MERT-v1-95M** | `transformers` (latest **5.8.1** 2026) + HF `m-a-p/MERT-v1-95M` | model weights stable | 768/layer × 13 layers (mean last 4 ≈ 768) | ~380 MB | 2-4 s | 0.2-0.5 s | yes | Music-specific (95M params). Higher quality on genre per [MARBLE benchmark](https://marble-bm.shef.ac.uk/). |
| **OpenL3** | `openl3` | **0.4.2** (latest, 2024 — NOT 0.5.0 as prior draft claimed) | 512 (music subset) | ~120 MB | 1-2 s | 0.2-0.4 s | NO (TF/Keras) | Lighter weights but pulls TensorFlow — different bundling headache than torch. |
| **MusicNN** | `musicnn` | **0.1.0** (last release 2020) | 200 (taggram) | ~30 MB | 0.5-1 s | n/a (TF1) | NO (TF1 legacy) | TF1-era, unmaintained. **Skip** unconditionally. |

- **Impl cost**: L | **Dim**: 200-768 (vector_blob 0.8-3 KB/track; 50k library = 40-150 MB) | **Installer delta**: +200-500 MB (torch wheel ~180 MB + model ~120-600 MB; `backend.spec:21` needs `torch` + model dir bundling — verified torch + transformers NOT in current `requirements.txt`) | **Extraction**: 1-4 s/track CPU = 14-55 h for 50k library cold scan, or 1.4-7 h on GPU | **Query P95**: ≤ 300 ms uncached (50k × 512-d brute cosine) | **Quality**: high (CLAP+text-query is a category-creating UX).
- **Pros**: Best similarity quality. CLAP text-query ("find tracks like 'late-night driving techno with strings'") is a flagship UX feature no competitor (Rekordbox/Serato/Engine) has.
- **Cons**: Installer ≥ 2× current size. PyInstaller `backend.spec` + Tauri sidecar bundling pain with torch — `collect_all('torch')` known to balloon, slow cold-start, and platform-specific wheel mismatches (Windows ARM, macOS Apple Silicon vs. x86_64). 50k library cold-scan = overnight job.
- **Risk**: Medium-High — installer-size acceptance, sidecar boot time, model licensing (LAION-CLAP CC0 OK; MERT Apache-2.0 OK; both fine commercially).

### Option C — LLM in ranking loop [REJECTED]
- **Sketch**: Build text profile per track + user; feed to LLM at every recommendation call.
- **Impl cost**: S | **Dim**: n/a | **Installer delta**: 0 (HTTP only) | **Extraction**: n/a | **Query P95**: 1500-3000 ms (LLM call) | **Quality**: medium (text-only; no audio signal)
- **Pros**: Free reasoning + explanations.
- **Cons**: **Violates local-first** (mandatory paid API call). Latency 10-30× the budget. Doesn't see audio. Per-recommendation cost: $0.002-$0.02 → unsustainable at "ranking sidebar updates on selection".
- **Decision**: Out as default path. Subsumed into Option D as opt-in explanation layer only.

### Option D — Hybrid: Option A (or B if benchmark passes) for ranking + LLM for explanation only [RECOMMENDED LONG-TERM]
- **Sketch**: Embedding-based ranking yields top-50 (Option A in M1, swap to Option B from M2 if benchmark passes). LLM consulted ONLY when user clicks "Why?" on a result — feeds ~5-10 features about seed + candidate + user-profile-summary, returns 1-paragraph explanation. Result cached on `(seed_id, candidate_id, taste_profile_hash)`.
- **Impl cost**: S over chosen A/B base | **Installer delta**: 0 above A/B | **Per-explanation latency**: 1-3 s (acceptable for opt-in UX) | **Cost per explanation**: ~$0.001 (small payload, single Claude/GPT call) | **Quality**: best practical UX
- **Pros**: Best of all worlds. Local-first preserved for the hot ranking path. LLM enrichment opt-in + cached.
- **Cons**: API-key UX (where does user enter it?). Cache invalidation on taste-profile changes (handled by hash in cache key).
- **Risk**: Low — explanation layer is additive; can ship A first, add LLM layer in M2 without touching ranking.

## Recommendation

**Commit to Option D phased over three milestones** with Option A as M1 base and Option B as M2 conditional swap-in. Storage schema inherited from sister doc (no duplicate `track_vectors.db` work).

### M1 — Option A taste-aware ranking (ships after sister M1 + Teil-1 `plays` lands)

**Deliverables (file-by-file expected diff):**

1. **NEW** `app/recommender_taste.py` (~250 LoC est.) — top-level:
   ```python
   # exact public signatures
   def find_taste_ranked(
       seed_or_context: Seed | ContextRequest,
       source: Literal["local", "soundcloud"],
       *,
       limit: int = 20,
       filters: TasteFilters | None = None,
       weights: TasteWeights | None = None,
   ) -> list[Result]: ...

   def _local_candidates(seed: Seed, f: TasteFilters) -> list[int]: ...
       # reads track_vectors.db; applies BPM/duration prefilter; returns track_ids

   def _soundcloud_candidates(seed: Seed) -> list[int]:
       # ONE call to soundcloud_api.get_related(seed.sc_id); returns ~20 ids

   def _score(taste_vec: np.ndarray, cand_vecs: np.ndarray,
              cand_meta: list[CandMeta], weights: TasteWeights) -> np.ndarray:
       # cosine(taste_vec, cand_vecs) * w.cosine + bpm_gauss * w.bpm
       # + camelot_dist_score * w.key + lufs_decay * w.lufs
       # + mytag_jaccard * w.mytag + recency_bias * w.recency
   ```

2. **NEW** `app/taste_profile.py` (~180 LoC est.) — exact sigs:
   ```python
   def build_taste_vector(
       user_id: str = "default",
       *,
       kind: Literal["centroid", "cluster_0", "cluster_1", "cluster_2"] = "centroid",
   ) -> np.ndarray: ...
       # reads plays table (if present) + Rekordbox Rating/MyTag fallback
       # returns 46-dim float32 numpy array

   def refresh_taste_profile(user_id: str = "default") -> dict[str, Any]:
       # invoked from POST /api/taste/refresh
       # writes 'centroid' kind unconditionally; 'cluster_*' only if user enabled
       # returns {"n_source_tracks": int, "computed_at": iso, "kinds_written": [...]}
   ```

3. **NEW** `app/db_taste.py` (~80 LoC est.) — SQLite helper for `user_taste_vectors(profile_id TEXT PK, kind TEXT, vector_blob BLOB, n_source_tracks INTEGER, computed_at TIMESTAMP)` sibling table in `app_data/track_vectors.db`. PK composite `(profile_id, kind)`. Atomic write via `INSERT OR REPLACE`.

4. **EDIT** `app/main.py` (~+15 LoC) — two new routes, both Bearer-gated via `Depends(require_session)` (already imported at line 33, used 87× elsewhere):
   ```python
   @app.get("/api/taste/ranked", dependencies=[Depends(require_session)])
   def taste_ranked(
       seed_track_id: int,
       source: Literal["local", "soundcloud"] = "local",
       limit: int = 20,
   ) -> list[Result]: ...

   @app.post("/api/taste/refresh", dependencies=[Depends(require_session)])
   def taste_refresh() -> dict: ...
   ```

5. **EDIT** `app/soundcloud_api.py` (~+30 LoC) — `get_related(sc_track_id: int) -> list[SCTrack]` wrapping `GET /tracks/{id}/related` (currently 0 hits for `related` in this file, re-verified 2026-05-17). Existing exponential backoff + 0.3 s polite-spacing reused.

6. **NEW** `frontend/src/components/TasteRankedPanel.jsx` (~120 LoC est.) — "Play next" / "Build a set" buttons → side-panel with reasons chips. Calls `GET /api/taste/ranked` via existing `frontend/src/api/api.js` axios instance (Bearer auto-attached by interceptor at line 199, re-verified 2026-05-17).

7. **NEW** `eval/taste_recommender_2026-05.jsonl` — 30 seeds × top-10 schema `{seed_id, candidate_id, rank, score: 0|1|2, ts}`.

**M1 pseudocode for `find_taste_ranked` first ~30 LoC:**
```python
def find_taste_ranked(seed_or_context, source, *, limit=20, filters=None, weights=None):
    f = filters or TasteFilters.default()
    w = weights or TasteWeights.default()  # cosine=.55 bpm=.15 key=.10 lufs=.10 mytag=.05 recency=.05

    taste_vec = _load_or_rebuild_taste_vector("default", kind="centroid")
    seed = _resolve_seed(seed_or_context)

    if source == "local":
        cand_ids = _local_candidates(seed, f)
    else:  # soundcloud
        cand_ids = _soundcloud_candidates(seed)  # single GET /related call
        if len(cand_ids) == 0:
            return []  # SC empty -> caller falls back to Teil-1 rules

    cand_vecs, cand_meta = _load_vectors_and_meta(cand_ids)  # (N, 46) float32
    if cand_vecs.shape[0] == 0:
        return []

    scores = _score(taste_vec, cand_vecs, cand_meta, w)
    top_idx = np.argpartition(scores, -limit)[-limit:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
    return [_make_result(cand_ids[i], scores[i], cand_meta[i], w, taste_vec, cand_vecs[i])
            for i in top_idx]
```

**M1 test artifacts — exact pytest signatures:**

```python
# tests/test_recommender_taste.py
def test_find_taste_ranked_local_returns_top_n(tmp_path, fixture_vectors_50): ...
def test_find_taste_ranked_local_respects_limit(): ...
def test_find_taste_ranked_local_filters_same_artist(): ...
def test_find_taste_ranked_local_bpm_window_excludes_outliers(): ...
def test_find_taste_ranked_soundcloud_single_api_call(mocker): ...
def test_find_taste_ranked_cold_start_falls_back_to_rules(monkeypatch): ...
def test_score_weights_sum_normalized(): ...
def test_score_cosine_dominant_at_default_weights(): ...
def test_score_reasons_emits_min_two(): ...

# tests/test_taste_profile.py
def test_build_taste_vector_centroid_shape_46(fixture_vectors_50): ...
def test_build_taste_vector_cluster_kind_returns_kmeans_centroid(): ...
def test_refresh_taste_profile_writes_centroid_row(tmp_path): ...
def test_refresh_taste_profile_cold_start_uses_rating_seed(): ...
def test_refresh_taste_profile_skip_weight_threshold(): ...

# tests/test_taste_perf.py (pytest-benchmark)
def test_taste_ranked_p95_local_50k(benchmark, fixture_vectors_50k): ...
    # asserts benchmark.stats['p95'] <= 0.200 (200 ms)

# tests/test_api_taste.py
def test_get_taste_ranked_requires_bearer(client): ...   # expects 401 without
def test_get_taste_ranked_200_with_bearer(client, valid_token): ...
def test_post_taste_refresh_requires_bearer(client): ... # expects 401 without
```

**Gates to enter M1 (`evaluated_` → `accepted_`):**
- Sister doc `recommender-similar-tracks` has shipped Option A vector to `track_vectors.db` (M1 of sister) — **HARD DEPENDENCY**.
- Teil-1 has shipped `plays` table — **HARD DEPENDENCY** for non-cold-start path.
- User confirms OQ2 default (centroid only in M1, mood-cluster toggle M2).
- User confirms OQ5 cold-start seed weights (`Rating≥4` weight 1.0, MyTag-overlap 0.5).
- User confirms OQ7 (M1 SC = `/related` only).

**Exit criteria (`inprogress_` → `implemented_`):**
- NDCG@10 vs. Teil-1 baseline: ≥ 0.15 absolute improvement on eval set.
- ≥ 4/10 audio-judged-similar in top-10 for 10 mismatch-genre seeds.
- P95 ranking latency ≤ 200 ms (local), ≤ 800 ms (SC, dominated by one `/related` call).
- Zero new heavy deps.

### M2 — Option B benchmark + conditional swap

**Concrete benchmark plan** (run on dev laptop i7-12700H, 32 GB, no discrete GPU; report on identical machine + a low-end reference: 4-core / 16 GB):

1. **Extraction-time spike** — script `scripts/benchmark_embeddings.py`:
   - Pick 100 tracks from real library spanning 2-10 min durations.
   - For each of `{CLAP-LAION, MERT-v1-95M, OpenL3-music}`: install in isolated venv, warm-cache decoded `y`, measure per-track wall-clock for embedding-only (no decode).
   - Report p50, p95, p99. Estimate 50k library cold-scan time.
2. **Installer-impact spike** — build `backend.spec` with `torch` + chosen model bundled (`collect_all('torch')` + datas for HF cache); record `dist/RB_Backend.exe` size delta vs. current build. Note startup cold-time delta (sidecar boot).
3. **Quality spike** — re-score eval set with each Option-B variant; compute NDCG@10 vs. Option-A baseline.

**Gate conditions for Option-B path adoption (path-A→path-B swap):**

| Criterion | Threshold | Source |
|---|---|---|
| Per-track CPU extract time (p95) | ≤ 3.0 s | Spike 1 |
| 50k-library cold-scan time | ≤ 30 h (i.e. overnight + day, batchable) | Derived |
| Installer delta vs. current | ≤ +400 MB | Spike 2 |
| Sidecar cold-boot delta | ≤ +5 s | Spike 2 |
| NDCG@10 improvement over Option A | ≥ +0.10 absolute | Spike 3 |
| PyInstaller bundling success (Win + macOS x86_64 + macOS ARM) | all green | Spike 2 |

**All-of-above pass → swap in M2.5.** **Any-of-above fail → stay on Option A indefinitely, revisit only if user requests text-query feature explicitly.**

Path-B preference order when gate passes: CLAP (text-query bonus) → MERT (music-quality bonus, no text) → OpenL3 (lighter, but TF dependency replaces torch headache with TF headache; only attractive if `Spike 2` shows torch fails). MusicNN deprecated — skip.

### M3 — LLM explanation layer (Option D enrichment)

**Triggers:**
- M1 lands and user reports "I want to know why a track was suggested" qualitatively.
- API-key UX surface lands (separate doc — not in this scope).

**Deliverables:** `POST /api/taste/explain` (opt-in), result cache on `(seed_id, candidate_id, taste_profile_hash)`, frontend "Why?" button on result rows.

### M3+ — parked

- Mood-cluster toggle (OQ2 second-pass)
- Stream feed + followed-user upload SC candidates (OQ7 extension)
- Per-track-context skip detection (OQ6 detail)
- Event-driven taste-vector update (OQ10 incremental path)

### Cross-coordination with sister doc

- **Storage shape**: this doc commits to consuming the sister's `track_vectors.db` schema unmodified. Adds only `user_taste_vectors` table in same file. Sister owns vector extraction; this doc owns taste-vector building + ranking.
- **Vector swap**: if sister later adopts Option B from this doc's benchmark, this doc's `find_taste_ranked()` ranking code is unchanged (cosine works on any dim); only `vector_blob` width changes.
- **No coordinated re-deploy required.**

### Open Question status summary (for `exploring_` → `evaluated_` gate)

- **DECIDED**: OQ1, OQ2, OQ3, OQ4, OQ5, OQ7, OQ8, OQ9, OQ10, OQ12.
- **TRIGGER-PARKED** (default committed, revisit only on named trigger): OQ6 (skip-thresholds; trigger = Teil-1 `plays` lands → 2-week tb logging spike), OQ11 (weight tuning; trigger = eval NDCG@10 < baseline+0.15), OQ13 (LLM API-key UX; trigger = "explain" feature ships in M3).
- **Gates before `evaluated_` → `accepted_`** (named hard-deps, not OQs): sister doc M1 lands vector pipeline; Teil-1 lands writable `plays` table; auth-hardening draftplan land (`require_session` available for `Depends`).

## Decision

_Not yet decided. Status: `exploring`._

## Log

### 2026-05-17 — implementation-ready bar pass

**Empirical re-verification** (cite line numbers + grep counts):
- `app/analysis_engine.py` = 2239 LOC; return dict 2152-2201; fallback 2219-2240. `detect_mood` at **line 1656** (prior cite said 1666 — off by 10; corrected). Mood return = 5 useful scalars + label.
- `Grep spectral_bandwidth|spectral_flatness|tempo_variability app/analysis_engine.py` = **0 hits**. Confirms not persisted; sister doc Finding #3 stands.
- `Grep fps_id app/` = **0 hits**. Column-name `fps_id` was carried-over draft jargon; corrected to `vector_version` (mirrors `analysis_cache.ANALYSIS_VERSION = 3` integer-bump pattern at `app/analysis_cache.py:30`).
- `app/auth.py` = 115 LOC; `require_session` at lines 95-115; Bearer-only via `safe_compare` (line 114). 87 usages in `app/main.py` already. `Grep X-Session-Token app/main.py` = **0 hits** (backend no longer validates this header). Wiring for M1 routes = `dependencies=[Depends(require_session)]` decorator — identical pattern to existing `app/main.py:557`.
- `frontend/src/api/api.js:199` already attaches `Authorization: Bearer ${token}` interceptor. No frontend auth work.
- `requirements.txt` (re-read 2026-05-17, 67 LOC): no torch, no tensorflow, no transformers, no sklearn pin. `numpy==1.26.4` line 31, `scipy==1.11.4` line 32, `librosa==0.10.1` line 34. Local dev installs `numpy=2.3.4 + sklearn=1.8.0` — environment loose, pinning gates production wheel only.
- `backend.spec:21` `collect_all` includes `sklearn` (transitive via librosa). Pin still required for Schicht-A even though sklearn already bundled.
- **PyPI status re-verified 2026-05-17** via `pip index versions`: `laion-clap==1.1.7`, `openl3==0.4.2` (CORRECTS prior "0.5.0 in 2025" claim — 0.4.2 IS the latest, max 0.4.x line), `musicnn==0.1.0` (stuck 2020), `transformers==5.8.1` (5.x line current; prior 4.x cite stale).
- `Grep related|stations/track app/soundcloud_api.py` = **0 hits** — confirms `/related` not yet called, must be added in M1 deliverable 5.

**Rewritten sections**: Constraints (line cites refreshed, `vector_version` correction, `detect_mood` line fixed), Open Questions (all 13 now DECIDED or TRIGGER-PARKED — none "PARTIALLY RESOLVED"), Options sub-variant table (versions corrected), Recommendation M1 (file-by-file diff prose, exact public signatures, 30-LoC pseudocode for `find_taste_ranked`, exact pytest test signatures, route shapes with `Depends(require_session)`), OQ-status summary (collapsed gates → named hard-deps).

**Cross-check sister** `exploring_recommender-similar-tracks.md` (2026-05-17 entry): sister also corrected sklearn-not-in-`requirements.txt`, also pivoted Bearer auth via `Depends(require_session)`, also path-(a)+(b) duality, also added OQ12 sklearn-status. This doc aligned: storage schema column-name change to `vector_version` matches sister's note that the `fps_id` ref was a misnomer (sister Constraints "Cache versioning" block).

**No code, no other docs, no git mv, no commit — exploration only.**

### 2026-05-15 — deeper exploration pass (toward `evaluated_` readiness)

**Reverified `app/analysis_engine.py`** post-hotfixes: file length 2239 LOC. Return dict at lines 2152-2201, fallback at 2219-2240. Earlier line-cites (114-118, 71) in this doc and the 2026-05-11 entry overstated which features are persisted — corrected via sister doc's Finding #3 + new Constraints section (~12-15 useful scalars actually persisted today, not the 26+ MFCC + 12 chroma claimed). MFCC at `analysis_engine.py:1132` is per-phrase only; chroma at lines 357-373 internal to `_detect_key`. `spectral_bandwidth`, `spectral_flatness`, `tempo_variability` not called anywhere (verified `Grep`). The 46-dim Option-A vector REQUIRES new extraction — sister doc M1 owns it.

**Sister doc carve-out absorbed**: [exploring_recommender-similar-tracks](exploring_recommender-similar-tracks.md) promoted to `exploring_` 2026-05-15, recommended Option A handcrafted ~46-dim → `app_data/track_vectors.db` schema `(track_id PK, vector_blob, fps_id, computed_at)`, swap-in compatible with Option B. This doc inherits the schema unmodified and adds a sibling `user_taste_vectors` table. No duplicate extractor.

**Pip + bundling verified**:
- Current `requirements.txt`: `librosa==0.10.1`, `madmom==0.16.1`, `essentia==2.1b6.dev1110`. **NO torch, NO tensorflow, NO transformers** — Option B = greenfield install.
- `backend.spec:21` `collect_all` set: `librosa, numba, scipy, sklearn, soundfile, audioread, lazy_loader`. Option B adds `torch` (~180 MB wheel) + HF cache datas — `collect_all('torch')` known to balloon installer; gate criterion added.
- Embedding-pkg PyPI status (2026-05-15): `laion-clap` active maintenance through 2025; `openl3` published 0.5.0 in 2025 (still TensorFlow-based, dropped TF1); `musicnn` last release 2020 — TF1-era, **skip** for any new work; MERT via `transformers` from `m-a-p/MERT-v1-95M` HF repo, Apache-2.0 license.

**Rewritten sections**: Goals (added metrics), Constraints (verified line-cites + sister inheritance), Open Questions (numbered status + gates + 3 new OQs), Options (added comparison table + concrete sub-variants), Recommendation (M1/M2/M3 phasing + 6-criterion benchmark gate table + sister coordination).

### 2026-05-15 — scope clarification + sibling doc landed

A new sibling doc — [idea_recommender-similar-tracks.md](idea_recommender-similar-tracks.md) — was created as the **local-only** seed-driven similar-tracks counterpart. This Teil 2 doc remains the broader taste-aware / embedding-based recommender covering **both local and SoundCloud modes**, with personalisation from play history. The local-only "find similar in my library" UX surface now lives in the sibling doc; the embedding extraction pipeline (Option A handcrafted, Option B CLAP/MERT) is the shared primitive both docs can consume.

**Open Question re-check:**
1. Embedding source — unresolved; sibling doc can ship on Option A independently.
2. Taste representation — unresolved; not affected by sibling.
3. LLM in loop — unresolved.
4. Storage — unresolved; sibling doc raises same question (its OQ-10), favours shared store.
5. Cold-start — unresolved; sibling sidesteps (seed-driven, not user-driven).
6. Negative signals — unresolved; still blocked on `plays` table.
7. SC candidate set — unresolved; sibling explicitly excludes SC.
8. Privacy — unresolved.

**Recent commits since 2026-05-11** touching `app/live_database.py`: `bd8c0f7` typed `restore_backup` return + structured logging, `cc171ee` dropped legacy session backups + `BACKUP_DIR`, `13b2197` type-hinted `LiveRekordboxDB` public API, `fbb4aad` typed bare excepts. No changes to `analysis_engine.py` or `audio_analyzer.py`. None alter signal sources for this doc.

### 2026-05-11 — initial drafting
- Created during the recommender-split discussion. Teil 1 (dumb, rules-based) is being implemented now.
- Captured what we know about the existing audio analysis pipeline from the codebase audit:
  - `app/analysis_engine.py` (~2200 LOC) already produces BPM, key, LUFS, spectral features, MFCCs, chroma, MFCC mean/std — enough for Option A out of the box.
  - `app/audio_analyzer.py` is the async wrapper running in `ProcessPoolExecutor`.
  - Rekordbox `master.db` is read-only with respect to schema additions — any new per-track data (embedding, taste vector) needs a sidecar store, not a Rekordbox column.
- No user-play history table exists yet. Rekordbox's `PlayCount` is static and import-time only. Need Teil 1 to introduce a local `plays` table before Teil 2 has signal to work with.
- SoundCloud `/tracks/{id}/related` and `/stations/track:{id}` are mentioned but not currently called by `app/soundcloud_api.py` — they would be the entry point for SC-mode candidates.
- Open question 4 (storage) is the most architecturally consequential — picking sqlite-vss vs. FAISS vs. flat .npy locks in update / portability characteristics. Worth a small spike before committing.

## Links

- Code (existing analysis pipeline):
  - [app/analysis_engine.py](../../app/analysis_engine.py)
  - [app/audio_analyzer.py](../../app/audio_analyzer.py)
  - [app/soundcloud_api.py](../../app/soundcloud_api.py)
- Audio embedding candidates (external):
  - CLAP — https://github.com/LAION-AI/CLAP
  - MERT — https://huggingface.co/m-a-p/MERT-v1-95M
  - OpenL3 — https://github.com/marl/openl3
  - MusicNN — https://github.com/jordipons/musicnn
- Vector store candidates:
  - FAISS — https://github.com/facebookresearch/faiss
  - sqlite-vss — https://github.com/asg017/sqlite-vss
  - chromadb — https://www.trychroma.com/
- Related research: [recommender-rules-baseline.md](exploring_recommender-rules-baseline.md) (Teil 1 — the deterministic baseline)
