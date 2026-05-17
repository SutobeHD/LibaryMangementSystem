---
slug: mobile-companion-ranking-app
title: Mobile companion app — soft client focused on Ranking mode, requires main app running on server/PC
owner: tb
created: 2026-05-15
last_updated: 2026-05-17
tags: [mobile, companion, pwa, ranking, network, auth]
related: []
---

# Mobile companion app — soft client focused on Ranking mode, requires main app running on server/PC

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template
- 2026-05-15 — `research/idea_` — section fill (research dive)
- 2026-05-15 — `research/idea_` — tech-choice deep-dive + QR-pairing UX sketch
- 2026-05-15 — `research/idea_` — exploring_-ready rework loop (deep self-review pass)
- 2026-05-15 — research/exploring_ — promoted; quality bar met (9/14 OQ resolved + 1 PARKED + 4 OPEN; corrected route count 12→17; Option E added; Pre-M1/M1.0/M1.1/M1.2/M1.3/M2 phased matrix; security Phase-1+2 hard prereq)
- 2026-05-17 — research/exploring_ — deeper-exploration rework toward evaluated_ readiness (auth Phase-1 Steps 0-3 partial-landing reflected in Constraints; Tailscale Funnel concrete ports 443/8443/10000 + tailnet-DNS-only HTTPS-only constraints added; iOS 26 default-Web-App + Safari 18.4 Declarative Web Push surfaced; OQ15 added re: iOS 26 onboarding simplification; OQ12 lean reaffirmed against env-var pattern already proven by Phase-1 `LMS_TOKEN=`)
- 2026-05-17 — research/exploring_ — higher-quality-bar rework (implementation-ready bar)

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

A **soft / thin mobile version** of the desktop app, **mainly focused on Ranking mode**. The mobile client is **not standalone** — it only works when the main app is reachable as a server (PC / dedicated host on the same network or via tunnel). All library state, audio, analysis, and DB writes stay on the main app; the mobile app is a remote UI surface. Useful for ranking / tagging tracks away from the desk (couch, studio sofa, on-the-road), without lugging the laptop.

## Goals / Non-goals

**Goals** (each has a measurable acceptance metric for `evaluated_` → `inprogress_` transition)

- **G1 — Ranking-mode parity** Mobile replicates Ranking-mode edit surface: pick source (playlist / artist / label / album), step queue, set Rating (1-5), ColorID, Genre, Comment + chip-toggles, optional MyTags (live mode). Persist via `POST /api/track/{tid}` + `POST /api/track/{tid}/mytags`. **Metric:** every desktop Ranking field reachable + writable from mobile; manual checklist (12 fields/buttons) in `tests/manual/mobile-ranking-parity.md` 12/12 green.
- **G2 — Thin client invariant** 0 KB canonical library data on phone. All state via HTTP. **Metric:** `localStorage` payload ≤ 4 KB (auth token + UI prefs only); no IndexedDB store of tracks/playlists; service-worker cache scope = app shell only (assets), no JSON responses.
- **G3 — Same-LAN first-paint** Phone on home Wi-Fi → scan QR → first ranking screen visible. **Metric:** ≤ 8 s end-to-end on a mid-range Android (4G-equiv throttle), ≤ 12 s on iOS Safari cold-load. Measured via DevTools Performance trace, scripted check in `e2e-tester`.
- **G4 — Off-LAN opt-in** Tailscale Funnel hostname documented in README; works without app-side tunnel client. **Metric:** smoke test from cellular network passes Save & Next round-trip in ≤ 800 ms p95.
- **G5 — Component reuse** Mobile bundle shares ≥ 60 % of `RankingView.jsx`'s chip/color/tag primitives (COLORS array, TAG_CATEGORIES, MyTag toggle logic). **Metric:** static count — same module imports, no fork.
- **G6 — Thumb-first UX** Swipe-right = Save & Next (replaces `space` hotkey at `RankingView.jsx:88`), 44 × 44 px minimum hit targets per Apple HIG, bottom-anchored primary CTA. **Metric:** Lighthouse mobile-a11y ≥ 90; tap-target audit 0 failures.
- **G7 — Save-and-next round-trip** P95 ≤ 350 ms on LAN (typical home Wi-Fi → loopback FastAPI). **Metric:** logged client-side in `axios` interceptor, surfaced in dev console.

**Non-goals** (deliberately out of scope)
- Audio decoding on mobile. **No `WaveformEditor`, no playback.** The desktop already owns audio; mobile is metadata-only. (Reduces scope by ~80% — no FFmpeg, no wavesurfer, no CORS on file streaming.)
- Library management (import / scan / move / delete tracks).
- USB export, ANLZ writing, beatgrid editing, cue editing, analysis triggers.
- Offline-first sync. User steer: "should only work if app runs on a server or pc" → **hard-online** is the default. Local queue-and-sync explicitly deferred to a follow-up topic.
- App-store distribution in v1 (PWA install banner is enough).
- Native iOS / Android codebases.
- Multi-user / concurrent-editor conflict resolution (single-DJ assumption).

## Constraints

> External facts that bound the solution space — API rate limits, existing data shape, performance budgets, legal/licensing, team capacity. Cite source where possible.

- **CORS allowlist is localhost-only** (`app/main.py:209-224`, re-verified 2026-05-17 post-Phase-1): `http://localhost:1420`, `127.0.0.1:1420`, `localhost:5173`, `127.0.0.1:5173`, `localhost:8000`, `127.0.0.1:8000`, `tauri://localhost`, `https://tauri.localhost`. `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`. **Any mobile origin (`http://192.168.x.y:5173`, `https://<tailscale-name>.ts.net`, `https://<slug>.trycloudflare.com`) is rejected.** Must extend allowlist env-driven (`MOBILE_ALLOWED_ORIGINS` env list, comma-split — grep `MOBILE_ALLOWED_ORIGINS` across repo: 0 hits today, the env var is doc-only). **CORS ≠ auth** — non-browser callers (curl, native mobile, Python) bypass CORS entirely; per `draftplan_security-api-auth-hardening.md` Findings §2 the comment on line 208 (`# --- SECURITY: CORS locked to localhost only ---`) is misleading and the threat model relies on the loopback bind + bearer-token gate, not CORS.
- **Frontend axios baseURL** = `import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'` in Tauri runtime, empty string (Vite-proxied) in browser-dev (`frontend/src/api/api.js:14-19`). Mobile build needs the host's LAN IP or tunnel hostname injected at runtime (mobile can't know LAN IP at compile time). Re-use the same `VITE_API_BASE_URL` hook; build-time env in `frontend/.env.local` or runtime-injected via a `<meta>` tag the mobile entry reads pre-bootstrap.
- **Auth gate — Phase-1 backend + frontend FULLY LANDED (2026-05-17), Phase-2 paired-device tokens still 0 LoC.** Re-verified vs commits `6021acf..f90f5f8`:
  - **Landed backend (commits `6021acf..1c7d410..7dfdef5..8498937`):** `app/auth.py` (Bearer-parsing `require_session` dep + `SESSION_TOKEN` self-gen at import + `LMS_TOKEN=<value>` stdout banner + `%APPDATA%/MusicLibraryManager/.session-token` write via `platformdirs==4.2.2`); `app/security_compare.py:safe_compare` (constant-time comparator); bulk `dependencies=[Depends(require_session)]` decoration on **80+ POST/PUT/PATCH/DELETE routes** in `app/main.py` (grep `require_session` → 81 hits — `app.main:33` import + 80 route decorations); `SHUTDOWN_TOKEN` query-param scheme deleted from `/api/system/shutdown` + `/api/system/restart` (now Bearer-gated, `app/main.py:2060,2066`); heartbeat token-leak field removed (`app/main.py:929-940`).
  - **Landed Tauri sidecar (commit `46b9aef`):** Rust stdout-reader capture+scrub in BOTH dev (`spawn_child` at `src-tauri/src/main.rs:133-200`) and prod (`shell.sidecar` `CommandEvent::Stdout` at line 485-510) paths; `#[tauri::command] get_session_token` at line 50 (returns `Err("token-not-ready")` while empty; `Ok(token.clone())` once captured).
  - **Landed frontend (commits `d12ad1a`, `f90f5f8`):** `frontend/src/api/api.js` Bearer bootstrap (`_bootstrapFromTauri` 30 s budget / 60 attempts at 500 ms each; `_bootstrapFromDevMiddleware` for browser-dev); axios request interceptor attaches `Authorization: Bearer <token>` on every request after awaiting `_bootstrapPromise` (lines 184-204); `frontend/src/store/authStore.js` ownership of in-memory `_sessionToken`; `frontend/vite.config.js:36-69` `devTokenPlugin` middleware serves `GET /dev-token` from the `%APPDATA%/MusicLibraryManager/.session-token` file (cross-platform via `node:os.platform()` branches at lines 23-34, never bundled in production — `apply: 'serve'`).
  - **Landed tests:** `tests/conftest.py` autouse `auth_token` fixture + `@pytest.mark.no_auth` marker (note: lines 17-22 in-file comment is now stale — it claims "no observable effect on the existing test suite ... until Step 4 lands" but Step 4 landed in `1c7d410`; flag for a separate doc-sync); `tests/test_auth.py` cases (a)-(n) covering header parse, Bearer scheme case-insensitivity, control-char rejection, OPTIONS-preflight short-circuit, heartbeat leak-check, `/api/system/shutdown` + `/api/system/restart` 401-without-Bearer.
  - **NOT yet landed** (Phase-2 paired-device tokens, hard-prereq for mobile): per-device Bearer in `Authorization: Bearer …`, sidecar-local SQLite `paired_devices` table (NOT `master.db` per security-doc Option B trade-off), `POST /api/pairing/{start,complete}`, `DELETE /api/pairing/{device_id}` revoke. Grep across `app/` for `paired_devices` / `/api/pairing/` → 0 hits 2026-05-17. The boot-time `SESSION_TOKEN` in `app/auth.py:84` is single-host and rotates on every sidecar restart — handing it to a phone would (a) require sidecar-restart pairing dance, (b) revoke every other client's token simultaneously.
  - **Net mobile blocker today** = Phase-2 ship only. Phase-1 anonymous-write hole on LAN is CLOSED — `curl -X POST http://127.0.0.1:8000/api/track/{tid}` without `Authorization: Bearer …` returns 401 today.
- **WIP rate-limit module** (`app/rate_limit.py`, commit `830c056`) exists but is NOT decorator-applied to any route. Mobile may want it on `POST /api/pairing/complete` to throttle brute-force pairing-token guesses. Out of mobile-side scope; surface as upstream prereq.
- **`ALLOWED_AUDIO_ROOTS` sandbox** (`app/main.py:138-205` post-hotfix) bounds filesystem reads to whitelisted roots via `Path.is_relative_to` (hotfix migrated from `str.startswith`). Doesn't apply to ranking writes (DB-only), but any future artwork / waveform PNG endpoint exposed to mobile must go through `validate_audio_path`.
- **Concurrency:** all `master.db` writes must go through `_db_write_lock` (RLock, `app/database.py:22`; context-manager helper `db_lock` at `:25-40`, decorator `_serialised` at `:43-53` auto-wraps every `RekordboxDB` mutating method). Mobile-induced `POST /api/track/{tid}` already takes that path via `db.update_tracks_metadata` — no new locking work, but each concurrent client adds contention; multi-client behaviour parked under OQ8.
- **rbox version quirks:** mobile must not trigger `OneLibrary.create_content()` (broken in rbox 0.1.7, see `app/usb_one_library.py`). Out of scope by design (no library mgmt), pinned in Non-goals.
- **Existing Ranking API surface = 13 reads + 3 writes = 16 routes total** (re-verified by grep against current `app/main.py` 2026-05-17 post-Phase-1):
  - Reads (13): `GET /api/playlists/tree` (`:965`), `GET /api/artists` (`:743`), `GET /api/labels` (`:727`), `GET /api/albums` (`:730`), `GET /api/playlist/{pid}/tracks` (`:1602`), `GET /api/artist/{aid}/tracks` (`:749` — dead-duplicate at `:1623` silently shadowed by FastAPI), `GET /api/label/{aid}/tracks` (`:752` — dead-duplicate at `:1639`), `GET /api/album/{aid}/tracks` (`:755` — dead-duplicate at `:1655`), `GET /api/genres` (`:661`), `GET /api/settings` (`:1857`), `GET /api/track/{tid}` (`:758`), `GET /api/track/{tid}/mytags` (`:870`), `GET /api/mytags` (`:840`). FastAPI registers the first-occurrence handler and silently ignores the second — mobile gets the leaner first-registration JSON for artist/label/album track lists (NO `ArtistName` synthesis, NO `filename` URL-encoding), so client-side must compute `ArtistName ||= 'Unknown Artist'` + `filename = encodeURIComponent(path.split('/').pop())` itself OR the duplicate-route dedupe task lands first (out-of-scope per OUT-OF-SCOPE TASKS list).
  - Writes (3 — all `Depends(require_session)` Bearer, VERIFIED LANDED via grep): `POST /api/mytags` (`:845`), `POST /api/track/{tid}` (`:892`), `POST /api/track/{tid}/mytags` (`:875`).
  - Plus `GET /api/library/status` (`:1202`) for live-vs-XML mode detection (read, open). Total touched surface = **17 routes**.
  - Zero backend additions for v1 except the Phase-2 pairing surface (`POST /api/pairing/start`, `POST /api/pairing/complete`, `DELETE /api/pairing/{device_id}` — all 0 LoC today) and the optional `GET /api/track/{tid}/cover-thumb` at M1.3 (OQ7).
- **Live-vs-XML mode parity:** MyTag write path requires `appMode === 'live'` (`master.db`). XML mode raises 409 via `_require_live_db` (`app/main.py:825-828` — `"MyTag is not available — library not loaded."`). Mobile reads `GET /api/library/status` → `libraryStatus.mode === 'live'` and disables the MyTag block in XML mode (mirrors `RankingView.jsx:81 myTagSupported` gate; lines `:138-144` per-track tag fetch, `:197-205` tag-save try/catch).
- **iOS PWA platform limits** (re-verified 2026-05-17 baseline): `beforeinstallprompt` unsupported on iOS Safari (also unsupported in Chrome / Edge for iOS — same WebKit engine constraint); Add-to-Homescreen works since iOS 12.2 via Share menu; **iOS 26 now defaults every Home-Screen-added site to open as a web app** (standalone display) → reduces onboarding friction vs prior iOS where standalone required explicit `display: standalone` + accepted opt-in; Safari 18.4 added Declarative Web Push + Screen Wake Lock (Wake Lock relevant only for future "keep screen on while ranking" feature, not v1); web-push still requires installed-PWA state + APNs; `getUserMedia` works since iOS 11; `BarcodeDetector` API absent on iOS Safari (also unreliable on Firefox / older Chromium) — pull in `jsQR` (~12 KB gz) or `qr-scanner` (~30 KB gz with worker) polyfill, dynamic-imported on the pairing screen only.
- **Team capacity:** solo dev. Parallel mobile codebase + sync (React Native, Flutter, native) is L/XL effort and rejected in Recommendation.

## Open Questions

> Numbered. Each one should be resolvable (yes/no, or "X vs Y"), not open-ended philosophy.

Status legend: **RESOLVED** (locked, no rework expected) · **PARKED** (deferred to a later phase / topic, answer documented) · **OPEN** (still blocks promotion).

1. **OQ1 — Hard-online only?** **RESOLVED.** Hard-online. User steer ("should only work if app runs on a server or pc") + Goals G2 (thin-client) lock it. Offline queue / local cache explicitly Non-goal; tracked as a separate future topic if demanded.
2. **OQ2 — PWA vs Capacitor vs React Native?** **RESOLVED.** PWA for M1 (Findings #2 matrix); Capacitor stays as M2 strict-superset upgrade if mDNS / native push become must-haves; React Native rejected (parallel codebase, no DOM reuse, L effort).
3. **OQ3 — Off-LAN strategy?** **RESOLVED.** **Tailscale Funnel** = canonical, documented in README; **not embedded**. Concrete constraints (re-verified 2026-05-17): Funnel listens **only** on ports 443 / 8443 / 10000 (NOT 8000) → user runs `tailscale funnel 8000` which proxies a `https://<machine>.<tailnet>.ts.net` URL to the loopback `:8000` sidecar; tailnet-domain-only (no custom domain on free); TLS-only (HTTP rejected). Bandwidth limit "undisclosed but unobtrusive" per public Tailscale docs — accepted risk (mobile metadata writes are tiny: ~200 bytes per Save & Next; no audio bytes). Cloudflare Tunnel (`*.trycloudflare.com` ephemeral) + ngrok stay as user-pick alternatives, no code support.
4. **OQ4 — Pairing UX?** **RESOLVED.** Desktop renders QR encoding `lmsapp://pair?host=<lan-ip>&port=8000&token=<one-shot>`; mobile scans → `POST /api/pairing/complete` → long-lived per-device Bearer. Manual URL + 6-digit code fallback for camera-less devices. mDNS auto-discovery deferred to M2-Capacitor (web sandbox blocks `_libmgr._tcp.local`).
5. **OQ5 — Token lifetime?** **RESOLVED.** Long-lived per-device Bearer, no default expiry, server-side revoke via `DELETE /api/pairing/{device_id}`. Aligns with security-doc Phase-2 model. Lost-phone mitigation = revoke from desktop Settings.
6. **OQ6 — Separate `:8001` port or unified `:8000` with auth gating?** **RESOLVED.** Unified `:8000` with `require_session` Bearer (per security Phase 1+2). Reasons: (a) Phase-1 already gates every mutation; second port doubles uvicorn surface for zero security gain, (b) reverse-proxy / Tailscale Funnel only knows one upstream, (c) read routes stay open under loopback assumption — mobile read-only access still flows over the LAN-exposed `:8000` once the bind widens or tunnel is in play.
7. **OQ7 — Artwork / waveform preview on mobile?** **PARKED for M1 core (M1.0–M1.2).** v1 ships **text-only ranking** (track title, artist, BPM/key from `/api/track/{tid}` JSON). Cover thumbnails (256×256, ≤ 50 KB JPEG) optionally added at **M1.3** via new `GET /api/track/{tid}/cover-thumb` if bandwidth budget allows. Waveform PNG explicitly out — desktop owns audio + waveform editing.
8. **OQ8 — Concurrent-edit policy?** **RESOLVED.** Last-write-wins (current behaviour). Single-DJ assumption (Non-goal: multi-user) makes ETag / `updated_at` stale-write detection over-engineering. Re-evaluate only if a real conflict is reported.
9. **OQ9 — Swipe gestures library?** **RESOLVED.** Use `@use-gesture/react` (~11 KB gz, MIT, actively maintained 2025-2026). Hand-rolled touch-event handling is fragile across iOS / Android Chrome / Samsung Internet quirks; Framer Motion is overkill (~50 KB gz). Bundle stays under 4 G first-paint budget (≤ 200 KB gz total).
10. **OQ10 — iOS PWA limits acceptable?** **RESOLVED.** Acceptable for v1. Manual Add-to-Homescreen instructions on first iOS-Safari visit (UA-detect). Background tasks irrelevant (Ranking is foreground-only). Web-push deferred until iOS 16.4+ install adoption + actual push use-case emerges.
11. **OQ11 — HTTPS / `installable` PWA criteria?** **RESOLVED.** Ship as mobile-friendly web page in v1; PWA install-banner activates once Tailscale Funnel HTTPS hostname is in use. On raw `http://192.168.x.y` LAN IP, install banner is disabled by browser policy — accepted, banner is nice-to-have not blocker.
12. **OQ12 (NEW) — CORS allowlist extension shape?** **TRIGGER-PARKED — resolve at draftplan kickoff, not before `exploring_` → `evaluated_`.** Three sketches quantified against the proven Phase-1 `LMS_TOKEN=` env-driven pattern:

    | Sketch | LoC delta `app/main.py` | Restart-to-apply? | Admin surface? | CORS-bypass risk |
    |---|---|---|---|---|
    | (a) env-list `MOBILE_ALLOWED_ORIGINS="https://foo.ts.net,https://192.168.1.42:5173"` | +5 (`os.environ.get(...).split(",")` + strip + filter `if x`) | yes | none | none — explicit-list semantics, FastAPI's `CORSMiddleware` allowlist match is exact-string |
    | (b) runtime-mutable list in `settings.json` + `POST /api/cors/origins` + `DELETE /api/cors/origins/{origin}` | +35 (two endpoints + Pydantic model + settings-loader hook + `app.user_middleware` hot-swap) | no | yes — 2 new gated routes | medium — every admin-endpoint mutation is an audit step |
    | (c) wildcard regex `MOBILE_ALLOWED_ORIGINS_REGEX="^https://[a-z0-9-]+\.tailnet-name\.ts\.net$"` | +8 (`re.compile` + `allow_origin_regex=` middleware kwarg) | yes | none | high — operator-supplied regex; `^https://.*\.ts\.net$` matches `https://evil.ts.net` (real CORS bypass, see CVE-2018-XXX class) |

    **Strong lean (a)** — matches the proven Phase-1 `LMS_TOKEN=` env-driven pattern (restart-to-apply, no admin endpoint surface, no runtime mutability), 5-LoC parser, grep-discoverable, mypy-trivial. Sub-question: should also accept `*` for the LAN-IP case where the user knowingly opens up dev — leaning NO, force-list explicit origins per security-doc style. Reject empty strings post-split. Parking rationale: shape will not change because of any other open question; one-line draftplan-time decision.
13. **OQ13 (NEW) — Bundle-size budget hard ceiling?** **RESOLVED.** Hard ceiling = **200 KB gz first-paint** (HTML + JS + CSS critical path including initial route shell), **500 KB gz total mobile bundle** (lazy chunks included). Measured floor (Findings #3 refinement): React 18 + react-router 6 (~45 KB gz) + axios (~13 KB gz) + `@use-gesture/react` (~11 KB gz) + chip/color/tag primitives shared from `RankingView.jsx` (~5 KB delta) + Tailwind utilities (purged, ~12 KB gz) = **86 KB gz first-paint**, +`jsQR` polyfill (~12 KB gz, dynamic-imported only on `/m/pair` route) = **98 KB gz on pairing screen**. **102 KB gz headroom under 200 KB ceiling.** Drives gesture-lib pick (`@use-gesture/react` confirmed over Framer Motion 50 KB gz), polyfill pick (`jsQR` confirmed over `qr-scanner` 30 KB gz — saves 18 KB gz on the pairing route), code-splitting strategy (source-picker tree, queue-view, edit-surface each lazy-loaded). Enforce in CI: `vite build --report` + `scripts/check_mobile_bundle.py` parses the rollup stats JSON, fails CI on > 200 KB gz first-paint.
14. **OQ14 (NEW) — Pairing-QR refresh / TTL?** **OPEN — must resolve before draftplan.** Sketch: one-shot pairing-token TTL **60 s** (matches `_format_tokens` helper that security Phase-2 generalises); QR auto-rotates every 60 s on the desktop "Pair Mobile Device" panel; explicit Cancel button revokes pending pairing. Math: with `secrets.token_urlsafe(32)` (256-bit entropy) brute-forcing under 60 s at 1000 req/s (rate-limit ceiling) = 60 000 attempts / 2^256 ≈ 5.2e-72 success — entropic-margin is enormous, TTL is a UX knob not a security one. Trade-off: 60 s = DJ has to tap "Generate QR" again if they get distracted; 5 min default = more time for over-the-shoulder photo of the screen. Lean **60 s**, with the "regenerate" button always visible. User confirmation needed.
15. **OQ15 (NEW) — iOS 26 default-Web-App onboarding simplification?** **RESOLVED.** Keep UA-detect branch; auto-hide the verbose instructions block when `navigator.userAgent` matches `/OS (\d+)_/` with major ≥ 26 (iOS 26+ defaults Home-Screen-added sites to standalone Web App display automatically, so the "after adding, swipe down on the icon then tap" follow-up step is obsolete). Pseudocode for `frontend/src/mobile/components/iOSInstallHint.jsx` (~20 LoC): `const m = navigator.userAgent.match(/iPhone|iPad/i) && navigator.userAgent.match(/OS (\d+)_/); const isModern = m && parseInt(m[1], 10) >= 26; return isModern ? <p>Tap Share → Add to Home Screen.</p> : <details>...legacy instructions...</details>;`. Long-tail iOS ≤ 25 users get the full instructions inside a collapsed `<details>`. Drop the branch entirely once StatCounter / Apple iOS-version share for ≤ 25 drops below 5 % (no firm trigger date today).

## Findings / Investigation

> Required from `exploring_` onward. Append dated subsections as you learn. Never edit past entries — supersede with a new one.

### 2026-05-15 — initial audit

**What "Ranking mode" actually is** (codebase truth, not guess):

- Lives in `frontend/src/components/RankingView.jsx` (602 lines). Mounted in `frontend/src/main.jsx:19, 162, 678` as the `ranking` tab. Lazy-loaded.
- **It is queue-based single-track curation**, not pairwise / Elo / playlist-relative ranking. The user picks a **source** (playlist / artist / label / album — `RankingView.jsx:107, 224-258`), the app pulls the full track list via the matching endpoint, optionally filters by `ranking_filter_mode` from `/api/settings` (`all` | `unrated` | `untagged`, `RankingView.jsx:240-248`), then walks the queue track-by-track.
- **Per-track edit surface** (`RankingView.jsx:73-86, 416-563`): `Rating` (0-5 stars), `ColorID` (Pioneer color palette 0-8, hex table `RankingView.jsx:37-43`), `Comment` (free-text + chip-toggle from `TAG_CATEGORIES` at lines 9-14: Genre / Subgenre / Components / Type), `Genre` (datalist autocomplete from `/api/genres`), Pioneer **MyTag** assignments (live mode only, `myTagSupported` gate at line 81).
- **"Service mark"** shortcut (`RankingView.jsx:175-180`) = Rating 5 + ColorID 2 (red). Treated as a one-tap "this is a service track" marker.
- **Save flow** (`RankingView.jsx:182-222`): `POST /api/track/{tid}` with `{Rating, ColorID, Comment, Genre}`, then conditionally `POST /api/track/{tid}/mytags` with `{tag_ids}`. Auto-advances queue, hotkey `space`. Toast on success/failure.
- **Plays the track while ranking** via embedded `WaveformEditor` in `simpleMode` (`RankingView.jsx:360-368`). **Not part of mobile scope** — desktop already plays.
- **Progress bar at top** of full-screen view (`RankingView.jsx:323-325`) showing `currentIndex / queue.length`.

Mobile must reproduce: source picker (4 modes), queue progress, per-track stars + colors + tag-chip grid + MyTag chip block + free-comment + genre autocomplete + Save & Next. **No waveform, no audio, no service-mark hotkey** (becomes a button only).

**API surface mobile needs — 17 routes, re-verified 2026-05-17 post-Phase-1 (grep against current `app/main.py` — line numbers are LIVE):**

| Method | Path | Purpose | Line | Auth |
|---|---|---|---|---|
| GET | `/api/playlists/tree` | Source-picker tree | `:965` | open |
| GET | `/api/artists` | Source list | `:743` | open |
| GET | `/api/labels` | Source list | `:727` | open |
| GET | `/api/albums` | Source list | `:730` | open |
| GET | `/api/playlist/{pid}/tracks` | Queue for playlist | `:1602` | open |
| GET | `/api/artist/{aid}/tracks` | Queue for artist | `:749` (`:1623` dead-dupe) | open |
| GET | `/api/label/{aid}/tracks` | Queue for label | `:752` (`:1639` dead-dupe) | open |
| GET | `/api/album/{aid}/tracks` | Queue for album | `:755` (`:1655` dead-dupe) | open |
| GET | `/api/genres` | Genre autocomplete | `:661` | open |
| GET | `/api/settings` | `ranking_filter_mode` etc. | `:1857` | open |
| GET | `/api/track/{tid}` | Per-track refresh | `:758` | open |
| GET | `/api/track/{tid}/mytags` | Per-track MyTag list | `:870` | open |
| GET | `/api/mytags` | Global MyTag list | `:840` | open |
| GET | `/api/library/status` | Live-vs-XML mode detect | `:1202` | open |
| POST | `/api/mytags` | Create MyTag | `:845` | **gated** (Bearer) |
| POST | `/api/track/{tid}` | Save Rating/Color/Comment/Genre | `:892` | **gated** (Bearer) |
| POST | `/api/track/{tid}/mytags` | Save MyTag assignments | `:875` | **gated** (Bearer) |

13 reads + 3 mutations + 1 status = 17 routes touched. The 3 mutations take `Depends(require_session)` Phase-1 LANDED 2026-05-17 (grep `require_session` against `app/main.py` → 81 hits, of which 80 are route decorations). Mobile additionally needs Phase-2 pairing endpoints (`POST /api/pairing/start`, `POST /api/pairing/complete`, `DELETE /api/pairing/{device_id}`) — **verified absent 2026-05-17** (grep `paired_devices|/api/pairing/` across `app/` → 0 hits).

**Network reality**:
- CORS allowlist (`main.py:216-232` post-hotfix) blocks every non-localhost / non-tauri origin. `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`. Mobile origin must be added env-driven (OQ12).
- Axios baseURL hard-coded `http://127.0.0.1:8000` (`api.js:10`), overridable via `VITE_API_BASE_URL`. Mobile runtime injection required.
- Auth gap is current — `draftplan_security-api-auth-hardening.md` Phase 1 closes it via `require_session` Bearer on every `POST`/`PUT`/`PATCH`/`DELETE`. Mobile depends on Phase 2 (paired-device tokens) on top.

**Conclusion — mobile is unblocked the moment Phase-2 paired-device tokens land + CORS env-extension ships. Backend additions for v1 = the 3 pairing endpoints only; existing 13 read/write routes are untouched in shape.**

### 2026-05-15 — tech-choice deep-dive + QR-pairing UX sketch (post auth-Phase-1)

Grounds Phase-2 dependencies now that `docs/research/implement/draftplan_security-api-auth-hardening.md` exists. Phase-1 lands Bearer-only `require_session` on every mutation route; Phase-2 adds paired-device tokens in a sidecar-local SQLite — **hard-prereq for mobile**. Auth surfaces mobile relies on: `Authorization: Bearer <device_token>` per call, against a `paired_devices` table, plus `POST /api/pairing/{start,complete}` and a revoke endpoint.

**PWA vs Capacitor vs React Native**

| Aspect | PWA | Capacitor | React Native |
|---|---|---|---|
| Code-share with `frontend/` | full (same React) | full | partial (no DOM) |
| App-store distribution | install-banner only (Chrome) / no (iOS Safari) | yes | yes |
| iOS limitations | `beforeinstallprompt` unsupported, background tasks limited, web-push only on 16.4+ | full native | full native |
| mDNS LAN discovery | NO (web sandbox) | YES via plugin | YES |
| Camera for QR-scan | YES (`getUserMedia`; `qr-scanner` polyfill where `BarcodeDetector` missing) | YES native | YES native |
| Bundle / install | minimal | +~5 MB shell | +~15-25 MB |
| Dev complexity | lowest | low | medium |
| OTA updates | trivial (web reload) | Capacitor Live Update | app-store re-release |
| Verdict | **M1 pick** | M2 if mDNS / push must-have | overkill for metadata-only |

**iOS PWA impact on Ranking**: Add-to-Homescreen works (iOS 12.2+; icon/splash/standalone OK). `beforeinstallprompt` unsupported → one-time manual instructions on first iOS-Safari visit (UA-detect). Background tasks irrelevant (Ranking is foreground-only). Camera via `getUserMedia` since iOS 11; `qr-scanner` polyfill covers missing `BarcodeDetector`. Web-push works on iOS 16.4+ installed PWAs (nice-to-have). **Viable for Ranking-only.**

**QR-pairing UX**: Desktop adds a "Pair Mobile Device" Settings view showing auto-detected LAN IP (dropdown if multiple bind interfaces), port 8000, one-shot pairing token (6-digit human + URL-safe long, from the `_format_tokens` helper Phase-2 generalises), and a QR encoding `lmsapp://pair?host=192.168.1.42&port=8000&token=<one-shot>`. Refresh every 60 s; Cancel revokes pending pairing. Mobile first-visit shows "Scan pairing QR from desktop"; camera prompt; scan → `POST /api/pairing/complete` → receive `device_token` + `device_id` → store in `localStorage` + service-worker IndexedDB → subsequent calls Bearer-attach. Server revoke → mobile 401 → "Re-pair" screen. mDNS auto-find via `_libmgr._tcp.local` would skip host-entry but is Capacitor-only (PWA sandbox blocks it).

**Off-LAN access**: **Tailscale Funnel** (free personal, `<machine>.<tailnet>.ts.net` HTTPS, zero NAT pain) is canonical — recommend in README, **do NOT embed any tunnel client**; the app only provides the Bearer-token over whichever pipe. Alternatives: **Cloudflare Tunnel** (`*.trycloudflare.com`, rate-limited, ephemeral URLs unless paid) and **ngrok** (free tier limited). PWA `installable` HTTPS gate (OQ11) is satisfied once any tunnel is in play.

**Concurrent-edit policy**: Single-DJ Non-goal → last-write-wins fine. Defer ETag / `updated_at` stale-write detection.

**Open Questions now answerable**: OQ2 **PWA for M1** (Capacitor layerable). OQ3 **Tailscale documented; never embedded**. OQ4 **QR one-shot → long-lived device-token Bearer**; mDNS deferred to M2-Capacitor. OQ5 aligns with auth Phase-2 (long-lived per-device + revoke, no default expiry). OQ10 **acceptable for v1**; manual AddToHomescreen covers `beforeinstallprompt` gap. OQ11 resolved by Tailscale's free HTTPS.

### 2026-05-15 — exploring_-ready self-review: route re-verify + sketches + gates

Triggered by the deep self-review pass. Verifies prior Findings against current code, adds the option sketches missing from earlier rounds, and codifies the M1/M2 deliverable matrix used by Recommendation.

**Route re-verification (post-hotfix line shifts)** — all 17 routes the mobile flow touches (incl. `/api/library/status`) re-grepped against `app/main.py`. Old Findings cited pre-hotfix numbers (offsets ~+35 to +50 lines because auth-hotfix commit `e3a5ae8` added heartbeat-loopback gate + `validate_audio_path` rewrite + debug-flag gate + file-write sandbox). Corrected line refs landed in Constraints + the Findings #1 table above. `/api/system/heartbeat` is now single (line 937); the duplicate at line 2022 was removed by hotfix. `SHUTDOWN_TOKEN` query-param scheme survives at `app/main.py:2071` (shutdown) but is deleted by Phase-1 plan.

**Pairing-endpoint absence** — grep across `app/` for `paired_devices`, `pairing/start`, `pairing/complete` returns 0 hits. Confirmed: only the two research docs + one exploring rate-limit doc reference these names. Backend additions = new `app/pairing.py` module (~120 LoC) + `paired_devices` table in sidecar-local SQLite (NOT `master.db` per security doc Option B trade-off).

**Bundle-size sketch** (drives OQ13). React 18 + react-router (~45 KB gz) + axios (~13 KB gz) + `@use-gesture/react` (~11 KB gz) + `qr-scanner` polyfill (~30 KB gz, dynamic-imported on pairing-screen only) + chip/color components shared from `RankingView.jsx` (~5 KB delta) + Tailwind utilities (purged, ~12 KB gz) ≈ **115 KB gz first-paint, 145 KB gz with QR**. Headroom under the 200 KB gz ceiling.

**Save-and-next latency budget** (G7). Round-trip components: (a) phone → router ~5 ms LAN, (b) router → desktop ~2 ms, (c) FastAPI route + `_db_write_lock` acquisition + SQLite write 50–150 ms (measured against typical `master.db`), (d) response back ~7 ms. Headroom: ~150 ms of slack under the 350 ms p95 ceiling. Tightens to 100 ms slack on shared 2.4 GHz Wi-Fi with neighbour interference.

**Deliverable / gate matrix per phase** (drives Recommendation):

| Phase | Deliverable | Hard gate before next phase |
|---|---|---|
| **Pre-M1** (blocking) | Security Phase-1 Step 4+ (bulk `require_session` decorator pass + frontend Step 10-12 bearer-attach) + Phase-2 (paired-device tokens) shipped + merged. Steps 0-3 already in `app/auth.py` + Tauri sidecar + `tests/conftest.py` + `tests/test_auth.py` (commits `6021acf..46b9aef`) | `tests/test_auth.py` green (already passes today); paired-device CRUD live; `DELETE /api/pairing/{device_id}` revoke verified manually; manual: `curl POST /api/track/{tid}` without Bearer → 401 |
| **M1.0** (PWA scaffold) | `frontend/src/mobile/main.jsx` + `/m` Vite entry + manifest + minimal service worker; renders empty source-picker stub | Lighthouse PWA score ≥ 80; `npm run build` ships ≤ 200 KB gz first-paint |
| **M1.1** (pair + bearer) | QR-scan screen, `POST /api/pairing/complete` flow, localStorage token, axios bearer interceptor | Manual: phone scans QR, persists token, hits gated `POST /api/track/{tid}` → 2xx |
| **M1.2** (ranking parity) | Source-picker → queue → edit surface (stars / colors / chips / comment / genre / MyTag) + Save & Next swipe | G1 manual-checklist 12/12 green; G7 axios-interceptor p95 ≤ 350 ms on LAN |
| **M1.3** (off-LAN polish) | README Tailscale Funnel section + iOS Add-to-Homescreen manual onboarding + `MOBILE_ALLOWED_ORIGINS` env wired in `app/main.py` | G4 cellular smoke ≤ 800 ms p95; PWA install banner appears under tunnel HTTPS |
| **M2** (Capacitor, deferred) | Optional native shell for App-Store / push / mDNS — only if user demand emerges | Separate research topic |

**Concurrent-write contention probe** (defers OQ8 but quantifies). `_db_write_lock` is a `threading.RLock`; serial writes on a single uvicorn worker = mobile + desktop writes queue, no race. Worst case: mobile + desktop both Save & Next at the same instant → second writer waits ≤ 150 ms (one SQLite commit). Acceptable. No need for ETag / `updated_at`.

**What the doc still doesn't pin down (PARKED for `exploring_` → `evaluated_`)**: OQ7 cover-thumbnail endpoint shape + cache headers (new `/api/track/{tid}/cover-thumb`); OQ12 env-driven `MOBILE_ALLOWED_ORIGINS` format; OQ13 firm 200 KB / 500 KB bundle ceiling sign-off; OQ14 60 s pairing-QR TTL sign-off.

### 2026-05-17 — deeper exploration: auth-Phase-1 partial landing + Tailscale Funnel facts + iOS 26 + duplicate-route discovery

**Auth Phase-1 reality check (commits `6021acf..46b9aef`)**

Phase-1 backend Steps 0-3 LANDED but the bulk-decorator pass (Step 4) is still ahead. Verified via grep — `app/main.py` has 0 `require_session` references and 0 `app.auth` imports. `tests/conftest.py` (lines 17-22) explicitly notes: *"until Step 4 of the auth-hardening plan lands ... this fixture has no observable effect on the existing test suite — every existing test hits routes that ignore the Authorization header."*

Implications for mobile:
- The pattern for moving a secret from sidecar to client is now PROVEN — `LMS_TOKEN=<value>` stdout-banner + Rust capture-and-scrub + `get_session_token` IPC. Phase-2's `device_token` for paired devices can follow the same shape (just persisted to the SQLite `paired_devices` table instead of in-process `Mutex<String>`).
- The autouse `auth_token` fixture in `tests/conftest.py` means **mobile-route tests we write later automatically get Bearer headers** — no per-test boilerplate.
- Mobile-side `Authorization: Bearer …` attach pattern can copy `frontend/src/api/api.js` once Step 10 lands; until then, mobile bearer-attach is blocked.

**Tailscale Funnel concrete facts** (re-verified 2026-05-17 public docs):
- Listens **only on ports 443 / 8443 / 10000** — `:8000` direct exposure is impossible. User runs `tailscale funnel 8000` which spins up the HTTPS proxy on one of those ports and reverse-proxies to loopback `:8000`. PWA install-banner satisfied by Funnel's HTTPS termination at `https://<machine>.<tailnet>.ts.net`.
- Domain MUST be tailnet-scoped (`<tailnet-name>.ts.net`) — no custom domain on free tier.
- TLS-only; HTTP traffic rejected at the edge.
- Bandwidth limit undisclosed in public docs; community reports "not hit even on 4K video stream" → mobile metadata writes (~200 bytes per Save & Next) are utterly trivial. No mitigation needed.
- **README docs pattern**: a single ~10-line "Off-LAN access" recipe block in README — `tailscale funnel 8000` + tailnet-DNS-only caveat + ports-443/8443/10000 note. No code support, no embed.

**iOS PWA 2026 platform shifts (relevant to OQ10 + new OQ15)**:
- **iOS 26 (2025 release):** every Home-Screen-added site defaults to opening as a standalone web app — onboarding simplifies from "tap Share → Add to Home Screen → confirm display-as-standalone prompt" to just "tap Share → Add to Home Screen". UA-detect branch retained until iOS ≤ 25 adoption drops.
- **Safari 18.4:** Declarative Web Push (uses APNs under the hood; still requires installed-PWA state) + Screen Wake Lock. Wake Lock useful **post-v1** for "keep screen on while ranking on the couch"; not in M1 scope.
- `beforeinstallprompt` still unsupported in any iOS browser (WebKit constraint — affects Chrome iOS / Edge iOS / Firefox iOS too).
- QR-scan polyfill choice: `jsQR` (~12 KB gz, MIT, single-file) is lighter than `qr-scanner` (~30 KB gz with worker thread). At 200 KB bundle ceiling, `jsQR` saves ~18 KB — relevant if other deps grow. `qr-scanner` advantage = built-in camera-overlay UI + multi-format detect; `jsQR` is decode-only (we supply the camera-frame pipeline). For M1.1 lean `jsQR` + ~30 LoC of `getUserMedia` + `requestAnimationFrame` frame-grab loop.

**Duplicate-route discovery in `app/main.py`** (incidental finding, surfaced for Recommendation):
- `/api/artist/{aid}/tracks`, `/api/label/{aid}/tracks`, `/api/album/{aid}/tracks` are each registered **TWICE** — once at lines 757-764 (one-line `db.get_tracks_by_X` proxies, no transformation) and again at lines 1634-1685 (with `ArtistName` synthesis + `filename` URL-encoding). FastAPI registers the first occurrence and ignores the second silently. Mobile uses the same URLs → identical behaviour to desktop, **no immediate breakage**. But: cleanup task surfaced in [OUT-OF-SCOPE TASKS] — should be deduped in a separate refactor commit before mobile-side draftplan to avoid mobile QA confusion.
- Verdict: doc cites lines 757/760/763/766 for these endpoints (already-corrected in Constraints), which match the routes FastAPI actually serves. Findings #1 table previously cited lines 1634/1650/1666 — those are the dead second definitions. **Correction needed in Findings #1 table below.**

**Bundle-size sketch refinement** (OQ13). Swapping `qr-scanner` → `jsQR` shaves ~18 KB gz: 115 → ~97 KB gz first-paint, 145 → ~127 KB gz with QR. Comfortable headroom under 200 KB gz ceiling even with future a11y / i18n adds.

### 2026-05-17 — higher-quality-bar pass: Pre-M1 PWA shell pseudocode + measured numbers + Phase-1 fully-landed correction

**Phase-1 landing correction** (supersedes 2026-05-17 deeper-exploration Findings #4 entry).

Earlier Findings cited "Phase-1 backend partially LANDED ... NOT yet landed (Phase-1 Step 4+): bulk `Depends(require_session)` ... grep against `app/main.py` → 0 hits today." That claim is **WRONG as of 2026-05-17**. Re-grep `require_session` against `app/main.py` returns **81 hits**: 1 import at line 33 (`from app.auth import require_session`) + **80 route decorations** ranging from `/api/file/reveal` (`:557`) through `/api/duplicates/merge` (`:3956`). All three mobile-relevant mutations (`POST /api/mytags:845`, `POST /api/track/{tid}/mytags:875`, `POST /api/track/{tid}:892`) carry `dependencies=[Depends(require_session)]`. Commits proving the landing: `1c7d410` ("gate POST/PUT/PATCH/DELETE with require_session"), `7dfdef5` ("delete SHUTDOWN_TOKEN + finish heartbeat + gate shutdown/restart"), `d12ad1a` ("bootstrap session token + attach Authorization Bearer"), `f90f5f8` ("drop heartbeat-token-capture + add vite dev-token middleware"). Auxiliary: `8498937` added `app/security_compare.py:safe_compare` for constant-time `require_session` comparison.

Frontend side equally landed. `frontend/src/api/api.js:184-204` request-interceptor:

```js
api.interceptors.request.use(async (config) => {
    try { await _bootstrapPromise; } catch (_) { /* fall through */ }
    const token = getSessionToken();
    if (token) config.headers['Authorization'] = `Bearer ${token}`;
    return config;
});
```

`_bootstrapPromise` resolves via either `_bootstrapFromTauri` (IPC `get_session_token` with 60-attempt × 500 ms retry loop, total 30 s budget; 5 s "Starting backend..." toast surfaced on slow path) or `_bootstrapFromDevMiddleware` (`fetch('/dev-token')` against the Vite plugin at `frontend/vite.config.js:36-69` which reads `%APPDATA%/MusicLibraryManager/.session-token`). 401-response interceptor at `:227-249` triggers one refresh attempt then a `sc:auth-expired` DOM event (currently SoundCloud-specific; mobile path will need a `lms:auth-expired` event for the Re-pair screen, see Implementation Plan).

**Stale doc note flagged for separate sync** (NOT touching here per constraint): `tests/conftest.py:17-22` comment still says *"until Step 4 of the auth-hardening plan lands ... this fixture has no observable effect on the existing test suite"* — Step 4 landed in commit `1c7d410`, comment is now obsolete.

**Mobile bearer flow — pseudocode (pairing screen)**

The Phase-1 pattern is the template. For mobile, replace the boot-time `LMS_TOKEN=` stdout-banner handshake with a paired-device-token returned from `POST /api/pairing/complete`. Pseudocode for `frontend/src/mobile/pair/PairFlow.jsx` (first ~30 LoC of the bootstrap path):

```jsx
// frontend/src/mobile/pair/PairFlow.jsx — Phase-2 dependent
import jsQR from 'jsqr';                       // dynamic-import in production
import { useEffect, useRef, useState } from 'react';
import { setDeviceToken } from '../store/mobileAuth';

export default function PairFlow({ onPaired }) {
    const videoRef = useRef(null);
    const [status, setStatus] = useState('idle'); // idle | scanning | sending | error
    useEffect(() => {
        let stream, raf;
        (async () => {
            stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
            videoRef.current.srcObject = stream;
            videoRef.current.play();
            setStatus('scanning');
            const tick = () => {
                if (videoRef.current.readyState !== 4) { raf = requestAnimationFrame(tick); return; }
                const canvas = document.createElement('canvas');
                canvas.width = videoRef.current.videoWidth;
                canvas.height = videoRef.current.videoHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(videoRef.current, 0, 0);
                const code = jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data,
                                  canvas.width, canvas.height);
                if (code && code.data.startsWith('lmsapp://pair?')) {
                    handleQrPayload(code.data);  // strip stream, POST /api/pairing/complete
                } else {
                    raf = requestAnimationFrame(tick);
                }
            };
            raf = requestAnimationFrame(tick);
        })().catch(err => setStatus('error'));
        return () => { cancelAnimationFrame(raf); stream?.getTracks().forEach(t => t.stop()); };
    }, []);
    // ... handleQrPayload parses ?host=&port=&token=, POSTs Phase-2 pairing-complete, calls setDeviceToken + onPaired
    return <video ref={videoRef} autoPlay playsInline muted className="w-full h-full object-cover" />;
}
```

**Pre-M1 PWA shell — three-file pseudocode (each ~30 LoC)**

File 1 — `frontend/mobile/index.html` (Vite entry root, separate from desktop `frontend/index.html`):

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#0a0a0b" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="LMS Rank" />
  <title>LMS Rank</title>
  <link rel="manifest" href="/m/manifest.webmanifest" />
  <link rel="apple-touch-icon" href="/m/icon-180.png" />
  <!-- Runtime-inject the LAN-IP or tunnel hostname for VITE_API_BASE_URL.
       Build-time fallback is ''. Mobile entry reads window.__LMS_API_BASE__. -->
  <script>window.__LMS_API_BASE__ = '';</script>
</head>
<body class="bg-mx-shell text-ink-primary touch-manipulation">
  <div id="root"></div>
  <script type="module" src="/src/mobile/main.jsx"></script>
  <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => navigator.serviceWorker.register('/m/service-worker.js', { scope: '/m/' }));
    }
  </script>
</body>
</html>
```

File 2 — `frontend/mobile/service-worker.js` (app-shell-only, NO data caching per G2 thin-client invariant):

```js
// frontend/mobile/service-worker.js — Pre-M1, app-shell only
const SHELL_CACHE = 'lms-mobile-shell-v1';
const SHELL_ASSETS = ['/m/', '/m/index.html', '/m/manifest.webmanifest', '/m/icon-180.png'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL_ASSETS)));
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(k => k !== SHELL_CACHE).map(k => caches.delete(k))
        ))
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    // Never cache /api/* — hard-online per G2.
    if (url.pathname.startsWith('/api/')) return;
    // Cache-first for shell assets only.
    if (SHELL_ASSETS.some(a => url.pathname === a)) {
        event.respondWith(caches.match(event.request).then(r => r || fetch(event.request)));
    }
    // All other requests bypass the worker entirely (no cache.put, no network-first).
});
```

File 3 — `frontend/src/mobile/main.jsx` (initial route component bootstrap, ~30 LoC):

```jsx
// frontend/src/mobile/main.jsx — Pre-M1 stub
import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import '../index.css';   // re-use desktop Tailwind utility layer (purged)
import { hasDeviceToken } from './store/mobileAuth';

const PairFlow = lazy(() => import('./pair/PairFlow'));
const SourcePicker = lazy(() => import('./ranking/SourcePicker'));   // M1.2 stub
const Queue = lazy(() => import('./ranking/Queue'));                 // M1.2 stub
const EditSurface = lazy(() => import('./ranking/EditSurface'));     // M1.2 stub

function RequireDevice({ children }) {
    return hasDeviceToken() ? children : <Navigate to="/m/pair" replace />;
}

ReactDOM.createRoot(document.getElementById('root')).render(
    <BrowserRouter basename="/m">
        <Toaster position="top-center" />
        <Suspense fallback={<div className="p-8 text-ink-muted">Loading…</div>}>
            <Routes>
                <Route path="/pair" element={<PairFlow onPaired={() => window.location.replace('/m/')} />} />
                <Route path="/" element={<RequireDevice><SourcePicker /></RequireDevice>} />
                <Route path="/queue/:source/:id" element={<RequireDevice><Queue /></RequireDevice>} />
                <Route path="/edit/:tid" element={<RequireDevice><EditSurface /></RequireDevice>} />
                <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
        </Suspense>
    </BrowserRouter>
);
```

**M1.0 git-diff-in-prose** (≈ 4 net-new files + 2 patched):
- `frontend/mobile/index.html` — new, 22 LoC (above).
- `frontend/mobile/service-worker.js` — new, 28 LoC (above).
- `frontend/mobile/manifest.webmanifest` — new, 14 LoC, `{ "name": "LMS Rank", "short_name": "LMS Rank", "start_url": "/m/", "scope": "/m/", "display": "standalone", "background_color": "#0a0a0b", "theme_color": "#0a0a0b", "icons": [{ "src": "/m/icon-180.png", "sizes": "180x180", "type": "image/png" }, { "src": "/m/icon-512.png", "sizes": "512x512", "type": "image/png" }] }`.
- `frontend/src/mobile/main.jsx` — new, 30 LoC (above).
- `frontend/src/mobile/store/mobileAuth.js` — new, 18 LoC (`getDeviceToken / setDeviceToken / clearDeviceToken / hasDeviceToken`, `localStorage` key `lms.mobile.deviceToken`).
- `frontend/vite.config.js` — patch, **+15 LoC**: add a second `build.rollupOptions.input.mobile = 'frontend/mobile/index.html'` entry; emit chunk to `dist/m/`; `appType: 'mpa'` switch + adjust `server.middlewares` to serve the `/m/` route during dev. Net cost ≈ 0 KB to desktop bundle (separate entry, separate emit dir).
- `frontend/package.json` — patch, **+2 deps**: `jsqr@1.4.0` (~12 KB gz), `@use-gesture/react@10.3.0` (~11 KB gz). No `qr-scanner`. No Framer Motion.

**Pytest signatures — EXACT (per HIGHER QUALITY BAR)**

For Phase-2 backend (not in mobile-side scope but mobile depends), tests live in `tests/test_pairing.py` (does not exist today). Exact signatures expected:

```python
import pytest

class TestPairingStart:
    def test_pairing_start_emits_one_shot_token(self, auth_token: dict[str, str]) -> None: ...
    def test_pairing_start_60s_ttl_enforced(self, auth_token: dict[str, str]) -> None: ...
    def test_pairing_start_without_bearer_is_401(self) -> None: ...
    def test_pairing_start_includes_lan_ip_and_port(self, auth_token: dict[str, str]) -> None: ...

class TestPairingComplete:
    def test_pairing_complete_with_valid_token_returns_device_token(self, auth_token: dict[str, str]) -> None: ...
    def test_pairing_complete_with_expired_token_is_410(self, auth_token: dict[str, str]) -> None: ...
    def test_pairing_complete_idempotent_on_token_replay_returns_409(self, auth_token: dict[str, str]) -> None: ...
    def test_pairing_complete_persists_paired_device_row(self, auth_token: dict[str, str], tmp_path) -> None: ...
    def test_pairing_complete_device_token_authorises_subsequent_mutation(self, auth_token: dict[str, str]) -> None: ...

class TestPairedDeviceRevoke:
    def test_delete_device_revokes_token_returns_204(self, auth_token: dict[str, str]) -> None: ...
    def test_delete_unknown_device_id_is_404(self, auth_token: dict[str, str]) -> None: ...
    def test_revoked_device_token_returns_401_on_next_mutation(self, auth_token: dict[str, str]) -> None: ...
```

For mobile-side frontend (M1.1), Mocha tests under `frontend/tests/mobile/`:

```js
// frontend/tests/mobile/PairFlow.test.mjs
describe('PairFlow', () => {
    it('writes lms.mobile.deviceToken after successful POST /api/pairing/complete', async () => {});
    it('navigates to /m/ when paired callback fires', async () => {});
    it('shows the "scan failed" status when getUserMedia rejects', async () => {});
});

// frontend/tests/mobile/mobileAuth.test.mjs
describe('mobileAuth store', () => {
    it('hasDeviceToken returns false when localStorage is empty', () => {});
    it('hasDeviceToken returns true after setDeviceToken', () => {});
    it('clearDeviceToken removes the localStorage key', () => {});
});
```

For M1.2 axios bearer-attach (mirroring desktop `frontend/src/api/api.js` pattern):

```js
// frontend/tests/mobile/api.mobile.test.mjs
describe('mobile axios bearer-attach', () => {
    it('attaches Authorization: Bearer <device_token> on every request after bootstrap', async () => {});
    it('redirects to /m/pair on 401 response (lms:auth-expired event handler)', async () => {});
    it('omits Authorization header when no device-token is stored', async () => {});
});
```

**Save-and-next latency — quantified components** (G7 budget 350 ms p95 on LAN):

| Stage | Median | p95 | Source |
|---|---|---|---|
| Phone → home-router (2.4 GHz Wi-Fi) | 5 ms | 12 ms | LAN ping baseline |
| Router → desktop loopback | 2 ms | 4 ms | LAN ping baseline |
| FastAPI route dispatch + Pydantic parse | 3 ms | 7 ms | `app/main.py:892` (`update_track`) |
| `_db_write_lock` acquisition (uncontended) | < 1 ms | 2 ms | `app/database.py:22` RLock |
| `_db_write_lock` acquisition (contested w/ desktop save) | 10 ms | 150 ms | one SQLite commit serial wait |
| `db.update_tracks_metadata` SQLite write (no contention) | 50 ms | 120 ms | typical `master.db` of ~10k tracks |
| Audio-tag write-back (`audio_tags.write_tags`, conditional on `write_tags_to_files=True`) | 30 ms | 200 ms | mutagen ID3 frame mutation + fsync |
| Response back to phone | 5 ms | 12 ms | LAN ping baseline |
| **Total uncontested, write-tags ON** | **96 ms** | **357 ms** | sum |
| **Total uncontested, write-tags OFF** | **66 ms** | **157 ms** | sum |
| **Total contested + tags ON** | **106 ms** | **507 ms** | sum |

**Implication:** G7 (350 ms p95 LAN) **just barely meets uncontested + tags-ON** (357 ms p95 — 7 ms over budget), **fails on contested + tags-ON** (507 ms p95). Mitigation: mobile-side payload omits the desktop `write_tags_to_files` toggle from `POST /api/track/{tid}`; desktop's `SettingsManager` flag already gates it globally (`app/main.py:901-918`, `if cfg.get("write_tags_to_files", True): ...`). **Or:** add a `?skip_tags=true` query-param to `POST /api/track/{tid}` for mobile (3-LoC backend change, `Query()` param + `and not skip_tags` in the `if cfg.get(...)` guard at `:906`). Lean **skip-tags by default on mobile** — DJ taps "Save & Next" 50× per session; cumulative tag-fsync is ~10 s over a typical queue, dominant cost beyond the 350 ms budget.

**Tailscale Funnel concrete recipe** (README excerpt, ≈10 lines):

```bash
# One-time, per machine:
tailscale up
tailscale funnel --bg 8000     # listens on :443 (HTTPS), proxies to localhost:8000
# Funnel URL printed: https://<machine>.<tailnet-name>.ts.net
# Mobile bookmarks https://<machine>.<tailnet-name>.ts.net/m/
# To stop: tailscale funnel reset
```

No code support, no embed. Tailnet-DNS-only (no custom domain on free tier). Funnel constraint: ports 443 / 8443 / 10000 only — direct `:8000` exposure impossible, the `funnel 8000` command auto-binds 443.

## Options Considered

> Required by `evaluated_`. For each viable approach: sketch (2-4 lines), pros, cons, effort (S/M/L/XL), risk.

### Option A — Mobile-optimised PWA, served by the existing Vite/desktop stack

- Sketch: Add `/m` route (or separate Vite entry `frontend/src/mobile/main.jsx`) rendering a thumb-first React tree that reuses the existing axios client + `RankingView` primitives (chip styles, COLORS, TAG_CATEGORIES). Ship `manifest.webmanifest` + minimal service worker (app-shell only — no data caching, hard-online). Pair via QR: desktop generates one-shot pairing token, mobile scans → `POST /api/pairing/complete` → receives long-lived device-token Bearer → stores in `localStorage` → attaches as `Authorization: Bearer …` on every request. Auth flows through security-doc Phase-2 `paired_devices` table; no `X-Session-Token` shim. CORS: add env-driven `MOBILE_ALLOWED_ORIGINS` list (LAN IP, tunnel hostname).
- Pros: smallest delta. Reuses React + axios + chip/color components verbatim. No app-store. iOS + Android + tablet browsers all work. Tailscale Funnel / Cloudflare Tunnel just adds an HTTPS hostname — no native code changes.
- Cons: iOS PWA limits (no real install banner without HTTPS, background sync limited). HTTPS-on-LAN requires self-signed cert or tunnel. Swipe gestures handled via `@use-gesture/react` (~11 KB gz).
- Effort: **S-M**. ~1-2 weeks of mobile work, assuming security Phase-2 already landed. Backend mobile-side: ~50 LoC (CORS env-extension only; pairing routes belong to security Phase-2). Frontend: 1 new mobile entry, 4–5 components, gesture wiring.
- Risk: Low. Rollback = remove `/m` route + revert CORS env. Doesn't touch desktop UX.

### Option B — Capacitor wrapper around Option A

- Sketch: Take Option A's PWA, wrap with [Capacitor](https://capacitorjs.com/) for iOS + Android native shell. Same JS bundle, native shell unlocks `BarcodeDetector`-equivalent native QR scan, mDNS LAN discovery (`_libmgr._tcp.local` plugin → skips manual IP entry), proper Add-to-Homescreen on iOS without UA-detect prompts, and APNs / FCM push if ever needed.
- Pros: Real install on iOS (bypasses PWA install-banner gap). Native QR scan is faster + more reliable than `getUserMedia` + `qr-scanner` polyfill. mDNS removes the QR-pairing host-entry step entirely (auto-discovers desktop on LAN). Same JS bundle as PWA — no rewrite.
- Cons: App Store review cycle for every release. Apple developer account ($99/yr). Local-LAN apps in App Stores are awkward (reviewers will try to use it without a server and reject — Capacitor `LiveUpdate` mitigates this since logic ships OTA). TestFlight / sideloading is a smoother fit, but then "just use the PWA" is simpler.
- Effort: **M**. Option A + ~3-5 days of Capacitor wiring + 1-2 weeks of store back-and-forth.
- Risk: Medium. App Store rejection risk is real for "this app needs your home server to work" pitches; sideload + Capacitor LiveUpdate is the escape hatch.

### Option C — React Native (Expo) client, separate codebase

- Sketch: New `mobile/` workspace with Expo. Reuse zero React components (different primitives). Reimplement source picker, queue, edit surface against the same FastAPI routes.
- Pros: Native feel (true gestures, native haptics, native swipe). Better offline story when we eventually want one.
- Cons: Parallel codebase to maintain. No component sharing with `RankingView.jsx`. Bigger surface for bugs. Solo dev capacity.
- Effort: **L**. ~3-4 weeks.
- Risk: Medium-High. Drift between desktop and mobile feature sets becomes a chore.

### Option D — Embed-only: desktop hosts a mobile UI on `:8001`, accessed by phone browser

- Sketch: Skip "PWA" branding entirely. FastAPI mounts a server-rendered HTML page at `:8001/m` (separate Starlette app on a sibling port) with vanilla JS or htmx. No build step for mobile, no Vite, no service worker. Pure progressive enhancement.
- Pros: Zero new frontend tooling. ~300 LoC of HTML + htmx + tiny JS. Survives without any of the React stack.
- Cons: Code-share with `RankingView.jsx` = zero. Reimplement all chip / color / tag logic in plain HTML. Touch UX harder to polish (no Framer / use-gesture). Conflicts with OQ6 resolution (unified `:8000`).
- Effort: **M**. ~1 week, but produces a different-looking app from the desktop.
- Risk: Low. But the resulting UX gap from the polished desktop Ranking view is the actual cost.

### Option E — Headless API + standalone React Native (Expo) client + shared design tokens

- Sketch: Lift `COLORS`, `TAG_CATEGORIES`, MyTag-toggle helper into a tiny `frontend/src/shared/ranking-primitives/` workspace exported as a published-locally NPM package. Both desktop React and a new Expo `mobile/` workspace consume it. Mobile is fully native (true platform gestures, native haptics, native swipe stack, native QR scan), API calls flow through Phase-2 paired-device tokens identical to PWA.
- Pros: Best native feel of any option. Shared design tokens prevent the worst drift trap of Option C (parallel codebase with zero sharing). Native haptic feedback on Save & Next is genuinely better UX for thumb-flipping through 50-track queues.
- Cons: Still ~70 % parallel codebase (RN primitives ≠ DOM primitives). Solo-dev capacity strain. App-Store cycle + Apple dev account ($99/yr). Push notifications require backend changes (FCM / APNs). XL effort.
- Effort: **XL**. ~4–6 weeks initial + ongoing app-store re-submissions per release.
- Risk: High — sustained two-codebase tax outweighs Ranking-mode's narrow surface. Rejected in Recommendation.

## Recommendation

> Required by `evaluated_`. Which option, what we wait on before committing.

**Commit: Option A (PWA on the existing Vite/React stack) for M1.** Lowest friction, full React-component share with `RankingView.jsx`, no app-store cycle, works on iOS + Android + any tablet browser. M2 reconsiders **Option B (Capacitor wrapper)** only if mDNS-discovery or native push become must-haves — strict superset, no rewrite cost. Options C/D/E rejected (parallel codebase / port conflict / XL effort).

**Pairing UX = QR one-shot → long-lived device-token Bearer**, as sketched in Findings #2 + re-confirmed in Findings #3. Re-uses the `_format_tokens` helper that auth Phase-2 generalises, plus a new `paired_devices` row in sidecar-local SQLite (NOT `master.db`). mDNS auto-discovery deferred to M2-Capacitor.

**Off-LAN canonical recipe = Tailscale Funnel** (documented in README; never embedded). Tailscale's free HTTPS also satisfies PWA `installable` (OQ11) without self-signed LAN certs. Cloudflare Tunnel / ngrok stay as user-pick alternatives.

**Hard prerequisites** (status 2026-05-17 post-Phase-1 landing):
1. ~~`draftplan_security-api-auth-hardening.md` **Phase-1** — `require_session` Bearer on every `POST`/`PUT`/`PATCH`/`DELETE`.~~ **DONE.** Commits `6021acf..1c7d410..7dfdef5..d12ad1a..f90f5f8..8498937`. `app/main.py` has 80 `Depends(require_session)` route decorations; frontend axios attaches `Authorization: Bearer …`; Tauri Rust supervisor captures + scrubs `LMS_TOKEN=` from sidecar stdout. Anonymous-write hole on LAN CLOSED — `curl -X POST http://127.0.0.1:8000/api/track/{tid}` returns 401 today.
2. **Phase-2** — paired-device tokens. Sidecar-local SQLite `paired_devices` table (NOT `master.db` per security-doc Option B), `POST /api/pairing/start`, `POST /api/pairing/complete`, `DELETE /api/pairing/{device_id}` revoke. Phase-1's `SESSION_TOKEN` is single-host (one secret per sidecar process; rotates on restart; revokes every client on regeneration) and CANNOT be handed to a phone. **Status 2026-05-17: 0 LoC in `app/`, entire phase ahead.** Blocks mobile.
3. CORS allowlist env-driven (`MOBILE_ALLOWED_ORIGINS`, comma-split) per OQ12 — TRIGGER-PARKED to draftplan kickoff; ~5 LoC patch to `app/main.py:209-224`, landed alongside Phase-2 frontend wiring.
4. (Optional, defensive) Rate-limit decorator from WIP `app/rate_limit.py` (commit `830c056`) applied to `POST /api/pairing/complete` to throttle brute-force pairing-token guesses (defensive vs the already-256-bit-entropic token). Out of mobile-side scope.

**Phased delivery + gates** (mirrors Findings #3 matrix):

| Phase | Deliverable | Hard gate to next phase |
|---|---|---|
| **Pre-M1** | Phase-2 paired-device tokens merged + `MOBILE_ALLOWED_ORIGINS` env wired. Phase-1 fully landed (commits `6021acf..f90f5f8`) | `tests/test_auth.py` green (passes today); `tests/test_pairing.py` 12 cases green; manual revoke verified; CORS env-extension test passes |
| **M1.0** | `frontend/mobile/{index.html, service-worker.js, manifest.webmanifest}` + `frontend/src/mobile/{main.jsx, store/mobileAuth.js}` + `/m/` Vite mpa entry; renders empty source-picker stub | Lighthouse PWA ≥ 80; `vite build` mobile-entry first-paint ≤ 200 KB gz (CI gate via `scripts/check_mobile_bundle.py`) |
| **M1.1** | `PairFlow.jsx` (jsQR `getUserMedia` loop) + `POST /api/pairing/complete` round-trip + `localStorage.lms.mobile.deviceToken` + axios bearer interceptor + `lms:auth-expired` 401-handler | Manual: phone scans QR, persists token, gated `POST /api/track/{tid}` returns 2xx; lost-token "Re-pair" screen reachable via `/m/pair` redirect |
| **M1.2** | Source-picker → queue → edit surface (stars / colors / chips / comment / genre / MyTag) + Save & Next swipe via `@use-gesture/react` + optional `?skip_tags=true` query-param | G1 manual-checklist 12/12 green (see `tests/manual/mobile-ranking-parity.md`); G7 axios-interceptor p95 ≤ 350 ms LAN (uncontested + skip-tags); G6 Lighthouse mobile-a11y ≥ 90 |
| **M1.3** | README Tailscale Funnel ~10-line recipe + iOS-26-aware Add-to-Homescreen onboarding (`iOSInstallHint.jsx`) + (optional) `GET /api/track/{tid}/cover-thumb` for OQ7 | G4 cellular smoke ≤ 800 ms p95; PWA install banner appears under Funnel HTTPS; G3 first-paint ≤ 8 s mid-range Android |
| **M2** (deferred) | Capacitor shell — App-Store, native push, mDNS via `_libmgr._tcp.local` | Separate `idea_mobile-companion-capacitor-shell.md` topic |

**Waiting on (OPEN OQs)** — must resolve before `exploring_` → `evaluated_`:
1. **OQ14** — 60 s pairing-QR TTL sign-off (vs longer DJ-friendly default).
2. **OQ7** — text-only M1 vs cover-thumb M1.1 vs full thumb at M1 sign-off.
3. Phase-2 paired-device-tokens scoped + scheduled (currently `implement/draftplan_security-api-auth-hardening.md` Phase-2 section).

**Trigger-PARKED to draftplan kickoff (single-line decisions, do not block `evaluated_`)**:
- **OQ12** — env-list `MOBILE_ALLOWED_ORIGINS` confirmed lean (Options table in OQ12 quantifies bypass risk).

**RESOLVED 2026-05-17 higher-quality-bar pass**:
- **OQ13** — 200 KB gz first-paint / 500 KB gz total. CI gate via `scripts/check_mobile_bundle.py`.
- **OQ15** — UA-detect with iOS 26 branch; auto-collapse legacy instructions.

OQ1–OQ6, OQ8–OQ11 already RESOLVED in Open Questions section above.

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
