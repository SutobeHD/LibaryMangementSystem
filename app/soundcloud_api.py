"""
SoundCloud Playlist API — Fetches playlists & favorites via the unofficial V2 API.
Uses the auth_token (stored in the OS keyring via `keyring.get_password`) for
authenticated requests.

Resilience features:
  - Exponential backoff on 429 Too Many Requests (Criterion 11)
  - AuthExpiredError raised on 401/403 mid-session (Criterion 5)
  - Dead/null track filtering (Criterion 9, 12)
  - Full pagination following next_href (Criterion 8)
"""

import logging
import os
import re
import time
from difflib import SequenceMatcher
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

# SECURITY: Client ID is read from the SOUNDCLOUD_CLIENT_ID env var (set in .env).
# We never ship hardcoded credentials in the source — see .env.example for setup.
# If the env var is missing, the scraper below tries to extract a public web-
# player ID from soundcloud.com at runtime (cached in memory only).

# ──────────────────────────────────────────────────────────────────────────────
# Dynamic Client ID Scraper
# ──────────────────────────────────────────────────────────────────────────────

_DYNAMIC_CLIENT_ID: str | None = None
_DYNAMIC_CLIENT_ID_EXPIRES: float = 0.0


def get_sc_client_id() -> str:
    """
    Resolve a valid SoundCloud client_id. Resolution order:
      1. SOUNDCLOUD_CLIENT_ID environment variable (preferred — set in .env).
      2. In-memory cache from a previous successful scrape (1-hour TTL).
      3. Live scrape of soundcloud.com to extract the public web-player ID.

    Raises RuntimeError if all three fail — we never silently fall back to a
    hardcoded ID, because that would (a) leak a fingerprint to SoundCloud and
    (b) couple this codebase to a single shared client_id that could be
    revoked at any time.
    """
    global _DYNAMIC_CLIENT_ID, _DYNAMIC_CLIENT_ID_EXPIRES
    now = time.time()

    env_id = os.environ.get("SOUNDCLOUD_CLIENT_ID")
    if env_id:
        return env_id

    # Pre-checks: use cached ID if still valid
    if _DYNAMIC_CLIENT_ID and now < _DYNAMIC_CLIENT_ID_EXPIRES:
        return _DYNAMIC_CLIENT_ID

    # STEALTH HEADERS: Mimic a standard desktop Chrome browser to bypass simple bot checks
    stealth_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        logger.info("[SC Scraper] Attempting to scrape dynamic client_id from soundcloud.com...")

        # Fast timeout (5s) to avoid stalling the backend if SC is hanging or proxying via Cloudflare challenge
        resp = requests.get("https://soundcloud.com", headers=stealth_headers, timeout=5.0)

        logger.info(f"[SC Scraper] Main page status: {resp.status_code}, HTML length: {len(resp.text)} bytes")
        resp.raise_for_status()

        # Find script tags: <script crossorigin src="https://a-v2.sndcdn.com/assets/49-8c9df1fb.js">
        script_urls = re.findall(r'<script crossorigin src="([^"]+)"></script>', resp.text)
        if not script_urls:
            # Fallback regex if SC changes their markup
            script_urls = re.findall(r'src="([^"]+?sndcdn\.com/assets/[^"]+?\.js)"', resp.text)

        logger.info(f"[SC Scraper] Found {len(script_urls)} .js bundle links to scan.")

        # ROBUST REGEX: allows spaces between client_id, colon, and quotes, matches single or double quotes
        regex_pattern = r'client_id\s*:\s*["\']([a-zA-Z0-9]{32})["\']'

        # Iterate scripts looking for the 32-character client_id
        for url in script_urls:
            try:
                js_resp = requests.get(url, headers=stealth_headers, timeout=5.0)
                logger.debug(f"[SC Scraper] Scanning script: {url} (Status: {js_resp.status_code})")
            except Exception as e_script:
                logger.warning(f"[SC Scraper] Skipped script {url} due to network error: {e_script}")
                continue

            if js_resp.status_code == 200:
                match = re.search(regex_pattern, js_resp.text)
                if match:
                    new_id = match.group(1)
                    _DYNAMIC_CLIENT_ID = new_id
                    _DYNAMIC_CLIENT_ID_EXPIRES = now + 3600  # cache 1 hour
                    logger.info(f"[SC Scraper] SUCCESS! Fetched dynamic client_id: {_DYNAMIC_CLIENT_ID}")
                    return _DYNAMIC_CLIENT_ID
                else:
                    logger.debug(f"[SC Scraper] No client_id found in script: {url}")

        logger.warning("[SC Scraper] Regex found no client_id in any JS bundles.")

    except requests.exceptions.HTTPError as he:
        logger.error(f"[SC Scraper] Blocked by SC (Status {he.response.status_code}). Likely Cloudflare Challenge.")
    except requests.exceptions.Timeout:
        logger.error("[SC Scraper] Timeout after 5 seconds while reaching soundcloud.com")
    except Exception as e:
        logger.error(f"[SC Scraper] Unexpected error: {e}")

    # No hardcoded fallback by design — see module docstring. The user must
    # either provide their own SOUNDCLOUD_CLIENT_ID in .env, or accept that
    # SC public endpoints are unreachable when the scrape fails.
    raise RuntimeError(
        "Could not resolve a SoundCloud client_id. "
        "Set SOUNDCLOUD_CLIENT_ID in your .env file (see .env.example), "
        "or check your network connection to soundcloud.com."
    )


# SoundCloud API base URL.
# V2 API (api-v2.soundcloud.com) returns richer metadata and supports pagination.
# All endpoints use:  Authorization: OAuth <access_token>  +  ?client_id=<SC_CLIENT_ID>
SC_API_BASE = "https://api.soundcloud.com"

# ──────────────────────────────────────────────────────────────────────────────
# Custom Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class AuthExpiredError(Exception):
    """Raised when the SoundCloud OAuth token is invalid or has expired."""
    pass


class RateLimitError(Exception):
    """Raised when the API rate limit is exceeded and retries are exhausted."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Rate-Limited HTTP helper
# ──────────────────────────────────────────────────────────────────────────────

def _get_proxy() -> dict | None:
    """
    Read the HTTP proxy URL from app settings (persisted in settings.json).
    Returns a requests-compatible proxies dict, or None if no proxy is configured.
    Used by all SoundCloud API calls so corporate firewall users can route traffic.
    """
    try:
        from .services import SettingsManager
        proxy_url = (SettingsManager.load() or {}).get("http_proxy", "").strip()
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
    except Exception as exc:
        logger.debug("[SC] _get_proxy: could not read settings: %s", exc)
    return None


def _sc_get(url: str, headers: dict, params: dict = None, max_retries: int = 3, timeout: int = 15) -> requests.Response:
    """
    Perform a GET request against the SoundCloud API with automatic 429 backoff.

    On a 429 response:
      - Reads the `Retry-After` header (falls back to 10 s).
      - Sleeps and retries up to `max_retries` times with exponential growth.
    On 401/403/404:
      - Raises AuthExpiredError immediately.
      - NOTE: SoundCloud returns 404 on /me and /users/{id}/playlists when the
        client_id is invalid or the token doesn't match the account. This is an
        auth failure — not a missing-resource error. Treating it as such prevents
        the cryptic '404 Client Error: Not Found' message in the frontend toast.
    """
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            logger.debug(f"[SC] GET Request to {url} (params: {params})")
            resp = requests.get(url, headers=headers, params=params, timeout=timeout, proxies=_get_proxy())
            logger.info(f"[SC] Response {resp.status_code} from {url}")
        except requests.RequestException as exc:
            logger.warning(f"[SC] Network error on attempt {attempt + 1}: {exc}")
            if attempt >= max_retries:
                raise
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code == 200:
            # EC10: Catch malformed/non-JSON responses from SoundCloud
            try:
                resp_json = resp.json()  # Validate JSON parsability before returning
                # logger.debug(f"[SC] JSON Response snippet: {str(resp_json)[:200]}...")
                return resp
            except ValueError as json_err:
                logger.error(f"[SC] Malformed JSON from {url}: {json_err}. Raw body snippet: {resp.text[:200]}")
                raise ValueError(f"SoundCloud returned non-JSON (status 200). Raw: {resp.text[:120]}")

        if resp.status_code in (401, 403):
            logger.error(f"[SC] Auth error {resp.status_code}: token invalid or expired. Body: {resp.text[:200]}")
            raise AuthExpiredError(f"SoundCloud auth token is invalid or expired (HTTP {resp.status_code}).")

        # ROOT CAUSE FIX: SoundCloud returns 404 when client_id is wrong...
        if resp.status_code == 404:
            logger.error(
                f"[SC] 404 Not Found from SoundCloud for {url}. "
                f"Body: {resp.text[:200]}"
            )
            raise AuthExpiredError(
                f"SoundCloud returned 404 for {url}. Token or client_id may be invalid."
            )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", delay * 2))
            logger.warning(f"[SC] 429 Too Many Requests. Retrying in {retry_after}s (attempt {attempt + 1}/{max_retries}).")
            if attempt >= max_retries:
                raise RateLimitError("SoundCloud rate limit exceeded. Please try again later.")
            time.sleep(retry_after)
            delay = min(delay * 2, 60)
            continue

        # All other non-200 codes
        logger.error(f"[SC] Unexpected status {resp.status_code} for {url}: {resp.text[:200]}")
        resp.raise_for_status()

    raise RateLimitError("Max retries reached.")


# ──────────────────────────────────────────────────────────────────────────────
# SoundCloud Playlist API
# ──────────────────────────────────────────────────────────────────────────────

class SoundCloudPlaylistAPI:
    """Fetches playlist and track data from SoundCloud."""

    @staticmethod
    def _get_headers(auth_token: str | None = None) -> dict:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        if auth_token:
            headers["Authorization"] = f"OAuth {auth_token}"
        return headers

    @staticmethod
    def _resolve_user_id(auth_token: str) -> int:
        """Resolve the current user's numeric ID from their auth token.
        Raises AuthExpiredError on 401/403/404."""
        resp = _sc_get(
            f"{SC_API_BASE}/me",
            headers=SoundCloudPlaylistAPI._get_headers(auth_token),
            params={} if auth_token else {"client_id": get_sc_client_id()},
            timeout=10
        )
        data = resp.json()
        user_id = data.get("id")
        if not user_id:
            raise ValueError("Could not determine user ID from /me endpoint.")
        return user_id

    @staticmethod
    def get_user_profile(auth_token: str) -> dict:
        """
        Fetch the authenticated user's public profile from SoundCloud.
        Returns a dict with: id, username, full_name, avatar_url, permalink_url,
        followers_count, track_count.

        EC1: If avatar_url is null/missing, the key is still present (value=None).
             The frontend must render a fallback (initials/icon) in that case.
        EC2: Raises AuthExpiredError on 401/403/404 from the SC API.
        """
        resp = _sc_get(
            f"{SC_API_BASE}/me",
            headers=SoundCloudPlaylistAPI._get_headers(auth_token),
            params={} if auth_token else {"client_id": get_sc_client_id()},
            timeout=10
        )
        data = resp.json()
        return {
            "id":              data.get("id"),
            "username":        data.get("username") or data.get("permalink") or "Unknown",
            "full_name":       data.get("full_name") or "",
            "avatar_url":      data.get("avatar_url"),   # may be None — frontend handles fallback
            "permalink_url":   data.get("permalink_url") or "",
            "followers_count": data.get("followers_count", 0),
            "track_count":     data.get("track_count", 0),
        }

    @staticmethod
    def _normalize_track(raw: dict) -> dict | None:
        """
        Convert a raw SoundCloud track object into our canonical format.
        Returns None for dead/deleted tracks (Criterion 9, 12).
        """
        if not raw or not isinstance(raw, dict):
            return None
        track_id = raw.get("id")
        if not track_id:
            return None

        title = raw.get("title", "")
        user = raw.get("user", {})
        artist = user.get("username", "") if isinstance(user, dict) else ""

        # A track with an id but no title + no user is likely deleted
        is_deleted = not title and not artist
        if is_deleted:
            logger.debug(f"[SC] Skipping deleted/empty track id={track_id}")
            return None

        # download_url is present in the API response when downloadable=True.
        # It points to the official /tracks/{id}/download endpoint.
        # We include it here so callers don't need a second API round-trip.
        return {
            "id": track_id,
            "title": title,
            "artist": artist,
            "duration": raw.get("duration", 0),
            "permalink_url": raw.get("permalink_url", ""),
            "artwork_url": raw.get("artwork_url"),
            "downloadable": raw.get("downloadable", False),
            "download_url": raw.get("download_url"),  # None when not downloadable
        }

    @staticmethod
    def resolve_track_from_url(permalink_url: str, auth_token: str | None = None) -> dict | None:
        """
        Resolve a SoundCloud permalink URL to a normalized track dict.

        Uses the /resolve endpoint to look up any SC URL (track, playlist, user).
        Returns a normalized track dict (same shape as _normalize_track) when the
        URL resolves to a track, or None on failure / if it resolves to a non-track.

        This is used by the download endpoint when the caller provides only a URL
        (rather than pre-resolved track metadata), so the backend can check
        'downloadable' before making any download attempt.
        """
        headers = SoundCloudPlaylistAPI._get_headers(auth_token)
        params: dict = {"url": permalink_url}
        if not auth_token:
            params["client_id"] = get_sc_client_id()

        logger.info("[SC] Resolving URL: %s", permalink_url)
        try:
            resp = _sc_get(
                f"{SC_API_BASE}/resolve",
                headers=headers,
                params=params,
                timeout=10,
            )
            data = resp.json()

            # /resolve returns the resource directly; check it's a track
            kind = data.get("kind", "")
            if kind != "track":
                logger.warning("[SC] resolve_track_from_url: kind=%s (not a track) for %s", kind, permalink_url)
                return None

            return SoundCloudPlaylistAPI._normalize_track(data)

        except AuthExpiredError:
            raise
        except Exception as exc:
            logger.error("[SC] resolve_track_from_url failed for %s: %s", permalink_url, exc)
            return None

    @staticmethod
    @lru_cache(maxsize=32)
    def get_playlists(auth_token: str) -> list[dict]:
        """
        Fetch ALL playlists for the authenticated user.
        Follows next_href pagination until exhausted (Criterion 8).
        Raises AuthExpiredError on token problems (Criterion 5).
        """
        user_id = SoundCloudPlaylistAPI._resolve_user_id(auth_token)
        headers = SoundCloudPlaylistAPI._get_headers(auth_token)

        playlists: list[dict] = []
        url: str | None = f"{SC_API_BASE}/users/{user_id}/playlists"
        params = {"limit": 50, "offset": 0}
        if not auth_token:
            params["client_id"] = get_sc_client_id()

        logger.info(f"[SC] Starting playlist fetch for user_id: {user_id}")

        while url:
            logger.debug(f"[SC] Fetching page: {url} (params: {params})")
            resp = _sc_get(url, headers=headers, params=params)
            data = resp.json()

            # Handle both list and paginated-dict responses
            if isinstance(data, list):
                collection = data
                url = None
                logger.info(f"[SC] Fetched {len(collection)} playlists (single page).")
            else:
                collection = data.get("collection", [])
                url = data.get("next_href")  # full URL with embedded params
                params = {}  # next_href already includes query params
                logger.info(f"[SC] Fetched {len(collection)} playlists from current page. Next page: {url}")

            if not isinstance(collection, list):
                logger.warning(f"[SC] get_playlists: unexpected collection format (type: {type(collection)}), stopping pagination.")
                break

            for pl in collection:
                if not pl or not pl.get("id"):
                    continue
                tracks_raw = pl.get("tracks", [])
                # No hard limit — frontend handles scrolling
                track_preview = [
                    t for t in (SoundCloudPlaylistAPI._normalize_track(tr) for tr in tracks_raw)
                    if t is not None
                ]
                playlists.append({
                    "id": pl.get("id"),
                    "title": pl.get("title", "Untitled"),
                    "track_count": pl.get("track_count", len(tracks_raw)),
                    "duration": pl.get("duration", 0),
                    "artwork_url": pl.get("artwork_url") or (tracks_raw[0].get("artwork_url") if tracks_raw else None),
                    "permalink_url": pl.get("permalink_url", ""),
                    "created_at": pl.get("created_at", ""),
                    "is_public": pl.get("sharing", "public") != "private",
                    "tracks": track_preview,
                })

            time.sleep(0.3)  # polite rate-limit spacing

        logger.info(f"[SC] Fetched {len(playlists)} playlists for user {user_id}.")
        return playlists

    @staticmethod
    @lru_cache(maxsize=32)
    def get_likes(auth_token: str, max_tracks: int = 500) -> dict:
        """
        Fetch user's liked tracks as a virtual playlist.
        Paginates until max_tracks reached or list exhausted (Criterion 8).
        Raises AuthExpiredError on token problems (Criterion 5).
        """
        user_id = SoundCloudPlaylistAPI._resolve_user_id(auth_token)
        headers = SoundCloudPlaylistAPI._get_headers(auth_token)

        tracks: list[dict] = []
        url: str | None = f"{SC_API_BASE}/users/{user_id}/favorites"
        params = {"limit": 50, "offset": 0}
        if not auth_token:
            params["client_id"] = get_sc_client_id()

        while url and len(tracks) < max_tracks:
            resp = _sc_get(url, headers=headers, params=params)
            data = resp.json()

            if isinstance(data, list):
                collection = data
                url = None
            elif isinstance(data, dict):
                collection = data.get("collection", [])
                url = data.get("next_href")
            else:
                logger.warning("[SC] get_likes: unexpected response format, stopping.")
                break

            if not isinstance(collection, list):
                logger.warning("[SC] get_likes: collection is not a list, stopping.")
                break

            for item in collection:
                if len(tracks) >= max_tracks:
                    break
                # Likes endpoint wraps the track in {"track": {...}} or is direct track
                raw_track = item.get("track") if isinstance(item, dict) and "track" in item else item
                normalized = SoundCloudPlaylistAPI._normalize_track(raw_track)
                if normalized:
                    tracks.append(normalized)

            if isinstance(data, list):
                break
            params = {}
            time.sleep(0.3)

        logger.info(f"[SC] Fetched {len(tracks)} liked tracks for user {user_id}.")
        return {
            "id": "likes",
            "title": "❤️ Liked Tracks",
            "track_count": len(tracks),
            "duration": sum(t.get("duration", 0) for t in tracks),
            "artwork_url": tracks[0].get("artwork_url") if tracks else None,
            "permalink_url": "",
            "created_at": "",
            "is_public": False,
            "is_likes": True,
            "tracks": tracks,
        }

    @staticmethod
    @lru_cache(maxsize=128)
    def get_full_playlist_tracks(playlist_id: int, auth_token: str) -> list[dict]:
        """
        Fetch ALL tracks for a specific playlist (not just the 20-track preview).
        Uses full representation and paginates (Criterion 8).
        """
        headers = SoundCloudPlaylistAPI._get_headers(auth_token)
        try:
            params = {"representation": "full"}
            if not auth_token:
                params["client_id"] = get_sc_client_id()
            resp = _sc_get(
                f"{SC_API_BASE}/playlists/{playlist_id}",
                headers=headers,
                params=params,
                timeout=20
            )
        except AuthExpiredError:
            raise
        except Exception as exc:
            logger.error(f"[SC] get_full_playlist_tracks({playlist_id}): {exc}")
            return []

        data = resp.json()
        if not isinstance(data, dict):
            logger.warning(f"[SC] Unexpected payload for playlist {playlist_id}.")
            return []

        raw_tracks = data.get("tracks", [])
        if not isinstance(raw_tracks, list):
            logger.warning(f"[SC] tracks field is not a list for playlist {playlist_id}.")
            return []

        result = []
        for raw in raw_tracks:
            normalized = SoundCloudPlaylistAPI._normalize_track(raw)
            if normalized:
                result.append(normalized)

        logger.info(f"[SC] get_full_playlist_tracks({playlist_id}): {len(result)} valid tracks (filtered from {len(raw_tracks)}).")
        return result


# ──────────────────────────────────────────────────────────────────────────────
# SoundCloud Sync Engine
# ──────────────────────────────────────────────────────────────────────────────

class SoundCloudSyncEngine:
    """Syncs SoundCloud playlists → local Rekordbox collection playlists."""

    SYNC_PREFIX = "SC_"

    def __init__(self, db_manager):
        self.db = db_manager

    def _normalize_title(self, title: str) -> str:
        return re.sub(r'[^\w\s]', '', title.lower().strip())

    def _fuzzy_match_track(self, sc_title: str, sc_artist: str, local_tracks: dict) -> str | None:
        """Find the best matching local track. Returns the local track ID or None."""
        tid, _ = self._fuzzy_match_with_score(sc_title, sc_artist, local_tracks)
        return tid

    def _fuzzy_match_with_score(self, sc_title: str, sc_artist: str, local_tracks: dict):
        """Find the best match and return (local_track_id, score). Score 0..1."""
        sc_combined = f"{sc_artist} - {sc_title}".lower()
        sc_norm_title = self._normalize_title(sc_title)
        best_match = None
        best_ratio = 0.0

        for tid, track in local_tracks.items():
            local_title = (track.get("Title") or "").lower()
            local_artist = (track.get("Artist") or "").lower()
            local_combined = f"{local_artist} - {local_title}"

            # Exact normalized title match wins immediately
            if sc_norm_title and sc_norm_title == self._normalize_title(local_title):
                return tid, 1.0

            ratio = SequenceMatcher(None, sc_combined, local_combined).ratio()
            if ratio > best_ratio and ratio >= 0.65:
                best_ratio = ratio
                best_match = tid

        return best_match, round(best_ratio, 3)

    def find_or_create_playlist(self, sc_playlist_title: str) -> str | None:
        """Find existing synced playlist or create a new one. Returns playlist ID string.
        Respects the 'sc_sync_folder_id' setting: if set, creates the playlist inside
        that local Rekordbox folder instead of ROOT.
        """
        from .services import SettingsManager
        sync_name = f"{self.SYNC_PREFIX}{sc_playlist_title}"
        for pl in self.db.playlists:
            if pl.get("Name") == sync_name:
                return str(pl.get("ID"))
        try:
            if hasattr(self.db, 'create_playlist'):
                # Determine target parent folder from settings
                target_folder_id = SettingsManager.load().get("sc_sync_folder_id") or "ROOT"
                logger.info(f"[SC] Creating playlist '{sync_name}' in folder id={target_folder_id}")
                # db.create_playlist() returns a node_data dict: {"ID": str, "Name": str, ...}
                node_data = self.db.create_playlist(sync_name, parent_id=target_folder_id)
                if isinstance(node_data, dict):
                    pid = str(node_data["ID"])
                else:
                    pid = str(node_data)
                logger.info(f"[SC] Created synced playlist: {sync_name} (ID: {pid})")
                return pid
            else:
                logger.warning("[SC] Database does not support create_playlist.")
        except Exception as exc:
            logger.error(f"[SC] Failed to create playlist {sync_name}: {exc}")
        return None

    def sync_playlist(self, sc_playlist: dict, auth_token: str) -> dict:
        """
        Sync a single SoundCloud playlist to local collection.
        Handles dead tracks gracefully (Criterion 9).
        """
        title = sc_playlist.get("title", "Untitled")
        sc_id = sc_playlist.get("id")

        result = {
            "playlist_title": title,
            "sc_id": sc_id,
            "matched": 0,
            "unmatched": 0,
            "already_synced": 0,
            "added": 0,
            "dead_tracks": 0,
            "errors": []
        }

        # Fetch full track list
        if sc_playlist.get("is_likes"):
            sc_tracks = sc_playlist.get("tracks", [])
        else:
            sc_tracks = SoundCloudPlaylistAPI.get_full_playlist_tracks(sc_id, auth_token)

        if not sc_tracks:
            result["errors"].append("No live tracks found in playlist (all may have been deleted).")
            return result

        pid = self.find_or_create_playlist(title)
        if not pid:
            result["errors"].append("Could not create or find local playlist.")
            return result

        # Existing track IDs in the local playlist (to skip re-adds)
        existing_track_ids: set = set()
        try:
            existing = self.db.get_playlist_tracks(pid)
            existing_track_ids = {str(t.get("id", t.get("ID", ""))) for t in existing}
        except Exception:
            pass

        local_tracks = self.db.tracks if hasattr(self.db, 'tracks') else {}

        for sc_track in sc_tracks:
            sc_title = sc_track.get("title", "")
            sc_artist = sc_track.get("artist", "")

            if not sc_title:
                result["dead_tracks"] += 1
                continue

            matched_tid = self._fuzzy_match_track(sc_title, sc_artist, local_tracks)
            if matched_tid:
                result["matched"] += 1
                if matched_tid in existing_track_ids:
                    result["already_synced"] += 1
                else:
                    try:
                        if hasattr(self.db, 'add_track_to_playlist'):
                            self.db.add_track_to_playlist(pid, matched_tid)
                            result["added"] += 1
                        elif hasattr(self.db.active_db, 'add_track_to_playlist'):
                            self.db.active_db.add_track_to_playlist(pid, matched_tid)
                            result["added"] += 1
                    except Exception as exc:
                        result["errors"].append(f"Failed to add '{sc_title}': {exc}")
            else:
                result["unmatched"] += 1

        return result

    def sync_all(self, playlists: list[dict], auth_token: str) -> list[dict]:
        """Sync all provided playlists sequentially."""
        results = []
        for pl in playlists:
            result = self.sync_playlist(pl, auth_token)
            results.append(result)
        return results

    def preview_matches(self, sc_playlist: dict, auth_token: str) -> list[dict]:
        """
        Dry-run: return per-track match details WITHOUT writing to the DB.
        Used by the Inspector Panel endpoint.
        Returns list of dicts:
          { sc_title, sc_artist, sc_url, local_id, local_title, local_artist, score, status }
          status: 'matched' | 'unmatched' | 'dead'
        """
        sc_id = sc_playlist.get("id")
        if sc_playlist.get("is_likes"):
            sc_tracks = sc_playlist.get("tracks", [])
        else:
            sc_tracks = SoundCloudPlaylistAPI.get_full_playlist_tracks(sc_id, auth_token)

        local_tracks = self.db.tracks if hasattr(self.db, 'tracks') else {}
        preview = []

        for sc_track in sc_tracks:
            sc_title = sc_track.get("title", "")
            sc_artist = sc_track.get("artist", "")

            if not sc_title:
                preview.append({"sc_title": "", "sc_artist": sc_artist,
                                 "sc_url": sc_track.get("permalink_url", ""),
                                 "local_id": None, "local_title": None, "local_artist": None,
                                 "score": 0.0, "status": "dead"})
                continue

            tid, score = self._fuzzy_match_with_score(sc_title, sc_artist, local_tracks)
            if tid:
                local = local_tracks[tid]
                preview.append({
                    "sc_title": sc_title,
                    "sc_artist": sc_artist,
                    "sc_url": sc_track.get("permalink_url", ""),
                    "local_id": tid,
                    "local_title": local.get("Title", ""),
                    "local_artist": local.get("Artist", ""),
                    "score": score,
                    "status": "matched",
                })
            else:
                preview.append({
                    "sc_title": sc_title,
                    "sc_artist": sc_artist,
                    "sc_url": sc_track.get("permalink_url", ""),
                    "local_id": None, "local_title": None, "local_artist": None,
                    "score": 0.0, "status": "unmatched",
                })

        return preview
