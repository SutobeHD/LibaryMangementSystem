import axios from 'axios';
import toast from 'react-hot-toast';

import {
    getSessionToken,
    setBootstrapFailed,
    setBootstrapPromise,
    setSessionToken,
} from '../store/authStore';

// ─── EC2: Runtime detection of Tauri context ───────────────────────────────────
// Tauri injects window.__TAURI_INTERNALS__ before the page loads.
// In browser-preview/dev-server mode we use an empty baseURL and rely on Vite's proxy.
const isTauri = !!(window.__TAURI_INTERNALS__ || window.__TAURI_METADATA__ || window.__TAURI__);

// EC1/EC2: No trailing slash on baseURL; all route paths start with '/'
const API_BASE_URL = isTauri
    ? (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000')
    : ''; // empty string → browser uses Vite proxy (see vite.config.js)

// ─── Axios Instance ────────────────────────────────────────────────────────────
const api = axios.create({
    baseURL: API_BASE_URL,
    timeout: 10_000,          // EC8: 10 s hard timeout on every request
    withCredentials: false,   // Bearer-in-header only; no cookie-auth transport
});

// Re-export the auth-store helpers under the legacy api.js names so
// existing callers keep working without an import-path migration.
export { getSessionToken, setSessionToken };

// ─── SECURITY: Bootstrap the session token ────────────────────────────────────
// Phase 1 of the API auth-hardening flow (see
// docs/research/implement/draftplan_security-api-auth-hardening.md):
//   - Tauri context  → ``invoke('get_session_token')`` with a retry loop
//     for the stdout-reader race window. After 5 s with no token we
//     surface a "Starting backend..." toast so the user knows we're
//     still waiting; total budget 30 s (60 attempts × 500 ms).
//   - Browser-dev    → ``fetch('/dev-token')`` which the vite
//     dev-middleware serves from %APPDATA%/MusicLibraryManager/.session-token.
// Both paths set ``_sessionToken`` (via the auth store). Failure flips
// ``_authBootstrapFailed`` so mutation UI can disable itself; we ALSO
// surface a non-dismissable toast.

const TAURI_BOOTSTRAP_INTERVAL_MS = 500;
const TAURI_BOOTSTRAP_MAX_ATTEMPTS = 60;          // 60 × 500 ms = 30 s
const TAURI_BOOTSTRAP_LONG_WAIT_MS = 5_000;       // toast after 5 s

async function _bootstrapFromTauri() {
    const { invoke } = await import('@tauri-apps/api/core');
    let longWaitToast = null;
    const longWaitTimer = setTimeout(() => {
        longWaitToast = toast.loading('Starting backend...');
    }, TAURI_BOOTSTRAP_LONG_WAIT_MS);

    try {
        for (let attempt = 0; attempt < TAURI_BOOTSTRAP_MAX_ATTEMPTS; attempt++) {
            try {
                const token = await invoke('get_session_token');
                if (token && typeof token === 'string') {
                    setSessionToken(token);
                    return token;
                }
            } catch (err) {
                // ``Err("token-not-ready")`` is expected during the
                // first ~50 ms while the Rust supervisor is still
                // tailing the sidecar's stdout for the LMS_TOKEN= line.
                if (err && String(err).indexOf('token-not-ready') === -1) {
                    console.error('[API] get_session_token IPC error:', err);
                }
            }
            await new Promise((resolve) => setTimeout(resolve, TAURI_BOOTSTRAP_INTERVAL_MS));
        }
        throw new Error('Tauri IPC get_session_token: 30 s timeout');
    } finally {
        clearTimeout(longWaitTimer);
        if (longWaitToast) toast.dismiss(longWaitToast);
    }
}

async function _bootstrapFromDevMiddleware() {
    const resp = await fetch('/dev-token', { credentials: 'omit' });
    if (!resp.ok) {
        throw new Error(`/dev-token HTTP ${resp.status}`);
    }
    const token = (await resp.text()).trim();
    if (!token) {
        throw new Error('/dev-token returned an empty body');
    }
    setSessionToken(token);
    return token;
}

async function _bootstrap() {
    try {
        if (isTauri) {
            return await _bootstrapFromTauri();
        }
        return await _bootstrapFromDevMiddleware();
    } catch (err) {
        console.error('[API] Authentication bootstrap failed:', err);
        setBootstrapFailed(true);
        toast.error(
            'Authentication bootstrap failed. Restart the app.',
            { duration: Infinity, id: 'auth-bootstrap-failed' }
        );
        throw err;
    }
}

// Top-level promise so callers can ``await ready()`` if they want to
// gate first-use on a known-good token. Axios calls do this implicitly
// in the request interceptor below.
const _bootstrapPromise = _bootstrap().catch((err) => {
    // Swallow at top level so the promise doesn't pop an unhandled
    // rejection warning; the interceptor still awaits and propagates.
    return Promise.reject(err);
});
setBootstrapPromise(_bootstrapPromise);

/** Await this from anywhere if you need the bootstrap to be done. */
export function ready() {
    return _bootstrapPromise;
}

// ─── EC15: Token-refresh state ────────────────────────────────────────────────
// Prevents an infinite refresh loop if the refresh request itself fails.
// Pattern: queue all 401-waiting requests, drain them after one refresh.
let _isRefreshing        = false;           // true while a refresh is in flight
let _refreshSubscribers  = [];             // resolve/reject callbacks queued during refresh
let _refreshFailCount    = 0;              // consecutive refresh failures
const MAX_REFRESH_FAILS  = 2;             // stop trying after this many failures

/** Add a callback that will be called once the token has been refreshed. */
function _subscribeRefresh(callback) {
    _refreshSubscribers.push(callback);
}

/** Drain the subscriber queue after a successful (or failed) refresh. */
function _drainRefreshQueue(newToken, error) {
    _refreshSubscribers.forEach(cb => cb(newToken, error));
    _refreshSubscribers = [];
}

/** Attempt to silently re-authenticate using a stored SC token.
 *  Returns the new token string, or throws on failure. */
async function _refreshScToken() {
    // EC15: Only one refresh in flight at a time.
    if (_isRefreshing) {
        // Return a promise that resolves when the in-flight refresh finishes.
        return new Promise((resolve, reject) => {
            _subscribeRefresh((token, err) => {
                if (err) reject(err);
                else resolve(token);
            });
        });
    }

    _isRefreshing = true;
    try {
        // The backend /api/soundcloud/auth-token endpoint validates and stores
        // the new token.  In Tauri the token comes from the native keystore;
        // in browser mode we can't silently refresh — throw immediately.
        if (!isTauri) throw new Error('Silent token refresh unavailable in browser mode.');

        // Dynamically import to avoid crashing in browser-preview mode.
        const { invoke } = await import('@tauri-apps/api/core');
        const newToken = await invoke('login_to_soundcloud');

        await api.post('/api/soundcloud/auth-token', { token: newToken });
        _refreshFailCount = 0;
        _isRefreshing = false;
        _drainRefreshQueue(newToken, null);
        return newToken;
    } catch (err) {
        _refreshFailCount++;
        _isRefreshing = false;
        _drainRefreshQueue(null, err);
        throw err;
    }
}

// ─── REQUEST INTERCEPTOR ──────────────────────────────────────────────────────
api.interceptors.request.use(
    async (config) => {
        // Block on the bootstrap promise the first time through so any
        // call that fires before the IPC handshake / dev-token fetch
        // resolves still carries the Bearer header. After the first
        // await this is a microtask away from instant.
        try {
            await _bootstrapPromise;
        } catch (_err) {
            // Bootstrap failed → let the request go without auth so the
            // backend's 401 path fires consistently; the toast from
            // _bootstrap() already alerted the user.
        }
        const token = getSessionToken();
        if (token) {
            config.headers['Authorization'] = `Bearer ${token}`;
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// ─── RESPONSE INTERCEPTOR ─────────────────────────────────────────────────────
api.interceptors.response.use(
    // ── Happy path ──────────────────────────────────────────────────────────
    (response) => response,

    // ── Error path ──────────────────────────────────────────────────────────
    async (error) => {
        const originalRequest = error.config;

        // EC6: Network abort / no response at all
        if (!error.response) {
            const isCancelled = axios.isCancel(error);
            if (!isCancelled) {
                console.error('[API] Network error / unreachable backend:', error.message);
            }
            return Promise.reject(error);
        }

        const { status } = error.response;

        // EC7/EC15: 401 Unauthorized → attempt silent token refresh once.
        if (status === 401 && !originalRequest._retried) {
            // EC15: Bail out if we've already failed MAX_REFRESH_FAILS times —
            // this breaks the infinite refresh loop.
            if (_refreshFailCount >= MAX_REFRESH_FAILS) {
                console.error('[API] Refresh loop threshold reached. Clearing SC session.');
                _refreshFailCount = 0;
                // Emit a custom DOM event so the UI can show the login screen.
                window.dispatchEvent(new CustomEvent('sc:auth-expired'));
                return Promise.reject(error);
            }

            originalRequest._retried = true; // guard: retry this request only once

            try {
                await _refreshScToken();
                // Re-send the original request now that the token is fresh.
                return api(originalRequest);
            } catch (refreshErr) {
                // Refresh failed → propagate the original 401
                window.dispatchEvent(new CustomEvent('sc:auth-expired'));
                return Promise.reject(error);
            }
        }

        // EC4/EC10: Pydantic validation error — FastAPI returns 422 with field-level detail.
        // Log the structured errors so they're visible in the browser DevTools console.
        if (status === 422) {
            const errs = error.response.data?.errors ?? [];
            console.error(
                `[API] Validation error on ${originalRequest?.url}:`,
                errs.length ? errs.map(e => `${e.field.join('.')} → ${e.message}`).join(', ') : error.response.data
            );
        }

        // EC10: 400 Bad Request — log the backend-provided reason so it's immediately visible.
        if (status === 400) {
            console.error(
                `[API] 400 Bad Request on ${originalRequest?.url}:`,
                error.response.data?.detail ?? error.response.data
            );
        }

        // EC4: SoundCloud API rate limit surfaced from backend as 429
        if (status === 429) {
            console.warn('[API] Rate limited (429). Retry after cooldown.');
        }

        // EC5: SoundCloud is down (502/503 from our backend proxying a 500 SC error)
        if (status >= 500) {
            console.error(`[API] Server error ${status}:`, error.response.data?.detail);
        }

        return Promise.reject(error);
    }
);

// ─── AbortController helpers ──────────────────────────────────────────────────

/** Create a manually-cancellable token (use in useEffect cleanup). */
export function createCancelToken() {
    const controller = new AbortController();
    return { signal: controller.signal, cancel: () => controller.abort() };
}

/** Cancellable GET — returns {promise, cancel}. */
export function cancellableGet(url, config = {}) {
    const controller = new AbortController();
    const promise = api.get(url, { ...config, signal: controller.signal });
    return { promise, cancel: () => controller.abort() };
}

/** Cancellable POST — returns {promise, cancel}. */
export function cancellablePost(url, data, config = {}) {
    const controller = new AbortController();
    const promise = api.post(url, data, { ...config, signal: controller.signal });
    return { promise, cancel: () => controller.abort() };
}

export default api;
export { API_BASE_URL };
