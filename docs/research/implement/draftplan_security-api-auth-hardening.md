---
slug: security-api-auth-hardening
title: API authentication / authorization hardening — audit and gate strategy for mutation endpoints
owner: tb
created: 2026-05-15
last_updated: 2026-05-15
tags: [security, auth, fastapi, blocker, priority-1]
related: [mobile-companion-ranking-app]
priority: 1
---

# API authentication / authorization hardening — audit and gate strategy for mutation endpoints

> **State**: derived from filename + folder. Do not store state in frontmatter.
> Start the file as `docs/research/research/idea_<slug>.md`. Rename + move on each transition (see `../README.md`).

## Lifecycle

> Append-only audit trail. One line per `git mv`. Newest at the bottom.

- 2026-05-15 — `research/idea_` — created from template (declared **Hauptpriorität** by user)
- 2026-05-15 — research/idea_ — full audit + options (priority-1 research dive)
- 2026-05-15 — research/exploring_ — promoted; 5 hot-fixes shipped (commit e3a5ae8); Phase-1 draftplan next
- 2026-05-15 — research/exploring_ — Phase-1 draftplan written by route-architect
- 2026-05-15 — implement/draftplan_ — promoted; 6 open questions resolved by user sign-off; plan revised (stdout+file token handoff, no env-var; no slowapi P1; Bearer-only, no X-Session-Token compat; init-token deleted)
- 2026-05-15 — implement/draftplan_ — review feedback applied (10 must-fix + ~12 should-fix items)
- 2026-05-17 — implement/draftplan_ — Phase-1 implementation complete (commits 6021acf..f90f5f8 + 8498937 safe_compare) + doc-sync (62eb9c0 + this)

---

## Problem

> Required from `idea_` onward. Keep under 100 words. What are we solving? Why does it matter? What happens if we don't?

The FastAPI sidecar has **incomplete authentication coverage**. Discovered during the `mobile-companion-ranking-app` research audit: track-write endpoints (`POST /api/track/{id}`, `/mytags`, and likely most mutation routes) have **no auth gate today** — `X-Session-Token` (the one-shot bootstrap via `POST /api/system/init-token`) only protects shutdown / restart / lifecycle endpoints. As long as the sidecar listens on `127.0.0.1` and the host firewall is intact this is contained. The moment the API is exposed to a LAN (Mobile Companion, second-device workflow, headless server install, accidental firewall hole), **any LAN reachable client can mutate library metadata** — Rating, ColorID, Comments, tags, playlist membership — without authentication.

This doc **audits every FastAPI route** for its current auth posture and designs a coherent gating model: which routes need auth, what auth mechanism (per-request token, OAuth-style bearer, IP allowlist, mTLS, hybrid), how device pairing works, how the existing `X-Session-Token` evolves vs. gets replaced, and how the Tauri frontend keeps friction zero while LAN/remote clients go through the gate. **Hard blocker** for the mobile companion roadmap; **architectural debt** for the standalone case (defense-in-depth against firewall misconfig, malware on the same machine binding to localhost, etc.).

## Goals / Non-goals

**Goals**
- Inventory **every** FastAPI route's current auth posture in one canonical table (no more "we think most are unprotected").
- Define an explicit, layered gating model that distinguishes **read** vs **write** vs **destructive/system** routes and **same-origin** (Tauri/dev) vs **foreign-origin** (LAN/remote) callers.
- Choose an auth mechanism that adds **zero friction in Tauri** (the 99% case), **manageable friction for the mobile companion** (pairing flow), and **defense-in-depth for the standalone case** (same-machine malware / browser-XSS pivot via a malicious tab).
- Identify the **hard prerequisites** that must land before the mobile-companion roadmap (i.e. what new code / refactors gate that feature).
- Capture **adjacent security gaps** spotted during the route audit so they aren't lost (`/api/file/write` path scope, heartbeat token leak, etc.) — they get their own follow-up docs.

**Non-goals** (deliberately out of scope)
- **No new transport layer** (HTTPS / mTLS for loopback). Loopback TLS adds key-management pain with negligible benefit for the standalone case; if remote access becomes a goal it's solved by a reverse proxy (Tailscale, Cloudflare Tunnel), not by terminating TLS in uvicorn.
- **No user-account system** (multi-user, login UI, password reset). One human runs the app on one machine; "auth" here means "this caller is authorised", not "this human is identified".
- **No row-level authorisation** (per-playlist ACLs etc.). Auth is binary: gated routes accept the caller or 401.
- **No replacement of the SoundCloud-OAuth token flow** (`app/main.py:2997`+). That's third-party-credential storage, a different concern. It only enters this scope insofar as we document **where the line is** between SC tokens and our own auth.
- **No rewrite of `app/main.py` route organisation** beyond what auth requires. Cluster-by-cluster refactor lives in its own doc.

## Constraints

- **Loopback-by-default deployment.** The sidecar binds `127.0.0.1:8000` only (`app/main.py:4012` — `uvicorn.run(app, host="127.0.0.1", port=8000)` and the log line at `app/main.py:1962`). The kernel firewall is the *first* defense; auth is layered on top. Any solution must remain usable when the bind is later widened (mobile companion → `0.0.0.0:8000` or `127.0.0.1` + reverse proxy).
- **Tauri / browser-dev parity.** The same frontend bundle runs in two contexts: Tauri (same machine, same human) and browser dev (`vite` proxy → `127.0.0.1:8000`, `app/main.py:215-216`). Whatever auth scheme we pick must work in both without forking the React code path. `frontend/src/api/api.js:6` detects Tauri via `window.__TAURI_INTERNALS__`; that hook is where transport-level credential plumbing can branch.
- **CORS is access control, NOT auth.** The current CORS allowlist (`app/main.py:210-225`) only rejects browser fetches from non-listed origins. Any non-browser client (curl, mobile app, Python script, Postman, malware bypassing the browser entirely) is **not subject to CORS** and reaches every route. This must be called out wherever the codebase comments imply CORS = security (e.g. `# --- SECURITY: CORS locked to localhost only ---` line 209 is misleading).
- **`docs/SECURITY.md` scope.** It documents *supply-chain* hardening (Schicht A: `ignore-scripts`, `save-exact`, lockfile-lint, Dependabot). It contains **no runtime auth / API threat model** today. This research doc is the first such artifact for the project.
- **No backwards-compat burden.** There is no public API contract; no third-party clients exist. We can break the wire format freely.
- **Single human, single host.** No notion of "users" or "tenants". One token / one credential per device pairing is enough.
- **Existing `_db_write_lock` invariant** (defined at `app/database.py:22`, NOT `app/main.py` — `CLAUDE.md` / `coding-rules.md` references that imply otherwise are stale) is unaffected by auth — auth gates the request *before* the DB write path, doesn't replace it.
- **rbox version quirks** (`app/usb_one_library.py` template hack) impose no auth constraint — auth is purely an HTTP-layer concern.

## Open Questions

1. **Is loopback-only the long-term default, or is the mobile companion the trigger for `0.0.0.0` bind?** If `0.0.0.0`, IP allowlist becomes meaningless and auth becomes load-bearing for *every* request. Affects choice between Options A vs C vs E.
2. **Tauri-to-sidecar pairing — IPC handshake or shared-secret env var?** Tauri can write a token into the sidecar's env on spawn (most secure) or call a `POST /api/system/init-token` endpoint after boot (current frontend design — but no such endpoint exists today, see Findings). The env-var route avoids any "leak the token" failure mode.
3. **Mobile companion pairing UX — QR code on desktop, scanned by phone?** The standard pattern is a one-time QR rendered on the desktop UI carrying `(host, port, paired_token)`. Decision: is the QR ephemeral (single-use) or a long-lived device credential?
4. **Token storage on the desktop** — env var (process-lifetime), keyring (cross-restart, already used for SC token via `keyring` lib, `app/main.py:39`), or `%APPDATA%/.../session.json` (fragile)?
5. **Revocation model** — single global "rotate all tokens" button vs per-device revoke. Affects whether we need a paired-device table in SQLite.
6. **Rate limiting layer** — wire `slowapi` middleware now, or wait? Cheap to add upfront; expensive to retrofit after auth ships.
7. **Same-origin trust shortcut** — should requests from `Origin: tauri://localhost` skip the bearer-token check? Pro: zero-config Tauri. Con: any rogue Electron-like context on the same machine can spoof `Origin`.
8. **Read-route policy** — do read-only routes need auth at all in the loopback case? Argument *for*: defense-in-depth against a malicious local app scraping library metadata. Argument *against*: simplifies API, lowers friction.
9. **Heartbeat redesign** — current `/api/system/heartbeat` (line 899/2022) returns the shutdown token in the response. Either rename + repurpose, or remove the token leak.
10. **What happens on token mismatch — 401 with error body, or silent 404?** Standard is 401; silent 404 is "deny existence" hardening but breaks the frontend's auto-refresh logic (`api.js:114-120`).

## Findings / Investigation

### 2026-05-15 — full route auth audit + threat-model framing

**Headline:** The Problem statement's premise is *understated*. There is **no `X-Session-Token` validator anywhere in `app/`** (zero grep matches across `app/**` for `X-Session-Token`, `session_token`, `init_token`, `validate_session`, `Depends`, `HTTPBearer`, `Security(`, `Authorization` for our own use, `api_key`). The frontend (`frontend/src/api/api.js:86-88`) attaches the header on every request, but the backend silently ignores it. The only thing that *does* gate anything today is `SHUTDOWN_TOKEN` (a `secrets.token_urlsafe(32)` at `app/main.py:125`) read by exactly **two** routes via a query-string `token=` param, **NOT** the header. And the token is leaked back to anyone who POSTs `/api/system/heartbeat` (line 899 and duplicate at line 2022 both return `{"status": "alive", "token": SHUTDOWN_TOKEN}`). Effective auth surface today: **zero routes meaningfully protected**.

#### Existing auth machinery (full survey)

| Mechanism | Location | What it actually does |
|---|---|---|
| `SHUTDOWN_TOKEN` | `app/main.py:125` (`secrets.token_urlsafe(32)`) | Per-process random secret. Compared in plain `==` at `main.py:2031` (`/api/system/shutdown`) and `main.py:2040` (`/api/system/restart`) against a **query-string** `token=` parameter, not the `X-Session-Token` header. |
| `/api/system/heartbeat` token leak | `app/main.py:899-903`, **duplicate** `app/main.py:2022-2026` | Anyone (no auth) who POSTs the heartbeat receives `SHUTDOWN_TOKEN` in the JSON body. Effectively neuters the only auth gate that exists. Browser CORS would block a malicious page, but a non-browser caller on the LAN, or any process on the same machine, walks through. |
| Frontend `_sessionToken` plumbing | `frontend/src/api/api.js:21-23,86-88`; `frontend/src/main.jsx:549-552` | React calls heartbeat at startup, captures `res.data.token`, then attaches it as `X-Session-Token` on every subsequent request. Backend never reads this header. |
| `_format_tokens` USB-format gate | `app/main.py:2340-2442` | One-shot per-drive 60-second token issued by `/api/usb/format/preview`, consumed by `/api/usb/format/confirm`. **This is the only correctly-implemented capability-style gate in the codebase** — short-lived, scoped to one operation, server-issued. Good pattern to generalise. |
| CORS allowlist | `app/main.py:210-225` | Localhost + `tauri://localhost` + `https://tauri.localhost`. **Browser-only enforcement** — does not gate non-browser callers. |
| SoundCloud OAuth | `app/main.py:2997` (`POST /api/soundcloud/auth-token`), keyring at `KEYRING_SC_TOKEN = "sc_token"` line 67 | Third-party credential storage. Not our auth. |
| `validate_audio_path` sandbox | `app/main.py:168-198` | Path-traversal sandbox for **audio reads only**. Uses `str.startswith(str(root))` (line 188) — not `Path.is_relative_to`, contrary to what `CLAUDE.md` claims. Has an escape hatch (line 192-194) accepting any path already known to `db.tracks`. **Not** applied to `/api/file/write` (line 580). |
| `requirepre-commit` / `--no-verify` deny | `.claude/settings.json` | Process discipline, not runtime. |

So: one ineffective gate (shutdown), one correct narrow gate (USB format), one phantom gate (`X-Session-Token` header — frontend ships it, backend ignores it). Everything else is open.

#### Per-route auth posture table

Counted **141 routes** in `app/main.py` (`@app.{get|post|put|patch|delete}` decorator count). Read = no mutation. Write = mutates `master.db`, filesystem, or external service. Destructive = irreversible (shutdown, delete, format, rename-on-disk). Gate column: `none` (no auth), `shutdown_tok` (query-param check vs `SHUTDOWN_TOKEN`), `format_tok` (one-shot USB token), `none*` (host-FS-write but inside library scope).

| Domain | Method + Path | Line | Kind | Current gate |
|---|---|---|---|---|
| **stream/media** | GET `/api/stream` | 459 | read | none |
| | GET `/api/audio/waveform` | 544 | read | none |
| | GET `/api/audio/stream` | 783 | read | none |
| | GET `/api/artwork` (StaticFiles mount) | 268 | read | none |
| **file ops** | POST `/api/file/reveal` | 558 | write (spawns OS shell) | **none** — `subprocess.run(["explorer","/select,",path])` with user-controlled path. No sandbox. |
| | POST `/api/file/write` | 580 | write (FS) | **none** — comment line 584 literally says "we trust the path". Writes anywhere on disk if absolute. |
| | POST `/api/xml/clean` | 600 | write (FS) | none — fixed target path, file-extension check only |
| **library reads** | GET `/api/genres` | 631 | read | none |
| | GET `/api/library/tracks` (dup at 1554) | 634, 1554 | read | none |
| | GET `/api/insights/low_quality` | 644 | read | none |
| | GET `/api/insights/no_artwork` | 667 | read | none |
| | GET `/api/insights/lost` | 675 | read | none |
| | GET `/api/labels` | 697 | read | none |
| | GET `/api/albums` | 700 | read | none |
| | GET `/api/artists` | 713 | read | none |
| | GET `/api/artist/{aid}/tracks` (dup 1585) | 719, 1585 | read | none |
| | GET `/api/label/{aid}/tracks` (dup 1601) | 722, 1601 | read | none |
| | GET `/api/album/{aid}/tracks` (dup 1617) | 725, 1617 | read | none |
| | GET `/api/track/{tid}` | 728 | read | none |
| | GET `/api/track/{tid}/cues` | 737 | read | none |
| | GET `/api/track/{tid}/beatgrid` | 740 | read | none |
| | GET `/api/playlist/{pid}/tracks` | 1564 | read | none |
| | GET `/api/playlists/tree` | 927 | read | none |
| | GET `/api/playlists/smart/{pid}/evaluate` | 1021 | read | none |
| | GET `/api/library/status` | 1164 | read | none |
| | GET `/api/library/folder-watcher/status` | 1891 | read | none |
| | GET `/api/mytags` | 810 | read | none |
| | GET `/api/track/{tid}/mytags` | 840 | read | none |
| | GET `/api/tools/duplicates` | 1091 | read | none |
| **library writes** | POST `/api/track/{tid}` | 862 | write (master.db) | **none** ← Rating/ColorID/Comment/Genre |
| | PATCH `/api/tracks/batch` | 896 | write | **none** ← bulk metadata patch |
| | POST `/api/track/{tid}/analyze` | 743 | write | none |
| | POST `/api/track/cues/save` | 856 | write | none |
| | POST `/api/track/grid/save` | 859 | write | none |
| | DELETE `/api/track/{tid}` | 964 | destructive | none |
| | POST `/api/track/delete` | 905 | destructive | none |
| | POST `/api/tracks/move` | 908 | write | none |
| | POST `/api/tools/rename` | 913 | **destructive (renames on disk)** | none |
| | POST `/api/metadata/merge` | 703 | write | none |
| | POST `/api/mytags` | 815 | write | none |
| | DELETE `/api/mytags/{tag_id}` | 829 | write | none |
| | POST `/api/track/{tid}/mytags` | 845 | write | none |
| | POST `/api/playlists/create` | 938 | write | none |
| | POST `/api/playlists/rename` | 943 | write | none |
| | POST `/api/playlists/move` | 946 | write | none |
| | POST `/api/playlists/delete` | 949 | destructive | none |
| | POST `/api/playlists/add-track` | 952 | write | none |
| | POST `/api/playlists/remove-track` | 955 | write | none |
| | POST `/api/playlists/reorder` | 971 | write | none |
| | POST `/api/playlists/smart/create` | 998 | write | none |
| | POST `/api/playlists/smart/update` | 1012 | write | none |
| | POST `/api/playlists/folder/create` | 1075 | write | none |
| | POST `/api/tools/batch-comment` | 1100 | write (subprocess + master.db) | none |
| | POST `/api/library/clean-titles` | 1160 | write | none |
| | POST `/api/library/mode` | 1175 | write | none |
| | POST `/api/library/sync` | 1190 | write | none |
| | POST `/api/library/load` | 1206 | write | none |
| | POST `/api/library/unload` | 1226 | write | none |
| | POST `/api/library/new` | 2060 | **destructive (creates lib)** | none |
| | POST `/api/library/scan-folder` | 1287 | write | none |
| | POST `/api/library/import-paths` | 1327 | write | none |
| | POST `/api/library/smart-playlists` | 1266 | write | none |
| | POST `/api/library/folder-watcher/{add,remove}` | 1899, 1922 | write | none |
| | POST `/api/library/analyze-batch` | 2635 | write | none |
| | GET `/api/library/analyze-status` | 2673 | read | none |
| | POST `/api/track/{tid}/analyze-full` | 2605 | write | none |
| | POST `/api/audio/analyze` | 2579 | write | none |
| | GET `/api/audio/analyze/{task_id}` | 2592 | read | none |
| | GET/POST `/api/settings` | 1819, 1825 | read/write | none |
| **rekordbox export/import** | POST `/api/rekordbox/export` | 1233 | write (file) | none |
| | POST `/api/rekordbox/import` | 1248 | write (master.db) | none |
| **projects** | GET `/api/projects` | 1633 | read | none |
| | POST `/api/projects/save` | 1646 | write | none |
| | GET `/api/projects/{name}` | 1653 | read | none |
| | GET `/api/projects/rbep/list` | 2564 | read | none |
| | GET `/api/projects/rbep/{name}` | 2569 | read | none |
| | POST `/api/artist/soundcloud` | 1663 | write | none |
| **audio edit** | POST `/api/audio/slice` | 1673 | write | none |
| | POST `/api/audio/render` | 1687 | write | none |
| | POST `/api/audio/import` | 1734 | write | none |
| **import tasks** | GET `/api/import/tasks` | 1492 | read | none |
| | POST `/api/import/tasks/clear` | 1499 | write | none |
| **system / lifecycle** | POST `/api/system/heartbeat` (dup at 2022) | 899, 2022 | read | **none — leaks `SHUTDOWN_TOKEN`** |
| | POST `/api/system/shutdown` | 2028 | **destructive** | `shutdown_tok` (query-string `?token=`) |
| | POST `/api/system/restart` | 2037 | **destructive** | `shutdown_tok` (query-string `?token=`) |
| | POST `/api/system/select_db` | 2053 | write (no-op today) | none |
| | POST `/api/debug/load_xml` | 2075 | write | none — also `/api/debug/*` should never be exposed in prod |
| **USB / Pioneer** | GET `/api/usb/devices` | 2120 | read | none |
| | GET/POST `/api/usb/profiles` | 2125, 2130 | read/write | none |
| | DELETE `/api/usb/profiles/{device_id}` | 2136 | destructive | none |
| | GET `/api/usb/{device_id}/contents` | 2143 | read | none |
| | GET `/api/usb/diff/{device_id}` | 2149 | read | none |
| | POST `/api/usb/sync` (+ `/sync/all`) | 2196, 2241 | write (USB) | none |
| | POST `/api/usb/profiles/prune` | 2228 | write | none |
| | POST `/api/usb/eject` | 2253 | destructive (drive) | none |
| | POST `/api/usb/reset` | 2258 | destructive | none |
| | POST `/api/usb/initialize` | 2269 | write | none |
| | POST `/api/usb/history` | 1035 | write | none |
| | GET `/api/usb/mysettings/schema` | 2288 | read | none |
| | GET `/api/usb/mysettings/{device_id}` | 2296 | read | none |
| | POST `/api/usb/mysettings` | 2314 | write | none |
| | POST `/api/usb/format/preview` | 2356 | issues `_format_tokens` | none (pre-token) |
| | POST `/api/usb/format/confirm` | 2421 | **destructive — formats drive** | `format_tok` (correct one-shot) |
| | POST `/api/usb/rename` | 2457 | write | none |
| | GET/POST `/api/usb/settings` | 2465, 2477 | read/write | none |
| | GET `/api/usb/playcount/diff` | 3455 | read | none |
| | POST `/api/usb/playcount/resolve` | 3503 | write | none |
| **duplicates** | POST `/api/tools/duplicates/merge` (+ `/merge-all`) | 2485, 2521 | write | none |
| | POST `/api/duplicates/scan` | 3868 | write | none |
| | GET `/api/duplicates/results` | 3903 | read | none |
| | POST `/api/duplicates/merge` | 3921 | write | none |
| **phrase generator** | POST `/api/phrase/generate` | 3563 | write | none |
| | POST `/api/phrase/commit` | 3632 | write (master.db, ANLZ files) | none |
| **SoundCloud** | POST `/api/soundcloud/download` (+ `/download-playlist`) | 2740, 2831 | write (FS + master.db) | none |
| | GET `/api/soundcloud/tasks` | 2907 | read | none |
| | GET `/api/soundcloud/task/{task_id}` | 2913 | read | none |
| | GET `/api/soundcloud/history` (+ `/stats`) | 2924, 2957 | read | none |
| | GET `/api/soundcloud/check/{sc_track_id}` | 2969 | read | none |
| | DELETE `/api/soundcloud/history/{sc_track_id}` | 2979 | destructive | none |
| | POST `/api/soundcloud/auth-token` | 2997 | **writes SC OAuth to keyring** | none |
| | GET/PUT `/api/soundcloud/settings` | 3053, 3068 | read/write | none |
| | GET `/api/soundcloud/playlists` | 3078 | read | none |
| | GET `/api/soundcloud/me` | 3143 | read | none |
| | POST `/api/soundcloud/sync` (+ `/sync-all`, `/merge`) | 3174, 3265, 3304 | write | none |
| | POST `/api/soundcloud/preview-matches` | 3220 | read (heavy) | none |

**Summary tally:** ~141 routes; **~135 unauthenticated mutation/destructive routes**; 2 routes use the (leaky) `SHUTDOWN_TOKEN` query-param scheme; 1 route (`/api/usb/format/confirm`) uses a proper one-shot capability token. Read-only routes ≈ 50; the rest are writes.

#### Threat model — three deployment shapes

**(a) Standalone loopback (current default).** Sidecar on `127.0.0.1:8000`. CORS blocks browser fetches from non-allowlisted origins. Concrete threats:
- **Same-machine malware / unprivileged process.** Any local process can `curl http://127.0.0.1:8000/api/track/123 -X POST -d '{"Rating":0}'` and there is no gate. With ~135 unauth'd writers this is a full library-overwrite primitive. Realistic risk: medium (requires existing local compromise but no privilege escalation).
- **Browser-XSS / drive-by from a malicious local HTML file.** A page opened from `file://` or any allowlisted origin (e.g. a stale `localhost:5173` dev server) can hit the API. CORS is the only barrier. `tauri://localhost` is in the allowlist (line 219-220), so any process that can render under that scheme bypasses CORS too. Realistic risk: low-medium.
- **CSRF from a regular web page** — `withCredentials: true` (`api.js:17`) means a session cookie *would* be sent, but we don't currently use cookies for our own auth, so classic CSRF doesn't apply to mutation routes (no ambient credential to ride on). Future-auth schemes must keep this property — bearer-in-header > cookie.
- **Local data exfil.** A malicious local app reads the entire library via the read routes. Lower severity than mutation, but worth noting.

**(b) LAN-exposed (mobile companion, second-device, headless install, accidental firewall hole).** Sidecar on `0.0.0.0:8000` *or* loopback + reverse proxy. Concrete threats:
- **Anyone on the network mutates the library** without authentication. Library rating, playlists, deletions, USB format, OS-level file writes (`/api/file/write`), filesystem rename (`/api/tools/rename`), SC OAuth token *overwrite* (`/api/soundcloud/auth-token`). All open.
- **Drive-by from network attacker on shared WiFi (café, hotel).** Same as (a) but no compromise needed first.
- **Shodan / mass-scanner discovery** once exposed to a public-routable LAN.

**(c) Remote tunnel (Tailscale / Cloudflare Tunnel / reverse-proxied behind nginx-basic-auth).** Outer transport is authenticated (Tailscale identity / Cloudflare Access). Threats:
- **Defense-in-depth still required** — if the tunnel is misconfigured (open ACL, exit-node footgun) the inner API is naked again. Bearer-token gate on the API itself is the second wall.
- **Replay attacks against `/api/system/shutdown`** if the token is ever logged or copied; mitigated by binding the token to a header (not query string).

#### Tauri vs browser context — current state

- **Tauri sidecar spawn** (`src-tauri/src/main.rs:123-128`): Rust spawns the Python sidecar process. No env-var token passing, no IPC handshake. Sidecar boots, generates `SHUTDOWN_TOKEN` privately at `app/main.py:125`, and the only way out is the heartbeat-leak. The Tauri Rust side does **not** know the token; it relies on the embedded React frontend to fetch it. There is **no Rust-side auth client** to the sidecar at all — the frontend is the only HTTP caller.
- **Browser dev mode** (`npm run dev:full`): frontend on `5173`, sidecar on `8000`, vite proxies. Same heartbeat-leak flow. No special handling.
- **The result:** in both contexts, "auth" is "ask the unauthenticated heartbeat endpoint for the secret, then attach it to a header the backend ignores". Theatre, not security.

#### Adjacent findings (small flag-list — followups, not in scope)

1. **`/api/file/write` (line 580)** — unsandboxed arbitrary-path file write. Comment line 584 admits this. Even with auth, this endpoint is dangerous; it should be scoped to `EXPORT_DIR` / project paths only.
2. **`/api/file/reveal` (line 558)** — passes user-controlled path to `subprocess.run(["explorer","/select,",path])`. The argument is positional (not shell=True) so command injection is hard, but path validation is absent. Worth a sandbox check.
3. **`/api/system/heartbeat` token leak** (lines 899-903 and **duplicated** 2022-2026). Two heartbeat handlers exist — duplicate route registration. FastAPI uses the last one wins (or first, depending). Either way, both leak the token. Fix: drop the token from the response.
4. **`/api/debug/load_xml` (line 2075)** — debug endpoint shipped in production builds. Should be gated behind an env flag or removed.
5. **`validate_audio_path` uses `str.startswith`** (line 188), not `Path.is_relative_to`. `CLAUDE.md`'s `coding-rules.md` claims the codebase uses `is_relative_to` — drift. `startswith` can be fooled by path-prefix tricks (`/home/user/musicX/...` matching root `/home/user/music`).
6. **`validate_audio_path` allowlist escape** (line 192-194) — any path already in `db.tracks` is accepted, even outside `ALLOWED_AUDIO_ROOTS`. Library-poisoning + read-anywhere primitive.
7. **No rate limiting anywhere.** `slowapi` / `fastapi-limiter` not installed.
8. **`secrets.compare_digest` not used** for `SHUTDOWN_TOKEN` comparison (`==` at lines 2031, 2040). Timing-attack relevant if/when this becomes load-bearing.
9. **CORS `allow_credentials=True` + `allow_methods=["*"]` + `allow_headers=["*"]`** (lines 222-224) is broadly permissive. If we add cookie-based auth later this combination becomes a real issue. Keep bearer-in-header to sidestep.
10. **Pydantic `extra: allow` on `SetReq`** (line 316) lets the settings POST accept any field. Combined with unauth, this is a stored-blob primitive — anyone can dump arbitrary keys into `settings.json`.
11. **Error handler at line 254** logs full exceptions with `exc_info=True`. Stack traces may leak filesystem paths despite `safe_error_message` redaction; redaction is applied to client responses (line 260) but not to log files.
12. **`/api/soundcloud/auth-token` (line 2997) accepts unauth'd writes to keyring.** Anyone on the network can overwrite the user's SC OAuth token (denial-of-service of SC integration, or substitution attack if attacker controls a fake SC).

## Options Considered

## Options Considered

> Required by `evaluated_`. For each viable approach: sketch (2-4 lines), pros, cons, effort (S/M/L/XL), risk.

### Option A — Single global bearer token in `X-Session-Token` header, FastAPI dependency on a gated subset
- Sketch: At sidecar startup, Tauri Rust generates a `secrets.token_urlsafe(32)` and passes it to the Python sidecar via env var (`MLM_SESSION_TOKEN`) on spawn. Tauri also exposes it to the React frontend via a `tauri::command` (`get_session_token`). Frontend calls `setSessionToken(token)` from `App` boot (replaces the heartbeat-leak dance). Backend defines `def require_session(x_session_token: str = Header(...))` and applies it as `Depends(require_session)` on every mutation/destructive route. Read routes optionally gated by config flag.
- Pros: minimal moving parts, single secret, fits existing frontend plumbing (the header is already attached at `api.js:87`), kills the heartbeat leak, works identically in Tauri + browser-dev (browser-dev would need a dev-only fallback token from env). Compatible with reverse-proxy / Tailscale.
- Cons: single token = full keys-to-the-kingdom; no per-device scoping or revoke; if leaked the only mitigation is restart. Doesn't solve "mobile companion needs its own credential" — would need Option B layered on top.
- Effort: **S–M** (one dependency function, one decorator across ~135 routes — can be done via `app.router.routes` introspection or a middleware that route-matches a denylist of unauth'd paths, e.g. heartbeat + healthcheck).
- Risk: low; the failure mode is "all-or-nothing" — easy to test.

### Option B — Per-device paired tokens (QR pairing flow, SQLite-backed token table)
- Sketch: Introduce `paired_devices(token_hash, device_name, created_at, last_seen, revoked)` table in `master.db` (or sidecar-local SQLite). New routes: `POST /api/pairing/start` (desktop UI, generates one-shot pairing code rendered as QR), `POST /api/pairing/complete` (mobile/second-device POSTs the code → receives long-lived bearer). Bearer carried in `Authorization: Bearer …`. Tauri main-frontend gets its bearer at sidecar boot the same env-var way as Option A. `require_session` dep accepts either the main token or a non-revoked paired bearer.
- Pros: proper device model, individual revoke, audit log of who-did-what, future-proof for the mobile companion. Pairing UX is the standard pattern.
- Cons: significantly more code (DB migration, pairing UI on both ends, revoke UI, expiry policy), and brings the "auth state" into `master.db` which conflicts with the "no shared DB schema changes lightly" rule. Could use a sidecar-local SQLite (`session_state.db`) to avoid touching `master.db`.
- Effort: **L** (DB + UI on desktop + protocol design + tests).
- Risk: medium; pairing-flow security is easy to get subtly wrong (replay, race, code-guessing). Mitigations are well-known (rate limit pairing endpoint, 60-s expiry on pairing code).

### Option C — IP allowlist (loopback-only by default, opt-in LAN range), no app-layer auth
- Sketch: FastAPI middleware checks `request.client.host`. Default allow = `{127.0.0.1, ::1}`. User opts into a CIDR (`192.168.1.0/24`) via settings. Combine with the current loopback bind for defense-in-depth.
- Pros: zero friction (no token to attach), defends against the "browser-XSS pivot" because XSS calls go through `localhost` which we still accept — wait, that's actually the failure mode: this option does NOT defend (a) same-machine attacker, only network-layer threats. Easy to ship, easy to revert.
- Cons: useless against same-machine threats; useless once we LAN-expose for the mobile companion (the companion is on the LAN, the attacker is on the same LAN — allowlist has to admit both). Spoofable on a hostile LAN (ARP). Reverse-proxy / Tailscale already does this layer better.
- Effort: **S**.
- Risk: low to ship, but high in "false sense of security" — does not address the actual threat model. Should be a complement, not a replacement.

### Option D — mTLS (mutual TLS, client certs)
- Sketch: Generate a CA on first run, issue a client cert to each device, sidecar terminates HTTPS with client-cert verification. The "auth" is "you presented a cert signed by our CA".
- Pros: cryptographically strong, no bearer-token-in-header attack surface, scales to many devices.
- Cons: heavy operational pain — cert distribution, renewal, revocation lists, mobile-app cert installation UX is awful, debugging is painful, breaks every `curl`/Postman workflow. For a DJ tool, gross overkill.
- Effort: **XL**.
- Risk: high friction risk; high likelihood of users disabling it.

### Option E — Hybrid: env-var bearer (Option A) + paired-device tokens (Option B), with IP allowlist (Option C) as the network-layer defense
- Sketch: Layered. (1) **Network layer:** loopback-only by default, opt-in CIDR for LAN exposure (Option C). (2) **App layer:** `require_session` dependency on every mutation / destructive route; accepts either the boot-time session token (Tauri/dev path) or a non-revoked paired-device bearer (mobile companion / remote). (3) **One-shot capabilities** for high-risk operations (USB format already does this; extend pattern to `/api/system/shutdown`, `/api/file/write`, `/api/library/new`, `/api/soundcloud/auth-token` overwrite). (4) **Heartbeat redesign:** heartbeat is auth-gated, never returns a token.
- Pros: defense-in-depth, addresses all three deployment shapes, doesn't paint into a corner. Each layer is independently testable and can ship incrementally — Option A first, Option B when the mobile companion is ready.
- Cons: more design surface; need to be careful not to over-engineer the early phase. Bigger total scope, but it's incremental.
- Effort: **M** for phase 1 (A + C + one-shot capabilities for the dangerous endpoints), **L** when adding B.
- Risk: low if phased; medium if all shipped at once.

## Recommendation

**Direction: Option E (hybrid), phased.**

Phase 1 (ship now, unblocks standalone hardening — can land before mobile companion):
1. **Tauri-injected boot token** (env var `MLM_SESSION_TOKEN` from Rust → Python `os.environ` at spawn; Rust also surfaces it to the frontend via a `tauri::command`). Removes the heartbeat-leak entirely.
2. **`require_session` FastAPI dep** applied to **all mutation / destructive routes** via a route-classification pass — concretely: every route NOT in a small `READ_ONLY_ALLOWLIST` (genres, library/tracks, insights, artists, labels, albums, playlists/tree, track GETs, …) gets the dep. Heartbeat itself becomes an unauth'd healthcheck that returns `{"status":"alive"}` only — no token in the body.
3. **`secrets.compare_digest`** for the token check (constant-time).
4. **Loopback bind stays default**; document explicit opt-in for `0.0.0.0`.
5. **Adjacent quick wins from the audit:** kill duplicate heartbeat handler, scope `/api/file/write` to project dir, gate `/api/debug/*` behind an env flag, fix `validate_audio_path` to use `Path.is_relative_to`, remove the `db.tracks` escape hatch in path validation.

Phase 2 (hard prerequisite for the mobile companion):
6. **Paired-device tokens** (Option B) in a sidecar-local SQLite (do NOT touch `master.db`). QR pairing UI on desktop, scan flow on mobile. Long-lived per-device bearer in `Authorization: Bearer …`. `require_session` accepts either header.
7. **Revoke UI** on desktop (list paired devices, revoke one).
8. **Rate limiting** (`slowapi`) on pairing endpoints and on `require_session` 401s.
9. **One-shot capability tokens** generalised — the `_format_tokens` pattern extracted into a reusable helper and applied to `/api/system/shutdown`, `/api/system/restart`, `/api/library/new`, `/api/usb/eject`, `/api/usb/reset` (anything destructive at filesystem or drive level).

Hard gates:
- **The mobile-companion roadmap is BLOCKED on Phase 1 + Phase 2 (steps 1, 2, 6).** No mobile feature ships until paired-device auth lands. This is explicit in the doc title's "blocker" tag.
- **Phase 1 alone is sufficient to declare the standalone product "auth-hardened"** and to allow safe loopback exposure during development of the mobile pieces.

Open against this recommendation: Open Questions 2 (env-var vs IPC handshake — leaning env-var), 7 (Origin-based shortcut — leaning no, treat Tauri the same as everything else), 8 (read-route policy — leaning gate everything once Phase 1 is in, because the cost is one header and the defense-in-depth gain is real).

---

## Decisions — 2026-05-15 user sign-off

The 6 open questions blocking promotion to `implement/draftplan_` are resolved as follows. **These supersede the original draftplan where they conflict.**

| Topic | Decision | Why |
|-------|----------|-----|
| **Tauri-to-sidecar pairing** (OQ 2) | **Sidecar self-generates token; writes to file + prints `LMS_TOKEN=<v>` line to stdout.** NO env-var pass-through. | env-var would be inherited by `ProcessPoolExecutor` workers in `app/anlz_safe.py` (rbox quarantine pool); crash-dump leak risk; `/proc/<pid>/environ` exposure on Linux. Stdout + file path neutralises all three. |
| **Token storage on desktop** (OQ 4) | **`%APPDATA%/MusicLibraryManager/.session-token`** (Windows-User-ACL; equivalent path on Linux/macOS via `platformdirs`). | Matches existing `keyring` lib location (already used for SC OAuth — `app/main.py:39`). Cannot accidentally land in git. Not in typical OneDrive/Time-Machine backup roots. |
| **Rate limiting** (OQ 6) | **Phase 2** — drop from Phase 1. | Keep Phase-1 surface lean; rate-limit needs its own design (per-IP vs per-token, header-attribution under reverse proxy). |
| **Legacy `SHUTDOWN_TOKEN` query-string scheme** | **Delete entirely.** Redundant under `require_session` Bearer gate. | No public API → no need to deprecate gracefully. |
| **`X-Session-Token` backwards-compat window** | **Hard cut.** Bearer-only from day one. | No public API → no consumers to migrate. |
| **`POST /api/system/init-token` endpoint fate** | **Confirmed absent.** No backend deletion needed (grep returns 0 hits); strip stale references in frontend + docs. | Endpoint doesn't exist today (frontend calls a non-existent route — pure theatre per Findings). New auth model makes it unnecessary: Tauri reads stdout, browser-dev reads file. |
| **Token rotation policy** | **Rotates only on sidecar process restart** (Phase 1 — no per-request or time-based rotation). | Long-running session (>12 h) keeps the same token. User can force-rotate by hitting `/api/system/restart`. Phase 2 paired tokens have separate per-device lifecycle. |

**Open Questions still un-answered** (don't block Phase 1, parked for Phase 2):
- OQ 1 (loopback-only forever vs `0.0.0.0` for mobile) — Phase 2 decides.
- OQ 3 (mobile QR pairing UX) — Phase 2.
- OQ 5 (revocation model) — Phase 2.
- OQ 7 (same-origin trust shortcut) — Phase 1 default: NO shortcut, Tauri treated identical to LAN client.
- OQ 8 (read-route policy) — Phase 1 default: read routes stay open, loopback-gated.
- OQ 9 (heartbeat redesign) — resolved: heartbeat strictly `{"status":"alive"}`, no token field.
- OQ 10 (401 vs 404 on mismatch) — Phase 1 default: 401 with error body (standard).

## Implementation Plan

> Required from `implement/draftplan_` onward. Concrete enough that someone else could execute it without re-deriving the design.

### Preconditions (already shipped — not part of this plan)

The following 5 hot-fixes landed in commit `e3a5ae8` and are assumed in-place. Phase 1 builds on top:

1. `/api/system/heartbeat` token leak gated to loopback callers only (still leaks on `127.0.0.1` — Phase 1 removes the leak entirely).
2. Duplicate heartbeat handler (lines 2022-2026) removed.
3. `validate_audio_path` switched from `str.startswith` to `Path.is_relative_to(resolved_root)`.
4. `/api/debug/load_xml` gated behind `LMS_ENABLE_DEBUG_ROUTES` env flag.
5. `/api/file/write` sandboxed to `ALLOWED_AUDIO_ROOTS` + extension allowlist.

### Scope

- **In (Phase 1 — Option E first half):**
  - Tauri-injected boot-token via env var (`LMS_AUTH_TOKEN`); sidecar reads at boot; Tauri exposes to frontend via IPC.
  - `app/auth.py` new module — boot-token loader + `require_session` FastAPI dependency (`Authorization: Bearer …` primary, `X-Session-Token` accepted for backwards-compat in Phase 1 only).
  - `Depends(require_session)` attached to **all** POST / PUT / PATCH / DELETE routes and the three lifecycle endpoints (`shutdown`, `restart`, `init-token`). Read routes stay open (loopback-gated; matches Recommendation note that Phase 1 hardens standalone first).
  - `secrets.compare_digest` for token comparison (constant-time).
  - Heartbeat: drop `token` field from response entirely; becomes an unauth'd `{"status":"alive"}` healthcheck.
  - `/api/soundcloud/auth-token` (line 2997) — keyring overwrite — explicitly gated.
  - Frontend `api.js` refactored: source of `_sessionToken` becomes Tauri IPC (`invoke('get_session_token')`) in Tauri context, or a `.lms-dev-token` file (read once at boot, served by vite dev-middleware or shipped as a Vite env var) in browser-dev context. Heartbeat-scraping logic in `main.jsx:549-552` removed.
  - **Optional bonus if cheap:** `slowapi` rate-limit middleware on `/api/system/*` and the SC auth-token route (5 req/min/IP).

- **Out (deliberately deferred to Phase 2):**
  - Paired-device tokens, QR pairing UX, `paired_devices` SQLite table.
  - Per-device revoke UI.
  - One-shot capability-token generalisation (extending the `_format_tokens` pattern to shutdown/restart/library-new/usb-eject/usb-reset).
  - Read-route gating (loopback-only continues to carry that load in Phase 1).
  - IP-allowlist middleware / `0.0.0.0` opt-in CIDR.
  - Removal of `X-Session-Token` backwards-compat header (kept in Phase 1 to avoid a flag-day rollout; removed early in Phase 2 once paired devices land).
  - mTLS, HTTPS termination in uvicorn.

### Step-by-step

0. **(Preamble — lands first)** Add `tests/conftest.py` with an autouse `auth_token` fixture that monkeypatches `app.auth.SESSION_TOKEN` to a known constant and injects `Authorization: Bearer <constant>` into the ASGITransport-driven test client. Also define a `@pytest.mark.no_auth` opt-out marker for negative tests that need to exercise the auth-rejection path (the autouse fixture skips header injection when the marker is present). This MUST land before any other step so the regression suite stays green throughout the bulk-decorator rollout.

1. **Add `app/auth.py`** — module exports: (a) `SESSION_TOKEN: str` self-generated at import-time via `secrets.token_urlsafe(32)`; written to `%APPDATA%/MusicLibraryManager/.session-token` with user-only ACL via `platformdirs.user_data_dir("MusicLibraryManager")` (cross-platform path; **`platformdirs==4.2.2` must land in `requirements.txt`** per Schicht-A pinning rule); print `LMS_TOKEN=<value>` to stdout as early as possible at boot (before any other logger output). At boot, sidecar checks for an existing `.session-token` and **overwrites silently** — a stale file from a prior crash/SIGKILL is expected because `atexit` does not fire on `TerminateProcess` / Tauri force-kill (`src-tauri/src/main.rs:394` calls `child.kill()`). (b) `require_session(authorization: str | None = Header(None)) -> None` parses the `Authorization` header by: (i) split on first whitespace (`maxsplit=1`), (ii) verify scheme equals `bearer` case-insensitively, (iii) `.strip()` the token component, (iv) reject empty or non-equal-length tokens **before** calling `secrets.compare_digest` (compare_digest requires equal-length bytes). Raises `HTTPException(401, "Unauthorized")` on any failure. Never log the token value, not even at DEBUG.
2. **Wire BOTH Tauri sidecar spawn paths to capture the token from stdout AND scrub the token line from log-forwarding.** Both readers MUST scan every stdout line for the `LMS_TOKEN=` prefix; on first match, extract+store the value AND **drop the line** (do NOT forward to `log::info!` — otherwise the token lands in Tauri's log file). All subsequent lines pass through to log-forwarding normally.
   - **Dev path** `spawn_dev_backend` at `src-tauri/src/main.rs:108-153`: child stdout reader at `:81-89` (in `spawn_child`) uses `Stdio::piped()` + `BufReader::lines()`. Modify the per-line loop to detect `LMS_TOKEN=`, capture once, skip logging that one line, then continue forwarding.
   - **Prod path** `shell.sidecar("rb-backend")` at `src-tauri/src/main.rs:332-365`: uses `tauri_plugin_shell::process::CommandEvent::Stdout` event stream. The `CommandEvent::Stdout(line)` arm at `:349-351` currently does `log::info!("backend: {}", ...)` unconditionally — add the same `LMS_TOKEN=` detect-and-drop logic before the `log::info!` call.
   - Both sites store the captured value in a shared `tauri::State<Mutex<String>>`. **No env-var pass-through; no shared secret pre-staged.**
3. **Add Tauri IPC command `get_session_token`** in `src-tauri/src/lib.rs` (returns the stored token to the frontend on demand). Register in the `invoke_handler` macro. Document with `///` + `# Errors` per `coding-rules.md`. Returns `Err("token-not-ready")` if frontend asks before stdout-reader resolved the line (race-handle).
4. **Audit-table → classification rule** — apply by HTTP method, not by path, to minimise per-route edits:
   - **All `POST`/`PUT`/`PATCH`/`DELETE` decorators in `app/main.py`** receive `dependencies=[Depends(require_session)]` (or per-handler `Depends`), **with one explicit exception left open in Phase 1:** `POST /api/system/heartbeat` (becomes the unauth'd healthcheck). `POST /api/system/init-token` is **deleted** per the Decisions section.
   - **All `GET` routes** stay open (loopback-gated). Includes `/api/stream`, `/api/audio/waveform`, `/api/library/tracks`, all `/api/track/{tid}` reads, `/api/usb/devices`, `/api/soundcloud/me`, `/api/projects`, etc.
   - **Domain summary (in/out for the gate in Phase 1):**
     | Domain | Phase-1 rule |
     |---|---|
     | track / mytags / playlist / playlists-smart / playlists-folder | all POST/PATCH/DELETE → gated |
     | library (sync, load, unload, new, scan-folder, import-paths, smart-playlists, clean-titles, mode, folder-watcher add/remove, analyze-batch) | all POST → gated |
     | file ops (`/api/file/reveal`, `/api/file/write`, `/api/xml/clean`) | all POST → gated (write already path-sandboxed via hotfix) |
     | system (`shutdown`, `restart`, `select_db`) | all POST → gated; `heartbeat` stays open; `init-token` deleted |
     | soundcloud (`download`, `download-playlist`, `auth-token`, `sync`, `sync-all`, `merge`, `history` DELETE, `settings` PUT) | all writes → gated. **Explicit:** `auth-token` at `app/main.py:3048` (post-hotfix line). |
     | usb (`sync`, `sync/all`, `profiles` POST, `profiles/prune`, `profiles/{id}` DELETE, `eject`, `reset`, `initialize`, `history`, `mysettings`, `rename`, `settings` POST, `playcount/resolve`) | all writes → gated. `format/preview` + `format/confirm` keep `_format_tokens` (capability) **in addition to** `require_session`. |
     | analysis (`/api/audio/analyze`, `/api/track/{tid}/analyze`, `/api/track/{tid}/analyze-full`, `/api/library/analyze-batch`) | all POST → gated; status GETs stay open |
     | duplicates (`tools/duplicates/merge`, `merge-all`, `duplicates/scan`, `duplicates/merge`) | all POST → gated |
     | phrase (`phrase/generate`, `phrase/commit`) | all POST → gated |
     | projects (`projects/save`, `artist/soundcloud`) | all POST → gated |
     | audio-edit (`audio/slice`, `audio/render`, `audio/import`) | all POST → gated |
     | rekordbox (`rekordbox/export`, `rekordbox/import`) | all POST → gated |
     | import-tasks (`import/tasks/clear`) | POST → gated |
     | settings (`POST /api/settings`) | gated |
     | debug | already env-flagged via hotfix; also add `Depends(require_session)` for defense-in-depth |
     | StaticFiles mounts (`/exports`, `/api/artwork`) | acknowledged out-of-scope: GET-only, stay loopback-gated, no `Depends(require_session)` (mounts don't support deps the same way) |
   - **Future-WebSocket constraint:** today `grep '@app.websocket' app/main.py` returns 0 hits. When a `@app.websocket` route is later added, it MUST call `await require_session_ws(websocket)` **inside the handler** (auth-fail → `await ws.close(1008)`) — `Depends(require_session)` works differently on WebSockets and raising `HTTPException` from a dep won't close the socket cleanly. Add this rule to `.claude/rules/coding-rules.md`.
5. **Implementation tactic for the bulk decorator add** — prefer per-route explicit `dependencies=[Depends(require_session)]` (greppable, mypy-clean) over a global middleware with a denylist (fragile, future routes inherit wrong default). Aim ≤ 1 line added per route; group by cluster in the diff for reviewability.
6. **Heartbeat finishing touches** — `/api/system/heartbeat` (post-hotfix at `app/main.py:937`) returns `{"status":"alive","version":APP_VERSION}` only. Drop the `token` field, drop the loopback-only conditional (no secret to gate). Stays unauth'd by design (used by Tauri to detect sidecar boot).
7. **Verify `POST /api/system/init-token` is absent** — grep across `app/main.py` confirmed the route does not exist (only the frontend dance + stale docs referenced it). Action: remove any stale `init-token` references in `CLAUDE.md`, `docs/backend-index.md`, and `frontend/src/api/api.js`; no backend deletion needed.
8. **`secrets.compare_digest`** — use in `require_session` for the Bearer path. **Delete the legacy `SHUTDOWN_TOKEN` query-string scheme** entirely (`shutdown` + `restart` post-hotfix are gated by `require_session` Bearer; the query-param `token` field is removed from those route signatures). The `SHUTDOWN_TOKEN` module-level constant (post-hotfix originally near `app/main.py:125`) can also be deleted (no longer referenced after the rewrite).
9. **`app/backend_entry.py`** — no auth-related change needed (no env-var pass-through in the new design). Skip unless other refactoring requires it.
10. **Frontend — `frontend/src/api/api.js`** — replace the `_sessionToken` setter chain so the value comes from `await invoke('get_session_token')` in Tauri context (retry loop: 500 ms × 60 attempts = 30 s with progressive backoff after first 5 s; show a `Starting backend...` toast after 5 s), or `fetch('/dev-token')` (served by the new vite dev-middleware reading `%APPDATA%/MusicLibraryManager/.session-token`) in browser-dev. **Attach only as `Authorization: Bearer ${token}` — `X-Session-Token` header attachment removed entirely** (no backwards-compat per Decisions). Wrap the bootstrap in a top-level promise so all axios calls await it before firing. If both Tauri IPC and dev-token-file fail (corrupted, missing in fresh dev clone, IPC unregistered), show a top-level toast `Authentication bootstrap failed. Restart the app.` AND set a global `_authBootstrapFailed = true` flag in a new tiny `frontend/src/store/authStore.js` that disables all mutation UI.
11. **Frontend — `frontend/src/main.jsx`** — delete the heartbeat-token-capture block (lines 549-552 per Findings). Heartbeat is now strictly an alive-check.
12. **Frontend — dev-token plumbing** — `app/auth.py` always writes the token file to `%APPDATA%/MusicLibraryManager/.session-token` (no longer conditional on env-var). Add small dev-middleware in `frontend/vite.config.js` that reads that file at vite startup and exposes its content at `GET /dev-token` (dev-only; not in production bundle). **No `.gitignore` entry needed** (file never lives in the repo).
13. **slowapi rate-limit** — **deferred to Phase 2** (per Decisions). Skip from Phase 1.
14. **Tests — `tests/test_auth.py` (NEW)** — pytest + `httpx.ASGITransport` (TestClient is incompatible with httpx 0.28, see `tests/test_security_hotfixes.py` for the pattern). Cases: (a) gated route w/o header → 401; (b) gated route w/ wrong token → 401 (constant-time use verified via code-read of `secrets.compare_digest` — timing assertion not practical in test); (c) gated route w/ correct `Authorization: Bearer` → 2xx; (d) heartbeat returns no `token` key; (e) `init-token` is 404 (route absent); (f) SC `auth-token` route now 401 without header; (g) shutdown / restart accept no `token=` query-param anymore (now 401 without Bearer, 2xx with); (h) Bearer with leading/trailing whitespace → 2xx; (i) bearer with wrong-case scheme (`bearer`, `BEARER`) → 2xx; (j) `Authorization` header empty value → 401; (k) token containing control chars (`\n`, `\x00`) → 401; (l) `Authorization: Basic …` (wrong scheme) → 401; (m) OPTIONS preflight on a gated route → 200 (CORS short-circuits); (n) `Authorization` header attached to non-gated heartbeat → 2xx (no error).
15. **Self-correction loop** — after edits: `ruff check app/ tests/`, `mypy app/auth.py app/main.py`, `pytest tests/test_auth.py -v`, `pytest tests/` (full suite — many existing tests will need a token fixture; add `auth_token` autouse fixture in `tests/conftest.py` that monkeypatches `app.auth.SESSION_TOKEN` to a known value and injects the `Authorization: Bearer …` header into the ASGITransport client). `cargo check --manifest-path src-tauri/Cargo.toml` + `cargo fmt` for the Rust side.

### Files touched (expected)

- **`app/auth.py`** *(NEW, ~80 lines)* — `SESSION_TOKEN` self-generated via `secrets.token_urlsafe(32)`; writes `%APPDATA%/MusicLibraryManager/.session-token` (cross-platform via `platformdirs.user_data_dir`) with user-only ACL; prints `LMS_TOKEN=<value>` as first stdout line at boot; `atexit`-registered file cleanup; `require_session(authorization: str | None = Header(None))` Bearer-parsing dependency using `secrets.compare_digest`; module-level logger that NEVER logs the token.
- **`app/main.py`** — add `from app.auth import require_session`; add `dependencies=[Depends(require_session)]` to every POST/PUT/PATCH/DELETE decorator except `/api/system/heartbeat`; drop `token` field from heartbeat response; **delete** `SHUTDOWN_TOKEN` module constant + the `token=""` query-param signatures on `shutdown` / `restart` / their `if token != SHUTDOWN_TOKEN` blocks; **delete** any registration of `/api/system/init-token` (if present); add `Depends(require_session)` explicitly to `/api/soundcloud/auth-token` (line 2997).
- **`app/backend_entry.py`** — no change expected (stdout-based handshake doesn't need env-var routing).
- **`src-tauri/src/lib.rs`** (or wherever sidecar `Command` is built — `src-tauri/src/main.rs:123-128` per Findings) — capture child stdout via `Stdio::piped()`, spawn a blocking thread that reads lines until `LMS_TOKEN=<value>` is seen, store value in `tauri::State<Mutex<String>>`, then continue tailing for log-forwarding. Register `#[tauri::command] get_session_token` returning the stored value or `Err("token-not-ready")`. **No env-var pass to the sidecar.**
- **`src-tauri/Cargo.toml`** — no change expected (no new crate needed — `rand` not used now that sidecar self-generates).
- **`frontend/src/api/api.js`** — `_sessionToken` source becomes `await invoke('get_session_token')` (with retry loop for `token-not-ready` race) in Tauri context; `fetch('/dev-token')` in browser-dev. Attach **only** as `Authorization: Bearer ${token}` — `X-Session-Token` header removed. Wrap bootstrap in top-level promise so all axios requests await it.
- **`frontend/src/main.jsx`** — drop the heartbeat-token-capture block (lines 549-552).
- **`frontend/vite.config.js`** — add a small dev-middleware: at vite startup, read `%APPDATA%/MusicLibraryManager/.session-token` (use Node's `path.join(process.env.APPDATA, ...)` on Windows; equivalent on other platforms), expose its content at `GET /dev-token` (dev-only).
- **`tests/test_auth.py`** *(NEW, ~150 lines)* — see step 14 for cases. Use `httpx.ASGITransport` (TestClient is incompatible with httpx 0.28).
- **`tests/conftest.py`** — add `auth_token` autouse fixture that monkeypatches `app.auth.SESSION_TOKEN` to a known constant + injects `Authorization: Bearer <constant>` into the ASGITransport-driven test client.
- **`requirements.txt`** — add `platformdirs==4.2.2` (or the current stable pin at implementation time, `==X.Y.Z` strict per Schicht-A). Required for cross-platform user-data-dir resolution in `app/auth.py`.
- **`docs/backend-index.md`** — annotate gated routes with "require_session" marker (or equivalent text column).
- **`docs/architecture.md`** — add an "Auth" section under data-flows: where the token is born (Python sidecar self-generates), how it reaches Rust (stdout-line parse), how it reaches React (IPC), how it flows on every request (header).
- **`docs/SECURITY.md`** — add a new chapter "Schicht-B: API Authentication" containing: threat model for the 3 deployment shapes (standalone-loopback, LAN-exposed, remote-tunnel); accepted risks (Windows ACL gap on `.session-token`, token-file-on-disk-during-Vite-dev, token-in-Tauri-process-memory readable by Process Explorer / ReadProcessMemory); permanent log-scrubbing constraint (`Authorization` + `Cookie` headers MUST be scrubbed in any future logging middleware); future-WebSocket auth constraint.
- **`.claude/rules/coding-rules.md`** — add two permanent constraints: (i) any logging middleware MUST scrub `Authorization` and `Cookie` headers before emitting; (ii) any `@app.websocket` route MUST authenticate via an in-handler `await require_session_ws(websocket)` helper (not bare `Depends(require_session)`).
- **`docs/FILE_MAP.md`** — row for `app/auth.py`; updated row for `src-tauri/src/main.rs`; updated row for `frontend/src/api/api.js`; new row for `frontend/src/store/authStore.js`.
- **`CHANGELOG.md`** — `Added: bearer-token auth on all mutation endpoints (Phase 1 of API auth-hardening).`
- **`docs/research/research/exploring_security-api-auth-hardening.md`** *(this doc)* — `Implementation Log` populated during `inprogress_`.

### Testing approach

- **Unit (pytest `tests/test_auth.py`):** all 8 cases from step 14. Use `fastapi.testclient.TestClient` against the actual `app` instance. Patch `os.environ["LMS_AUTH_TOKEN"]` via monkeypatch + reimport `app.auth` to control the boot token per-test.
- **Regression suite:** run full `pytest tests/` after the conftest autouse fixture lands. Expect failures on every existing test that hits a now-gated route → fix by relying on the autouse fixture's auto-injected header. Audit count of failures matches expected gated routes (sanity check the bulk-decorator pass didn't over- or under-apply).
- **Manual Tauri smoke:** `npm run tauri dev`, verify sidecar boots, frontend can call `POST /api/track/{tid}` with a valid Rating change, heartbeat returns `{"status":"alive"}` (no `token` key in network tab).
- **Manual browser-dev smoke:** `npm run dev:full`, verify `.lms-dev-token` is created in repo root, frontend reads it, axios calls carry the bearer.
- **Negative manual:** `curl -X POST http://127.0.0.1:8000/api/track/123 -d '{"Rating":0}'` → expect `{"detail":"unauthorized"}` 401.
- **Rust:** `cargo test --manifest-path src-tauri/Cargo.toml` — add a unit test for the `get_session_token` command if the test scaffold supports it; otherwise smoke via manual run.
- **Delegate to `test-runner` subagent** post-edit; delegate UI smoke to `e2e-tester`.

### Risks & rollback

- **Frontend can't get the token (Tauri IPC mis-registered, dev-middleware can't read the file, or stdout-reader race not handled) → all writes 401 → app read-only.** Mitigation: rollback is a single `git revert <commit>` + sidecar restart. Token-fetch failure surfaces as a startup toast (`api.js` bootstrap promise rejection).
- **Stdout-reader race**: Tauri reads stdout after sidecar prints `LMS_TOKEN=` line; if frontend invokes `get_session_token` before the line arrives, IPC returns `Err("token-not-ready")`. Mitigation: frontend retry loop (200 ms backoff, 20 attempts) in `api.js` bootstrap; sidecar guaranteed to print the line within first ~50 ms of import time (`app/auth.py` is import-loaded by `app/main.py`).
- **Token file on disk in `%APPDATA%/MusicLibraryManager/.session-token`** — leak via backup software, antivirus quarantine, malicious local app. Mitigation: best-effort `atexit` cleanup (skipped on SIGKILL / `TerminateProcess` / Tauri force-kill `child.kill()` at `src-tauri/src/main.rs:394` — sidecar handles stale files at boot by overwriting). **On Windows, `os.chmod(0o600)` is a no-op (NTFS uses ACLs, not POSIX mode bits) — accepted risk in Phase 1; Phase 2 option is `icacls` via `subprocess` or `pywin32`.** Document in `docs/SECURITY.md` Accepted-Risks chapter.
- **Tauri sidecar-respawn token drift** — if the sidecar dies and Tauri respawns it, the new sidecar generates a fresh token; Tauri's `Mutex<String>` still holds the OLD value until the reader thread catches the new `LMS_TOKEN=` line; frontend will 401 on every call in that window. **Decision: no auto-respawn in Phase 1** — sidecar death = Tauri exits with an error toast; auto-respawn deferred to Phase 2 with a token-refresh IPC.
- **`Authorization` header log-scrubbing constraint** — today no code logs request headers (verified: `app/main.py:264` global exception handler logs only `request.method` + `request.url`). Any future logging middleware MUST scrub `Authorization` and `Cookie` headers before emitting. Codified as a permanent rule in `.claude/rules/coding-rules.md` (see Files-Touched).
- **OPTIONS preflight + CORS** — browser OPTIONS preflight is handled by `CORSMiddleware` and short-circuits before `Depends(require_session)` fires (verified). Non-browser OPTIONS callers (`curl -X OPTIONS …`) WILL hit the route and 401 — expected behavior, not a bug.
- **Atomic commit boundary** — Steps 0–9 (backend) and Steps 10–12 (frontend) MUST land together OR Step 4 ships with a `LMS_AUTH_ENFORCE=0` feature-flag short-circuit. A mid-state with backend gated + frontend not yet attaching `Bearer` breaks the app immediately. Use `[skip-push]` on the backend commit and let auto-push fire on the frontend commit so both land on `origin` together.
- **An existing test hits a route we miss-classified.** Mitigation: full `pytest tests/` is part of the merge gate; the autouse `auth_token` fixture in conftest covers the bulk; CI catches misses.
- **`secrets.compare_digest` requires bytes of same length** — bad-format Authorization headers must be rejected pre-compare. Mitigation: length-check before compare in `require_session`.
- **No env-var, no inheritance**: ProcessPoolExecutor workers (in `app/anlz_safe.py`) and any `subprocess.run` calls don't see the token. Defended by design.
- **Hard rollback path:** Phase 1 ships as a small number of focused commits; each independently revertable. `git revert <sha>` restores prior auth-off behaviour. No DB migrations to undo.

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

### 2026-05-17 — Phase-1 shipped

- **84/85 mutation routes gated.** Every POST/PUT/PATCH/DELETE in `app/main.py` declares `dependencies=[Depends(require_session)]`. Only `POST /api/system/heartbeat` is intentionally open — body-less loopback healthcheck with no token leak. Verified by `grep -c "Depends(require_session)" app/main.py` = 84 vs `grep -cE "@app\.(post|put|patch|delete)\(" app/main.py` = 85.
- **`SHUTDOWN_TOKEN` query-string scheme deleted** in commit `7dfdef5`. Single auth axis now: every mutation goes through `require_session`. `POST /api/system/init-token` removed (now returns 404 — asserted in `tests/test_auth.py`). Heartbeat response no longer carries any token field.
- **Token handoff:** `app/auth.py` generates `secrets.token_urlsafe(32)` on sidecar boot in the main process only (worker subprocesses get empty string), emits `LMS_TOKEN=<value>\n` once on stdout, persists to `<user_data_dir>/MusicLibraryManager/.session-token`. Tauri Rust supervisor's stdout reader (`src-tauri/src/main.rs`) matches the prefix, stashes value in `SessionToken(Arc<Mutex<String>>)`, **drops the banner line before forwarding to `log::info!`** so the token never reaches `log/app.log`. Browser-dev path reads the file via new `devTokenPlugin` in `frontend/vite.config.js` (`GET /dev-token`).
- **Frontend bootstrap:** axios interceptor in `frontend/src/api/api.js` blocks on a bootstrap promise that races Tauri IPC (`get_session_token`) against the dev-middleware. Token landed in `frontend/src/store/authStore.js`. On hard failure flips `_authBootstrapFailed=true` + persistent toast (`id: 'auth-bootstrap-failed'`) so mutation UI can disable itself.
- **`safe_compare` extracted** (commit `8498937`) as `app/security_compare.py`. Wraps `secrets.compare_digest` with the 5-case fragility matrix from the codebase audit (`docs/research/research/evaluated_security-secrets-compare-digest-codebase-audit.md`). `require_session` rewritten to use it.
- **Rate limiter shipped + wired on 3 HIGH-tier routes.** Module `app/rate_limit.py` landed in `830c056` per the evaluated rate-limit design doc. Decorator applied to `POST /api/system/shutdown`, `POST /api/system/restart`, `POST /api/soundcloud/auth-token` in commit `e78fb24` (`@rate_limit(steady=5.0, burst=10, key_mode="both")`). Decorator order is `@app.post(...)` outermost → `@rate_limit(...)` inner → `Depends(require_session)` via `dependencies=` kwarg, so `require_session` raises 401 BEFORE the bucket is decremented (verified by `tests/test_rate_limit.py::test_auth_before_ratelimit` and `tests/test_auth.py` TestCaseF/G). Remaining MEDIUM/LOW-tier wiring deferred to Phase-2.
- **Test suite green: 219+ tests pass.** New: `tests/conftest.py` (autouse `auth_token` fixture + `no_auth` marker), `tests/test_auth.py` (20 cases — Bearer parsing matrix), `tests/test_security_compare.py` (17 cases — fragility matrix), `tests/test_rate_limit.py` (7 cases — token-bucket refill/burst/whitelist/auth-ordering). Existing tests continued to work without modification — the autouse fixture monkeypatches `SESSION_TOKEN` per-test so they speak Bearer transparently.
- **Atomic boundary held.** Phase-1 landed as 9 small revertable commits (`1c7d410`, `7dfdef5`, `d12ad1a`, `f90f5f8`, `8498937`, `830c056` rate-limit module, `49ddc63` rate-limit tests, `e78fb24` rate-limit wiring, `62eb9c0` partial doc-sync), with this commit closing the doc-sync. Each independently revertable per the draftplan's "Hard rollback path".
- **Surprises / deviations from plan:**
  - Plan said "kept in Phase 1 to avoid a flag-day rollout" re: `X-Session-Token` compat header — we dropped it anyway. Bearer-only from day 1; no compatibility scaffolding needed since the frontend was rewritten in lockstep.
  - Plan didn't anticipate the `multiprocessing.current_process().name` gate in `app/auth.py` — needed because the rate-limit module's `BucketStore` and the anlz subprocess pool both import `app.auth` transitively at fork time on Linux/macOS, which would have generated a SECOND token (and emitted a SECOND `LMS_TOKEN=` banner to stdout) per worker. Worker subprocesses now get empty string + skip the banner/file-write.

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
