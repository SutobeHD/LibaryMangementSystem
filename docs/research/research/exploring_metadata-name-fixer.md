---
slug: metadata-name-fixer
title: Normalise artist/title metadata (artist-in-title, featuring, parentheses)
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
ai_tasks: false
tags: []
related: []
---

# Normalise artist/title metadata (artist-in-title, featuring, parentheses)

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — `research/idea_` — section fill (research dive)
- 2026-05-15 — research/idea_ — option refinement after Problem framing
- 2026-05-15 — research/idea_ — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (6/10 OQ resolved + 4 PARKED; Option E added; M0/M1/M2 with Deliverables + Gate blocks)
- 2026-05-17 — research/exploring_ — deeper exploration toward evaluated_-ready: fixed stale `_db_write_lock` ref (now `app/database.py:22`); added auth-hardening Phase-1 Constraint (`Depends(require_session)` on all metadata-fixer mutation routes); added MusicBrainz client-lib Constraint (`httpx` not pinned, `musicbrainzngs==0.7.1` PyPI-available); added ANLZ string-cache Constraint; split revert-equality Goal (DB byte-identical vs tag semantic); added OQ #11 (ANLZ regen-vs-lazy) + OQ #12 (MB client lib choice); M0 Gate now requires Class-{2} empirical sample + OQ #11 decision; M1 deliverables explicitly require `Depends(require_session)`; M2 deliverables defer client-lib pick to draftplan
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

Real-world DJ libraries (especially anything sourced from SoundCloud, YouTube, or DMs) carry **malformed artist/title metadata** — artist accidentally inside the title parens, `feat. X` glued to the wrong field, `Title - Artist` reversed, `01 - Title` track-number prefixes, mismatched casing, smart-quote vs ASCII drift, HTML entities, embedded label/year markers, double-encoded remix attributions. Cumulative effect: search misses, sort-by-artist groups split, USB-export to Pioneer hardware shows confusing labels **at the gig**. Manual fixing across 10k–50k tracks is infeasible. This doc designs the automation — deterministic patterns + canonical-source lookup + LLM-assisted edge-cases — with hard safety boundaries (dry-run, per-track preview, undo log, file-snapshot) since it **writes to user metadata** with maximal blast radius.

## Goals / Non-goals

**Goals** (each testable with a metric)
- Detect malformed `artist`/`title` pairs across DB + ID3 tags. **Metric:** detector flags ≥ 90% recall over all 8 documented classes on a synthetic 500-track corpus (Findings 2026-05-15 — class numbering matches the second table); per-class recall reported separately so noisy classes ({2}, {3}) don't mask precision regressions in high-precision classes.
- Deterministic transforms with low false-positive rate. **Metric:** on the seeded corpus, applied-rule **precision ≥ 98%** (≤ 2% wrong fixes) on the M1 active subset {1, 4, 5, 6, 7, 8}; **recall ≥ 95%** on the same subset. Classes {2}, {3} excluded from precision SLO until M2.
- Dry-run preview UI: per-track before/after, grouped by rule, batch-confirm with select/unselect. **Metric:** zero mutation paths reachable without an explicit Apply click; one E2E test asserts this.
- Audit log of applied changes for revert. **Metric:** every applied mutation produces exactly one undo-log row `{run_id, track_id, rule_id, field, before, after, tag_sha1_before, tag_sha1_after, db_row_before}`. Revert restores **(a)** `DjmdContent` row to byte-identical JSON and **(b)** file SHA1 = `tag_sha1_before` for `.mp3` (mutagen 1.47.0 ID3 round-trip empirically byte-stable 2026-05-17, see Findings). Non-ID3 formats (MP4, Vorbis, FLAC): raw-tag-block sidecar as forensic fallback until M0 Gate per-format check.
- Optional canonical-source enrichment (MusicBrainz; Discogs deferred). **Metric:** ISRC-driven lookups round-trip a known set of 50 hand-curated tracks to the correct MBID with ≥ 95% accuracy.

**Non-goals** (deliberately out of scope)
- Album/genre/year/label normalisation (separate topic; share infra later).
- Audio-fingerprint-based identification (AcoustID/Chromaprint) — defer to a dedicated research doc.
- Auto-apply without user confirmation. Never. Not even for "obviously safe" rules.
- Filename rewrites on first iteration — file moves cascade into Rekordbox `Location` and break broken-link state. Tags + DB only, phase 1.

## Constraints

> External facts that bound the solution space — API rate limits, existing data shape, performance budgets, legal/licensing, team capacity. Cite source where possible.

- **Blast radius huge.** Wrong regex on 30k tracks silently corrupts library. Every mutation path gated by user confirm + audit-log + revert.
- **DB writer serialisation — CURRENTLY BROKEN.** `app/database.py:22` defines `_db_write_lock = threading.RLock()`; `app/database.py:25` defines `@contextmanager db_lock()` wrapper. Empirical 2026-05-17: **0 of 85 write routes (`@app.{post,put,patch,delete}`) in `app/main.py` import or hold either symbol** (grep `db_lock|_db_write_lock` in `app/main.py` → no matches). `update_tracks_metadata` (line 1007) does NOT acquire internally either — so today, `POST /api/track/{tid}` (line 892) and `PATCH /api/tracks/batch` (line 926) write `master.db` lock-free under concurrent requests. CLAUDE.md / `route-architect.md` invariant is aspirational, not enforced. Fixer must (1) acquire `db_lock()` at every metadata-fixer write route, and (2) spawn a parked follow-up topic to retrofit existing routes (out of scope here, but document the gap).
- **rbox quirks.** rbox 0.1.5/0.1.7 has `unwrap()` panics on parse; mutation goes through `pyrekordbox` directly, not via `SafeAnlzParser`. Writer here must not re-enter `OneLibrary.create_content` (broken — `app/usb_one_library.py`).
- **Three sources of truth.** ID3 tag (file), Rekordbox `DjmdContent` row (DB), filename. Today `app/audio_tags.py:read_tags` (line 355) reads ID3, falls back to filename **only** for missing artist/title (lines 388–394). `app/audio_tags.py:write_tags` (line 254) mirrors updates back to file; `app/database.py:update_tracks_metadata` (line 1007) writes DB. **Precedence policy undefined today** — must be decided per-field before fixer ships. See §Findings matrix.
- **Backup engine removed** (commit `8fe5036`, 2026-05-12 — "refactor(backend): drop backup engine, routes, scheduler"; deleted `app/backup_engine.py` and `tests/test_backup_engine.py` plus ~239 lines from `app/main.py`, net -1333 lines per `git show --stat`). Rationale: Rekordbox keeps its own DB backups. Routes removed: `POST /api/library/backup`, `GET /api/library/backups`, `POST /api/library/restore`, `GET /api/library/backup/<h>/diff`, `POST /api/system/cleanup`. No automatic snapshot in-app. Fixer must ship its own scoped before-state capture (per-track JSON diff log minimum) or block on a snapshot replacement.
- **MusicBrainz rate-limit.** Official policy (`musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting`): **1 req/s per IP** standard. User-Agent **mandatory**, must include contact info (`AppName/version ( contact-url-or-email )`). Anonymous / `python-urllib` UAs throttled to a shared 50 req/s pool. ISRC lookup is the cleanest disambiguator when ID3 carries one (`audio_tags.py` already extracts `isrc` via `_READ_KEYS`).
- **Discogs** — 60 req/min authenticated, rich electronic-music coverage, requires OAuth token in `.env`. Not wired. Deferred past M2.
- **Beatport / Spotify** — clean data, commercial APIs, ToS limits redistribution; legally murky for "rewrite my local library from your catalog". Out of scope.
- **Security sandbox.** `ALLOWED_AUDIO_ROOTS` + `validate_audio_path` (see `docs/SECURITY.md`, `app/main.py`) bound openable files. Fixer must honor — no shortcuts.
- **Auth hardening — PARTIALLY LANDED.** `draftplan_security-api-auth-hardening.md` Phase 1 ships gate on **all** `POST`/`PUT`/`PATCH`/`DELETE` routes via `Depends(require_session)` (Bearer-only). Verified 2026-05-17: `app/main.py:892` (`@app.post("/api/track/{tid}")`) + `:926` (`@app.patch("/api/tracks/batch")`) already carry `dependencies=[Depends(require_session)]`. `app/auth.py:95` `require_session` enforces `Authorization: Bearer <SESSION_TOKEN>` (split on whitespace, scheme lowercase compare, `safe_compare` constant-time match against boot-time token at `app/auth.py:114`). All proposed `POST /api/metadata-fixer/{scan,apply,revert}` routes declare `dependencies=[Depends(require_session)]` from M1 day 1. No blocking — Phase 1 active.
- **MusicBrainz client lib not pinned.** `requirements.txt:20` ships `requests==2.33.1` only; `mutagen==1.47.0` at line 58; **no `httpx`, no `musicbrainzngs`** (verified 2026-05-17). PyPI: `musicbrainzngs==0.7.1` (last release 2020-01-11, stale but stable + maintained ratelimiter), `httpx==0.28.1` (active). M2 adds one of: (a) `httpx==0.28.1` + hand-rolled token-bucket; (b) `musicbrainzngs==0.7.1` (ships own 1 req/s rate-limiter, requests-based, smaller surface). Dep choice is a draftplan-time security decision — both require explicit user approval per `agentic-mode.md` "Confirm first" list. `musicbrainzngs` 6-year staleness is a soft-no for new deps in this repo (Schicht-A hardening preference for actively-maintained code) — leans (a).
- **ANLZ string caches.** `.DAT`/`.EXT` files cache title/artist in waveform-overlay strings (Pioneer-side label rendering on CDJ). `app/anlz_writer.py:737 write_anlz_files` is the regen entry point (writes all three: `.DAT`/`.EXT`/`.2EX`; `backup_existing=True` keeps `_DEFAULT_BACKUP_KEEP` rolling copies). Open: whether Rekordbox lazy-regens after `DjmdContent` mutation alone, or whether fixer must call `write_anlz_files` itself. M0 Gate empirical: mutate title on one fixture track, reload Rekordbox, inspect `ANLZ0000.DAT` SHA before/after — same SHA = lazy never triggers, fixer owns regen; different SHA = lazy regen works, fixer no-op. M1 Gate re-runs `pytest tests/test_pdb_structure.py` after a mass-fix touches USB re-export.
- **`_split_artists` exists already** at `app/database.py:268` (`re.split(r'(?i),|&|/|;|\s+feat\.?\s+|\s+ft\.?\s+|\s+vs\.?\s+|\s+with\s+', artist_str)`). Splitting ≠ normalising — fixer should call this for downstream artist-list parsing but ship its own normaliser for the field-level fixes.
- **`usb_pdb.py` byte invariants** downstream of metadata. Re-export after string fixes re-runs PDB writer; short-ASCII vs long-UTF-16-LE path handled by string encoder, but length changes shift page packing. Run `pytest tests/test_pdb_structure.py` after a mass fix lands.

## Open Questions

> Numbered. Each one should be resolvable (yes/no, or "X vs Y"), not open-ended philosophy.

Status: **(R) resolved this iteration**, **(P) parked till evaluated_/draftplan_**, **(O) still open, blocking exploring_-ready bar = NONE**.

1. **(R)** Precedence on conflict: ID3 vs Rekordbox DB vs filename — which wins per field? → **DB authoritative** for title/artist/album; **tag authoritative** for ISRC; filename **never mutated** in phase 1. Per-field matrix in §Findings (2026-05-15). Configurable per-run deferred to M2.
2. **(R)** Scope of mutation phase 1: DB only / DB + ID3 / DB + ID3 + filename? → **DB + ID3 write-through**. Filename rewrites cascade into Rekordbox `Location` and break broken-link state — explicitly Non-goal. See M1 deliverables.
3. **(R)** Regex-only first pass, or hybrid (regex → LLM for low-confidence)? → **Regex-only mutation source**. LLM lives in opt-in per-track suggestion slot inside M1 UI; never batch-applies. See M1 deliverables ("LLM-suggest button (Option C role)").
4. **(P)** External enrichment: MusicBrainz only (free, ISRC-driven), or Discogs added behind opt-in OAuth? → MB-only in M2. Discogs deferred to a follow-up topic. Tripwire heuristic (not a hard contract): revisit Discogs if M2 ISRC-hit-rate < 60% on a real ≥1k-track library — chosen as a "useful enrichment" floor, not benchmarked.
5. **(R)** Granularity of confirmation: per-track / per-rule / per-batch with select-all? → **Per-track checkbox grouped by rule**; mass-apply requires an extra "I reviewed N tracks" modal gate. Safety architecture layer 2.
6. **(R)** Audit-log format and location? → **Sidecar SQLite at app-data dir** (`metadata_fixer_log.db`), one row per field-mutation. Not `master.db` — avoids `_db_write_lock` contention during fix run. JSONL rejected (no query support for "revert run X by user").
7. **(P)** Revert granularity: per-change row / per-run / per-track? → All three offered in UI; backend stores per-row, computes per-run and per-track as filters. Final UI shape decided at `draftplan_`.
8. **(R)** Snapshot requirement given backup-engine removal? → **Yes, narrowly scoped**. Fixer captures (a) original ID3 byte-block as sidecar in app-data dir and (b) original `DjmdContent` row JSON, both into undo log, **per-track-per-run** before any mutation. No project-wide `master.db` copy — too expensive on 30k libraries and Rekordbox holds the lock often.
9. **(P)** Smart-quotes / unicode normalisation: NFC vs NFKC? → **NFC for storage** (CDJ-3000 historically more tolerant of NFC); NFKC considered too aggressive (collapses ligatures, full-width chars). Verify on hardware before M1 lock-in.
10. **(P)** Featuring placement: `Artist feat. X` vs `Title (feat. X)`? → Need empirical pass: export 5 known-good Rekordbox tracks with `feat.` artists and inspect what Rekordbox itself writes to the exported ANLZ + PDB. Decision deferred till that empirical sample exists. Default policy in M1: **leave `feat.` in whichever field it was found**, surface as suggestion only. M0 Gate captures the sample → Class {2} policy resolvable at M1 start, not perpetually deferred.
11. **(P)** ANLZ string-cache regen after metadata mutation? → `.DAT`/`.EXT` files cache title/artist for CDJ overlays. Two possibilities: (a) Rekordbox lazy-regens on next library reload (no fixer action); (b) ANLZ retains stale strings until explicit re-analyse (fixer triggers via `app/anlz_writer.py:737 write_anlz_files`). Resolve at **M0 Gate** (moved from M1) via pre/post-mutation + Rekordbox-reload `ANLZ0000.DAT` SHA on one fixture track. **Same SHA after reload = stale = fixer owns regen (case b)**; **different SHA after reload = Rekordbox regenerated = fixer no-op (case a)**. M1 ships `write_anlz_files` call in apply path only if (b) confirms.
12. **(P)** MusicBrainz client lib: `musicbrainzngs==0.7.1` (PyPI, ships own rate-limiter, requests-based, ~500 LOC surface) vs hand-rolled `httpx.AsyncClient` + token-bucket. → Defer to M2 draftplan. `musicbrainzngs` smaller surface + battle-tested rate-limit, but `httpx` async aligns with `coding-rules.md` "no `requests.get()` in `async def`". Pick at draftplan time alongside dep-add user-approval.

## Findings / Investigation

> Required from `exploring_` onward. Append dated subsections as you learn. Never edit past entries — supersede with a new one.

### 2026-05-15 — initial audit

**Malformation catalogue (real-world DJ libraries):**

| # | Pattern | Example (bad → good) | Source typical |
|---|---------|----------------------|----------------|
| 1 | Artist embedded in title parens | `Title: "Strobe (Deadmau5)"` / `Artist: ""` → `Artist: "Deadmau5", Title: "Strobe"` | YouTube rips |
| 2 | "feat." in title, should be artist-or-artist-suffix | `Title: "Levels (feat. Ne-Yo)"` → keep in title OR migrate to `Artist: "Avicii feat. Ne-Yo"` (policy call) | Beatport edge cases |
| 3 | "X vs Y" / "X & Y" remix attribution in title | `Title: "Track (Artist1 vs Artist2 Remix)"` → leave title intact, parse remixer for tagging | SoundCloud, Bootleg |
| 4 | Double-encoded `Artist - Title (Artist Remix)` in title field | `Title: "Daft Punk - One More Time (Daft Punk Remix)"` / `Artist: "Daft Punk"` → `Title: "One More Time (Daft Punk Remix)"` | YouTube, mislabeled rips |
| 5 | Reversed `Title - Artist` | `Artist: "One More Time", Title: "Daft Punk"` → swap | Filename-derived imports |
| 6 | Track-number prefix in title | `Title: "01 - Intro"` → `Title: "Intro"` | Album rips |
| 7 | HTML-entity encoding | `Title: "Rock &amp; Roll"` → `"Rock & Roll"` | Web-scraped imports |
| 8 | Smart-quotes vs ASCII | `Title: "Don't Stop"` (U+2019) → `"Don't Stop"` (U+0027) | Beatport, copy-paste |
| 9 | Trailing/leading whitespace, double spaces | `"  Title  "` → `"Title"` | Universal |
| 10 | Casing inconsistencies | `"ARTIST NAME"` / `"artist name"` → titlecase per locale | SoundCloud uploads |
| 11 | Bracket style drift | `"Title [Original Mix]"` vs `"Title (Original Mix)"` | Beatport vs labels |
| 12 | Year/label tag in title parens | `"Title (2024 Remaster)"` | Reissues |
| 13 | Catalog number in title | `"Title (DEF123)"` → strip to `label`/`catno` field | Promo channels |
| 14 | Misencoded UTF-8 → Latin-1 → UTF-8 (mojibake) | `"Björk"` → `"Björk"` | Old Windows imports |

**Source-of-truth quality (anecdotal):** Beatport > Spotify > MusicBrainz > Discogs (UGC variance) > YouTube/SoundCloud (worst). Free + license-clean for derivative writes: MusicBrainz only.

**Existing metadata paths (codebase):**
- Read: `app/audio_tags.py:read_tags` — mutagen-based, ID3/MP4/Vorbis/RIFF/AIFF; filename fallback only when fields missing; handles `feat.`/`vs.`/`&`/`/` etc. as artist *splitters* (`app/database.py:_split_artists` line 271) — note: splitting != normalising.
- Write to file: `app/audio_tags.py:write_tags` — same matrix, format-agnostic key map at line 30.
- Write to DB: `app/database.py:update_tracks_metadata` (used via `app/main.py` routes, holds `_db_write_lock`).
- Filename fallback parsing: `app/audio_tags.py:_parse_filename` line 342 — naive ` - ` split.

**Backup-engine status:** removed in `8fe5036` (`refactor(backend): drop backup engine, routes, scheduler`). No replacement landed. The metadata fixer can't rely on a project-wide snapshot — must ship its own narrow before-state capture.

**Parsing strategy options:**
- **Pure regex/rules.** Deterministic, fast, auditable, no external deps. Coverage caps at ~70-80% of well-formed malformations; ambiguous cases (#5 reversed, #4 double-encoded with no anchor) need either a corpus check or external lookup.
- **LLM-assisted.** Local LLM via Ollama / OpenAI API for ambiguous cases. Cost + latency + non-determinism. Hard to audit. Probably acceptable behind a "review every suggestion" UI; unacceptable for batch-apply.
- **Hybrid (recommended below).** Regex first pass with confidence score; route low-confidence (<0.85) to external MusicBrainz lookup (ISRC if available, else fuzzy title+artist); fall back to "leave alone, surface in UI".

### 2026-05-15 — pattern catalogue + safety design after Problem framing

**Real-world pattern audit** (concrete before/after):

| # | Class | Before | After |
|---|-------|--------|-------|
| 1 | Artist-in-title parens | `Artist:"" Title:"Strobe (Deadmau5)"` | `Artist:"Deadmau5" Title:"Strobe"` |
| 2 | `feat.` wrong field | `Artist:"Avicii feat. Ne-Yo" Title:"Levels"` | policy A: keep / policy B: `Artist:"Avicii" Title:"Levels (feat. Ne-Yo)"` |
| 3 | Reversed Title-Artist (SC convention) | `Artist:"One More Time" Title:"Daft Punk"` | `Artist:"Daft Punk" Title:"One More Time"` |
| 4 | Track-num prefix | `Title:"01 - Intro"` | `Title:"Intro"` |
| 5 | HTML entities | `Title:"Rock &amp; Roll"` | `Title:"Rock & Roll"` |
| 6 | Smart-quotes | `Title:"Don’t Stop"` (U+2019) | `Title:"Don't Stop"` (U+0027) |
| 7 | Double-encoded remix | `Artist:"Daft Punk" Title:"Daft Punk - One More Time (Daft Punk Remix)"` | `Artist:"Daft Punk" Title:"One More Time (Daft Punk Remix)"` |
| 8 | Label+catalog marker | `Title:"Strobe [MAU5001]"` | `Title:"Strobe"` + `label:"mau5trap" catno:"MAU5001"` |

**Source-of-truth decision matrix.** Decision: **Rekordbox `DjmdContent` row is the editable copy**; ID3 tag is write-through, filename is read-only context. DB drives CDJ export and Rekordbox UI; tag drift is recoverable from DB but not vice-versa.

| Field | Authority | On conflict |
|-------|-----------|-------------|
| title / artist / album | DB | DB wins; tag rewritten |
| isrc | tag (canonical) | tag wins; DB filled if blank |
| filename | read-only | never mutated phase 1 |

**Safety architecture (4 layers).**

1. **Always dry-run preview** — detector emits proposed-changes set; nothing mutates until explicit Apply.
2. **Per-track confirm by default** — UI groups by rule, checkbox per track. Mass-apply needs an extra "I reviewed N tracks" gate.
3. **Snapshot-before-mutate** — backup-engine removed in `8fe5036`, so fixer captures (a) original ID3 block (raw, sidecar file) and (b) original `DjmdContent` row (JSON) into the undo log before any write.
4. **Append-only undo log** — sidecar SQLite at app-data dir (NOT `master.db`; avoids `_db_write_lock` contention). One row per field-mutation: `run_id, ts, track_id, rule_id, field, before, after, applied`. Revert = replay-in-reverse.

**MusicBrainz / Discogs match-cost.** Same constraint as remix-detector: MB 1 req/s per IP → 30k tracks ≈ 8h wall-clock. Mitigation: persistent cache keyed on `sha1(normalise(title)||"\x00"||normalise(artist)||"\x00"||normalise(album))` → canonical answer + ETag. Survives sessions, cuts repeat runs to delta-only. ISRC lookups (when tag has one) bypass fuzzy and are O(1) once cached.

### 2026-05-15 — codebase + external-source verification pass

**Codebase claims verified (with line numbers):**
- `app/audio_tags.py:read_tags` — confirmed line **355**. Mutagen-based, format-agnostic, filename fallback only when artist/title missing (lines 388–394). Returns dict of possibly-empty strings; never raises.
- `app/audio_tags.py:write_tags` — confirmed line **254**. Returns bool; format dispatched via `_DISPATCH` table at line 245. Best-effort: PermissionError (Rekordbox holding file) logged, returns False (lines 280–283).
- `app/audio_tags.py:_parse_filename` — confirmed line **342**. Naive ` - ` split for artist/title.
- `app/audio_tags.py:_READ_KEYS` — confirmed line **309**. Per-field candidate-key list. Drives `read_tags` probe order.
- `app/database.py:_split_artists` — confirmed line **268**. Splits on `,`, `&`, `/`, `;`, ` feat. `, ` ft. `, ` vs. `, ` with `. Calls `_normalize_artist_name` (line 274) per part — which already strips leading `01-` / `01.` prefixes (line 288). **Implication:** Class #6 (track-number prefix) partially handled today on **read path** for artist field only — fixer must also fix the **stored** title field.
- `app/database.py:update_tracks_metadata` — confirmed line **1007**. Loops `track_ids`, dispatches to `update_track_metadata` in live mode (no explicit `_db_write_lock` acquisition in this method itself — lock held by HTTP-route caller in `app/main.py`). **Implication:** fixer's batch path MUST acquire the lock at the route layer, not rely on this method.
- `commit 8fe5036` — verified via `git show`. Subject `refactor(backend): drop backup engine, routes, scheduler`. Deleted `app/backup_engine.py` (583 LOC) + `tests/test_backup_engine.py` (519 LOC). Removed routes: `POST /api/library/backup`, `GET /api/library/backups`, `POST /api/library/restore`, `GET /api/library/backup/<h>/diff`, `POST /api/system/cleanup`. `POST /api/library/sync` kept but live-mode is no-op now. Constraint stands: no project-wide snapshot.

**External-source claim verified (with citation):**
- MusicBrainz rate-limit policy — confirmed at `musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting`. Standard limit **1 req/s per IP**. User-Agent **header mandatory**; format `AppName/version ( contact-url-or-email )`. Anonymous / generic UAs (`Java`, `python-urllib`, `Jakarta Commons-HttpClient`, blank) classified anonymous → throttled into a shared 50 req/s pool across all such clients. Global cap 300 req/s. **Implication:** ship a dedicated UA string `MusicLibraryManager/<version> ( contact-email )`; pull contact from env or settings.

**Pattern-table example sanity-check (8-class catalogue from prior entry):**
| # | Before | Verified plausible? | Notes |
|---|--------|---------------------|-------|
| 1 | `Artist:"" Title:"Strobe (Deadmau5)"` | Yes | YouTube-rip pattern; empty artist + bracket-name common. Confidence: high — captured by `\(([^()]+)\)$` + empty-artist precondition. |
| 2 | `Artist:"Avicii feat. Ne-Yo" Title:"Levels"` | Yes | Beatport standard. Already split by `_split_artists` for views; question is whether to *normalise the stored field*. Policy split (A keep / B migrate) is the real call. |
| 3 | `Artist:"One More Time" Title:"Daft Punk"` | Yes | SC-import reversal. **Hardest class** — no anchor without external truth. Needs MB cross-check. M1 surfaces as "low-confidence suggestion" only. |
| 4 | `Title:"01 - Intro"` | Yes | Album-rip prefix. Regex `^\d{1,2}\s*[-.\s]\s*` already used in `_normalize_artist_name` (line 288). Reusable. |
| 5 | `Title:"Rock &amp; Roll"` | Yes | HTML-entity. `html.unescape()` one-liner. |
| 6 | `Title:"Don't Stop"` (U+2019) | Yes | Smart-quote. `str.translate({0x2019: 0x27, 0x2018: 0x27, 0x201C: 0x22, 0x201D: 0x22, 0x2013: 0x2D, 0x2014: 0x2D})`. NFC after. |
| 7 | `Artist:"Daft Punk" Title:"Daft Punk - One More Time (Daft Punk Remix)"` | Yes | Double-encoded remix. Anchor: title prefix equals artist (case-insensitive, post-trim). Strip prefix + ` - `. |
| 8 | `Title:"Strobe [MAU5001]"` | Yes | Label/catalog-no marker. Regex `\s*\[[A-Z]{2,5}\d{2,5}\]\s*$`. **Caveat:** can collide with `[Original Mix]`-style mix-name brackets — must whitelist-or-extract-then-strip, never blind-strip.

**Class-confidence ranking (for M1 high-precision subset):** 4, 5, 6, 1 → very high precision (pure pattern, no semantic ambiguity). 7 → high (anchor on artist string). 8 → medium-high (needs collision check). 2 → policy decision, not a precision question. 3 → low precision without MB (defer to M2).

### 2026-05-17 — higher-quality-bar rework

**Verified empirically:**
- **mutagen 1.47.0 ID3 round-trip byte-stability** (Python repro 2026-05-17): create `ID3()` with `TIT2 encoding=3`, `TPE1`, `TALB`, save → SHA1 `470849c1`. Re-open, set `TIT2 encoding=1 text='Different'`, save. Re-open, set `TIT2 encoding=3 text='Original Title (Mix)'`, save → SHA1 `470849c1` (identical), file size 5206 bytes, delta 0. **Conclusion:** ID3 revert-equality assertion can use raw-bytes SHA1 directly; raw-block sidecar is redundant for `.mp3`. Per-format check still needed for MP4/Vorbis/FLAC (M0 Gate). Goal text updated.
- **`_db_write_lock` usage in `app/main.py`** (grep 2026-05-17): `grep "db_lock\|_db_write_lock" app/main.py` → **0 matches** out of **85** `@app.{post,put,patch,delete}` route definitions. `app/main.py:897` (`update_track`) and `:927` (`batch_up`) both call `db.update_tracks_metadata` lock-free. CLAUDE.md / `route-architect.md` / `audio-stack-reviewer.md` invariant is aspirational, not enforced. Fixer routes must hold `db_lock()` — but also surfaces a parked retrofit task (out of scope, document gap, see PARKED).
- **Auth Phase 1 active**: `app/main.py:892` (`POST /api/track/{tid}`) and `:926` (`PATCH /api/tracks/batch`) carry `dependencies=[Depends(require_session)]`. `app/auth.py:95-115` enforces `Authorization: Bearer <token>` via `safe_compare`. No `X-Session-Token` fallback in the path.
- **PyPI dep verification** (2026-05-17): `musicbrainzngs==0.7.1` (released 2020-01-11 — 6yr stale), `httpx==0.28.1` (active). `requests==2.33.1` already at `requirements.txt:20`; `mutagen==1.47.0` at `:58`. Neither MB client lib currently pinned.
- **`app/anlz_writer.py:737`** `write_anlz_files(anlz_dir, track_path, analysis_result, filename_base, backup_existing)` — three-file emitter with built-in rolling backup (`_DEFAULT_BACKUP_KEEP`). This is the regen entry point if Open Q11 resolves to "fixer owns regen". No need to introduce new code paths.
- **Backup-engine removal** (`git show 8fe5036 --stat`): `app/backup_engine.py` (-583 LOC), `tests/test_backup_engine.py` (-519 LOC), `app/main.py` (-231 LOC), total `3 files changed, 8 insertions, 1333 deletions`. Routes confirmed removed: `POST /api/library/backup`, `GET /api/library/backups`, `POST /api/library/restore`, `GET /api/library/backup/<h>/diff`, `POST /api/system/cleanup`. `POST /api/library/sync` kept but live-mode no-op.

**Line numbers re-verified 2026-05-17** (post-Phase-1):
- `app/audio_tags.py:_DISPATCH` line **245**, `write_tags` line **254**, `_READ_KEYS` line **309**, `_parse_filename` line **342**, `read_tags` line **355**. Unchanged.
- `app/database.py:_db_write_lock` line **22**, `db_lock` context-manager line **25-26**, `update_tracks_metadata` line **1007**. `_split_artists` and `_normalize_artist_name` defs no longer match `^def` anchor (likely re-indented as methods inside the DB-singleton class — re-grep without anchor needed before M1, parked).

**Decision:**
- Open Q11 (ANLZ lazy vs fixer-owned regen) gets a sharper resolution rule: same-SHA pre/post mutation = fixer owns regen via `app/anlz_writer.py:737`; differing SHA = lazy works, fixer no-op.
- M0 Gate adds per-format tag round-trip test (only ID3 verified to date).
- Goal text on revert-equality corrected: ID3 IS byte-stable; raw-block sidecar is forensic for non-ID3 formats only.

## Options Considered

> Required by `evaluated_`. For each viable approach: sketch (2-4 lines), pros, cons, effort (S/M/L/XL), risk.

### Option A — Pure-regex rules engine, dry-run UI
- Sketch: catalogue 12-14 patterns from the table above as Python rule classes with `match() → (confidence, suggested_fix)`. UI lists matches grouped by rule, user selects, batch-apply through existing `database.update_tracks_metadata` path + `audio_tags.write_tags`. Audit log to JSONL.
- Pros: deterministic, fully offline, no API rate-limit drama, easy to unit-test, reviewable in diff.
- Cons: caps at ~75% of real malformations. Won't catch #5 (reversed) without external truth.
- Effort: M
- Risk: medium — wrong rule on a large library is still catastrophic. Confidence threshold + per-track confirm mandatory.

### Option B — Hybrid regex + MusicBrainz enrichment
- Sketch: Option A + MusicBrainz client. For low-confidence matches OR tracks with an ISRC tag, fetch canonical artist/title from MB. Compare local vs MB. Surface mismatches as suggestions, never auto-apply.
- Pros: catches the hard cases (reversed, mojibake, casing). ISRC path is highly accurate. Builds a reusable enrichment service.
- Cons: rate-limit (1 req/s) makes 30k-track sweep a ~8-hour job; need persistent cache. Network failure modes. Legal: MB is CC0, safe to write back.
- Effort: L
- Risk: medium — same blast radius, but more confidence on hard cases.

### Option C — LLM-assisted classification
- Sketch: send `{artist, title, filename}` to a local LLM (Ollama) or cloud API, ask for normalised pair + confidence. Apply only if confidence > threshold AND user confirms.
- Pros: handles arbitrary patterns including mojibake and reversed pairs in one shot.
- Cons: non-deterministic, hard to audit, latency 200-2000ms/track, cost if cloud, privacy if cloud. Cannot defend rule decisions in a code review.
- Effort: M (wiring) + ongoing tuning cost
- Risk: high — opaque mutations on user data.

### Option D — Surface-only (no mutation, just reporting)
- Sketch: ship the catalogue + detector. UI lists "probably malformed" tracks with suggestions. User edits manually through existing track-edit UI.
- Pros: zero blast-radius. Useful even as a phase-0 ship.
- Cons: solves only half the problem (the "what's wrong" half). User still does the work.
- Effort: S
- Risk: low.

### Option E — Per-track inline editor with regex-pre-fill
- Sketch: extend the existing track-edit UI with "Suggest fix" button per field. Runs the regex catalogue inline, pre-fills the form, user accepts/rejects/edits, single save uses the existing `update_tracks_metadata` path. No batch UI, no audit log infra beyond what already exists.
- Pros: smallest surface change. Reuses existing single-track confirm path. Zero new mutation primitive.
- Cons: doesn't scale past a few hundred manual edits. No mass-fix story. Audit-log absent (single-track edits aren't currently versioned).
- Effort: S
- Risk: low — but solves the wrong problem at library scale.

### Comparison matrix (decision support)

| Option | Coverage % (est.) | Wall-clock 30k tracks | New deps | Net LOC | Audit-log | False-pos risk |
|--------|-------------------|-----------------------|----------|---------|-----------|----------------|
| A regex-only | 70–80 | < 60s detector, apply gated by user | 0 | ~600 (3 files) | yes (sidecar SQLite) | 1–2% on subset {1,4,5,6,7,8} |
| B hybrid+MB | 88–95 | 8h MB sweep / 30k (1 req/s); incremental delta after cache | 1 (`httpx` or `musicbrainzngs`) | ~900 (A + MB client + cache) | yes | < 1% (MB cross-check) |
| C LLM | 90+ (opaque) | 200–2000ms/track → 1h–16h; non-deterministic | 1+ (Ollama or cloud SDK) | ~400 | yes but un-auditable rules | unknown — opaque |
| D detect-only | n/a (no mutation) | < 60s scan | 0 | ~300 | n/a | 0 (no writes) |
| E inline editor | n/a (manual per track) | unbounded human-time | 0 | ~150 | none new | 0 (user-confirmed) |

Coverage estimates: A from 8-class catalogue precision-rank (Findings 2026-05-15 §codebase verification — 4 classes very-high, 2 high, 1 medium, 1 policy = ~75% recall on real lib by class-frequency weight, not corpus weight). B uplift assumes MB ISRC hit-rate ≥ 70% on libraries with ISRC tags (Finding 2026-05-15 cites MB CC0 + 1 req/s; ISRC fast-path O(1) after cache). C is bibliographic — no in-repo measurement.

## Recommendation

Phased, each milestone shippable independently. Each phase has explicit deliverables + a gate that must be green to start the next.

### M0 — Detector + report only (Option D)
**Deliverables**
- `app/metadata_fixer/__init__.py` (empty package marker) + `app/metadata_fixer/detector.py` — class hierarchy for 8 rule classes; each rule has `match(track) → (confidence: float, suggested: dict)`; **no writes**.
- New route `GET /api/metadata-fixer/scan` returning `{run_id, matches: [{track_id, rule_id, confidence, before, suggested}]}`. Auth note: GET is read-only — `Depends(require_session)` optional per `coding-rules.md` (mutation-only gate), but recommend including it anyway to keep contract uniform across the fixer API.
- Read-only frontend view `frontend/src/components/MetadataFixerReport.jsx` listing matches grouped by rule with counts; CSV export.
- Test corpus: synthetic 500-track JSON fixture covering all 8 classes.
- `pytest tests/test_metadata_fixer_detector.py` — exact test signatures:
  - `def test_class1_artist_in_parens_precision_recall()` — asserts `>= 0.95` precision + `>= 0.95` recall against 100 seeded class-1 + 100 negative samples.
  - `def test_class4_track_num_prefix_strips_only_leading()` — confirms `"01 - Intro"` → `"Intro"` and `"Track 01"` (no leading) → unchanged.
  - `def test_class5_html_entities_unescape_idempotent()` — applies `html.unescape` twice, asserts second pass = first.
  - `def test_class6_smart_quotes_to_ascii_then_nfc()` — `"Don’t"` → `"Don't"`, NFC-normalised.
  - `def test_class7_double_encoded_anchor_match_case_insensitive()` — `"Daft Punk - One More Time"` with artist `"daft punk"` → strip.
  - `def test_class8_catalog_no_bracket_strip_collision_safe()` — `"Title [MAU5001]"` strip; `"Title [Original Mix]"` preserved.
  - `def test_detector_full_500_corpus_per_rule_precision_recall()` — fixture-driven, per-rule SLO.
  - `def test_detector_zero_writes_smoke()` — runs detector on 500-track corpus, asserts `master.db` mtime unchanged + no file in `ALLOWED_AUDIO_ROOTS` mtime-changed.

**Pseudocode — first ~30 LoC of `app/metadata_fixer/detector.py` (catalogue scaffold):**

```python
# app/metadata_fixer/detector.py
"""Read-only detection of malformed artist/title metadata.
No writes. No filesystem touches outside read_tags().
"""
from __future__ import annotations
import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

# Smart-quote translation map (Class 6). NFC after.
_SMART_QUOTES = {0x2019: 0x27, 0x2018: 0x27, 0x201C: 0x22, 0x201D: 0x22, 0x2013: 0x2D, 0x2014: 0x2D}

# Reused from app.database._normalize_artist_name (line 288 leading "NN - " strip).
_TRACK_NUM_PREFIX_RE = re.compile(r"^\d{1,2}\s*[-.\s]\s*")

# Class-8 catalog bracket; whitelist mix-name brackets to avoid collision.
_CATALOG_BRACKET_RE = re.compile(r"\s*\[[A-Z]{2,5}\d{2,5}\]\s*$")
_MIX_NAME_WHITELIST = {"original mix", "extended mix", "radio edit", "club mix", "dub mix"}

@dataclass(frozen=True)
class Match:
    rule_id: int           # 1..8 per Findings table
    confidence: float      # 0.0..1.0
    before: dict[str, str] # {"title": ..., "artist": ...}
    suggested: dict[str, str]

@dataclass(frozen=True)
class Rule:
    rule_id: int
    name: str
    match_fn: Callable[[dict], Match | None]
```

Followed by 8 `def _rule_{1..8}(track: dict) -> Match | None` functions registered into `_CATALOGUE: list[Rule]`. `scan(tracks)` iterates the catalogue; never mutates `track`.

**Gate to M1:** corpus precision ≥ 95% for classes {1, 4, 5, 6, 7, 8}; user acks scan report on real library; Open Q9 (NFC vs NFKC) decided; **Open Q10 empirical sample captured** (5 `feat.` tracks exported, ANLZ+PDB inspected) — unblocks Class {2} policy at M1 start; **Open Q11 ANLZ regen-vs-lazy decided** via mutate → Rekordbox-reload → `ANLZ0000.DAT` SHA diff on one fixture track (same SHA = stale = fixer owns regen; different SHA = Rekordbox regenerated, fixer no-op); **per-format tag round-trip test** for MP4/Vorbis/FLAC (ID3 already verified byte-stable 2026-05-17) — if any format drifts, raw-block sidecar is mandatory for that format's revert path.

### M1 — Apply path (Option A)
**Deliverables**
- `app/metadata_fixer/applier.py` — atomic per-track mutation. Order: (1) compute `before_sha1 = sha1(open(file,'rb').read())` for tag-bytes; (2) snapshot `DjmdContent` row to sidecar; (3) `with db_lock(): db.update_tracks_metadata([tid], updates)`; (4) `audio_tags.write_tags(path, updates)`; (5) compute `after_sha1`; (6) commit row to `metadata_fixer_log.db`.
- New routes (all in `app/main.py`, holding `db_lock()` on writers): `POST /api/metadata-fixer/apply` (body `{run_id, accepted: [{track_id, rule_id}]...}`), `GET /api/metadata-fixer/runs`, `POST /api/metadata-fixer/revert` (body `{run_id}` or `{mutation_ids: [...]}`). All POSTs declare `dependencies=[Depends(require_session)]` (verified Phase 1 active — pattern same as `app/main.py:892, 926`).
- Sidecar SQLite schema (in `metadata_fixer_log.db`):
  - `runs(run_id TEXT PRIMARY KEY, started_ts INTEGER, finished_ts INTEGER, rule_subset TEXT, user_note TEXT)`
  - `mutations(mutation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, track_id TEXT, rule_id INTEGER, field TEXT, before TEXT, after TEXT, db_row_before TEXT, tag_sha1_before TEXT, tag_sha1_after TEXT, applied_ts INTEGER, reverted_ts INTEGER NULL, FOREIGN KEY(run_id) REFERENCES runs(run_id))`
- Frontend: extend M0 view with per-track checkboxes, rule grouping, "Apply selected (N)" with extra "I reviewed N tracks" modal gate for N > 50.
- Default-active rule subset: high-precision classes {1, 4, 5, 6, 7, 8} only. Class {2} surfaced as suggestion only (policy unresolved). Class {3} disabled (needs M2 MB cross-check).
- LLM-suggest button (Option C role): per-track only, surfaces proposal into the same accept-flow. No batch.
- Tests (exact pytest signatures):
  - `def test_apply_holds_db_write_lock()` — patches `db_lock` to a `MagicMock` ctxmgr, asserts `__enter__` called once per route invocation.
  - `def test_apply_then_revert_db_row_byte_identical_json()` — assert `json.dumps(row_after_revert, sort_keys=True) == db_row_before` for all 500 corpus tracks.
  - `def test_apply_then_revert_tag_sha1_equal_id3()` — `tag_sha1_before == sha1(open(path,'rb').read())` after revert; ID3 only (verified byte-stable 2026-05-17).
  - `def test_apply_then_revert_tag_sha1_equal_per_format(format)` — parametrised on `["mp3", "flac", "m4a", "ogg"]`; xfail format ≠ mp3 unless M0 Gate per-format check passes.
  - `def test_no_mutation_without_explicit_apply_click()` — E2E via `e2e-tester` subagent: open MetadataFixerReport with detected matches, navigate away, assert `master.db` mtime + `metadata_fixer_log.db` row-count unchanged.
  - `def test_pdb_structure_green_after_mass_fix()` — runs M1 apply over 100-track corpus + invokes USB exporter on a tmp drive image, calls existing `tests/test_pdb_structure.py::test_*` against the result.

**Expected git diff scope:** new files `app/metadata_fixer/__init__.py`, `app/metadata_fixer/detector.py` (~250 LOC), `app/metadata_fixer/applier.py` (~200 LOC), `app/metadata_fixer/schema.py` (~50 LOC for sidecar DDL), `tests/test_metadata_fixer_detector.py` (~300 LOC fixtures + assertions), `tests/test_metadata_fixer_applier.py` (~150 LOC), `frontend/src/components/MetadataFixerReport.jsx` (~300 LOC), `frontend/src/api/metadataFixer.js` (~60 LOC axios wrappers). Modified: `app/main.py` (+~80 LOC for 3 routes + lock acquisition), `requirements.txt` (no change for M0/M1; +1 line at M2 for `httpx==0.28.1`), `docs/backend-index.md` (+3 route entries), `docs/frontend-index.md` (+1 component entry).

**Gate to M2:** on real-library dry-run, applied-rule false-positive rate ≤ 2% (user-reported); revert verified once on a real run; `pytest tests/test_pdb_structure.py` green after a mass-fix touches USB re-export.

### M2 — MusicBrainz enrichment (Option B)
**Deliverables**
- `app/metadata_fixer/musicbrainz_client.py` — client lib **TBD at draftplan** (Open Q12: `musicbrainzngs==0.7.1` vs `httpx.AsyncClient` + hand-rolled token-bucket). Either path: `User-Agent: MusicLibraryManager/<version> ( <contact-from-settings> )` mandatory, 1 req/s ceiling, retry-on-503 with `Retry-After` respect. Dep-add (`musicbrainzngs` or `httpx`) requires explicit user approval per `agentic-mode.md` "Confirm first".
- Persistent cache table in `metadata_fixer_log.db`: key = `sha1(normalise(title)||"\x00"||normalise(artist)||"\x00"||normalise(album))`; value = canonical JSON + ETag + cached-at.
- ISRC fast-path: if track has ISRC, lookup-by-ISRC bypasses fuzzy; O(1) after first hit.
- Enable Class {3} (reversed pairs) as confidence-scored suggestion, never auto-apply.
- Settings UI: contact-email field (required to enable MB), enable-MB toggle.
- Tests: 50-track hand-curated ISRC corpus, ≥ 95% MBID-match accuracy.

**Gate to graduate to `inprogress_`:** explicit user sign-off (per pipeline rules — agent may not promote unilaterally).

**Cross-phase invariants** (apply to all milestones)
- DB writes only inside `_db_write_lock`-held route handlers.
- No mutation reachable without explicit user Apply.
- Every mutation produces exactly one undo-log row.
- No `master.db` writes to the audit log (sidecar SQLite only).
- LLM never batch-applies. Single track, single click, single confirm.

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
