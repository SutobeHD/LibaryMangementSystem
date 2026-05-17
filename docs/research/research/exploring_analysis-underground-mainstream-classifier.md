---
slug: analysis-underground-mainstream-classifier
title: Underground vs Mainstream classifier / certifier for tracks
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
tags: [analysis, popularity, sidecar-db, multi-source, fuzzy-match]
related: [external-track-match-unified-module]
ai_tasks: false  # set true to opt-in AI routines — see ## AI Tasks below
---

# Underground vs Mainstream classifier / certifier for tracks

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — research/idea_ — section fill (research dive)
- 2026-05-15 — research/idea_ — concrete API economics + match-key precision research
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (Spotify cold-scan 3h→3.3min via batch endpoint; 8/12 OQ resolved; Findings #2 supersede; M1-M4 phased rollout)
- 2026-05-15 — research/exploring_ — deeper exploration pass (toward evaluated_ readiness)
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

## AI Tasks

<!--
Opt-in queue for remote AI routines. Activate by setting `ai_tasks: true` in frontmatter.
Each item: 1 concrete sub-task. Routine processes 1/run, ticks done, commits via PR.

Item-prefix routes to a routine:
  resolve Q<N>: <text>          → research-exploring-push (resolves that Open Question)
  investigate: <topic>          → research-exploring-push (web+code, appends to Findings)
  grep <pattern> in <area>      → research-exploring-push (codebase lookup, appends to Findings)
  web: <query>                  → research-exploring-push (WebSearch, appends to Findings)
  promote to <state>            → research-exploring-push (git mv to next state, with preconditions)
  generate draftplan            → research-draftplan-scout (only on evaluated_ docs)

Routine MUST tick items it processed: `- [x] <original text> — done YYYY-MM-DD`.
When archived: keep section as audit trail.
-->

- [x] grep `SELECT.*Genre` in app/database.py to confirm Genre-coverage measurement path for Q2/Q12 audit — done 2026-05-17 (0 SQL hits; Genre lives in XML at L114/L229/L628; audit reads in-memory `db.tracks.values()` per Findings #6)
- [ ] web: Spotify Web API Client Credentials commercial-use ToS clause (Q11 PARKED → legal-track answer; Q11 already resolved via user-supplied-keys fallback, but ToS clarity still valuable for documentation)
- [ ] investigate: ListenBrainz API rate limits + scrobble-data shape for M3 4th-source candidate

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

Classify each track as **Underground vs Mainstream** by estimating cross-platform popularity. Signal source: aggregated **play counts across all available platforms** (Spotify, SoundCloud, YouTube, Apple Music, Beatport, Tidal, Deezer, etc.) normalised into a single 0–1 mainstream score (or a banded label: `underground` / `niche` / `rising` / `mainstream`). Useful as a curatorial signal (filter, group, badge), for set-planning ("warm-up only underground"), and as a discoverability tag.

## Goals / Non-goals

**Goals** (each testable — success metric in `[]`)
- Assign mainstream/underground signal to every local track usable for filter/group/badge UI. `[≥ 80% of library has non-null score after first full scan, given any track is on ≥ 1 of MVP-set platforms]`.
- Aggregate across platforms — no single-source dependency. `[score uses ≥ 2 platforms for ≥ 50% of tracks where Spotify+SC+LastFM coverage overlaps]`.
- Robust to missing data — most tracks only on 1–2 platforms. `[1-of-3 platform availability returns valid score, not null; flagged `low_confidence`]`.
- Genre-aware normalisation — underground-in-techno ≠ underground-in-pop. `[percentile rank computed within genre cluster when ≥ 100 tracks share genre; else falls back to library-wide]`.
- Local-first — no per-user cloud, no telemetry. `[zero outbound to non-platform hosts; verify in M1 via grep of `app/popularity_engine.py` for hostname allowlist + manual `mitmproxy` audit]`.
- Recomputable / providers swappable. `[deleting sidecar DB row + re-running re-fetches; weighting in config file, not code]`.
- Performance — full cold scan of 30k-track library completes in ≤ 6h on residential broadband. `[Spotify-batch 50/call + SC 0.3s spacing + LastFM 5 req/s = ~3h with overlap; budget 2× for retries]`.

**Non-goals** (deliberately out of scope)
- Editorial taste — pure popularity signal only.
- Real-time / per-play tracking — daily-or-slower refresh suffices.
- `master.db` user-facing column write — sidecar SQLite only (see Constraints).
- Predict future popularity ("rising" beyond simple delta = follow-up topic).
- ToS-forbidden scraping (Apple Music web, Tidal, Bandcamp playcounts).
- Local-DSP popularity proxy (no audio-feature → popularity model — separate research domain).

## Constraints

> External facts that bound the solution space — API rate limits, existing data shape, performance budgets, legal/licensing, team capacity. Cite source where possible.

- **SoundCloud V2 API in place** — `app/soundcloud_api.py:36` (`get_sc_client_id`). `Grep playback_count` in `app/` returns 0 hits — field arrives in payload but `_normalize_track` (line 297-330) drops it. Adding popularity = pure read + 1 dict-key in normalizer, no new auth path.
- **Fuzzy matcher exists** — `app/soundcloud_api.py:566` (`SoundCloudSyncEngine._fuzzy_match_with_score`), threshold `0.65` hardcoded at line 583. Cross-doc note: shared with `external_track_match_unified_module` (Option C/M1) — coordinate to import from `app/external_track_match.py` once that module lands, don't re-instantiate fuzzy logic here.
- **ISRC end-to-end verified** — parsed `app/audio_tags.py:319` (TSRC frame ID3 alias), stored `app/services.py:1111` (track_data ISRC field), exported `app/usb_pdb.py:497` (devicesql string slot 0). SoundCloud-sourced downloads rarely carry ISRC: `app/soundcloud_downloader.py:723-727` acknowledges "audio_tags.write_tags doesn't have an ISRC alias — skip silently". Library ISRC coverage thus skewed toward commercial purchases / Bandcamp / Beatport rips.
- **`master.db` lock lives at `app/database.py:22`** (NOT `app/main.py` — corrected vs prior idea_ draft). `RLock` + `db_lock()` context-manager + `_serialised` decorator (lines 25-53). All Rekordbox writes serialise through it; popularity data MUST stay in sidecar, not as new `master.db` columns (Pioneer hardware rejects unknown columns silently).
- **Sidecar precedent already in repo** (corrects prior "no per-library sidecar DB exists" claim):
  - `app/sidecar.py` — JSON sidecar `app_data.json` (artist→soundcloud_link map). Tiny scale only.
  - `app/analysis_cache.py` — per-file gzipped JSON + `index.json` (DSP results cache, lines 11-30).
  - `app/anlz_sidecar.py` — per-track ANLZ files in `.lms_anlz/<sha>/` next to audio.
  Popularity scale (30k rows × ~6 fields) → new SQLite sidecar (precedent shape: `app/analysis_cache.py` index, but SQL not JSON for query/sort).
- **Spotify Web API** — Client Credentials flow (no user OAuth) suffices for `popularity` field. New env: `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` joining existing `SOUNDCLOUD_CLIENT_ID/SECRET` in `.env.example`. Token TTL 3600s, refresh cheap.
- **YouTube Data API v3 quota** — 10 000 units/day/project. `videos.list?part=statistics` = 1 unit; `search.list?q=...` = 100 units. 30k tracks cold via search = 30 days of quota → infeasible without ISRC pre-resolution.
- **Last.fm API** — API key only (no OAuth). `track.getInfo` returns `playcount` (total scrobbles) + `listeners` (unique). Documented limit 5 req/s. Counts demographically skewed to Western indie / classic rock / early-2010s Pitchcore.
- **MusicBrainz / Discogs** — no playcounts but canonical ID / ISRC linkage. MB rate limit 1 req/s; Discogs 60 req/min authenticated. Useful as match-key resolvers only.
- **Beatport** — no public popularity API; chart-position scraping is fragile + ToS-grey. Excluded from MVP.
- **`ALLOWED_AUDIO_ROOTS` sandboxing** — `app/main.py:138-204` (`validate_audio_path` body; `Path.is_relative_to` check at line 191). Popularity enrichment does NOT open audio files — pure HTTP — so sandbox does not gate this feature. Score lookups by `track_id`/ISRC/title+artist, not by file path.
- **Genre source for normalisation** — `app/database.py:114` (`"Genre": node.get("Genre")` — read from Rekordbox XML `Genre` attribute per track). Genre histogram computed at `app/database.py:229` (`if t.get("Genre"): genre_counts[t["Genre"]] += 1`). Live SQLite DB path mirrors via `LiveRekordboxDB` (imported at `app/database.py:14`). Genre tags inherit Rekordbox population semantics — depend on user's import discipline + source-format ID3 (`TCON` frame) coverage. Empty/null `Genre` collapses Q2-genre-aware path to library-wide ECDF fallback (per Goals G4 default).
- **Analysis pipeline isolation** — `app/analysis_engine.py` is offline DSP (librosa/madmom/essentia), no network. Popularity = orthogonal phase, runs after analysis completes or on demand. No `_db_write_lock` contention with analysis batches.
- **httpx pattern required** — `coding-rules.md` forbids `requests.get` in `async def`. Popularity adapters MUST use `httpx.AsyncClient` + timeout + retry (`tenacity` or hand-rolled exponential backoff, see SC pattern at `app/soundcloud_api.py:220-232`).
- **Schicht-A dep pinning** — every new dep in `requirements.txt` as `==X.Y.Z`. Hand-rolled httpx adapters preferred over per-source SDKs (smaller surface, easier pin / CVE check). Approx new pins: `spotipy` or hand-roll (prefer hand-roll), `pylast` or hand-roll (prefer hand-roll).

## Open Questions

> Numbered. Each one should be resolvable (yes/no, or "X vs Y"), not open-ended philosophy.

1. **Score shape** — continuous 0–1 OR discrete bands OR both? **RESOLVED:** both — store continuous (`mainstream_score: float`) + derived band (`mainstream_band: enum`). Continuous wins for sort/filter; band wins for badge UX + cross-platform aggregation tolerance. Cheap to derive band on read.
2. **Genre-relative vs global normalisation** — depends on genre-tag coverage. **PARKED-with-plan (Findings #5)**: audit query specified (`app/database.py:114` source → histogram per Findings #5 snippet). Coverage-threshold decision rules locked: `<0.50` → library-wide only; `0.50-0.80` → hybrid; `≥0.80` → genre-aware default. Cluster eligibility `≥ 100` hard / `≥ 50` soft (user-tunable). Pre-ECDF normalisation (lowercase + strip parenthetical + collapse whitespace) MUST run before clustering to avoid free-form-input fragmentation. Measurement deferred to M1 side-deliverable; logic ready.
3. **MVP platform set** — **RESOLVED (Findings #2):** Spotify + SoundCloud + Last.fm. YouTube deferred to Phase 3 (quota math: 30k × 100 units search = 30 days). Last.fm IN (5 req/s + free + MBID match path).
4. **Trust weighting** — equal mean OR genre-aware? **RESOLVED (partial):** start equal-mean for MVP simplicity; user-tunable weights in `settings.json` once enough listening proves a default per-genre profile (e.g. SC>>Spotify for techno). Defaults shipped: `{spotify: 1.0, soundcloud: 1.0, lastfm: 1.0}`. Genre profiles = Phase 2 enhancement.
5. **Refresh cadence** — per-import vs scheduled vs lazy? **RESOLVED (Findings #2):** TTL sidecar (SC=24h, Spotify=7d, LastFM=7d) refreshed by scheduled background sweep + lazy fallback (`fetched_at IS NULL OR > TTL` triggers fetch on detail-panel open). Per-import = first-time scoring of newly added track only.
6. **Zero-platform tracks** — `unknown` vs pessimistic `underground`? **RESOLVED:** `unknown` band with `confidence=null`. Pessimistic-bias would silently corrupt set-planning filters ("warm-up only underground" picks unmatched obscure tracks as if curated). UI shows distinct visual (gray badge, "?" tooltip).
7. **Fuzzy threshold** — `0.65` vs `0.80` cross-platform? **RESOLVED (Findings #2):** ISRC > MBID > fuzzy@0.80. Tighten from 0.65 (which is SC-self matching — local tracks all share roughly comparable cleanliness) because cross-platform false-positive cost is higher (wrong-track popularity poisons score worse than missing data). Coordinate with `external_track_match_unified_module` OQ9 (per-source override pattern).
8. **UI display** — badge, color, both, sortable column? **PARKED until UX exploration**: UI affordance decision belongs in a frontend exploration loop, not idea_. Backend ships score + band + raw per-platform; frontend chooses surface (badge / color-coded row / column / facet filter) iteratively.
9. **Per-platform raw counts to user** — transparency vs simplicity? **RESOLVED:** ship both. Aggregated score on main row; expandable "popularity breakdown" panel showing per-platform raw + normalised. Transparency lets users sanity-check matches (`Spotify: 88, SC: 12k, LastFM: 0.5k` — does this match the track they expect?) and report bad matches.
10. **Cache responses** — sidecar with `fetched_at` vs always fresh? **RESOLVED (Findings #2):** caching mandatory — cold rescan is hours-scale. SQLite sidecar row per (track_id, platform) with `raw_count`, `normalised`, `fetched_at`, `match_method`, `match_confidence`. Re-fetch on TTL expiry only.
11. **Spotify ToS for desktop distribution** — does Client Credentials use in a Tauri-bundled binary qualify as "commercial use" requiring extended terms? **RESOLVED (via fallback)**: ship Spotify integration gated by user-supplied keys (env vars `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` in `.env`), NOT bundled creds. Sidesteps the distribution-credential ToS question entirely — each user runs under their own Spotify-developer-dashboard registration. Owner-read of ToS becomes informational-only, not blocking. UX cost: user must obtain Spotify dev creds (5min one-time setup); mitigation: in-app onboarding link + clear "popularity gated by your Spotify keys" badge. Bundled-creds re-evaluation deferred to post-M2 if user friction proves high.
12. **Genre-cluster size threshold for genre-relative percentile** — `≥ 100 tracks` heuristic from Goals; needs validation. **PARKED-with-plan (sibling of Q2, Findings #5)**: hard threshold `100` defended (1% ECDF quantile resolution → bands well-separated); soft fallback `50` (2% resolution still acceptable); `< 20` (5% resolution) rejected — single track flips band assignment. User-tunable down to 30 via `settings.json` `popularity.min_genre_cluster`. Measurement validates real histogram; logic ready.

## Findings / Investigation

> Required from `exploring_` onward. Append dated subsections as you learn. Never edit past entries — supersede with a new one.

### 2026-05-15 — initial scope audit

Codebase already has partial scaffolding for the matching half of the problem. `app/soundcloud_api.py` holds an authenticated V2 client (`get_sc_client_id` at line 36, dynamic-scrape fallback at line 69+) plus a fuzzy track matcher (`SoundCloudSyncEngine._fuzzy_match_with_score` at line 566, SequenceMatcher, threshold `0.65`). The same payload that `sync_playlist` consumes carries `playback_count` natively in the SoundCloud V2 response but the file does not currently read it — adding popularity is a pure read, no new auth. ISRC is stored end-to-end (tags → DB → USB export: `app/audio_tags.py:319`, `app/services.py:1111`, `app/usb_pdb.py:497`) which makes it the strongest cross-platform match key when present, but SoundCloud-sourced tracks largely lack ISRC (`app/soundcloud_downloader.py:723-727` shows the plumbing is acknowledged-incomplete). `master.db` is Rekordbox-managed and write-serialised by `app/main.py:_db_write_lock`; popularity therefore wants a sidecar SQLite (precedent: `app/anlz_sidecar.py` for per-track artefacts; no per-library sidecar DB exists yet). Spotify `popularity` is 0–100 already-normalised, Last.fm exposes raw playcount+listeners, YouTube exposes raw view counts; magnitudes span 4–6 orders of magnitude, so log + percentile-within-genre is the realistic normalisation path. No existing research doc touches this topic (checked `docs/research/_INDEX.md` is the canonical index).

### 2026-05-15 — concrete API economics + match-key precision research

Per-provider cost, match precision, demographic bias. Q3/Q5/Q7/Q10 become directly answerable — see follow-ups inline.

**Spotify Web API — `popularity`.** `GET /v1/tracks/{id}` returns `popularity: 0–100` (already log-normalised, proprietary recency-weighted — a 2020 hit may score lower than a current sleeper). Batch `GET /v1/tracks?ids=...` accepts up to 50 IDs/call. Auth: Client Credentials (no user OAuth); env vars `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET`; ~60min token refresh. Rate limit ~180 req/min, bursty + `Retry-After`. Match: `GET /v1/search?q=isrc:USAB12345678&type=track` is exact (near-complete ISRC coverage for commercial releases). Without ISRC: `q=artist:"X" track:"Y"` + fuzzy-rank.

**SoundCloud V2 — `playback_count`.** Already authenticated via `app/soundcloud_api.py:36`. Track JSON also carries `favoritings_count`, `comment_count`, `reposts_count`. No ISRC in SC search — fuzzy on title+artist via existing `_fuzzy_match_with_score`. SC catalogue is bedroom-producer-heavy: SC `playback_count` is THE key underground signal (where Spotify shows 5/100, SC may show 50k plays). V2 rate limit undocumented; observed-safe at 0.3s spacing (`app/soundcloud_api.py:169-234`).

**YouTube Data API v3 — `viewCount`.** `videos.list?part=statistics&id=...` costs 1 unit; `search.list?q=...` costs 100. Quota 10 000 units/day. Hard ceiling: 100 cold lookups/day (search) or 10 000 warm (videos.list with known IDs). Auth: API key. No ISRC. Search results polluted (covers, fan uploads, lyric videos); low confidence without uploader-vs-canonical-artist gate.

**Last.fm — `track.getInfo`.** Returns `playcount` (total scrobbles) + `listeners` (unique). Auth: API key only. Documented 5 req/sec — generous. MBID-based when available (highest precision; MB-fetched cheaply); else artist+title fuzzy. Demographic skew: older / Western / indie-heavy.

**MusicBrainz / Discogs.** No play counts but excellent canonical-ID / ISRC linkage. Use as match-key resolvers: library track → MBID → MB-linked services. MB 1 req/s; Discogs 60 req/min (auth).

**Normalisation math.** Counts span 4–6 orders of magnitude. (1) per-platform `log10(1 + count)`. (2) per-platform percentile-rank within library (SC=10k is low percentile in bedroom-techno collection, high in Top-40 — context matters). Aggregate weighting genre-aware: house/techno → SC > Beatport > Spotify > YouTube > Last.fm; pop → Spotify > YouTube > Last.fm > SC. Banding (0–1 composite): underground <0.25, niche 0.25–0.55, rising 0.55–0.75, mainstream >0.75. User-tunable.

**Caching + cadence.** Sidecar SQLite row per (track_id, platform) with raw_count, fetched_at, score. TTL: SC daily, Spotify/YouTube/Last.fm weekly. Cold scan of 30k library: Spotify ≈3h (30k/180/min) [SUPERSEDED by Findings #3 — batch endpoint changes economics to ~3.3 min], SC ≈2.5h (0.3s spacing), YT NOT FEASIBLE without ISRC (30k×100 units = 30 days of quota). Mitigation: YT-lookup only tracks where Spotify+SC+LastFM is inconclusive (rising band).

**Open Questions touched:** Q3 (MVP set) — Spotify+SC+Last.fm; YouTube fails economics. Q5 (cadence) — TTL sidecar refresh wins over per-import or full-rescan. Q7 (threshold) — ISRC > MBID > fuzzy@0.80 (tighten from 0.65; cross-platform false positives costlier than within-SC). Q10 (caching) — mandatory; cold rescan is hours-scale.

### 2026-05-15 — normalisation + banding defended by data / precedent + sidecar verification

**Sidecar precedent correction.** Prior Findings #1 claimed "no per-library sidecar DB exists yet" — wrong. Repo has 3 sidecar shapes today:
- `app/sidecar.py` — JSON `app_data.json` (tiny scale: artist→SC link map).
- `app/analysis_cache.py` — per-file gzip JSON + master `index.json` (DSP cache, lines 11-30).
- `app/anlz_sidecar.py` — per-track ANLZ files in `.lms_anlz/<sha>/` next to audio.
None of these fit popularity's shape (30k rows × 6 fields × multi-platform × needs query/sort). New SQLite sidecar warranted — precedent for filename + lifecycle from `analysis_cache.py`, SQL shape new.

**Cross-doc dependency.** `external_track_match_unified_module` (sibling idea_) owns fuzzy + ISRC/MBID match infrastructure. Recommended import direction once that module lands (Option C/M1): popularity calls `from app.external_track_match import fuzzy_match, resolve_isrc, resolve_mbid` rather than re-importing `_fuzzy_match_with_score` from `soundcloud_api.py` directly. Per-source threshold override (sibling OQ9) — popularity wants 0.80 for cross-platform; SC sync keeps 0.65 for within-platform. Both flow through the unified module's per-source config.

**Normalisation formula (concrete).** Per (track, platform):
```
raw_count                      # int from API (SC playback_count, Spotify popularity, LastFM playcount)
log_count   = log10(1 + raw_count)              # squash 6-order range to ~6.5 max
percentile  = ECDF_within_genre(log_count)      # 0..1, computed across all library tracks sharing genre
                                                 # OR ECDF_library_wide if genre-cluster < 100 (Q12)
platform_score = percentile                      # already 0..1
```
Per track aggregate (MVP equal-weight):
```
score = mean(platform_score for p in available_platforms)
band  = "underground" if score < 0.25
        "niche"       if 0.25 <= score < 0.55
        "rising"      if 0.55 <= score < 0.75
        "mainstream"  if score >= 0.75
confidence = "high"   if len(available_platforms) >= 2
             "low"    if len(available_platforms) == 1
             "unknown" if len(available_platforms) == 0  → band = "unknown"
```

**Spotify `popularity` exception.** Already 0–100 normalised by Spotify proprietary recency-weighted algorithm (not raw count) — skip `log10` step, use `popularity/100` directly. Documented behavior: weighting favours recent plays, so 2015-hit-now-cold scores lower than 2024-sleeper-trending. This actually aligns with mainstream/underground intent (current cultural cachet).

**Banding threshold defence.** 0.25 / 0.55 / 0.75 chosen because:
- 25-th percentile naturally splits "long tail" from "tracked"; mirrors Pareto-style 80/20.
- 55 / 75 leaves a wider `niche` band than `rising` / `mainstream` because most curated DJ libraries skew underground-and-niche (intentional curation removes a chunk of pure-pop, so the natural distribution of a DJ library should not be uniform 0-1). Asymmetric bands compensate.
- Tunable: thresholds in `settings.json` as `{"underground_max": 0.25, "niche_max": 0.55, "rising_max": 0.75}`. User can re-band without recompute.

**ISRC coverage hypothesis.** Library composition shapes match-key choice:
- Beatport/iTunes/Bandcamp commercial purchases: ~100% ISRC (verified via `app/audio_tags.py:319` TSRC frame consumption).
- SC downloads via `app/soundcloud_downloader.py`: ~0% (line 723-727 explicitly drops ISRC, `audio_tags.write_tags` has no alias).
- Drag-drop / unknown source: ~30-50% (depends on ripper / source).
- → ISRC-first match works for purchased majority; fuzzy@0.80 fallback for SC + drag-drop tail. Q12 audit informs the exact split.

**Performance math redux (confirms goal G7).**
- Spotify: 30 000 tracks ÷ 50 IDs/batch = 600 batches × ~333ms (180 req/min) = 200s = **3.3 min** (was over-estimated as 3h — batch endpoint changes economics).
- SoundCloud: 30 000 × 0.3s = 9 000s = **2.5h** (no batch endpoint).
- LastFM: 30 000 ÷ 5/s = 6 000s = **1.7h**.
- All parallel-capable → wall-time ≈ max ≈ **2.5h** plus retries + match resolution. Within 6h budget (G7).

**Open Questions touched (this entry):** Q1 (score shape) — both stored. Q6 (zero-platform) — `unknown` not pessimistic. Q9 (raw counts to user) — both. Q11 (Spotify ToS) — PARKED legal-track. Q12 (cluster-size) — PARKED until audit.

### 2026-05-15 — code-prefetch verification + sibling-doc cross-check

**Zero Spotify/Last.fm code in repo today.** Verified via `Grep -i "spotify|lastfm|last_fm|last\.fm" app/` → only stray refs:
- `app/analysis_engine.py:1635` — LUFS comment "(Spotify), -16 (Apple) or -18 (EBU R128 broadcast)" (loudness-target context, not API).
- `app/database.py:765` — pseudo-path filter `p.startswith('spotify:')` (streaming-service URI exclusion from local-file logic).
- `app/usb_manager.py:1291/1294/1296/1484` — USB-export skip list for `(soundcloud, spotify, tidal, beatport, http, https)` pseudo-tracks.
No Spotify client code, no Last.fm adapter. M1 → M2 path is greenfield for both adapters. No prior auth scaffolding to inherit; clean `httpx.AsyncClient` builds OK.

**Sibling-doc M1 alignment confirmed.** Re-read `docs/research/research/exploring_external-track-match-unified-module.md`:
- M1 public surface (lines 113-118): `fuzzy_match_with_score(query_title, query_artist, candidates, *, threshold=0.65) → (best_id_or_None, rounded_score)` — exact signature popularity needs.
- M1 lifts `_fuzzy_match_with_score` + `_normalize_title` from `SoundCloudSyncEngine` to module-level pure functions (line 202).
- M1 ships `extract_title_stem`, `parse_version_tag`, `Candidate` dataclass, `SourcePlugin` Protocol, registry mutators (lines 130-145).
- M1 OQ9 (line 83): per-source threshold tuning is M2-deferred but `threshold` parameter exposed at call-site from M1 — popularity can pass `threshold=0.80` from day one.
- M1 zero new deps (line 156): `difflib.SequenceMatcher` stays. Popularity inherits same dep posture for fuzzy.

**Import strategy locked.** Once sibling M1 ships, popularity imports `from app.external_track_match import fuzzy_match_with_score, extract_title_stem` instead of touching `app/soundcloud_api.py` directly. Pre-sibling-M1 fallback (popularity ships first): import `SoundCloudSyncEngine._fuzzy_match_with_score` as instance-method delegate via thin wrapper, swap at sibling-M1 ship. Either ordering = mechanical refactor, not architecture change. M1 entry condition in popularity Recommendation already captures this (line 219).

**Retry pattern citation correction.** Prior Constraints cited `app/soundcloud_api.py:220-232` for httpx-retry pattern. Re-verified: the cited range is the 429-handler block (lines 219-226); the full retry envelope is `_sc_get` body at `app/soundcloud_api.py:167-232` (max_retries loop, exponential backoff, `Retry-After` parsing, 401/403/404 classification). Popularity adapter should mirror the full envelope shape, not just the 429-handler.

**Open Questions touched (this entry):** none directly; all are infrastructure/coordination notes.

### 2026-05-15 — genre-coverage gating for normalisation (Q2/Q12 unblock path)

**Q2/Q12 are co-PARKED on the same measurement.** Both depend on per-genre track-count histogram. Audit query path concrete:

```python
# In M1 side-deliverable audit (runs once, output to log + sidecar):
from app.database import db
genre_counts = {}
null_count = 0
for t in db.tracks.values():
    g = t.get("Genre") or ""
    if g.strip():
        genre_counts[g] = genre_counts.get(g, 0) + 1
    else:
        null_count += 1
total = len(db.tracks)
coverage = (total - null_count) / total if total else 0
clusters_ge_100 = {g: c for g, c in genre_counts.items() if c >= 100}
```

Output captured: `(total, coverage_ratio, num_genres, distinct_clusters_ge_100, top_20_genre_histogram)`. Decision rules:
- `coverage < 0.50` → Q2 falls back permanently to library-wide ECDF (genre-aware path unreliable when half the library is uncategorised). User-facing log line: "popularity: genre coverage <50%, using library-wide normalisation only".
- `0.50 ≤ coverage < 0.80` → hybrid: genre-aware for tracks with genre tag AND in a `≥100` cluster; library-wide for the rest. Document `mainstream_score_source = "genre_ecdf" | "library_ecdf"` in sidecar for transparency.
- `coverage ≥ 0.80` → genre-aware default, library-wide only as null-fallback.

**Cluster-size threshold (Q12) sensitivity.** `≥ 100` heuristic conservative for ECDF percentile stability. ECDF noise at small N: 1/N quantile resolution; at N=100 → 1% resolution → bands (0.25/0.55/0.75) well-separated. At N=50 → 2% resolution → still acceptable. At N=20 → 5% resolution → risk that a single track flips band assignment. Soft threshold: `cluster_size ≥ 50` as fallback to widen genre-aware applicability when `coverage` is borderline. Hard threshold in tracker config (`settings.json`): `popularity.min_genre_cluster: 100` (user-tunable down to 30 if their library is genre-diverse).

**Genre normalisation fragility.** Rekordbox's `Genre` field is a single string (e.g. `"Tech House"` not `["Tech House", "Minimal"]`). User free-form input → fragmented clusters (`"Techno"` vs `"techno"` vs `"Techno (Peak Time)"` vs `"Peak Time / Driving"`). Pre-ECDF normalisation MUST: (1) lowercase + strip parenthetical (regex `\([^)]*\)$`), (2) collapse whitespace, (3) optional: alias-map for top-30 known synonyms (`"deep house"` ↔ `"deephouse"`; out-of-scope for M1, deferred to M3 alongside genre-aware weighting). Without normalisation, "Techno" / "Techno " / "techno" cluster separately → fragments stay below 100 threshold → genre-aware path silently degrades to library-wide.

**Decision flow for popularity-engine startup:**
1. On first scan, run audit → emit `popularity_audit.json` next to `popularity.sqlite`.
2. If `coverage < 0.50` → set `genre_aware_enabled = false` in runtime config; log warning.
3. Else → set per-cluster eligibility: cluster ∈ eligible iff `size ≥ min_genre_cluster` AND post-normalisation cluster (not raw).
4. Re-run audit on every full rescan (cheap, ~30k dict ops).

**Open Questions touched (this entry):** Q2 (genre-relative vs global) — UNBLOCKED with concrete coverage-based decision rules; PARKED-with-plan. Q12 (cluster-size threshold) — `≥ 100` defended + `≥ 50` soft fallback + user-tunable; PARKED-with-plan.

### 2026-05-17 — implementation-readiness re-verification (higher-bar pass)

**Re-Grep empirical (2026-05-17):**
- `Grep "playback_count" app/` → 0 hits. Confirms no SC popularity code path today; M1 = pure additive change to `_normalize_track` at `soundcloud_api.py:297-330`.
- `Grep "_normalize_track" app/soundcloud_api.py` → defined L297, called from L366, L419, L479, L538 (4 call-sites all flow through same normalizer — single-point edit propagates everywhere).
- `Grep "_db_write_lock" app/database.py` → defined L22, `db_lock()` ctx-mgr L26-40, `_serialised` decorator L43-53. Popularity sidecar is its own SQLite file → **does NOT** share `_db_write_lock`; new per-popularity-file lock (`threading.Lock`) sufficient. Avoids contention with Rekordbox writes (key design property).
- `Grep "Genre" app/database.py` → L114 parse (`node.get("Genre")`), L229 count (`genre_counts[t["Genre"]] += 1`), L628 XML setter. `SELECT.*Genre` grep returns 0 — Genre lives only in XML, not SQLite query layer. Q2 audit script reads `db.tracks.values()` in-memory dict, not SQL.
- `Grep "require_session" app/main.py` → import L33, applied to every POST/PUT/PATCH/DELETE route. Confirms popularity routes follow same pattern: GET reads unauthed (cache lookup); POST refresh authed.

**PyPI verification (2026-05-17):**
- `spotipy` latest = **2.26.0** (PyPI verified). MIT, ~5 deps (requests, urllib3, redis-optional). Decision: **hand-roll** — Findings #2 already chose Client Credentials only; spotipy's value-add (PKCE, scopes, refresh) is OAuth flows we don't use. Saves transitive deps (esp. blocking `requests` — Schicht-A prefers httpx async per coding-rules.md).
- `pylast` latest = **7.0.2** (PyPI verified). Apache-2.0, ~3 deps (httpx, certifi). Closer fit (httpx-native). **Trade-off**: pylast handles XML→Python type marshalling for `track.getInfo`. Hand-roll = 1 httpx call + 1 `xml.etree.ElementTree.fromstring`. Verdict: **hand-roll** (consistent with spotipy decision; consistent with `app/soundcloud_api.py` hand-roll pattern); 0 new pinned deps in `requirements.txt`. Re-evaluate if Last.fm adds OAuth-only endpoints we need later (unlikely for popularity-read).
- **Net new pins at M2:** zero. Continues `app/soundcloud_api.py` hand-roll posture.

**Sidecar precedent re-verified at byte level:**
- `app/analysis_cache.py:42-44`: cache dir defaults to `Path.home() / ".cache" / "rb_editor_pro" / "analysis_cache"` — XDG-cache convention. **Popularity mirrors**: `Path.home() / ".cache" / "rb_editor_pro" / "popularity" / "popularity.sqlite"`.
- `app/analysis_cache.py:124-126`: `self._lock = threading.Lock()` + atomic temp+rename for index writes. **Popularity adopts**: per-instance `threading.Lock` for write-path, sqlite WAL mode for concurrent reads.
- `app/anlz_sidecar.py:24-27`: `sha1(abs_path)[:16]` deterministic per-track key. **Not adopted**: popularity keys on `track_id` (Rekordbox int ID, stable across rescans) — not file path (renames break it). track_id source = `RekordboxXMLDB.tracks` dict key (string of int).

**Genre coverage sample audit (2026-05-17 — local M1-blocker resolution path):**
The Q2 audit is **already runnable today** without new code — the dict is in-memory:
```python
# Run-once at sidecar boot; output to popularity_audit.json
from app.database import db
from collections import Counter
genres = Counter()
null_count = 0
for t in db.tracks.values():
    g = (t.get("Genre") or "").strip().lower()
    g = re.sub(r"\([^)]*\)\s*$", "", g).strip()  # strip "Techno (Peak Time)" tail
    g = re.sub(r"\s+", " ", g)                    # collapse whitespace
    if g:
        genres[g] += 1
    else:
        null_count += 1
total = len(db.tracks)
coverage = (total - null_count) / total if total else 0.0
clusters_ge_100 = {g: c for g, c in genres.items() if c >= 100}
audit = {
    "total": total,
    "coverage": round(coverage, 3),
    "null_count": null_count,
    "distinct_genres": len(genres),
    "clusters_ge_100": len(clusters_ge_100),
    "clusters_ge_50": sum(1 for c in genres.values() if c >= 50),
    "top_20": dict(genres.most_common(20)),
}
```
M1 ships this in `app/popularity_audit.py` as standalone module — single dependency on `app.database.db` singleton (already imported across `app/main.py`). Cost: ~30k dict iterations; sub-second on real libraries.

**Tooling discipline re-verification:**
- `httpx.AsyncClient` retry envelope: `app/soundcloud_api.py:167-232` is canonical pattern (max_retries loop, exponential backoff, 429 `Retry-After` parsing, classified 401/403/404 outcomes). Popularity adapters copy verbatim with platform-specific exception types.
- `require_session` gate on writes: `app/main.py:33` (import), applied on lines L557, L582, L630, L733, L773, L845, L859, L875, L886 (sampled). Popularity refresh endpoint (POST) gated; popularity read (GET) ungated.

**Open Questions touched (this entry):** none directly resolved — entire entry is implementation-readiness verification. All prior PARKED/RESOLVED states preserved.

## Options Considered

> Quantified table. Effort = engineer-hours including tests. LoC = net new across `app/` (excludes deletions). Risk score 1–5 (5 = high).

| Opt | Sketch | Pros | Cons | Effort (h) | LoC (≈) | Risk | New deps |
|-----|--------|------|------|------------|---------|------|----------|
| **A** | SC-only. Read `playback_count` from existing SC payload at `_normalize_track` (`soundcloud_api.py:297-330`) + log10 + library-ECDF + sidecar SQLite. | Zero new auth; 4 SC call-sites already routed through `_normalize_track`; ships fastest; validates UX + storage. | Bias: SC demographics (electronic). Silent on non-SC tracks. 1-of-1 = `confidence=low` mandatory. | 20-30 | ~350 | 2 | 0 |
| **B** | A + Spotify (`q=isrc:` batch 50) + Last.fm (`track.getInfo` MBID/fuzzy@0.80). Per-genre ECDF normalisation. Equal-mean aggregate. Banding heuristic from D layered. | 3-platform cross-validation; covers bedroom/indie/commercial; per-platform raw counts surfaced (Q9). | 2 new env-var pairs (Spotify + Last.fm). Match-quality dominates signal; bad fuzzy = bad score. Spotify recency-bias misrepresents vintage hits. | 80-120 | ~1100 | 3 | 0 (hand-roll) |
| **C** | MB-MBID anchoring first; popularity keyed by MBID (Spotify, Last.fm, ListenBrainz). Fuzzy fallback per-source. | Highest match precision. ListenBrainz = 0 quota. MBID stable across title edits. | MB coverage on underground electronic ≈ 30-50% (vs ≥ 95% on commercial). Inverts signal: most-underground = unscored. +1 resolution hop per track. | 100-160 | ~1500 | 4 | 0 (MB hand-roll) |
| **D** | Banding-only. Fixed per-platform log-thresholds → vote across available. No continuous score. | Simplest UX. No normalisation maths. Resilient to null Genre. | Magic thresholds; loses within-band ordering; recombining platforms = re-tune. | 40-60 | ~600 | 3 | 0 |

**Risk legend:** 1 trivial · 2 reversible feature-flag · 3 ToS/match-quality concern · 4 data-shape inversion possible · 5 architecture-blocking.

## Recommendation

**Composite path = Option A → Option B (with banding heuristic from Option D layered on top).** Option C deferred to Phase 4 (post-MVP); coverage-gap risk on underground tracks inverts the signal.

**Phased rollout** — each milestone has concrete deliverable + entry/exit conditions:

### M1 — Single-source MVP (Option A)
- **Deliverable**: SoundCloud-only popularity. New `app/popularity_engine.py` reads `playback_count` from existing SC payload (just add 1 dict key in `_normalize_track` at `soundcloud_api.py:297-330`). New sidecar SQLite `popularity.sqlite` with schema `(track_id, platform, raw_count, log_count, percentile, fetched_at, match_method, match_confidence)`. Backend endpoint `GET /api/popularity/{track_id}`. Frontend stub: numeric badge on track row.
- **Entry**: Q1, Q3, Q6 RESOLVED (yes — see Findings #2 + #3). No Spotify dep at M1 → Q11 not gating.
- **Exit**: Backend + sidecar SQLite landed; `GET /api/popularity/{id}` returns valid JSON for ≥ 80% of SC-imported tracks in `tests/test_popularity_engine.py`; UI badge renders without breaking layout (e2e screenshot). Single-source `confidence=low` correctly tagged. Library audit (Q2/Q12) for genre histogram landed as side-deliverable so M2 entry isn't blocked on data-gathering.
- **Effort**: S (one week).
- **Rollback**: delete sidecar SQLite + revert endpoint registration; SC sync untouched (only payload-key add reverts cleanly).

### M2 — Multi-source aggregate (Option B)
- **Deliverable**: Add Spotify (`q=isrc:` batch lookup, 50 IDs/call) + Last.fm (`track.getInfo` via MBID/fuzzy@0.80). Aggregate score formula from Findings #3 (log + ECDF + mean). Banding (Option D heuristic layered). Per-platform raw counts surfaced in detail panel (Q9). UI: expandable popularity breakdown.
- **Entry**: M1 EXITED. Q2 + Q12 confirmed (M1's audit emitted `popularity_audit.json`; runtime config branched per coverage ratio per Findings #5 decision flow). Q11 RESOLVED — user-supplied-key fallback wired per resolution; in-app onboarding link present. `external_track_match_unified_module` either shipped M1 (popularity imports `fuzzy_match_with_score` per Findings #4) OR popularity ships local thin wrapper around `SoundCloudSyncEngine._fuzzy_match_with_score` + refactor scheduled at sibling-M1 ship.
- **Exit**: First full scan completed; coverage metric measured (`≥ 50% of library has 2+ platform coverage` — if below, re-tune fuzzy thresholds or match-key order before declaring exit); cold full scan completes in ≤ 6h on residential broadband (G7 metric); per-platform raw counts displayed in UI detail panel without exposing API errors as visible noise.
- **Effort**: L (2-3 weeks).
- **Rollback**: feature-flag in `settings.json` — `{"popularity_platforms": ["soundcloud"]}` collapses M2 to M1 without code revert. Spotify/LastFM rows in sidecar remain (harmless data).

### M3 — Bias-correction + adaptive weighting
- **Deliverable**: Genre-aware weighting (techno → SC > Spotify > LastFM; pop → Spotify > LastFM > SC). User-tunable in `settings.json`. ListenBrainz integration (open scrobble data, no quota) as 4th MVP source.
- **Entry**: M2 EXITED for ≥ 2 weeks (real-usage telemetry from local-only logs reveals systematic genre bias OR user reports it).
- **Exit**: Equal-mean baseline replaced; "set-planning warm-up underground" filter selects tracks user agrees are underground in ≥ 80% spot-check (manual eval, n=30).
- **Effort**: M (one week, mostly config + tests).
- **Rollback**: `settings.json` toggle reverts to equal-weight.

### M4 — YouTube + Beatport (deferred, post-MVP)
- **Deliverable**: YouTube `videos.list` only on tracks still in `rising` band after M1-M3 (gates quota usage). Beatport chart-position scrape (opt-in ToS-grey).
- **Entry**: M3 EXITED. Quota math re-verified — `rising`-band size < 100 tracks/day refresh OR user accepts longer refresh cycle.
- **Exit**: YouTube/Beatport adapters in production, quota dashboard / log line shows < 50% daily budget consumed.
- **Effort**: M.

### M1 skeleton — first ~30 LoC + route sigs (implementation-ready)

`app/popularity_engine.py` (NEW, ~350 LoC total at M1 exit; head ~30 LoC pseudocode):
```python
"""
Popularity sidecar engine — SoundCloud-only at M1.
Sidecar lives at ~/.cache/rb_editor_pro/popularity/popularity.sqlite.
Mirrors layout precedent of app/analysis_cache.py (XDG-cache convention).
"""
from __future__ import annotations
import logging, sqlite3, threading, time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 86_400  # 24h for SC; per-platform override at M2
Platform = Literal["soundcloud", "spotify", "lastfm"]

def _default_db_path() -> Path:
    p = Path.home() / ".cache" / "rb_editor_pro" / "popularity" / "popularity.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

class PopularityStore:
    """Per-track per-platform popularity sidecar. SQLite WAL, threadsafe writes."""
    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _default_db_path()
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS popularity (
                  track_id TEXT NOT NULL, platform TEXT NOT NULL,
                  raw_count INTEGER, log_count REAL, percentile REAL,
                  fetched_at INTEGER NOT NULL,
                  match_method TEXT, match_confidence REAL,
                  PRIMARY KEY (track_id, platform)
                );
                CREATE INDEX IF NOT EXISTS ix_pop_fetched ON popularity(fetched_at);
            """)
```

`app/main.py` route additions (~25 LoC net):
```python
# READ — unauthed (cache lookup only, no mutation)
@app.get("/api/popularity/{tid}")
async def get_popularity(tid: str) -> dict: ...
# Returns {"track_id", "mainstream_score", "mainstream_band", "confidence",
#          "platforms": [{"platform","raw_count","percentile","fetched_at"}]}

# REFRESH — authed (mutates sidecar)
@app.post("/api/popularity/{tid}/refresh", dependencies=[Depends(require_session)])
async def refresh_popularity(tid: str, force: bool = False) -> dict: ...
# force=True bypasses TTL. Returns same shape as GET after refresh.

# BULK BACKGROUND — authed
@app.post("/api/popularity/scan", dependencies=[Depends(require_session)])
async def scan_popularity(platforms: list[Platform] | None = None) -> dict: ...
# Returns {"job_id","queued","ttl_skipped"} — actual work runs background task.
```

### M1 pytest signatures (exact — drop into `tests/test_popularity_engine.py`)

```python
# Unit — sidecar storage
def test_popularity_store_init_creates_schema(tmp_path): ...
def test_popularity_store_upsert_then_get_roundtrip(tmp_path): ...
def test_popularity_store_ttl_expiry_returns_none(tmp_path, monkeypatch): ...
def test_popularity_store_concurrent_writes_no_corruption(tmp_path): ...  # 4 threads × 100 writes

# Unit — normalisation math
def test_log_count_zero_returns_zero(): ...               # log10(1+0) == 0
def test_ecdf_within_genre_basic_distribution(): ...      # 100-track synthetic
def test_ecdf_library_wide_fallback_when_genre_null(): ...
def test_band_thresholds_underground_niche_rising_mainstream(): ...
def test_aggregate_score_mean_two_platforms(): ...
def test_aggregate_score_single_platform_low_confidence(): ...
def test_aggregate_score_zero_platforms_unknown_band(): ...

# Integration — SC payload reads playback_count after _normalize_track edit
def test_normalize_track_now_carries_playback_count(): ... # asserts SC payload key propagates

# Audit (Q2/Q12 measurement side-deliverable)
def test_genre_audit_coverage_ratio_basic(): ...
def test_genre_audit_parenthetical_normalisation(): ...   # "Techno (Peak Time)" → "techno"
def test_genre_audit_emits_audit_json(tmp_path): ...

# Route — FastAPI TestClient
def test_get_popularity_returns_404_for_unknown_track(client): ...
def test_get_popularity_returns_score_for_seeded_track(client, seed_pop): ...
def test_post_refresh_requires_session_token(client): ...  # 401 without Bearer
def test_post_refresh_force_bypasses_ttl(client, seed_pop, monkeypatch): ...
```

**Run target at M1 exit:** `pytest tests/test_popularity_engine.py -v` green; ≥ 18 tests.

### M1 git diff lines (prose preview — no `git mv`, no commit yet)

- NEW `app/popularity_engine.py` ~350 LoC (sidecar store + SC reader + normaliser + aggregator).
- NEW `app/popularity_audit.py` ~80 LoC (genre histogram + audit JSON emitter, runs on first scan).
- NEW `tests/test_popularity_engine.py` ~300 LoC (~18 tests per sigs above).
- EDIT `app/soundcloud_api.py:329` — add `"playback_count": raw.get("playback_count", 0),` to `_normalize_track` dict. 1-line addition; non-breaking.
- EDIT `app/main.py` — register 3 routes (GET/POST refresh/POST scan). ~25 LoC + import.
- EDIT `requirements.txt` — **no changes** (hand-roll httpx posture; httpx already pinned).
- EDIT `docs/backend-index.md` — append 3 routes under new "popularity" group.
- EDIT `docs/MAP.md` / `MAP_L2.md` — regenerated via `python scripts/regen_maps.py` (deterministic).

**Gates before promotion `exploring_` → `evaluated_`:**
- M1 entry conditions met (Q1/Q3/Q6 RESOLVED; Q11 ToS-path chosen: user-supplied-keys fallback ships from day one to sidestep distribution-credential question).
- Library audit logic specified (Q2 + Q12 PARKED-with-plan per Findings #5 — concrete query + coverage-threshold decision rules + cluster-size soft/hard thresholds; runnable audit code in Findings #6).
- Cross-doc coordination point with `external_track_match_unified_module` locked (Findings #4: M1 surface is exact match for popularity's needs; pre-sibling-M1 fallback = thin wrapper around `SoundCloudSyncEngine._fuzzy_match_with_score`, swap at sibling-M1 ship — mechanical, not architectural).
- Concrete sidecar SQLite location: `Path.home() / ".cache" / "rb_editor_pro" / "popularity" / "popularity.sqlite"` (mirrors `app/analysis_cache.py:44`); audit output sibling `popularity_audit.json`.
- Zero new pinned deps at M1 + M2 (Findings #6: spotipy + pylast hand-rolled; httpx already in tree).
- pytest signatures + M1 LoC skeleton + git-diff preview specified above — no design discovery left at implementation time.
- Owner confirms Q8 (UI surface) belongs in separate frontend exploration — backend ships score + band + per-platform raw, frontend chooses surface independently. PARKED status preserved.

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

- Code (existing, M1 entry points):
  - `app/soundcloud_api.py:36` (`get_sc_client_id`) — auth resolution.
  - `app/soundcloud_api.py:297-330` (`_normalize_track`) — payload normalizer; add `playback_count` key here.
  - `app/soundcloud_api.py:566-587` (`_fuzzy_match_with_score`, threshold `0.65` at L583) — fuzzy matcher; coordinate with unified module.
  - `app/audio_tags.py:319` — ISRC tag parse (`TSRC` ID3 frame + iTunes `----:com.apple.iTunes:ISRC` MP4 atom).
  - `app/services.py:1111` — ISRC writer into `track_data` dict pre-`master.db` insert.
  - `app/usb_pdb.py:497` — ISRC USB-export devicesql string slot 0.
  - `app/database.py:22` — `_db_write_lock` RLock (NOT `app/main.py` — corrected vs prior).
  - `app/main.py:138-198` — `ALLOWED_AUDIO_ROOTS` sandboxing (not gating popularity; informational).
  - `app/sidecar.py:7-38` — JSON sidecar precedent (filename + lifecycle pattern).
  - `app/analysis_cache.py:11-30` — gzip JSON cache precedent.
  - `app/anlz_sidecar.py:24-27` — sidecar-dir hash pattern.
- External docs:
  - Spotify Web API: https://developer.spotify.com/documentation/web-api/reference/get-several-tracks (batch up to 50 IDs), https://developer.spotify.com/documentation/web-api/reference/search (ISRC search).
  - SoundCloud V2: undocumented; observed via dynamic-client-id scrape (`app/soundcloud_api.py:69-110`).
  - Last.fm `track.getInfo`: https://www.last.fm/api/show/track.getInfo
  - MusicBrainz: https://musicbrainz.org/doc/MusicBrainz_API (1 req/s)
  - YouTube Data API v3 quota: https://developers.google.com/youtube/v3/determine_quota_cost
- Related research:
  - `external-track-match-unified-module` (sibling) — owns fuzzy + ISRC/MBID resolution at M1; popularity imports from it once shipped.
