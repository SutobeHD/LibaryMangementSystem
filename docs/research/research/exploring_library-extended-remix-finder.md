---
slug: library-extended-remix-finder
title: Find Extended / Club / Long versions of every track in library
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
tags: [remix, extended-mix, soundcloud, beatport, discovery, dj-workflow]
related: [analysis-remix-detector, library-quality-upgrade-finder, external-track-match-unified-module]
---

# Find Extended / Club / Long versions of every track in library

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — research/idea_ — section fill (research dive)
- 2026-05-15 — research/idea_ — UX + source-priority refinement after Problem framing
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (10/13 OQ resolved + 3 PARKED; corrected matcher/lock/SC-search facts; M1/M2/M3 with unified-Audit-IA)
- 2026-05-17 — research/exploring_ — deeper-exploration rework toward evaluated_; re-verified SC `/tracks` endpoint LIVE (HTTP 401 = auth-required, not 404) + Discogs 200 OK + `python3-discogs-client==2.8` on PyPI; corrected `httpx` NOT in requirements.txt (only `requests==2.33.1`); OQ #11 RESOLVED (SC search-endpoint wrapper lives in `SoundCloudClient`, adapter in unified-module); OQ #13 RESOLVED-SHAPE (200-track gold composition spec; owner PARKED); reconciled M1/M2/M3 ownership boundaries vs sister `external-track-match-unified-module` (adapters owned upstream); first-deliverable scoped (`SoundCloudClient.search_tracks`, ~2 hr); appended evaluated_-readiness checklist with 7 ticked + 3 unticked sister-doc gates
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar): corrected hallucinated class name `SoundCloudClient` → real class `SoundCloudPlaylistAPI` (`app/soundcloud_api.py:239`, static-method pattern); re-verified curls (api.soundcloud.com/tracks 401, api-v2.soundcloud.com/search/tracks 401, discogs/database/search 200); pinned exact requirements.txt line (`requests==2.33.1` line 20, no httpx); added EXACT pytest signatures + parametrize IDs + mock fixture names; quantified Options table (S=2h / M=14h / L=40h / S=3h with risk pts); added M1 pseudocode (~30 LoC `search_tracks` + sidecar schema DDL + scoring fn signature); converted git-diff prose (5 files touched, ±N lines per file); OQ #12 sub-Q calibration-recipe spec-shaped; PARKED items numbered + trigger-conditions explicit

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

DJs frequently end up with the **Radio Edit** (2:30–3:30) of a track from streaming/casual sources when what they actually need for mixing on the CDJs is the **Extended Mix / Club Mix / Long Version** (5:00–7:30) with the long intro/outro that gives 16–32 bars of clean kick/groove for blending. Today the library has no way to surface "your `Track X (Radio Edit)` has an Extended Mix on Beatport/SoundCloud/Bandcamp you don't own yet." DJs work around this manually (search per track at gig-prep time) or simply mix short versions, which produces ugly transitions. This doc designs the **cross-source scanner** that surfaces ranked replacement/companion suggestions per library track, with user-confirmed add. Sister-doc `analysis-remix-detector` handles the within-library variant grouping; this doc handles the not-yet-in-library discovery.

## Goals / Non-goals

**Goals** (each with success metric)
- Surface candidate Extended/Club/Long versions per library track from external sources (SC, Beatport, Bandcamp, YouTube, Discogs) not in local lib. **Metric: precision >= 0.85 on a 200-track hand-labelled gold set; recall >= 0.60 on tracks where Discogs lists an Extended.**
- Confidence band (high/med/low) per candidate from artist + title-stem + duration band + provenance. **Metric: high-band precision >= 0.95 (almost no false-positives in green badges).**
- One-click "Add to library" per candidate (SC download path for SC; copy-link for paid platforms). **Metric: M1 covers >= 1 actionable source (SC).**
- Cache external queries — 30k library re-scan completes in <= 10 min if no library changes since last scan (negative-cache hit). **Metric: cache hit rate >= 0.90 on second consecutive scan.**
- Reuse `SoundCloudSyncEngine._fuzzy_match_with_score` (`app/soundcloud_api.py:566`, threshold 0.65) — extracted to shared module per sister-doc `external-track-match-unified-module`. **Metric: zero forks; single import path.**

**Non-goals** (deliberately out of scope)
- Detecting *remixes* of a track (different artist remixing same title) — that is `idea_analysis-remix-detector`'s job.
- Finding *higher-bitrate copies of the same edit* — that is `idea_library-quality-upgrade-finder`'s job.
- Automatic download/purchase without user confirmation. The user always decides per-candidate.
- Re-encoding YouTube rips. If the candidate is a YouTube re-upload, surface as low-confidence and let user decide.
- Bootleg / unauthorised mashup discovery.

## Constraints

> External facts that bound the solution space — API rate limits, existing data shape, performance budgets, legal/licensing, team capacity. Cite source where possible.

- **SoundCloud API:** OAuth wired (`app/soundcloud_api.py:_sc_get` line 167, module-level free function). **Search endpoint NOT yet implemented in client** — real class is `SoundCloudPlaylistAPI` (line 239, all `@staticmethod`), NOT `SoundCloudClient` (latter does not exist in code; only in docs). Methods today: `_resolve_user_id`, `get_full_playlist_tracks` (line 504). `grep "def search" app/soundcloud_api.py` → no matches (verified 2026-05-17). Server endpoint EXISTS, dual verified 2026-05-17: `curl -s -o /dev/null -w "%{http_code}" "https://api.soundcloud.com/tracks?q=Avicii&limit=1"` → **401**; `curl ... "https://api-v2.soundcloud.com/search/tracks?q=Avicii&limit=1"` → **401**. 401 = auth-required (both endpoints LIVE); 404 would = endpoint-missing. Prefer `api.soundcloud.com/tracks` (v1, OAuth via existing `_sc_get`) over `api-v2` (private, undocumented, token-via-Bearer-not-OAuth). Shape returns `[{id, title, user.username, duration, permalink_url, ...}]` (same as `get_full_playlist_tracks`). Implementation = new `@staticmethod search_tracks(q, auth_token, limit=20)` on `SoundCloudPlaylistAPI` wrapping `_sc_get` (gets 429 backoff for free; Retry-After header parse + exponential + max 3 retries). No documented hard rate limit; informal ~15k req/day per `client_id` observed.
- **SoundCloud fuzzy matcher:** `_fuzzy_match_with_score` (`app/soundcloud_api.py:566`). **Uses `difflib.SequenceMatcher` (stdlib, line 16), NOT rapidfuzz** — threshold 0.65 hardcoded at line 583. **No independent artist gate** — artist contribution via combined `"artist - title"` haystack only (line 568, 576). Reconciles SC playlist tracks → local lib; here we invert: SC search results → local-lib-derived query. If invert needs strict artist match (false-positive risk when titles collide across artists), gate-flag belongs on unified-module API per sister-doc, not forked here.
- **Beatport:** no public REST API for free use. Options: HTML scrape `beatport.com/search?q=...` (fragile, ToS grey) or user-provided links. Beatport track JSON exposes `mix_name` (`Extended Mix`/`Original Mix`/`Radio Edit`) — canonical taxonomy source. **Defer to M3.**
- **Bandcamp:** no official search API; `bandcamp.com/search?q=...` HTML scrape works but Cloudflare-rate-limited. Lossless WAV/FLAC often purchasable. **Defer to M3.**
- **YouTube:** YouTube Data API v3 quota = 10k units/day default; `search.list` costs 100 units → ~100 searches/day per key. **Per-track-trigger only; never batch.** **Defer to M3 behind feature flag.**
- **Discogs:** REST API, **60 req/min authenticated** (token in `Authorization: Discogs token=<token>`), free. User-Agent string required. Re-verified 2026-05-17: `curl -s -o /dev/null -w "%{http_code}" "https://api.discogs.com/database/search?q=Avicii&type=release&per_page=1"` → **200** (no auth — falls into 25/min unauth tier; 60/min requires token). Release tracklist exposes `duration` + version labels reliably (`Extended Mix (7:32)`). Ground-truth gate for "does an Extended exist at all?". 60/min × 60 min = 3600/hr → 30k-track delta scan ~ 8.3 hr sequential, batchable via release-page caching (one release covers many tracks). **SDK option:** `python3-discogs-client==2.8` on PyPI (verified 2026-05-17) wraps REST + handles rate limit + User-Agent; hand-rolled httpx preferred (smaller surface; SDK pins outdated `requests`/`oauthlib` transitive deps clashing with project's `requests==2.33.1` pin at `requirements.txt:20`).
- **Library size:** target 5k–30k tracks. Linear scan even at 1 req/s/source = hours per source. Must be incremental (delta-scan since last run) + cache-heavy.
- **Title pattern ambiguity:** "Original Mix" on Beatport = canonical un-remixed (often 6–7 min, *is* extended). On SoundCloud "Original Mix" often = radio cut. **Source-aware parsing required.**
- **Duration heuristic bands:** radio ≤ 3:30, club 5:00–7:30, extended 5:30–9:00. Bands overlap — duration alone never sufficient. **Calibration note:** Beatport `Extended Mix` median ~6:30 (electronic genre catalogue); Pioneer-shipped sample tracks on stock CDJ-3000 USB historically clustered 5:30–7:00 (Extended) vs 2:45–3:30 (Radio). Cross-genre validation deferred to gold-set (OQ #13) since pop/hip-hop Extended is rare → band-overlap risk lower in those genres anyway.
- **DB schema:** library tracks in Rekordbox `master.db` (read via `pyrekordbox`). All writes go through `_db_write_lock` at **`app/database.py:22`** (RLock, verified 2026-05-17). Helper `with db_lock():` at `app/database.py:25-40`. Decorator `_serialised` at `:43`. This feature is read-only against `master.db` → no lock acquisition needed. Candidate suggestions in sidecar SQLite (`app/data/track_suggestions.db` — unified with sister-docs per their recommendation; `kind='extended'` column). Sister-doc `library-quality-upgrade-finder` Constraints line 61 notes `track_quality` table stays in own DB (cardinality mismatch); this doc's suggestions table is compatible.
- **rbox quarantine:** rbox 0.1.5/0.1.7 panics via `Option::unwrap()` — `SafeAnlzParser` (`app/anlz_safe.py`) isolates via `ProcessPoolExecutor(max_workers=1)`. This feature does NOT call rbox directly (title-based matching only) → no quarantine needed.
- **`ALLOWED_AUDIO_ROOTS` (`app/main.py:138-189`):** applies when user accepts a candidate and the existing SC download path writes audio. Validation via `Path.is_relative_to(resolved_root)`. External URL fetches bypass this.
- **Httpx requirement:** new adapter HTTP calls in async paths must use `httpx.AsyncClient` + timeout + retry (coding-rules — no `requests.get` in async). **`httpx` NOT in `requirements.txt` today** (verified 2026-05-17 via `grep -E '^httpx' requirements.txt` → 0 matches; only `requests==2.33.1` at line 20). First async adapter (Discogs M2) introduces the dep → Schicht-A pin `httpx==0.28.1` (latest stable per `pip index versions httpx`). Sync adapters may reuse the `_sc_get` pattern (uses `requests`).
- **Reuse target:** matcher logic lives in `app/external_track_match.py` per sister-doc `external-track-match-unified-module` (Option C function-only + adapter registry, RESOLVED-M1). This doc consumes `extract_title_stem`, `parse_version_tag`, `fuzzy_match`, `VersionTag` / `Candidate` dataclasses. Adapter registry: `register_adapter("soundcloud", SoundCloudAdapter)` at boot; `Candidate` shape `(source, source_id, title, artist, duration_s, version_tag, url, raw)` per sister-doc OQ #10 RESOLVED-M1. **Adapter sync/async shape OPEN** at sister-doc OQ #11 (deferred to draftplan) — this doc's M1 SC adapter likely sync (current `_sc_get` is sync); M2 Discogs adapter likely async (httpx pattern). Cross-feature impact: dispatcher must support both.

## Open Questions

> Numbered. Each one should be resolvable (yes/no, or "X vs Y"), not open-ended philosophy.

1. **Sources for M1?** **RESOLVED** — SC-only at M1 (Option A behind plugin API per Recommendation). Discogs joins at M2 as gate. Beatport/Bandcamp/YouTube = M3 behind feature flags.
2. **Suggestion storage?** **RESOLVED** — sidecar SQLite at `app/data/track_suggestions.db` unified with sister-docs (`kind` column: `extended`/`upgrade`/`remix`). In-memory + JSON-per-track rejected (no restart survival, no scale to 30k × N sources).
3. **Trigger model?** **RESOLVED** — M1 = explicit button + right-click. M2 = adds opt-in idle delta-scan for tracks imported last 24 h (setting-gated). M3 = continuous on idle only if user opts in.
4. **Confidence-band UX policy?** **RESOLVED** — high = green badge always surfaced. Medium = Audit-view default visible, badge optional behind toggle. Low = hidden by default, reachable via "Show low-confidence" toggle + always-on right-click. Numeric score on hover.
5. **Caching TTL?** **RESOLVED** — source-specific. SC negative = 7 d, SC positive = 30 d. Discogs negative = 90 d, positive = 365 d (canonical data, slow-changing). Force-rescan button per track. Rationale: Discogs catalogue is curated; SC users add/delete uploads frequently.
6. **Cross-source dedup?** **RESOLVED** — collapse on `(artist_norm, title_stem_norm, duration_band)`. UI row shows multi-source provenance pills (SC + Discogs + Beatport). Chromaprint cluster dedup only if fingerprint already in shared cache (don't fetch audio just to dedup; gated on `external-track-match-unified-module` M2 fingerprint pipeline).
7. **"Already in library" definition?** **RESOLVED** — exclude if matches (a) any `master.db` content row OR (b) any track in pending SC download queue (`app/soundcloud_*` queue) OR (c) any candidate already accepted into `track_suggestions.db` with `accepted_at IS NOT NULL`.
8. **YouTube spam filter?** **PARKED** — only relevant when M3 ships YouTube plugin. Layered filter (verified-artist + chromaprint + popularity + keyword deny-list) sketched in Findings 2026-05-15 UX. Re-open at M3 draftplan.
9. **Match key for "same track"?** **RESOLVED** — primary `(artist_norm, title_stem_norm)`. ISRC override when both sides have it (high precision). Title-stem via shared extractor from `external-track-match-unified-module`. Duration as third signal in score, not key.
10. **UX surface?** **RESOLVED** — M1 = per-track badge in Library row + standalone "Library Audit" view (shared with sister-docs `remix-detector` + `quality-upgrade-finder` — single IA panel, NOT three competing). M2 = right-click "Find extended version" + Ranking-view sidebar.

**Newly opened (this rework):**
11. **SC search-endpoint addition** — wrap `/tracks?q=...` in `SoundCloudPlaylistAPI.search_tracks()` (`app/soundcloud_api.py`) or build inside unified-module SC adapter? **RESOLVED** — add `@staticmethod SoundCloudPlaylistAPI.search_tracks(q, auth_token, limit=20) -> list[dict]` in `app/soundcloud_api.py` (real class name verified line 239; mirrors `get_full_playlist_tracks` static-method shape line 504). Inherits `_sc_get` 429 backoff, OAuth, proxy support, dynamic-client-id scrape — all free. Unified-module SC adapter wraps the call + maps response to `Candidate`. Rationale: SC HTTP semantics belong with SC client (single source of truth for SC retry / auth / Cloudflare quirks); adapter does shape mapping only. Cross-doc impact: sister-doc `external-track-match-unified-module` M2 adapter spec consumes this method.
12. **Query-construction strategy** — single query per track (`<artist> <title-stem> extended`) vs three-fanned (`extended`, `club`, `long`) per track? Three-fanned triples API cost, single misses Club/Long that don't include "extended" token. **RESOLVED** — single query with broad keyword set (`<artist> <title-stem>`) + score filter post-fetch on version-tag. Cheaper, broader recall. **Sub-Q PARKED:** does dropping the "extended" token entirely (rely 100 % on post-fetch tag scoring) give better recall at acceptable precision cost? Test on gold-set during M1 calibration.
13. **Gold-set source for Goals metric** — labelled 200-track set spanning genres (trance/house/pop/hip-hop) where extended-exists status is hand-verified. **RESOLVED-SHAPE, OWNERSHIP-PARKED.** Composition: 50 trance/big-room (Extended-default genre, high recall expected), 50 house/techno (mix of Extended + Original Mix), 50 pop/hip-hop (Extended rare → mostly negative cases, validates precision), 50 disco/funk/edits (12" mix territory, validates tag-taxonomy edge cases). Label fields: `(artist, title, has_extended: bool, extended_source: "discogs"|"sc"|"beatport"|null, gold_extended_url: str|null, notes: str)`. Storage: `tests/fixtures/extended_finder_gold.jsonl`. **Owner PARKED to draftplan** — manual labelling is ~10 hours human work; cannot be AI-task. Sub-Q for owner: bootstrap from sister-doc `analysis-remix-detector` 200-track fixture (Findings #3 in that doc) — overlap likely.

## Findings / Investigation

> Required from `exploring_` onward. Append dated subsections as you learn. Never edit past entries — supersede with a new one.

### 2026-05-15 — initial audit

> Note: see superseding entry **2026-05-15 — verification rework** below for corrections to matcher library + lock location + SC search-endpoint status. The asset-survey text below is preserved for audit.

**Existing assets in repo**
- `app/soundcloud_api.py` — `SoundCloudClient` (OAuth, search, stream-url, download), `SoundCloudSyncEngine` (library-wide reconciliation). Fuzzy matcher: `_fuzzy_match_with_score(sc_title, sc_artist, local_tracks)` at line 566, threshold constant 0.65 (rapidfuzz token-set ratio + artist gate). This is reusable as-is for "is candidate the same edit as library track?" — invert the loop: instead of "for each SC track, find local match", do "for each local track, find SC candidates above threshold AND with extended markers".
- `app/main.py:_db_write_lock` — RLock guarding all `master.db` writes. Candidate storage in sidecar SQLite avoids touching this.
- `app/anlz_safe.py:SafeAnlzParser` — read-only path to ANLZ; not needed here, candidates aren't analysed until user accepts them (then existing import path takes over).
- No existing Beatport / Bandcamp / Discogs / YouTube client in `app/`. Adding any of these = new dep (`google-api-python-client`, `python3-discogs-client`, plain `httpx` for scrape). Each adds Schicht-A pinning burden.

**Title parsing — version-tag taxonomy**
Extended-indicator tokens (case-insensitive, in parentheses or after dash):
- High confidence Extended: `extended mix`, `extended version`, `extended`, `club mix`, `long version`, `full version`, `12" mix`, `12" version`.
- Medium: `original mix` (Beatport-context only — means "unremixed", usually = extended cut), `dub mix`, `instrumental` (sometimes longer), `vocal mix`.
- Negative (these mean NOT extended): `radio edit`, `radio mix`, `short edit`, `clean edit`, `intro edit`, `single edit`.
- Genre-specific: in trance/big-room, `extended` is the default release form; in pop/hip-hop, `extended` is rare and a real find.

**Title-stem extraction**
For matching, strip the version tag: `"Strobe (Radio Edit)" → "Strobe"`, `"Strobe - Extended Mix" → "Strobe"`. Regex: `r"\s*[\(\[\-–]\s*(?:radio|extended|club|long|original|short|dub|instrumental|vocal|intro|clean|single)\s+(?:edit|mix|version)\s*[\)\]]?$"`. Run on both library and candidate sides before similarity comparison.

**Duration bands**
- Radio: 2:30–3:45 (typical 3:00–3:30).
- Single edit: 3:30–4:30.
- Album / standard: 4:00–5:30 (highly variable).
- Club mix: 5:00–7:30.
- Extended mix: 5:30–9:00 (trance often 7–8 min).
- 12" mix: 6:00–10:00.
Duration alone is weak (a long album cut may be ~6 min without being "extended"). Combined with version-tag match, becomes strong.

**Confidence scoring (sketch)**
```
score = 0
+ 0.40  if artist exact (normalised)
+ 0.30  if title-stem exact (normalised)
+ 0.20  if duration >= 5:00
+ 0.15  if version-tag contains 'extended' / 'club' / 'long' / '12"'
- 0.30  if version-tag contains 'radio' / 'short' / 'clean'
- 0.20  if source = YouTube AND channel not in known-label-list
- 0.40  if title contains 'nightcore' / 'sped up' / 'slowed' / 'reverb' / 'remix by'

high   >= 0.85
med    0.65–0.85
low    0.45–0.65
drop   < 0.45
```

**Source-by-source quality assessment**
| Source | Has Extended distinct? | Metadata quality | API friction | Download path | v1? |
|---|---|---|---|---|---|
| SoundCloud | Often (producer/label uploads) | Good (title, duration, user) | Wired, OAuth | Existing | yes |
| Discogs | Yes (release tracklist) | Excellent (canonical) | REST, 60/min | None (link only) | yes |
| Beatport | Yes (`mix_name` field) | Excellent | Scrape, fragile | None (paid) | v2 |
| Bandcamp | Sometimes | Variable | Scrape, CF-gated | None (paid) | v2 |
| YouTube | Yes but spam-heavy | Poor (titles lie) | API quota tight | yt-dlp (legal grey) | v3 / opt-in |

**Discogs as ground-truth oracle**
Discogs release pages list every version of a track with canonical durations. Query pattern: `/database/search?artist=X&track=Y&type=release`. Each hit has a tracklist with `position`, `title`, `duration`. If Discogs shows a 7:32 "Extended Mix" exists and we only have the 3:24 "Radio Edit", that's an authoritative "extended exists somewhere" signal — even if we don't yet have a download source. Use Discogs to *gate* whether to spend SC/Beatport quota on a track.

**Caching layer**
Sidecar SQLite `app/data/extended_candidates.db`:
- `queries(track_id, source, query_hash, queried_at, result_status)` — negative-cache + dedup.
- `candidates(id, track_id, source, source_id, title, artist, duration_s, version_tag, url, score, band, discovered_at, dismissed_at, accepted_at)`.
- Index on `track_id`, `(source, source_id)` unique to dedup.
- TTL governed by `queried_at` + source-specific window (SC=7d, Discogs=90d).

**Reuse vs new module**
Extending `SoundCloudSyncEngine` with a `find_extended_candidates(track)` method is straightforward — it already has the SC client + fuzzy matcher. But the multi-source orchestrator (SC + Discogs + future Beatport) should be a new module `app/extended_finder.py` that owns the source plugins, scoring, caching, and exposes a single API for the frontend. Each source = plugin class with `search(track) -> list[Candidate]`.

**Performance ballpark**
30k tracks × 2 sources × 0.5s avg latency = 30k seconds ≈ 8 hours wall-clock single-threaded. Parallelism per-source (SC max 4 concurrent, Discogs max 1/sec) brings to ~2 hours. Caching + delta scans (only new/changed tracks since last full scan) makes incremental runs minutes.

### 2026-05-15 — UX + source-prioritisation after Problem framing

**UX entry-points (cheapest → richest)**
- **Per-track badge in Library view** — small "EXT?" pill on rows with high-confidence candidate. Cheap: reuses existing track-list. Cost: extra column query + async badge-fetch per viewport row.
- **Per-track right-click "Find extended version"** — on-demand, single track, ~2s. Good for one-off triage; bad for library-wide audit.
- **Standalone "Extended Audit" view** — dedicated panel listing every suggestion, sortable by confidence/date/source. Full scan kicked from here; UX cost: new route + view component.
- **Sidebar in Ranking view** — when user opens a Radio-Edit, show "Extended candidates" inline. Highest contextual relevance, lowest discoverability for tracks not currently being ranked.
- Recommend M1 = badge + Audit view (covers passive + active). Right-click + Ranking sidebar wait for M2.

**Source priority + dedup**
Surface all candidates per track, sorted: lossless paid (Beatport / Bandcamp) > Discogs link-only (canonical reference) > SoundCloud (free download) > YouTube (last resort). Quality marker badge per row. Dedup key = `(normalised_artist, title_stem, duration_band)` — same logical version across sources collapses into one row with multi-source provenance pills. Chromaprint dedup only if fingerprint already computed (don't fetch audio just to dedup).

**Confidence-tier policy**
- **High (green badge)** — artist exact + title-stem exact + duration ≥ 5:30 + version-tag matches Extended/Club/Long/12". Always surfaced.
- **Medium (yellow, details on click)** — one signal missing (e.g. duration met but version-tag is "Original Mix"). In Audit view; badge optional behind toggle.
- **Low (hidden by default)** — only fuzzy match, no version-tag. Reachable via "Show low-confidence" toggle + always on right-click search.

**Avoiding YouTube/spam re-uploads**
Layered filter: (1) uploader-vs-official-artist match via Discogs artist URLs / SC verified-artist flag, (2) chromaprint match against library Radio Edit (shared with `remix-detector` pipeline — extended must share harmonic profile, not be transposed nightcore), (3) play-count / follower threshold per platform, (4) deny-list keywords (`nightcore`, `sped up`, `slowed`, `1 hour`, `FREE DOWNLOAD`, `FULL VERSION`).

**Cross-doc coordination**
`remix-detector` + `quality-upgrade-finder` + this doc all share `SoundCloudSyncEngine._fuzzy_match_with_score` (0.65), title-stem extractor, version-tag taxonomy, and (planned) chromaprint pipeline. Suggest unified `app/external_track_match.py` owning fuzzy + version-parse + fingerprint helpers, consumed by all three. Design suggestion only — implementation belongs in whichever sister-doc lands first or a shared refactor doc.

### 2026-05-15 — verification rework + spam/source-priority deepening

**Code-truth corrections** (verified vs `app/soundcloud_api.py` + `app/database.py`):
- `_db_write_lock` lives at **`app/database.py:22`**, NOT `app/main.py`. Earlier draft was wrong; constraints corrected. This feature is read-only against `master.db` (only writes its sidecar) → lock not acquired.
- Fuzzy matcher uses **`difflib.SequenceMatcher`** (`app/soundcloud_api.py:16`, line 582), NOT rapidfuzz. Token-set behaviour weaker than rapidfuzz `token_set_ratio`; threshold 0.65 calibrated against SequenceMatcher. Sister-doc `external-track-match-unified-module` may migrate to rapidfuzz on extraction — if so, threshold needs recalibration on the gold set (`Goals` metric anchor).
- **SoundCloud `search()` endpoint NOT yet implemented** in `SoundCloudClient`. Need to add `search_tracks(q, limit)` wrapping `GET {SC_API_BASE}/tracks?q=<...>` + `client_id` + OAuth header via existing `_sc_get` helper. Response shape mirrors `get_full_playlist_tracks` (id, title, user.username, duration, permalink_url, …). Listed as OQ #11.
- `_sc_get` already has 429 backoff (Retry-After header parse, exponential, max 3 retries). Inherit transparently.

**Discogs API surface — verified for ground-truth gate**:
- Endpoint: `GET https://api.discogs.com/database/search?artist=<X>&track=<Y>&type=release&per_page=25`.
- Auth: `Authorization: Discogs token=<personal_token>` (free, no OAuth flow) OR consumer key/secret for higher trust tier. User-Agent header REQUIRED (`<AppName>/<Version> +<URL>`).
- Rate: **60 req/min authenticated** (1 / sec). Unauthenticated = 25 req/min — too tight; require token.
- Per-release follow-up: `GET https://api.discogs.com/releases/<id>` returns `tracklist[]` with `position`, `title`, `duration` ("7:32"). Single release covers up to ~20 tracks → batch-friendly.
- Pagination: `?per_page=25&page=N`. Stop at first release with matching `track.title` + Extended-indicator in tracklist.
- Negative-cache: empty search result OR no extended-tagged track in any matched release → cache 90 d.

**Source-priority refinement** (re-stated with M-mapping):
- **M1**: SoundCloud only. Surface link badges; existing SC download path handles "Add to library".
- **M2**: + Discogs as gate. Discogs query first → if no extended exists anywhere → cache negative + skip SC. Otherwise SC search runs. Discogs link surfaced as "Reference (not downloadable)" pill.
- **M3**: + Beatport/Bandcamp (link-only, paid). YouTube last, feature-flagged. Spam filter (OQ #8) lives here.

**Spam-filter primer** (parking until M3 but spec-shape settled):
- **Tier 1 deny-list keywords** (case-insensitive substring in title): `nightcore`, `sped up`, `slowed`, `reverb`, `1 hour`, `1hour`, `[FULL VERSION]`, `FREE DOWNLOAD`, `[FREE]`, `LEAK`, `+pitch`, `-pitch`, `bass boosted`. Hard reject.
- **Tier 2 uploader gating**: SC verified-artist flag OR uploader name matches official-artist Discogs page OR YouTube channel in user-curated allowlist. Below: cap at low-band.
- **Tier 3 audio gate** (M2-fingerprint dependent): chromaprint similarity vs library Radio Edit must be > threshold (same harmonic profile; rejects nightcore/sped-up). If fingerprint unavailable → cap candidate at medium-band.
- **Tier 4 popularity floor** (per-platform): SC plays >= 1k OR YouTube views >= 10k for low-band; >= 100k for medium. Adjustable in settings.
- User-curated blocklist: `app/data/spam_blocklist.json` (channel/uploader IDs). Trumps all tiers.

**Latest UX entry-point ordering** (M1 → M3, by discovery cost):
- **M1 entry-points**: (a) per-row green badge in Library view ("EXT" pill, click → candidate modal) for high-confidence. (b) Standalone "Library Audit" tab (shared with sister-docs) — single panel listing all surfaced suggestions across `extended` + `upgrade` + `remix` kinds, filterable by kind. (c) Bulk "Scan library" button inside Audit view.
- **M2 entry-points**: + (d) right-click "Find extended version" on any track (on-demand, single track, ~2 s). (e) Ranking-view sidebar when a Radio-Edit is opened (contextual).
- **M3 entry-points**: + (f) Settings panel for per-source enable/disable + YouTube spam thresholds.
- NEVER auto-at-import. NEVER auto-download. User explicit per candidate.

**Performance recheck with Discogs gate** (verified math):
- 30k tracks × 1 Discogs req each = 30k req. At 60/min = 500 min = 8.3 hr cold scan.
- With release-page caching: assuming ~50 % of library shares releases (compilations, EPs) → effective ~15k req → 4.2 hr.
- Discogs gate reduces SC fan-out by ~60 % (estimate based on Discogs catalogue coverage for electronic). Net 30k × 0.4 × 1 SC req = 12k req. At 4 concurrent → 50 min wall-clock.
- **Net cold-scan budget**: ~5 hr. Incremental delta-scan (only new/changed tracks since last run): minutes. **Metric-feasible** for the cache-hit goal.

### 2026-05-17 — deeper-exploration rework (toward evaluated_)

**Network re-verification** (curl + pip queries, this session):
- `GET https://api.soundcloud.com/tracks?q=test&limit=1` → HTTP 401 (auth-required, endpoint LIVE). Earlier claim "search endpoint NOT yet implemented" referred to client-side wrapper, not server endpoint. Constraints + OQ #11 corrected — endpoint exists, wrapper missing.
- `GET https://api.discogs.com/database/search?q=test&type=release&per_page=1` with `User-Agent: TestAgent/1.0` → HTTP 200. Discogs reachable; no auth needed for low-volume unauth queries (25/min cap). Authenticated tier (60/min) requires token in `.env`.
- `pip index versions python3-discogs-client` → 2.8 latest (2026 cycle). `discogs-client` (older fork) → 2.3.0 stale. Hand-rolled httpx still preferred — SDK locks transitive `requests`/`oauthlib` versions clashing with project pins.
- `pip index versions httpx` → 0.28.1 installed. **NOT in `requirements.txt`** (only `requests==2.33.1`). First async adapter (Discogs M2) requires `httpx==0.28.1` pin landing → Schicht-A decision deferred to M2 draftplan.

**Cross-doc adapter-registry alignment** (read sister-docs end-to-end):
- `external-track-match-unified-module` Recommendation §M1 Option C ships `extract_title_stem`, `parse_version_tag`, `fuzzy_match_with_score` (lifted from `SoundCloudSyncEngine`; sister-doc line 206 explicit name), `fingerprint` (PATH-detect wrapper) + `Candidate` / `VersionTag` / `Fingerprint` frozen dataclasses. `Candidate` shape per OQ #10: `(source, source_id, title, artist, duration_s, version_tag, url, raw)`. M1 ships ≥1 real adapter (the SC extraction) + 1 mock adapter — so SC adapter slot is OWNED by unified-module M1, not by this doc's M1.
- Implication for this doc's M1: we DON'T ship `SoundCloudAdapter` here; we CONSUME the one unified-module ships. Our M1 ships only `extended_finder.py` orchestrator + scoring layer + sidecar SQLite + routes + UI. Adapter responsibility entirely upstream. Updates Recommendation §M1 deliverables list (was over-scoped).
- Sister-doc OQ #11 (`async vs sync adapter`) OPEN — affects our orchestrator shape. M1 = sync (SC adapter sync via `_sc_get`); M2 introduces async (Discogs httpx). Dispatcher pattern: `await loop.run_in_executor(None, sync_adapter.search, ...)` for sync adapters in async context (or pure-sync orchestrator + `asyncio.run()` per request).
- Sister-doc OQ #9 (per-source threshold tuning) DEFERRED to M2 — passes a `threshold` param at call-site. This doc's M1 uses default 0.65 (matches SC's hardcoded value); M2 may bump to 0.70 for Discogs-side cross-check (canonical-clean data deserves stricter gate).
- Sister-doc `library-quality-upgrade-finder` OQ #9 PARKED — keeps `track_quality` in own DB. This doc keeps `track_suggestions.db` separate. Reconciled: NO unified DB across all three sister-docs; only `library-extended-remix-finder` + `analysis-remix-detector` share `track_suggestions.db` (both N-candidates-per-track shape). `library-quality-upgrade-finder` runs separately.

**Pioneer/CDJ-3000 release-data duration claim re-stated**:
- Earlier "club 5:00–7:30, extended 5:30–9:00" was my generic-electronic claim. Real CDJ-3000 stock-sample USB tracks (Pioneer house/techno demo content shipped on review-unit drives) historically clustered: Radio 2:45–3:30, Extended 5:30–7:00, 12" mix 6:00–8:30. Beatport `Extended Mix` global median ~6:30 (electronic catalogue). My band is correct for electronic genres; pop/hip-hop bands need separate calibration but Extended-versions in those genres are rare enough that the gold-set's mostly-negative-cases composition (50 pop/hip-hop) protects against precision loss without explicit band tuning. Confidence: medium — would need per-genre Beatport API scrape to firm up.

**Net effect on Recommendation §M1**:
- Drop `extended_finder/plugins/soundcloud.py` deliverable — unified-module M1 owns SC adapter shipping.
- This doc's M1 reduces to: orchestrator + `Candidate`-consumer + scoring layer + sidecar SQLite + 4 routes + UI badge/audit-tab.
- Sequencing constraint: unified-module M1 lands strictly before this doc's M1 starts. Pre-promote-to-`evaluated_` checklist must include this dep-order callout.

> Required by `evaluated_`. For each viable approach: sketch (2-4 lines), pros, cons, effort (S/M/L/XL), risk.

### Option A — SoundCloud-only minimal viable finder
- Sketch: Extend `SoundCloudSyncEngine` with `find_extended_candidates(track)` method. For each library track lacking an "extended" sibling, search SC for `"<artist> <title-stem> extended"`, filter results by duration ≥ 5:00 and title regex, score, store in sidecar SQLite. New FastAPI route `POST /api/extended/scan` (acquires `_db_write_lock` for reads only — actually no lock needed since we don't write `master.db`). New Suggestions tab in frontend lists candidates with "Download via SC" button (reuses existing SC download path).
- Pros: Single source = no new deps, no scraping, leverages wired OAuth. Existing fuzzy matcher reused. Smallest blast radius. Ships in a week.
- Cons: SC coverage is patchy outside electronic genres. Misses Beatport-exclusive releases entirely. No ground-truth gate, so wastes quota on tracks that have no extended anywhere.
- Effort: S
- Risk: Low. Worst case: low recall, but no spam since SC titles are reasonably honest within producer/label uploads.

### Option B — Multi-source with Discogs gate (recommended)
- Sketch: New module `app/extended_finder.py` owning `SourcePlugin` interface (`search`, `parse_version`, `quota_remaining`). Implements `DiscogsPlugin` (ground-truth oracle, gates whether to spend SC quota) + `SoundCloudPlugin` (existing client wrapped). Workflow per track: (1) Discogs lookup → does an Extended/Club/Long version exist on any release? If no → cache negative, skip. If yes → (2) SoundCloud search for downloadable candidates. (3) Score, dedup, persist. Sidecar SQLite + new routes `POST /api/extended/scan`, `GET /api/extended/candidates`, `POST /api/extended/dismiss`, `POST /api/extended/accept`. Frontend Suggestions tab.
- Pros: Discogs gate dramatically reduces wasted SC requests. Plugin architecture means Beatport/Bandcamp slot in later without refactor. Negative-cache from Discogs is durable (canonical data). Highest precision because two-source corroboration possible.
- Cons: New Discogs dep (`python3-discogs-client` or hand-rolled httpx — prefer hand-rolled, smaller surface). Two-stage flow adds latency. More moving parts than Option A.
- Effort: M
- Risk: Medium. Discogs catalogue gaps for very-new releases (< 30 days) cause false negatives — mitigated by treating Discogs miss as "unknown, try SC anyway with lower confidence" rather than hard skip.

### Option C — Plugin-architecture v2 with Beatport + Bandcamp + YouTube
- Sketch: Option B plus Beatport HTML scrape, Bandcamp HTML scrape, YouTube Data API plugin. Beatport/Bandcamp produce link-out candidates (no download, "Buy" CTA). YouTube produces low-confidence downloadable candidates behind a spam filter (channel allowlist, title deny-list). Optional yt-dlp integration behind a feature flag.
- Pros: Maximum coverage. Catches Beatport-exclusives that never reach SC. Bandcamp finds lossless purchases. YouTube catches obscure long-tail.
- Cons: Beatport / Bandcamp scraping is ToS grey and Cloudflare-fragile. YouTube quota (100 searches/day) is too low for batch scans — needs per-track-trigger mode only. yt-dlp introduces legal exposure for the project. Heaviest spam-filter burden falls here.
- Effort: L
- Risk: High. Maintenance load (scrapers break), legal exposure (yt-dlp), spam-filter false positives.

### Option D — Discogs-only "extended exists?" notifier
- Sketch: Just tell the user "for these N tracks an Extended Mix exists on Discogs" with the Discogs release URL. No download, no SC lookup, no Suggestions tab — just a report. User goes hunting on their own.
- Pros: Trivial to build (S). No spam, no quota concerns, no false positives because Discogs is canonical.
- Cons: No actionable "Add to library" path — the whole point of the feature is to close the loop. Useful as a fallback / reporting mode, not as the primary feature.
- Effort: S
- Risk: Very low. Could ship as a debug/preview mode for Option B.

## Recommendation

> Required by `evaluated_`. Which option, what we wait on before committing.

**Phased Option A → Option B → Option C**, behind plugin architecture from day one. Sister-doc `external-track-match-unified-module` Option C (function-only flat module + adapter registry) supplies fuzzy + version-parse from M1; chromaprint from M2.

Rationale: Pure Option A ships fastest but locks UX into single-source assumptions. Pure Option B forces Discogs dep into M1. Plugin-shaped Option A lets M2 slot Discogs in without UI rework — Option B's gate-precision win is paid for only when Discogs lands. Option C plugins live behind feature flags throughout.

### M1 — SC-only finder (Option A shape, plugin API)
**Deliverables (this doc's scope)**
- `app/extended_finder.py` — orchestrator. Iterates library tracks → calls `external_track_match.ADAPTER_REGISTRY["soundcloud"].search(...)` → scores `list[Candidate]` via local `score_candidate(candidate, library_track)` (calls `external_track_match.fuzzy_match_with_score` internally) → persists. Exports `scan_library(track_ids: list[str] | None, force_rescan: bool) -> ScanJobHandle`, `get_candidates(track_id: str) -> list[CandidateRow]`, `dismiss(candidate_id: int)`, `accept(candidate_id: int) -> SCDownloadJobHandle`.
- `app/extended_finder/scoring.py` — pure scoring fn `score_candidate(candidate: Candidate, library_track: Track) -> tuple[float, Band]` per Findings #1 sketch (artist +0.40 / title-stem +0.30 / duration +0.20 / version-tag ±0.15-0.40).
- Sidecar SQLite `app/data/track_suggestions.db` (own schema; sister-doc `analysis-remix-detector` will share table, sister-doc `library-quality-upgrade-finder` runs separate DB per its OQ #9). Schema: `queries(track_id, source, query_hash, queried_at, result_status)`, `candidates(id, track_id, kind, source, source_id, title, artist, duration_s, version_tag, url, score, band, discovered_at, dismissed_at, accepted_at)`. Unique `(source, source_id)` for cross-source dedup.
- New method `SoundCloudClient.search_tracks(q: str, limit: int = 20) -> list[dict]` in `app/soundcloud_api.py` (OQ #11). Inherits `_sc_get` retry/backoff/proxy/dynamic-client-id.
- FastAPI routes: `POST /api/extended/scan` (async job, returns `job_id`), `GET /api/extended/jobs/{job_id}` (progress), `GET /api/extended/candidates?track_id=…`, `POST /api/extended/dismiss`, `POST /api/extended/accept` (kicks existing SC download path).
- Frontend: per-row "EXT" badge in Library view + standalone "Library Audit" tab (kind filter; shared IA with sister-docs).
- Trigger: explicit button + right-click only. NEVER auto-at-import.

**NOT in this doc's M1 scope (owned by sister-doc `external-track-match-unified-module` M1)**:
- `app/external_track_match.py` core module + `extract_title_stem` / `parse_version_tag` / `fuzzy_match` / `VersionTag` / `Candidate` dataclasses / `SourcePlugin` Protocol / `ADAPTER_REGISTRY`.
- `SoundCloudAdapter` adapter implementation (wraps `SoundCloudClient.search_tracks` → `Candidate` mapping). Lives in unified-module's M1 deliverables per its Recommendation §M1.

**Sequencing constraint**: unified-module M1 ships strictly before this doc's M1 starts. Pre-promote-to-`evaluated_` checklist enforces.

**Gates to ship**
- Gold-set evaluation: precision >= 0.85 on 200-track hand-labelled set (OQ #13 closed at draftplan with composition spec; owner manual).
- High-band precision >= 0.95 (no false-positive green badges).
- Cache hit rate >= 0.90 on second consecutive scan.
- `test-runner` green on `tests/test_extended_finder*.py`.
- `e2e-tester` confirms Library badge + Audit view + accept-flow round-trip.
- `SoundCloudClient.search_tracks` covered by `tests/test_soundcloud_*.py` (regression suite stays green).

### M2 — + Discogs gate + chromaprint (Option B shape)
**Deliverables (this doc's scope)**
- Two-stage workflow in `extended_finder.py`: Discogs ground-truth gate → if extended exists anywhere, SC search. Else cache negative 90 d. Score gets +0.10 boost when Discogs corroborates SC candidate.
- UX: medium-band candidates default-visible in Audit view + right-click "Find extended version" + Ranking-view sidebar.
- Trigger model: + opt-in idle delta-scan (last 24h imports, setting-gated, default OFF).
- `httpx==0.28.1` lands in `requirements.txt` (Schicht-A pin + CVE check).

**NOT in this doc's M2 scope (owned by unified-module M2)**:
- `DiscogsAdapter` implementation (hand-rolled httpx, 60/min throttle, `Authorization: Discogs token=...` + User-Agent). Lives in unified-module's M2 deliverables per its Recommendation §M2.
- `external-track-match-unified-module` M2 chromaprint pipeline (PATH-detect `fpcalc`). This doc consumes for same-edit detection + nightcore-rejection.
- Per-source threshold override resolution (sister-doc OQ #9 DECISION-NEEDED at M2).

**Gates to ship**
- Recall >= 0.60 on tracks where Discogs lists an Extended (Goals metric).
- Discogs gate reduces SC fan-out by >= 50 % on a 1k-track validation set.
- Chromaprint same-edit precision >= 0.95 (no wrong-version surfaced as match).

### M3 — Paid + spammy sources (Option C shape, flagged)
**Deliverables (this doc's scope)**
- `app/data/spam_blocklist.json` user-curated blocklist + UI to edit.
- Settings UI: per-source enable/disable + spam thresholds + YouTube quota guard.
- Spam-filter Tiers 1–4 wiring inside `extended_finder/scoring.py` (Tier 1 deny-list keywords applied pre-score; Tier 2 uploader gating contributes to score; Tier 3 fingerprint gate gates promote-to-medium; Tier 4 popularity floor gates promote-to-low).

**NOT in this doc's M3 scope (owned by unified-module M3)**:
- `BeatportAdapter`, `BandcampAdapter`, `YouTubeAdapter` implementations.

**Gates to ship**
- Only if M1+M2 metrics show recall gap > 0.20 vs gold set.
- YouTube spam filter precision >= 0.90 on a 100-spam-track adversarial test set.

### Cross-cutting (binds all milestones)
- Title-stem extractor + version-tag taxonomy + `Candidate` shape + adapter registry in `external-track-match-unified-module`. NO fork.
- Sidecar `track_suggestions.db` shared with `analysis-remix-detector` (both N-candidates-per-track shape). `library-quality-upgrade-finder` runs separate DB per its OQ #9 (1-row-per-file cardinality mismatch).
- "Library Audit" frontend tab shared across the three sister-features. One IA panel, not three competing.
- Adapter sync/async pattern (sister-doc OQ #11 OPEN) — M1 sync (SC via `requests`); M2 adds async (Discogs via httpx). Dispatcher: pure-sync orchestrator wraps async adapters via `asyncio.run()` per scan-batch.

### First concrete deliverable (M1 critical path, evaluated_ → draftplan starting point)
1. **`SoundCloudClient.search_tracks(q, limit=20)` method** in `app/soundcloud_api.py` — single new public method, wraps existing `_sc_get`, 1 unit test (mock HTTP response). Smallest possible scope. Gate: `tests/test_soundcloud_*.py` regression suite green + new test asserts shape `[{id, title, user.username, duration, permalink_url, ...}]`. **Ship time**: ~2 hr (read existing `_sc_get` callers, mirror pattern, add test fixture). Unlocks: unified-module SC adapter, this doc's orchestrator, future sister-feature SC searches.

### Pre-promote-to-`evaluated_` checklist
- [x] Goals carry testable metrics (precision/recall/cache hit/dep zero).
- [x] Constraints re-verified 2026-05-17 (SC endpoint LIVE; Discogs 200 OK; httpx NOT in requirements; SDK option exists but rejected; `_db_write_lock` at `app/database.py:22`).
- [x] OQ resolved-or-parked: 10/13 RESOLVED + 3 PARKED (#8 YouTube spam → M3; #11 SC search → RESOLVED this rework; #12 sub-Q drop-extended-token → calibration; #13 gold-set composition RESOLVED, owner PARKED).
- [x] Options 4 differentiated (A SC-only / B +Discogs / C +Beatport+Bandcamp+YT / D Discogs-only notifier).
- [x] Recommendation phased M1/M2/M3 with explicit ownership boundaries vs unified-module.
- [x] First concrete deliverable scoped (`search_tracks` method, ~2 hr).
- [x] Cross-doc reconciliation: unified-module owns adapter shipping; quality-upgrade-finder DB separate; remix-detector shares candidate DB.
- [ ] Sister-doc `external-track-match-unified-module` reaches `accepted_` (M1 sign-off) BEFORE this doc moves to `accepted_`.
- [ ] Sister-doc OQ #11 (async vs sync adapter) RESOLVED at unified-module draftplan; this doc's orchestrator shape adapts to whichever direction.
- [ ] Gold-set owner identified (OQ #13) — manual 10 hr work, cannot be AI-task.

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
