# Security — Dependency Hardening (Schicht A)

This project follows **2026 supply-chain security best practices**.

## What's Active

### NPM (`.npmrc`)
- **`ignore-scripts=true`** — Postinstall/preinstall scripts disabled (blocks ~80% of npm supply-chain malware: lottie-player, ua-parser-js, chalk-style hijacks)
- **`save-exact=true`** — `npm install` writes exact versions, no caret/tilde drift
- **`audit-level=high`** — Builds fail on high+ CVEs
- **`engine-strict=true`** — Node version pinning enforced
- **`prefer-offline=true`** — Reduces network calls during install

### Python (`requirements.txt`)
- All versions pinned with `==` (no `>=`)
- For maximum security: regenerate with hashes via `pip-compile --generate-hashes`
- Audit via `pip-audit`

### Cargo (`Cargo.lock`)
- Lockfile committed
- Audit via `cargo audit` (install: `cargo install cargo-audit`)

### Automated
- **Dependabot** weekly checks for security CVEs (config: `.github/dependabot.yml`)
- **Lockfile-Lint** ensures all packages from npm registry only (config: `.lockfile-lintrc.json`)
- **Audit script** runs all checks: `bash scripts/security-audit.sh` (or `.ps1` for Windows)

## Required Workflow

### Installing Dependencies (NEVER use `npm install`)
```bash
# Frontend
cd frontend && npm ci          # ← deterministic install from lockfile

# Root
npm ci

# Python
pip install -r requirements.txt
# OR (recommended): pip install --require-hashes -r requirements-hashed.txt

# Rust
cd src-tauri && cargo build    # uses Cargo.lock
```

### Adding a New Dependency
**ALWAYS verify before installing** (Slopsquatting defense — AI tools may suggest non-existing packages):

```bash
# 1. Check the package exists, has reasonable age + downloads
npm view <package-name>        # Look for: created date, weekly downloads, maintainers

# 2. Check Sigstore provenance
npm view <package-name> attestations

# 3. Only after verification, add it
cd frontend
npm install --save-exact <package-name>

# 4. Run audit immediately
npm audit
```

### Before Every Release
```bash
# Run full audit
bash scripts/security-audit.sh

# Verify Cargo
cd src-tauri && cargo audit && cd ..

# Generate SBOM (when Schicht B is added)
# syft . -o cyclonedx-json > sbom.json
```

## Threats Defended Against

| Threat | Defense |
|---|---|
| Postinstall malware (lottie-player 2024, chalk hijack) | `ignore-scripts=true` |
| Caret-version silent drift | `save-exact=true` + exact pinning |
| Lockfile manipulation | `lockfile-lint` |
| Self-propagating worms (PyTorch Lightning 2026) | Hash-pinning + Dependabot alerts |
| Maintainer account compromise | `npm audit signatures` (Sigstore) |
| Slopsquatting (AI-induced) | Manual verification workflow |
| Known CVEs | `npm audit` + `pip-audit` + Dependabot |

## Threats NOT Defended Against (Out of Scope)

- **Initial-source backdoors** (XZ-style) — would require reproducible builds + multi-source verification
- **Targeted attacks on this specific project** — would require additional defenses (HSM, air-gapped builds)
- **Bugs in self-written code** — handled by code review + linting (separate concern)

## Known Accepted Risks

### picomatch <=2.3.1 || 4.0.0–4.0.3 — high ReDoS (GHSA-3v7f-55p6-f55p, GHSA-c2c7-rcm5-vvqj)
- **Scope**: Build-time glob matching (transient via vite, tailwindcss, tinyglobby)
- **Production impact**: ZERO — only used during `vite build` / `tailwind compile`
- **Exploitable**: Only with attacker-controlled glob patterns in tailwind/vite config (would require separate compromise to inject)
- **Mitigation**: Build runs in isolated environment; config files are git-tracked and reviewed
- **Status**: No upstream patch released yet (May 2026). Latest picomatch=4.0.3 still vulnerable.
- **Action**: Watching upstream for 4.0.4+ release. Dependabot will auto-PR when available.

## Reporting Security Issues

Found a vulnerability? Email **<email-redacted>** (do not open public GitHub issue).

---

# Schicht B — API Authentication

> Runtime auth gate on the FastAPI sidecar. Phase 1 landed 2026-05-17 (commits `1c7d410..f90f5f8`). Source: `docs/research/implement/draftplan_security-api-auth-hardening.md`.

## What's Active

- **Bearer-token gate** on **all 84 mutation routes** (POST/PUT/PATCH/DELETE) in `app/main.py`. Only exception: `POST /api/system/heartbeat` stays open as the liveness probe (returns `{"status":"alive"}` only — no token leak).
- **Session token** self-generated at sidecar boot via `secrets.token_urlsafe(32)` in `app/auth.py`. Printed as first stdout line `LMS_TOKEN=<value>` and persisted to `%APPDATA%/MusicLibraryManager/.session-token` (cross-platform via `platformdirs`).
- **Constant-time compare** via `secrets.compare_digest` after length-check + control-char reject.
- **Tauri Rust supervisor** (`src-tauri/src/main.rs`) captures the banner on both spawn paths (dev `spawn_child` + prod `shell.sidecar`), scrubs it from log-forwarding, and surfaces it to the React frontend via the `get_session_token` IPC.
- **React frontend** (`frontend/src/api/api.js`) attaches `Authorization: Bearer <token>` on every axios request. Bootstrap promise gates all calls until the token is fetched (IPC in Tauri, `GET /dev-token` middleware in browser-dev — `frontend/vite.config.js` reads the token file at vite startup and re-exposes it).
- **Legacy `SHUTDOWN_TOKEN` deleted.** `/api/system/shutdown` + `/restart` now gate via `require_session` Bearer; no `?token=` query-string fallback.

## Deployment Shapes — Threat Model

### (a) Standalone loopback (current default)

Sidecar binds `127.0.0.1:8000`. CORS blocks browser fetches from non-allowlisted origins.

| Threat | Defense |
|---|---|
| Same-machine malware POSTs to mutation routes | `Authorization: Bearer` gate — attacker must read the token file or `LMS_TOKEN=` banner |
| Browser-XSS pivot from a malicious local HTML/`file://` page | Same gate; CORS still blocks the request reaching the API |
| CSRF from a random web page | No ambient cookie — bearer-in-header is not auto-sent |
| Local data exfil via read routes | Out of scope Phase 1 (read routes loopback-gated only) |

### (b) LAN-exposed (mobile companion, second-device, headless install, accidental firewall hole)

Sidecar on `0.0.0.0:8000` or loopback + reverse proxy.

| Threat | Defense |
|---|---|
| LAN attacker mutates library / formats USB / overwrites SC OAuth token | Bearer gate — attacker has no token unless they breach the host |
| Drive-by on shared WiFi | Same |
| Shodan / mass-scanner discovery on public LAN | Bearer gate (also: Phase 2 will add IP allowlist + paired-device tokens) |

### (c) Remote tunnel (Tailscale, Cloudflare Tunnel, reverse proxy)

Outer transport authenticated by the tunnel (Tailscale identity / Cloudflare Access).

| Threat | Defense |
|---|---|
| Tunnel ACL misconfig leaves inner API naked | Bearer gate is the second wall |
| Replay attacks on shutdown/restart | Token bound to header (not query string); HTTPS at the tunnel layer prevents wire interception |

## Accepted Risks — Phase 1

1. **Windows ACL gap on `.session-token` file.** Python `os.chmod(0o600)` is silently a no-op on NTFS (POSIX mode bits are not honored). Any local user with read access to `%APPDATA%/MusicLibraryManager/` can lift the token. Phase 2 option: `icacls` via subprocess or `pywin32` to set per-user DACL.
2. **Token-on-disk during Vite-dev.** The dev-middleware in `frontend/vite.config.js` reads `%APPDATA%/MusicLibraryManager/.session-token` and exposes it at `GET /dev-token`. The file path is unchanged from prod, so dev backups / OneDrive / Time-Machine may capture it. Mitigation: token rotates on every sidecar restart.
3. **Token in Tauri-process memory.** The captured token sits in a `Mutex<String>` for the lifetime of the supervisor. Process Explorer / `ReadProcessMemory` on Windows or `/proc/<pid>/mem` on Linux can lift it from any process running as the same user. Accepted — same trust boundary as the file itself.
4. **No per-device revoke / no rotation mid-session.** Token rotates **only** on sidecar restart. User can force-rotate by hitting `POST /api/system/restart`. Phase 2 introduces paired-device tokens with explicit revoke UI.
5. **Read routes stay open** (loopback-gated only) in Phase 1. Same-machine attacker can still scrape library metadata without a token. Phase 2 may flip this to require_session-everywhere.

## Permanent Constraints — Future Code MUST Honor

These are codified in `.claude/rules/coding-rules.md`:

- **Log-scrubbing.** Any logging middleware added later MUST scrub `Authorization` and `Cookie` headers before emitting. Today no code logs headers, but a careless `request.headers` dump would leak the token to `log/app.log`.
- **WebSocket auth.** When the first `@app.websocket` route lands, it MUST call `await require_session_ws(websocket)` **inside the handler** and `await ws.close(1008)` on auth-fail. A bare `Depends(require_session)` on a WebSocket route does not close the socket cleanly when the dep raises `HTTPException`.
- **Never log the token.** Not at INFO, not at DEBUG, not redacted. `app/auth.py` enforces this contract; downstream code MUST too.

## Phase 2 — Out of Scope for Now

Deferred per draftplan Decisions table:

- Paired-device tokens (QR pairing flow, SQLite-backed `paired_devices` table) — hard prerequisite for the mobile companion roadmap.
- Per-device revoke UI.
- `slowapi` rate-limit middleware on `/api/system/*` and `/api/soundcloud/auth-token`.
- IP allowlist middleware / opt-in CIDR for `0.0.0.0` bind.
- One-shot capability-token generalisation (extend `_format_tokens` pattern to shutdown/restart/library-new/usb-eject/usb-reset).
- mTLS / HTTPS termination in uvicorn.
