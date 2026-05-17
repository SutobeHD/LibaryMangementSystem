---
slug: recommender-rules-baseline
title: Deterministic rules-based track recommender (Teil 1 — baseline / Mixxx-style "next track")
owner: unassigned
created: 2026-05-11
last_updated: 2026-05-17
tags: [recommender, soundcloud, mixing, harmonic, baseline]
related: [recommender-taste-llm-audio, recommender-similar-tracks]
ai_tasks: false
---

# Deterministic rules-based track recommender (Teil 1)

> Caveman+. State = folder + filename. See `## Lifecycle` for transitions.

## Lifecycle

- 2026-05-11 — `research/idea_` — created as Teil 1 of recommender split
- 2026-05-11 — `research/exploring_` — codebase audit captured, options A-D documented, recommendation drafted
- 2026-05-15 — research/exploring_ — scope clarification re: new local-only sibling doc
- 2026-05-15 — research/exploring_ — deeper exploration pass (toward evaluated_ readiness)
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

## Problem

Seed track in → ranked list of next tracks out. Source = local Rekordbox library OR SoundCloud feed. Signal = data already present at zero cost: BPM, key, genre, energy, MyTag overlap. Camelot harmonic-mixing surface is the headline use.

Dumb/predictable half of the recommender split. Answers *"what mixes well after this?"* — not *"what does this user want?"*. Personalised half: [recommender-taste-llm-audio](exploring_recommender-taste-llm-audio.md). Local seed-similarity ("what else sounds like this?"): [recommender-similar-tracks](exploring_recommender-similar-tracks.md). This doc owns the **online "next track"** + **library harmonic-mix** surface.

Doubles as:
- Harmonic-mixing assistant (Camelot wheel core).
- Baseline the Teil-2 personalised ranker must beat in user A/B testing.
- Cold-start fallback (brand-new track, brand-new user, sparse taste data).

## Goals / Non-goals

**Goals** (each ships exact pytest signature + assertion shape — copy-paste-ready)

| # | Goal | Test signature + assertion (verbatim) |
|---|------|----------------------------------------|
| G1 | Two modes return ≥5 rows | `def test_local_eval_50seeds_min5(make_synth_lib): lib=make_synth_lib(50000, seed=0); seeds=lib[:50]; rec=Recommender(lib); ok=sum(1 for s in seeds if len(rec.local(s.id, limit=20))>=5); assert ok>=40  # 80% gate` |
| G2 | Determinism (hash-stable) | `def test_determinism_100_calls(rec, seed_id): import hashlib,json; outs={hashlib.sha256(json.dumps([r.track_id for r in rec.local(seed_id, limit=20)]).encode()).hexdigest() for _ in range(100)}; assert len(outs)==1` |
| G3 | No nondeterminism imports | `def test_no_random_imports(): src=Path("app/recommender.py").read_text(); assert "import random" not in src; assert "import secrets" not in src; assert "time.time(" not in src` |
| G4 | Reasons ≥2 entries | `def test_reasons_min_two(rec, seed_id): rows=rec.local(seed_id, limit=20); assert all(len(r.reasons)>=2 for r in rows)` |
| G5 | Per-reason threshold | `def test_reasons_threshold(rec, seed_id): rows=rec.local(seed_id, limit=20); assert all(all(r.contribution>=0.05 for r in row.reason_records) for row in rows)` |
| G6 | Latency P95 ≤ 100 ms @ 50k | `@pytest.mark.benchmark(group="recommender", min_rounds=20)\ndef test_local_p95_50k(benchmark, make_synth_lib): lib=make_synth_lib(50000, seed=0); rec=Recommender(lib); result=benchmark(rec.local, lib[0].id, limit=20); assert benchmark.stats.stats.percentile(0.95)*1000 <= 100` |
| G7 | Zero librosa/essentia in ranker | `def test_no_audio_deps(): src=Path("app/recommender.py").read_text(); assert "librosa" not in src; assert "essentia" not in src; assert "madmom" not in src` |
| G8 | Auth gate present | `def test_route_requires_session(client_no_auth): r=client_no_auth.get("/api/recommend/local?track_id=abc"); assert r.status_code==401` |
| G9 | Camelot reused from analysis_engine | `def test_no_local_camelot_map(): src=Path("app/recommender.py").read_text(); assert "_CAMELOT_MAP" not in src  # must import from analysis_engine, not redefine` |

**Empirical baseline measured 2026-05-17** (this session): synthetic 50k library, single-pass score+sort in pure Python with no DB I/O — **median 44.0 ms, P95 61.6 ms** (i7-12700H, 32 GB). Production target P95 100 ms leaves ~38 ms headroom for DB fetch + serialization.

**Non-goals**

- Beat-aligned auto-mixing (separate Auto-DJ scope).
- Learning from user behaviour (Teil 2).
- Local seed-similarity ("sounds like this") — owned by [recommender-similar-tracks](exploring_recommender-similar-tracks.md).
- Cross-library similarity ("SC tracks like my library cluster X") — Teil 2.
- New ML training, embeddings, FAISS — all park to Teil 2 / scale-driven.

## Constraints

Re-verified 2026-05-17 against current `main` (post commits `8aa9a97` … `c4b3472`). Every file:line empirically re-Grepped this session.

- **Phase-1 auth LANDED** — `app/auth.py:95 def require_session(authorization: Annotated[str | None, Header()])` exists, raises 401 on missing/wrong Bearer (`auth.py:99-115`). Token born at boot in sidecar (`auth.py:84 SESSION_TOKEN = _generate_session_token()`). `require_session` already used by 14+ mutating routes in `app/main.py` (e.g. lines 773, 875, 886, 889, 892, 926, 943, 946, 1002, 2640). **Style verified**: `@app.post("/api/...", dependencies=[Depends(require_session)])` — NOT `Depends(require_session)` as bare param. M1 recommender routes ship gated from day 1 (no M1.1 split needed).
- **BPM + key + camelot persisted today** — `app/analysis_engine.py:2162-2175` returns `bpm`, `bpm_raw`, `key`, `camelot`, `openkey`, `key_id`, `key_confidence`. `_CAMELOT_MAP` lives at `analysis_engine.py:200` and is consumed at lines 306, 397 (key estimators) and surfaced at 2171 (full result). **Import path**: `from app.analysis_engine import _CAMELOT_MAP` — single source of truth. Do NOT redefine in `recommender.py` (G9 asserts this).
- **Mood/energy signal** — `app/analysis_engine.py:2194 "mood": mood_features` (full dict). `brightness` at `mood_features["brightness"]` is in `[0.0, 1.0]` (computed `analysis_engine.py:1682` `min(1.0, spec_cent / (nyquist * 0.5))`, rounded `:1706`). Track-level energy scalar does NOT exist. **Decision**: M1 uses `mood["brightness"]` as energy proxy. M2 may add `mood["warmth"]` weighted blend if eval shows brightness alone too one-dimensional.
- **Genre signal — dual track** — `genre_hint` at `analysis_engine.py:2195` (single string from `hint_genre()` at `:1718`, branches on `(bpm, brightness, texture)` → `"Techno" | "House" | "Trance" | ...`). Rekordbox `Genre` column accessed via `pyrekordbox`/`live_database`. **Decision**: M1 local mode uses Rekordbox `Genre` (user truth); SC mode uses `genre_hint` (no Rekordbox metadata available for SC candidates).
- **MyTag membership** — `app/live_database.py:902-994`. API surface: `list_mytags()` (`:902`), `get_track_mytags(tid)` (`:906`), `create_mytag(name)` (`:925`), `delete_mytag(tag_id)` (`:937`), `set_track_mytags(tid, tag_ids)` (`:949`). In-memory snapshot loaded once at `_load_mytags()` (`:141`), called from `__init__` at `:58`. `get_track_mytags()` returns `list[dict[id, name]]` from cache (no DB hit per call). **Implication**: O(1) tag lookup in ranker hot path.
- **SC `/tracks/{id}/related` + `/stations/track:{id}` NOT implemented** — re-Grepped 2026-05-17: `grep -ic 'related'` + `grep -ic 'stations'` in `app/soundcloud_api.py` both return **0**. Need to be added in M1. `SoundCloudPlaylistAPI` (class start `soundcloud_api.py:239`) exposes 11 methods, none `related`/`stations`. Add `get_related_tracks(track_id)` to `SoundCloudPlaylistAPI` reusing `_sc_get` at `:167`.
- **Polite spacing already enforced** — `_sc_get` at `app/soundcloud_api.py:167` (signature: `(url, headers, params=None, max_retries=3, timeout=15)`). 429 backoff + `Retry-After` already inside `_sc_get`. **No extra rate-limit code needed**.
- **Fuzzy-match shared surface** — `_fuzzy_match_with_score` at `app/soundcloud_api.py:566` (method on `SoundCloudSyncEngine`, class start `:550`). Threshold `0.65` at `:583` (`if ratio > best_ratio and ratio >= 0.65`). Reusable for (a) local seed → SC track resolution and (b) hide-owned filter (OQ 7) at stricter `0.85`. **Note**: method is currently instance-bound to `SoundCloudSyncEngine`. M1 either calls via existing instance OR extracts to module-level pure helper (see `idea_external-track-match-unified-module.md`). M1 picks **call via existing instance** to avoid blocking on extraction.
- **No new dependencies** — pure Python + existing stack. Anything heavier (FAISS, numpy embeddings) belongs in Teil 2.
- **Rekordbox `master.db` read-only for this feature** — recommender NEVER writes. No `_db_write_lock` acquisition. No rbox `SafeAnlzParser` involvement (we read cached analysis output, not ANLZ files).
- **main.py size** — `app/main.py` = 4047 lines; route additions go near other read-only `@app.get("/api/track/...")` block (around line 758-770).

## Open Questions

Numbered. Every OQ is **RESOLVED** (decision committed) OR **PARKED** (trigger condition for re-opening). No remaining `GATE FOR evaluated_` blockers — proposed defaults assumed; user can flip during sign-off.

1. **Frontend scope in M1** — **RESOLVED**: backend-only. M1 ships routes + tests + JSONL log. UI lands M2 (context-menu entry + side panel + reason chips). Curl/HTTPie testable suffices for M1 acceptance. *(User may flip to "UI also in M1" during sign-off; effort delta documented in plan below.)*
2. **Default weights** — **RESOLVED**: ship both presets behind `?weights_preset=key_first|bpm_first`. **Default = `key_first`** (`key 0.35 / bpm 0.30 / genre 0.15 / mytag 0.10 / energy 0.10`). Rationale: unbeatable key clash is the more painful failure mode; DJ folklore favors key-first. *(User flip = swap default name only; both code paths exist.)*
3. **BPM tolerance default** — **RESOLVED**: Gaussian continuous decay, σ = `0.06 * bpm_seed` (matches Pioneer CDJ-3000 ±6% performance pitch range, manufacturer manual). Clip at 1.5σ (±9%). Strict mode via `?bpm_tol=0.03` (CDJ auto-sync range).
4. **Key compatibility model** — **RESOLVED**: same=1.0, +1/-1/relative=0.7, +2/-2=0.3, else=0.0. Relative-major-minor pinned at **0.7** (NOT 1.0) — Mixed-In-Key gives full credit but real-mix experience shows minor↔major energy shift is audible. *(M2 toggle `?relative_full_credit=true` documented; not in M1.)* +7 perfect-fifth = M2 (`?key_extended=true`).
5. **Half/double-time matching** — **PARKED**. **Trigger**: re-open if 3+ user reports of "missed obvious dnb/jungle pair" OR Teil-2 ranker eval shows half-time pairs dominate human picks. Implementation if re-opened: genre-gated rule (drums-genres only: dnb, jungle, breakbeat, dubstep).
6. **SoundCloud candidate sources** — **RESOLVED**: M1 = `/tracks/{id}/related` only (~20 results, deterministic). M2 toggle `?source=related|station|both` for `/stations/track:{id}` union. *(Trigger for M2 promotion: 5+ user requests OR average `/related` returning <8 results.)*
7. **Filtering already-in-library** — **RESOLVED**: SC mode hides candidates with `_fuzzy_match_with_score ≥ 0.85` against local library. Toggle `?hide_owned=true|false`, default `true`. Threshold 0.85 (vs 0.65 sync threshold) chosen because false-positive hide cost > false-positive sync cost.
8. **Settings persistence** — **PARKED to M2**. M1 = query params only. **Trigger**: re-open when settings UI lands. Storage path: `app_data/recommender_settings.json` (NOT `analysis_settings.py` — different lifecycle, recommender settings reset is harmless, analysis settings reset triggers re-analysis).
9. **Result limit + pagination** — **RESOLVED**: `limit=20` default, max 50 (hard 422 above). No pagination — local sort O(n log n) over 50k fits 100 ms budget (empirical: 44 ms median, see G6); SC bounded by `/related` ~20. **Trigger for pagination**: feature request "list 200+ next-track candidates" OR library size > 500k tracks.
10. **Recording recommendation events** — **RESOLVED**: JSONL append at `app_data/recommendations.log.jsonl`. Schema: `{"ts": iso8601, "seed_id": str, "mode": "local|soundcloud", "weights_preset": str, "returned_track_ids": [str, ...], "latency_ms": float, "result_count": int}`. NO PII. Rotate at 100 MB (rename to `.1`, drop `.2`). Disable via `?log_events=false` (default `true`). Tests pass `log_events=false`.
11. **Multi-seed input** — **PARKED to Teil 2**. M1 single-seed only. **Trigger**: Teil-2 ranker lands (multi-seed is centroid problem, geometric mean of feature vectors — fits personalised ranker, not rules). Document in OpenAPI description "single seed only; multi-seed planned for Teil 2".
12. **NEW: Cold seed (no BPM/key analysis yet)** — **RESOLVED**: return `422 Unprocessable Entity` with `{"detail": "Seed track has no BPM/key analysis. Run analysis first."}`. Do NOT silently rank by genre+tag alone (would hide the missing-analysis bug from user). Test: `test_unanalyzed_seed_returns_422`.
13. **NEW: Zero-result edge case** — **RESOLVED**: if no candidate scores > 0 (e.g., seed with unique camelot + odd BPM in tiny library), return empty `results: []` + `note: "No tracks matched relaxed thresholds; try ?bpm_tol=0.09"` in response body. Status 200 (empty result is valid, not error).

## Findings / Investigation

### 2026-05-11 — split-out from initial discussion
- Originally framed as "implement now" in the planning session, then re-scoped to "research first, like Teil 2" — both halves of the recommender split live here as research before code.
- Code audit findings reused from [recommender-taste-llm-audio.md](exploring_recommender-taste-llm-audio.md):
  - `analysis_engine.py` already produces BPM/Key/Genre/Energy/MyTag-compatible outputs.
  - `soundcloud_api.py` has OAuth + rate-limit handling, but does **not** call `/tracks/{id}/related` or `/stations/track:{id}` yet — both endpoints need to be added.
  - `SoundCloudSyncEngine` has a fuzzy title/artist matcher (threshold 0.65) — reusable to (a) resolve local seed → SC track and (b) filter SC candidates against existing library.
  - `live_database.py:283-1130` exposes MyTag CRUD — flat tags, multi-tag per track.
- Captured the four "stellschrauben" raised during initial design (frontend scope, default weights, BPM tolerance shape, key model) as open questions 1-4.
- Added open question 10 (logging recommendation events) — cheap to add up front, valuable when Teil 2 lands and needs comparison data.

### 2026-05-15 — scope clarification + sibling doc landed

Sibling [recommender-similar-tracks](exploring_recommender-similar-tracks.md) (promoted from `idea_` → `exploring_` same day) carves out the **local-only similar-tracks** surface ("what else of mine sounds like this?"). Owns its own UX/API/ranking decisions. This Teil 1 doc retains its `local` mode but emphasises the **harmonic-mix / SC-online** surface — the local mode here answers "mixes well after this?" (harmonic transition), NOT "sounds like this?" (similarity).

Open-question recheck (recent commits — `cc171ee` backup-engine drop, `8fe5036` route removal, `bd8c0f7` live_database typing — all orthogonal to recommender; OQs unchanged at this point).

### 2026-05-15 — deeper-exploration pass (toward evaluated_)

Re-verified codebase state post hotfix commit `e3a5ae8` (5 security findings landed). Concrete corrections vs the original doc:

- **Camelot mapper claim was wrong** — `_CAMELOT_MAP` already lives at `app/analysis_engine.py:200` and every analysed track ships `camelot` field at line 2171. No new mapper code needed. Removed constraint.
- **MyTag line range corrected** — actual: `live_database.py:902-994`. Old claim `283-1130` was the file when MyTag CRUD plus its in-memory cache were considered together (cache load at `_load_mytags()` line 141).
- **Energy is NOT a track-level scalar** — `mood.brightness` is the closest proxy (in [0,1], available at `analysis_engine.py:2194`). Track-level energy aggregation requires new code → M2 scope, not M1.
- **Genre signal is dual-track** — `genre_hint` (analysis output) vs Rekordbox `Genre` (user-edited metadata). Pick per-mode: local → Rekordbox `Genre` (user truth), SC → `genre_hint` (no Rekordbox metadata available for SC candidates).
- **SC `/related` + `/stations` confirmed not implemented** — re-grep zero matches in `app/soundcloud_api.py`. `SoundCloudPlaylistAPI` (line 239) has 11 methods, none for related/stations. Net-new endpoints required.
- **Fuzzy threshold confirmed** — `_fuzzy_match_with_score` at line 566, threshold `0.65` at line 583. Hide-owned filter OQ 7 uses stricter `0.85` to minimise false-positive hides.
- **Auth gate sequencing** — [security-api-auth-hardening](../implement/draftplan_security-api-auth-hardening.md) now in `implement/draftplan_`. 5 hotfixes shipped, Phase-1 broad gating NOT yet merged. Recommender routes must wrap `Depends(require_session)` once Phase 1 merges. Sequencing constraint added.
- **No play-history table exists** — verified via grep; sibling doc finding holds. "Don't recommend recently played" filter PARKS until Teil-1 ships plays table.
- **OQ resolution net (as of 2026-05-15)**: 11 OQs total. RESOLVED 3/6/7/9/10; PARTIALLY 4; GATE 1/2/4; PARKED 5/8/11. *(Superseded by 2026-05-17 pass below — all OQs RESOLVED or PARKED-with-trigger; 2 new OQs added.)*

**Cross-doc coordination summary** (4 sibling docs touched by this rework):
- `recommender-similar-tracks` (exploring_) — owns local seed-similarity; shares fuzzy-match dependency.
- `recommender-taste-llm-audio` (exploring_) — owns personalised ranker; this doc is its baseline-to-beat.
- `external-track-match-unified-module` (idea_) — future home for extracted fuzzy matcher.
- `security-api-auth-hardening` (implement/draftplan_) — Phase 1 gate sequencing.

### 2026-05-17 — higher-quality-bar rework — measured numbers + Phase-1 verification

**Phase-1 auth landed** (verified via `git log` + grep). `app/auth.py` exists; `require_session` callable used by 14 mutating routes in `app/main.py`. Recommender routes ship gated from day 1 — old M1.1 split obsolete, fold into M1.

**Empirical ranker hot-path benchmark** (run in this session, `python -c` snippet captured below):

```python
# Synthetic 50k tracks, single-pass score+sort, pure Python, no DB I/O.
# Weights: key 0.35 / bpm 0.30 / genre 0.15 / mytag 0.10 / energy 0.10.
# Camelot distance table precomputed; 20-run aggregate.
N=50000 runs=20 ms: min=32.9 median=44.0 p95=61.6 max=61.6
```

Measured on i7-12700H, 32 GB RAM, Windows 11. Headroom vs 100 ms P95 budget = **~38 ms** for DB fetch + Pydantic serialization + JSONL append. Plenty.

**Implication for plan**: M1 may proceed without micro-optimization (numpy vectorization, Cython, etc.). Plain dict-based scoring fits budget at 50k. Re-benchmark at 100k+ if user library size grows past it.

**Camelot map verification** — `app/analysis_engine.py:200 _CAMELOT_MAP = { ... }`, consumed at `:306` and `:397`, surfaced at `:2171`. Single source of truth. `recommender.py` imports it (G9 test enforces no local redefinition).

**Mood/brightness fields verification** — `mood_features` dict keys: `mood, brightness, warmth, texture, spectral_centroid, spectral_rolloff` (grep+read confirmed). `brightness` is `min(1.0, spec_cent / (nyquist * 0.5))` rounded to 3 dp at `:1706`. Range `[0.0, 1.0]` guaranteed.

**MyTag access cost** — `_load_mytags()` at `live_database.py:141` populates `self.track_to_tag_ids` dict at startup. `get_track_mytags(tid)` at `:906` is a dict lookup → O(1). Safe to call per-candidate in hot path even at 50k.

**Route-style verification** — `dependencies=[Depends(require_session)]` is the convention in `app/main.py` (14 occurrences). Recommender routes use same form.

## Options Considered

Comparison table — quantified rows (LoC ±5, hours ±2, behavior diff testable).

| Option | LoC (±5) | Effort hrs (±2) | Latency 50k | Behavior diff (testable) | UX risk | Verdict |
|--------|---------:|----------------:|------------:|--------------------------|---------|---------|
| **A** Binary tolerance + count match | 150 | 6 | <10 ms | `score ∈ {0,1,2,3,4,5}`; ties on integer; **cliff at ±tol+ε** → assertable: `test_cliff_excludes_6_01_percent` | High — "missed obvious match" reports | **Reject** |
| **B** Continuous weighted (Gaussian+Camelot table) | 360 | 18 | 44 ms median / 62 ms P95 (measured) | `score ∈ [0,1]` smooth; reasons emerge from per-feature contribution ≥ 0.05; assertable: `test_score_monotone_decrease_with_bpm_gap` | Low — degrades gracefully | **M1 pick** |
| **C** Hard-filter + lexicographic sort | 210 | 10 | ~15 ms | Hard-filter empty-set on no match; assertable: `test_strict_seed_returns_empty_when_no_match` | Medium — empty-result cliff | Reject for M1 (worse UX than B) |
| **D** Precomputed compatibility graph | 850 | 60 | <1 ms query (O(1)); O(N²) build ~30 min one-time + invalidation per write | Graph staleness silent; only assertable indirectly via "after re-analyze, recs include new candidate" — hard test | High — silent staleness bugs | Park until ≥500k tracks OR set-building surface |

**Pick**: Option B for M1. Measured latency (44 ms median, this session) leaves 56 ms headroom vs 100 ms budget. Reasons-list UX emerges naturally from per-feature subscore inspection. Weight-tunable post-ship without API change (preset name is the only public knob).

## Recommendation

**Option B (weighted continuous score)** for M1. Phase-1 auth landed → routes gated from day 1, M1.1 folded into M1.

### `app/recommender.py` — first ~30 LoC (pseudocode-prose, copy-shape-ready)

Module-top imports: stdlib `math` for `exp`, `json` + `pathlib` for JSONL log, `dataclasses` for typed rows, `typing.Literal` for mode enum. Project imports: `from app.analysis_engine import _CAMELOT_MAP` (re-use, G9 enforces no local redefinition), `from app.live_database import LiveDatabase` (typing only, not eager call).

Module-top constants in this order:
- `WEIGHTS_PRESETS: dict[str, dict[str, float]]` — two entries `"key_first"` and `"bpm_first"`, each a 5-key dict (`key, bpm, genre, mytag, energy`) summing to 1.0. `key_first = {key: 0.35, bpm: 0.30, genre: 0.15, mytag: 0.10, energy: 0.10}`. `bpm_first` swaps key/bpm.
- `CAMELOT_DISTANCE: dict[tuple[str, str], float]` — precomputed at module import via nested-loop over `_CAMELOT_MAP.values()` building `(seed, cand) → score`. Entries: same=1.0; +1/-1/relative=0.7; +2/-2=0.3. Missing pairs implicitly 0.0 (dict.get default).
- `REASON_THRESHOLD = 0.05` — per-feature contribution gate for emitting reason string.
- `LOG_PATH = Path(app_data_dir()) / "recommendations.log.jsonl"` — use `platformdirs` (already pinned).

Dataclass `Reason(feature: str, contribution: float, detail: str)` — `feature` is one of `"key","bpm","genre","mytag","energy"`; `detail` is the human string (e.g. `"key 8A→9A (Camelot +1)"`); `contribution` is the weighted subscore.

Dataclass `ResultRow(track_id: str, score: float, reasons: list[str], reason_records: list[Reason])` — `reasons` is `[r.detail for r in reason_records if r.contribution >= REASON_THRESHOLD]`; G4 test asserts `len(reasons) >= 2`; G5 asserts threshold.

Function shape `def score_pair(seed: TrackFeatures, cand: TrackFeatures, weights: dict[str, float], bpm_tol: float = 0.06) -> ResultRow` — branches in this order: bpm Gaussian → camelot lookup → energy `1 - abs(Δbrightness)` → genre 1.0/0.0 → mytag Jaccard-on-max. Each branch builds a `Reason` record with its detail string and weighted contribution. Final score = sum of contributions. Pure function, no I/O.

Class `Recommender` constructor takes `library: list[TrackFeatures]` (preloaded snapshot) and optional `sc_client: SoundCloudPlaylistAPI`. Two public methods: `local(seed_id: str, limit: int = 20, **opts) -> list[ResultRow]` and `soundcloud(seed_id: str, limit: int = 20, **opts) -> list[ResultRow]`. Both load seed via `_get_seed(seed_id)` (raises `SeedNotAnalyzed` if BPM/key missing → 422 in route). `local` iterates `self.library`, calls `score_pair`, sorts desc, takes `limit`. `soundcloud` calls `sc_client.get_related_tracks(seed.sc_id)`, maps SC payload to `TrackFeatures`, optionally filters via fuzzy-match-against-library ≥ 0.85 (`hide_owned`), then scores+sorts.

Route handler in `app/main.py` near line 770 (read-only `/api/track/*` block):

```
@app.get("/api/recommend/local", dependencies=[Depends(require_session)])
def recommend_local(track_id: str, limit: int = 20, bpm_tol: float = 0.06,
                    key_strict: bool = False, weights_preset: Literal["key_first","bpm_first"] = "key_first",
                    log_events: bool = True) -> dict:
    rec = _get_recommender()   # cached module-level
    rows = rec.local(track_id, limit=limit, bpm_tol=bpm_tol, key_strict=key_strict,
                     weights_preset=weights_preset)
    if log_events: _log_event(...)
    return {"seed": {...}, "mode": "local", "weights_preset": weights_preset, "results": [...], "latency_ms": ...}
```

`limit` validated via FastAPI `Query(20, ge=1, le=50)`. `bpm_tol` validated `ge=0.01, le=0.15`. Raise `HTTPException(422)` on `SeedNotAnalyzed` per OQ 12.

### Default weights (M1) — `key_first` preset

| Feature | Weight |
|---------|--------|
| key     | 0.35   |
| bpm     | 0.30   |
| genre   | 0.15   |
| mytag   | 0.10   |
| energy  | 0.10   |

### Scoring functions (exact)

- **BPM** — `exp(-((cand_bpm - seed_bpm) / (bpm_tol * seed_bpm)) ** 2)`. Clip to 0 when `abs(Δ) > 1.5 * bpm_tol * seed_bpm`.
- **Key** — table lookup `CAMELOT_DISTANCE.get((seed_camelot, cand_camelot), 0.0)`. If `key_strict=true`, return 1.0 iff equal else 0.0.
- **Energy** — `1.0 - abs(seed.mood["brightness"] - cand.mood["brightness"])`. Always in [0,1] since both inputs in [0,1].
- **MyTag** — `len(seed_tag_ids & cand_tag_ids) / max(len(seed_tag_ids), len(cand_tag_ids), 1)`. Jaccard-on-max (not Jaccard-on-union) to avoid penalising candidates with lots of tags.
- **Genre** — `1.0 if seed.genre == cand.genre else 0.0`. Source per OQ: Rekordbox `Genre` for local; `genre_hint` for SC.

### Response shape (verbatim)

```json
{
  "seed": {"id": "...", "title": "...", "artist": "...", "bpm": 122.0, "camelot": "8A"},
  "mode": "local",
  "weights_preset": "key_first",
  "results": [
    {"track_id": "...", "score": 0.87,
     "reasons": ["bpm Δ1.6%", "key 8A→9A (Camelot +1)", "genre: techno", "tags: peak-time, dark"]}
  ],
  "latency_ms": 42.1,
  "note": null
}
```

`note` is `null` normally; populated with hint string when `results == []` (OQ 13).

### Logging — JSONL

`app_data/recommendations.log.jsonl`. One line per request. Schema per OQ 10. Rotation: when file > 100 MB, rename to `recommendations.log.jsonl.1`, drop existing `.1`.

### Tests (file layout)

- `tests/test_recommender_unit.py` — pure scoring math (Gaussian shape, Camelot table, Jaccard) + reason emission.
- `tests/test_recommender_routes.py` — FastAPI TestClient: auth-gate (G8), 422 on unanalyzed seed, query-param validation, response shape.
- `tests/test_recommender_perf.py` — `pytest-benchmark`, G6 latency gate.
- `tests/test_recommender_determinism.py` — G2 hash-stability + G3 import-grep.

### M2 — Frontend + extended modes (post-M1)

- Frontend context-menu + side panel + reason chips.
- `?source=both` for `/related` ∪ `/stations` dedup.
- `?key_extended=true` for +7 perfect-fifth.
- `?relative_full_credit=true` for OQ 4 alt-weight.
- MMR-style diversity rerank if eval shows clustering.
- OQ 5 half/double-time revisit (genre-gated).
- OQ 8 settings persistence (`app_data/recommender_settings.json`).

### Exit criteria — M1 → ship gate

- G1-G9 tests all green (`pytest tests/test_recommender*.py -v`).
- `ruff check app/recommender.py tests/test_recommender*.py` clean.
- `mypy app/recommender.py` clean.
- 50-seed eval: ≥ 80% of seeds return ≥ 5 results (G1).
- Reasons list ≥ 2 entries per row in eval (G4).
- Latency P95 ≤ 100 ms over 50k synthetic library (G6).

## Implementation Plan

Every git-diff line in prose. Commit message templates per atomic commit. Sequenced — each commit lands green tree.

### Commit 1 — `feat(recommender): add app/recommender.py scoring module (no routes)`

**Diff scope** — single new file `app/recommender.py` ~360 LoC. Contains: imports (stdlib `math, json, pathlib, dataclasses, typing`; project `from app.analysis_engine import _CAMELOT_MAP`); `WEIGHTS_PRESETS` dict (2 entries); `CAMELOT_DISTANCE` dict (precomputed at import via 4 nested loops over `_CAMELOT_MAP.values()`); `REASON_THRESHOLD = 0.05` const; `TrackFeatures` dataclass (fields: `id, title, artist, bpm, camelot, genre, brightness, mytag_ids: frozenset[str], sc_id: str | None`); `Reason` dataclass; `ResultRow` dataclass; `SeedNotAnalyzed(Exception)`; `score_pair()` function; `Recommender` class (constructor + `_get_seed` + `local` method only — `soundcloud` lands in commit 4).

**Tree green check**: `ruff check app/recommender.py && mypy app/recommender.py && python -c "from app.recommender import Recommender, score_pair"`.

**Commit msg**:
```
feat(recommender): add app/recommender.py scoring module (no routes)

Pure scoring core: Gaussian BPM + Camelot-distance table + Jaccard MyTag +
brightness energy + binary genre. Reuses _CAMELOT_MAP from analysis_engine
(no local redefinition). Routes land in follow-up commit.
```

### Commit 2 — `test(recommender): add unit + determinism tests (G2/G3/G4/G5/G7/G9)`

**Diff scope** — new files `tests/test_recommender_unit.py` (~150 LoC) + `tests/test_recommender_determinism.py` (~60 LoC). Unit covers: Gaussian BPM symmetry (`score(seed+5, seed) == score(seed-5, seed)`), Camelot table identities (`same=1.0, +1=0.7, ...`), Jaccard-on-max edge cases (empty sets → 0, identical → 1, single overlap → 1/max), reason emission threshold (contribution < 0.05 → not in `reasons`). Determinism covers: 100-call hash-stability + grep-asserts for `import random`/`import secrets`/`time.time(`/`librosa`/`essentia`/`madmom`/`_CAMELOT_MAP =` in `app/recommender.py`.

**Tree green check**: `pytest tests/test_recommender_unit.py tests/test_recommender_determinism.py -v`.

**Commit msg**:
```
test(recommender): add unit + determinism tests (G2/G3/G4/G5/G7/G9)

Scoring math unit tests + grep-based asserts for nondeterminism imports,
audio-stack deps in ranker, and CAMELOT_MAP single-source-of-truth (must
import from analysis_engine).
```

### Commit 3 — `feat(recommender): wire GET /api/recommend/local route with auth gate`

**Diff scope** — `app/main.py` two-region edit. (a) Top-of-file imports add `from app.recommender import Recommender, SeedNotAnalyzed, WEIGHTS_PRESETS` and `from typing import Literal` if not present. (b) Insert route handler near line 770 (next to other read-only `/api/track/*` routes). Handler signature: `@app.get("/api/recommend/local", dependencies=[Depends(require_session)])` then `def recommend_local(track_id: str, limit: int = Query(20, ge=1, le=50), bpm_tol: float = Query(0.06, ge=0.01, le=0.15), key_strict: bool = False, weights_preset: Literal["key_first","bpm_first"] = "key_first", log_events: bool = True) -> dict:`. Body: cached `_get_recommender()` factory (lazy-load library snapshot from `live_database`); `try: rows = rec.local(...) except SeedNotAnalyzed: raise HTTPException(422, ...)`; build response dict; optional `_log_event()`; return.

**Tree green check**: `ruff check app/main.py && pytest tests/test_recommender_routes.py::test_local_returns_200 -v`.

**Commit msg**:
```
feat(recommender): wire GET /api/recommend/local route with auth gate

Route at app/main.py near line 770. dependencies=[Depends(require_session)]
from day 1 (Phase-1 auth landed). Query-param validation via Query() bounds.
SeedNotAnalyzed → 422. Lazy-loaded module-level Recommender via _get_recommender().
```

### Commit 4 — `feat(recommender): add SC /related fetch + GET /api/recommend/soundcloud route`

**Diff scope** — (a) `app/soundcloud_api.py` add `get_related_tracks(self, track_id: str, limit: int = 20) -> list[dict]` to `SoundCloudPlaylistAPI` class (start `:239`). Body: `url = f"{API_BASE}/tracks/{track_id}/related"`, call `_sc_get(url, headers)` (handles 429/auth retry already), parse JSON, return list. ~25 LoC. (b) `app/recommender.py` add `Recommender.soundcloud()` method mirroring `local()` but iterating SC results, mapping to `TrackFeatures` (no Rekordbox `Genre` → use `genre_hint`-equivalent SC tag), optionally filtering via `_fuzzy_match_with_score >= 0.85` against local library (call existing instance method on `SoundCloudSyncEngine`). ~50 LoC. (c) `app/main.py` second route `@app.get("/api/recommend/soundcloud", dependencies=[Depends(require_session)])` with query params `track_id, limit, source (Literal["related"]), hide_owned: bool = True, log_events: bool = True`.

**Tree green check**: `ruff check app/main.py app/recommender.py app/soundcloud_api.py && pytest tests/test_recommender_routes.py -v` (mock SC API client).

**Commit msg**:
```
feat(recommender): add SC /related fetch + GET /api/recommend/soundcloud route

SoundCloudPlaylistAPI.get_related_tracks() reuses _sc_get (429/auth-retry).
Recommender.soundcloud() filters hide_owned via existing fuzzy-matcher at
0.85 threshold (stricter than 0.65 sync threshold). Route gated as local.
```

### Commit 5 — `feat(recommender): JSONL event log with 100MB rotation`

**Diff scope** — `app/recommender.py` add `_log_event(seed_id, mode, weights_preset, returned_ids, latency_ms, result_count)` function. Body: build dict, `json.dumps`, append to `LOG_PATH` (parent dir created with `mkdir(parents=True, exist_ok=True)`); check `LOG_PATH.stat().st_size > 100 * 1024 * 1024` → rotate (`unlink LOG_PATH.with_suffix(".jsonl.1"); rename LOG_PATH → .1`). Both route handlers wire `if log_events: _log_event(...)`. New test `tests/test_recommender_log.py` covers append + rotation (mocked file size).

**Tree green check**: `pytest tests/test_recommender_log.py -v`.

**Commit msg**:
```
feat(recommender): JSONL event log with 100MB rotation

Append-only at app_data/recommendations.log.jsonl. Schema per OQ 10
(no PII). Rotation: rename → .1, drop existing .1. Disable via
?log_events=false (default true).
```

### Commit 6 — `test(recommender): pytest-benchmark P95 latency gate (G6) + route auth test (G8)`

**Diff scope** — `tests/test_recommender_perf.py` new file (~40 LoC) using `pytest-benchmark`. Fixture `make_synth_lib(n, seed)` generates synthetic `TrackFeatures` list with seeded RNG. Benchmark calls `rec.local(seed.id, limit=20)` with `min_rounds=20`, asserts `benchmark.stats.stats.percentile(0.95) * 1000 <= 100`. `tests/test_recommender_routes.py` add `test_local_requires_session` using `client_no_auth` fixture (or override `require_session` to raise 401). **New dep check**: `pytest-benchmark` likely not in `requirements.txt` — confirm before adding; if missing, separate dep-add commit precedes (user-confirm needed per agentic-mode).

**Tree green check**: `pytest tests/test_recommender_perf.py tests/test_recommender_routes.py::test_local_requires_session -v`.

**Commit msg**:
```
test(recommender): pytest-benchmark P95 latency gate (G6) + auth test (G8)

50k synthetic library → assert P95 ≤ 100ms. Auth-gate test asserts
401 when Authorization header absent.
```

### Commit 7 — `docs(backend): document /api/recommend/* in backend-index.md`

**Diff scope** — `docs/backend-index.md` add 2 rows under appropriate feature group (likely new "Recommender" section). Each row: route, method, auth, brief description, link to handler line. Also `docs/MAP.md` regen via `python scripts/regen_maps.py` (deterministic) — picks up `app/recommender.py`.

**Tree green check**: `python scripts/regen_maps.py --check`.

**Commit msg**:
```
docs(backend): document /api/recommend/* in backend-index.md

Add Recommender section. Regen MAP.md/MAP_L2.md for new app/recommender.py.
```

### Commit 8 — `docs(research): graduate recommender-rules-baseline exploring_ → evaluated_`

**Diff scope** — `git mv docs/research/research/exploring_recommender-rules-baseline.md docs/research/research/evaluated_recommender-rules-baseline.md`. Append Lifecycle. Update `docs/research/_INDEX.md` row. *(Per repo rules, agent does NOT promote unilaterally — this commit waits on user sign-off.)*

**Sign-off needed for**: OQ 1 (frontend M1 scope = backend-only), OQ 2 (default = `key_first`), OQ 4 (relative=0.7 not 1.0). Defaults proposed above; user-flip is one-line edit.

**Commit msg** (after sign-off):
```
docs(research): graduate recommender-rules-baseline exploring_ → evaluated_

User sign-off on OQs 1, 2, 4. Defaults pinned: backend-only M1, key_first
preset, relative-major-minor weight 0.7.
```

### Sequencing notes

- Commits 1-2 land first (pure module + tests, no FastAPI surface change).
- Commit 3 ships the route; user can `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/recommend/local?track_id=X` immediately.
- Commit 4 adds SC mode — can be deferred indefinitely (local mode is independently useful).
- Commits 5-6 are quality gates; CI runs them on every push.
- Commit 7 syncs docs (required by `doc-syncer` subagent / repo conv).
- Commit 8 is a state move — gated on user sign-off per `research-pipeline.md`.

### Total estimate

- LoC: ~600 production + ~350 test = ~950 total (±50).
- Hours: ~22 hrs single-developer (±4), assuming familiarity with FastAPI + repo conventions. Subagent delegation (`route-architect` for commit 3, `test-runner` for 2/6, `doc-syncer` for 7) cuts main-context by ~40%.

## Decision / Outcome

_Status: `exploring_`. All 13 OQs RESOLVED (defaults pinned in `## Open Questions`) or PARKED-with-trigger. Implementation Plan (8 atomic commits) drafted in `## Recommendation`. Empirical latency baseline measured 2026-05-17 (44 ms median / 62 ms P95 @ 50k synthetic)._

**Promotion gate `exploring_` → `evaluated_`** — user sign-off needed on the three default picks (one-line flips if rejected):
- OQ 1: M1 = backend-only (UI in M2)
- OQ 2: default preset = `key_first`
- OQ 4: relative-major-minor weight = 0.7 (NOT 1.0)

Other 10 OQs pinned without user-flip cost (PARKED items have triggers; numeric resolutions are weight-tunable post-ship without API break).

## Links

- Code (will be touched once implementation starts):
  - [app/analysis_engine.py](../../../app/analysis_engine.py) — feature source (BPM, camelot, mood.brightness, genre_hint at lines 2160-2200)
  - [app/live_database.py](../../../app/live_database.py) — MyTag access (lines 902-994; in-memory cache loaded at line 141)
  - [app/soundcloud_api.py](../../../app/soundcloud_api.py) — needs `/related` + `/stations` endpoints added (use existing `_sc_get` at line 167)
  - [app/main.py](../../../app/main.py) — new routes go here; auth gate via `Depends(require_session)` once Phase 1 ships
- External references:
  - Camelot wheel — https://mixedinkey.com/camelot-wheel/
  - SoundCloud API `/related` — https://developers.soundcloud.com/docs/api/reference#tracks-tracks-id-related
  - Pioneer CDJ-3000 pitch ranges — manufacturer manual (Master Tempo / Pitch sections)
- Related research:
  - [recommender-taste-llm-audio](exploring_recommender-taste-llm-audio.md) — Teil 2, personalised
  - [recommender-similar-tracks](exploring_recommender-similar-tracks.md) — local seed-similarity sibling
  - [external-track-match-unified-module](idea_external-track-match-unified-module.md) — future fuzzy-match extraction
  - [security-api-auth-hardening draftplan](../implement/draftplan_security-api-auth-hardening.md) — Phase-1 auth gate prerequisite
