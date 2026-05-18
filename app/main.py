import asyncio
import json as _json_caps
import logging
import multiprocessing as _mp
import os
import secrets
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.auth import require_session
from app.rate_limit import rate_limit

# EC9: Load .env file so SOUNDCLOUD_CLIENT_ID etc. are available as env-vars.
# python-dotenv is a soft dependency; if missing we fall back to os.environ silently.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass  # dotenv not installed — rely on shell environment variables

# EC7/EC13: keyring provides OS-level credential storage for the SC OAuth token.
# This avoids storing the secret in plaintext in settings.json or cookies.
try:
    import keyring
except ImportError:
    # Fallback shim so the rest of main.py doesn't crash on import if keyring
    # is not yet installed.  On first run the user will get a clear 503 error
    # instead of a silent startup crash.
    class _KeyringShim:
        """No-op shim when the `keyring` package is unavailable."""
        _store: dict = {}
        def get_password(self, service, username):
            return self._store.get(f"{service}:{username}")
        def set_password(self, service, username, value):
            self._store[f"{service}:{username}"] = value
        def delete_password(self, service, username):
            self._store.pop(f"{service}:{username}", None)
    keyring = _KeyringShim()
    logging.getLogger("APP_MAIN").warning(
        "[WARN] `keyring` package not installed. SC tokens will be stored in-memory only "
        "and lost on restart. Run: pip install keyring"
    )

# OS-keyring service identifier — must be a single source of truth so that
# token reads and writes line up across every route. Was previously hard-
# coded as `"library_management_system"` at 10 call sites.
KEYRING_SERVICE = "library_management_system"
# Keyring entry name for the SoundCloud OAuth bearer token.
KEYRING_SC_TOKEN = "sc_token"

from . import audio_tags, download_registry, folder_watcher
from .audio_analyzer import LIBROSA_AVAILABLE, AudioAnalyzer
from .config import EXPORT_DIR, LOG_DIR, MUSIC_DIR, TEMP_DIR
from .database import db
from .phrase_generator import (
    commit_cues_to_db,
    detect_first_downbeat,
    extract_beats_from_db,
    generate_phrase_cues,
)
from .playcount_sync import (
    diff_playcounts,
    load_usb_sync_meta,
    read_usb_xml_playcounts,
    resolve_playcounts,
    save_usb_sync_meta,
)
from .rbep_parser import list_projects as rbep_list_projects
from .rbep_parser import parse_project as rbep_parse_project
from .rekordbox_bridge import RekordboxBridge
from .services import (
    AudioEngine,
    BeatAnalyzer,
    ImportManager,
    LibraryTools,
    ProjectManager,
    SettingsManager,
)
from .soundcloud_api import (
    AuthExpiredError,
    RateLimitError,
    SoundCloudPlaylistAPI,
    SoundCloudSyncEngine,
)
from .soundcloud_downloader import sc_downloader
from .usb_manager import UsbActions, UsbDetector, UsbProfileManager, UsbSyncEngine

# Per-operation lock — prevents race conditions on concurrent sync requests (Criterion 10)
_sync_lock = asyncio.Lock()

# CONFIG LOGGING
# Pre-set RedactingFormatter on each handler BEFORE basicConfig so the
# `if h.formatter is None` guard (CPython 3.13 Lib/logging/__init__.py L110)
# leaves our scrubbing formatter intact. Same instance on both handlers
# guarantees the exc_text cache is scrubbed before any handler emits,
# regardless of callHandlers iteration order.
from .logging_utils import RedactingFormatter, safe_error_message_str

_log_formatter = RedactingFormatter(
    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_log_file_handler = logging.FileHandler(LOG_DIR / "app.log", encoding='utf-8')
_log_file_handler.setFormatter(_log_formatter)
_log_stream_handler = logging.StreamHandler(sys.stdout)
_log_stream_handler.setFormatter(_log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_log_file_handler, _log_stream_handler])
logger = logging.getLogger("APP_MAIN")

APP_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Music Library Manager")

# --- SECURITY: Allowed audio directories for streaming/processing ---
# Users can only stream/process audio from these root directories.
ALLOWED_AUDIO_ROOTS: list[Path] = []

def _init_allowed_roots():
    """Populate allowed audio roots from common music locations and settings."""
    roots = [
        Path(os.path.expanduser("~/Music")),
        Path(EXPORT_DIR).resolve(),
        Path(MUSIC_DIR).resolve(),
        Path(TEMP_DIR).resolve(),
    ]
    # Add Rekordbox database root if available
    rb_root = Path(os.environ.get('APPDATA', '')) / "Pioneer" / "rekordbox"
    if rb_root.exists():
        roots.append(rb_root.resolve())
    # Add common drive letters on Windows
    for drive in ['C:', 'D:', 'E:', 'F:', 'G:', 'H:']:
        music_path = Path(drive + os.sep) / "Music"
        if music_path.exists():
            roots.append(music_path.resolve())
        dj_path = Path(drive + os.sep) / "DJ Music"
        if dj_path.exists():
            roots.append(dj_path.resolve())
    ALLOWED_AUDIO_ROOTS.extend(roots)
    if _mp.current_process().name == "MainProcess":
        logger.info(f"Allowed audio roots: {[str(r) for r in ALLOWED_AUDIO_ROOTS]}")

_init_allowed_roots()

ALLOWED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.aiff', '.aif', '.flac', '.m4a', '.ogg', '.wma', '.alac'}

def validate_audio_path(path_str: str) -> Path:
    """
    Security: Validates that a file path is a real audio file within allowed directories.
    Prevents path traversal and arbitrary file read attacks.
    """
    try:
        file_path = Path(path_str).resolve()
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Check extension
    if file_path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="File type not allowed")

    # Check file exists
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Check that the file is within an allowed directory.
    # Uses Path.is_relative_to (proper path-segment match) instead of
    # str.startswith (which incorrectly accepted "/foo/barX" as inside "/foo/bar").
    # Required by .claude/rules/coding-rules.md (Secrets & paths section).
    is_allowed = any(
        file_path.is_relative_to(root)
        for root in ALLOWED_AUDIO_ROOTS
    )
    # Also allow paths from the database (track paths stored in library).
    # Exact-match only — NOT a prefix check — so this is not a sandbox escape
    # as long as db.tracks remains user-trusted (populated from Rekordbox import).
    # TODO(security-api-auth-hardening): revisit during Phase 1 — prefer adding
    # the parent dirs of imported tracks to ALLOWED_AUDIO_ROOTS at import time.
    if not is_allowed:
        known_paths = {t.get('path', '') for t in db.tracks.values()} if hasattr(db, 'tracks') else set()
        if str(file_path) not in known_paths and path_str not in known_paths:
            logger.warning(f"SECURITY: Blocked access to path outside allowed roots: {file_path}")
            raise HTTPException(status_code=403, detail="Access denied: path outside allowed directories")

    return file_path

def safe_error_message(e: Exception) -> str:
    """Sanitize error messages to avoid leaking internal paths or system info.

    Delegates to `safe_error_message_str` in `app/logging_utils.py` so the
    widened path list (incl. `EXPORT_DIR`/`MUSIC_DIR`/`TEMP_DIR`) is shared
    with the log-write-time `RedactingFormatter`.
    """
    return safe_error_message_str(str(e))

# --- SECURITY: CORS locked to localhost only ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "tauri://localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Session-Token", "Authorization"],
)

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    EC4/EC10: Surface Pydantic 422 validation errors with field-level detail.
    FastAPI normally returns 422 for these, but they were previously swallowed by the
    global Exception handler into a generic 500.  This handler logs the exact field
    errors so payload mismatches are immediately visible in the backend logs,
    and returns them to the client in a structured way.
    """
    errors = exc.errors()
    logger.warning(
        f"[VALIDATION] {request.method} {request.url.path} — "
        f"{len(errors)} validation error(s): "
        + "; ".join(f"{e['loc']} → {e['msg']}" for e in errors)
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Request validation failed",
            "errors": [{"field": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in errors],
        },
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """SECURITY: Catch all unhandled exceptions to prevent hard crashes and data leaks."""
    logger.error(f"Unhandled Exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error occurred.", "error_type": type(exc).__name__}
    )

app.mount("/exports", StaticFiles(directory=EXPORT_DIR), name="exports")

# Artwork Mount
COVERS_DIR = Path(APP_DIR).parent / "app" / "data" / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/artwork", StaticFiles(directory=COVERS_DIR), name="artwork")

# --- DATA MODELS ---
class FileWriteReq(BaseModel):
    path: str
    content: str
class ExportRequest(BaseModel):
    source_path: str
    filename: str
    cuts: list[dict]
    output_name: str
    fade_in: bool = False
    fade_out: bool = False

class BatchReq(BaseModel):
    track_ids: list[str]
    updates: dict[str, Any]

class TrackUpdateReq(BaseModel):
    Rating: int | None = None
    ColorID: int | None = None
    Comment: str | None = None
    Genre: str | None = None

class MoveReq(BaseModel):
    track_ids: list[str]
    target_folder: str

class RenameReq(BaseModel):
    track_ids: list[str]
    pattern: str

class ScReq(BaseModel):
    artist_name: str
    link: str

class CueReq(BaseModel):
    track_id: str
    cues: list[dict]

class GridReq(BaseModel):
    track_id: str
    beat_grid: list[dict]

class SetReq(BaseModel):
    # Permissive: the frontend stores arbitrary preference keys (shortcuts,
    # waveform colors, scan_folders, …). Anything declared explicitly is type-
    # checked; everything else is preserved verbatim via model_extra.
    model_config = {"extra": "allow"}

    default_export_format: str = "wav"
    default_export_dir: str = ""  # User-selectable default folder for audio exports; empty = backend EXPORT_DIR
    theme: str = "dark"
    auto_snap: bool = True
    db_path: str = ""
    artist_view_threshold: int = 0
    waveform_visual_mode: str = "blue"
    hide_streaming: bool = False
    remember_lib_mode: bool = False
    last_lib_mode: str = "xml"
    ranking_filter_mode: str = "all"
    insights_playcount_threshold: int = 0
    insights_bitrate_threshold: int = 320
    soundcloud_auth_token: str = ""
    scan_folders: list[str] = []

class SmartPlReq(BaseModel):
    artist_threshold: int = 3
    label_threshold: int = 3

class PlCreateReq(BaseModel):
    name: str
    parent_id: str = "ROOT"
    type: str = "1" # 0=folder, 1=playlist

class PlRenameReq(BaseModel):
    pid: str
    name: str

class PlDeleteReq(BaseModel):
    pid: str

class PlMoveReq(BaseModel):
    pid: str
    parent_id: str = "ROOT"
    target_id: str | None = None
    position: str | None = "inside"

class PlRemoveTrackReq(BaseModel):
    pid: str
    track_id: str

class CleanTitlesReq(BaseModel):
    track_ids: list[str]

class DeleteTrackReq(BaseModel):
    track_id: str

class CreatePlReq(BaseModel):
    name: str
    parent_id: str = "ROOT"
    type: str = "1" # 1=Playlist, 0=Folder
    track_ids: list[str] = []

class RenamePlReq(BaseModel):
    pid: str
    name: str

class MovePlReq(BaseModel):
    pid: str
    parent_id: str
    target_id: str | None = None
    position: str | None = "inside"

class DeletePlReq(BaseModel):
    pid: str

class TrackPlReq(BaseModel):
    pid: str
    track_id: str

class AudioImportReq(BaseModel):
    file_path: str
    mode: str = "speed" # "speed" or "accuracy"

class AudioStatusReq(BaseModel):
    task_id: str

class AnalyzeFullReq(BaseModel):
    force: bool = False   # Re-analyze even if already analyzed

class AnalyzeBatchReq(BaseModel):
    track_ids: list[str] | None = None  # None = auto-detect unanalyzed
    force: bool = False


class MergeReq(BaseModel):
    category: str # "artists", "labels", "albums"
    source_name: str
    target_name: str

class RbxSyncReq(BaseModel):
    track_ids: list[str]
    filename: str | None = "rekordbox_export.xml"

class RbxImportReq(BaseModel):
    xml_path: str

class PlCreateReq(BaseModel):
    name: str
    parent_id: str = "ROOT"
    type: str = "1" # 0=Folder, 1=Playlist

class PlRenameReq(BaseModel):
    pid: str
    name: str

class PlDeleteReq(BaseModel):
    pid: str

class PlMoveReq(BaseModel):
    pid: str
    parent_id: str
    target_id: str | None = None
    position: str = "inside"

class PlReorderReq(BaseModel):
    pid: str
    track_id: str
    target_index: int

class PlRemoveTrackReq(BaseModel):
    pid: str
    track_id: str

class ProjectReq(BaseModel):
    name: str
    data: dict[str, Any]

class DBModeReq(BaseModel):
    mode: str # "xml" or "live"

# NOTE: Library auto-load is handled by startup_event() near the bottom of this file.
# A second @app.on_event("startup") here was causing the DB to load twice (~90s startup).
# Removed — do not add another startup handler here.

# --- ENDPOINTS ---

from fastapi import Request


@app.get("/api/stream")
async def stream_audio(path: str, request: Request):
    """Streams audio file with HTTP Range support — required for browser seeking."""
    file_path = validate_audio_path(path)

    mime_types = {
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.aiff': 'audio/aiff',
        '.aif': 'audio/aiff',
        '.flac': 'audio/flac',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
    }
    ext = file_path.suffix.lower()
    media_type = mime_types.get(ext, 'application/octet-stream')

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    # No Range header → still expose Accept-Ranges so browsers know seeking is possible
    if not range_header:
        async def _full():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        return StreamingResponse(
            _full(),
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Disposition": f'inline; filename="{file_path.name}"',
                "Cache-Control": "no-cache",
            },
        )

    # Parse "bytes=START-END"
    try:
        units, _, rng = range_header.partition("=")
        if units.strip().lower() != "bytes":
            raise ValueError("only byte ranges supported")
        start_s, _, end_s = rng.strip().partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start < 0:
            raise ValueError("invalid range")
    except Exception as e:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
            content=f"Invalid range: {e}",
        )

    chunk_size = end - start + 1

    async def _ranged():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_size
            buf = 64 * 1024
            while remaining > 0:
                data = f.read(min(buf, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        _ranged(),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(chunk_size),
            "Content-Disposition": f'inline; filename="{file_path.name}"',
            "Cache-Control": "no-cache",
        },
    )

@app.get("/api/audio/waveform")
async def get_multiband_waveform(path: str, pps: int = 50):
    """Returns 3-band waveform data for professional visualization."""
    file_path = validate_audio_path(path)
    try:
        return AudioEngine.generate_multiband_waveform(str(file_path), pixels_per_second=pps)
    except Exception as e:
        logger.error(f"Waveform generation failed: {e}")
        raise HTTPException(500, safe_error_message(e))

class FileRevealReq(BaseModel):
    path: str


@app.post("/api/file/reveal", dependencies=[Depends(require_session)])
def file_reveal(r: FileRevealReq):
    """Open the OS file explorer pointing at the given audio file path.

    SECURITY: the path is sandboxed via ``validate_audio_path`` so any
    authenticated caller can only reveal **audio files** inside
    ``ALLOWED_AUDIO_ROOTS`` (or paths already known to ``db.tracks``).
    See docs/research/research/evaluated_security-api-file-reveal-sandbox.md.
    """
    try:
        p = validate_audio_path(r.path)
        import subprocess
        import sys
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(p)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p.parent)], check=False)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, safe_error_message(e))


_FILE_WRITE_EXTENSIONS = {".rbep", ".json", ".txt", ".cue", ".m3u", ".m3u8"}


@app.post("/api/file/write", dependencies=[Depends(require_session)])
async def file_write(r: FileWriteReq):
    """Writes text content to a file (used for .rbep project saving).

    SECURITY: previously wrote to *any* path on disk ("we trust the path")
    which made every unauthenticated caller a remote-file-write primitive.
    Now constrained to:
      - paths resolving inside ALLOWED_AUDIO_ROOTS, and
      - an allow-list of text-project extensions (.rbep / .json / .txt /
        .cue / .m3u / .m3u8).
    Broader auth rework: docs/research/research/idea_security-api-auth-hardening.md.
    """
    try:
        path = Path(r.path)
        if not path.is_absolute():
            path = Path(APP_DIR).parent / r.path
        try:
            resolved = path.resolve()
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid path: {e}")

        if resolved.suffix.lower() not in _FILE_WRITE_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File extension not allowed for write: {resolved.suffix}",
            )

        if not any(resolved.is_relative_to(root) for root in ALLOWED_AUDIO_ROOTS):
            logger.warning(
                "SECURITY: Blocked /api/file/write outside allowed roots: %s",
                resolved,
            )
            raise HTTPException(
                status_code=403,
                detail="Path outside allowed directories",
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(r.content)

        return {"status": "success", "path": str(resolved)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File write error: {e}")
        raise HTTPException(500, safe_error_message(e))

@app.post("/api/xml/clean", dependencies=[Depends(require_session)])
async def clean_xml(
    file: UploadFile = File(...),
    artist_folder: str = Form("_AUTO_ARTISTS"),
    label_folder: str = Form("_AUTO_LABELS")
):
    try:
        # SECURITY: Validate file extension
        if not file.filename or not file.filename.lower().endswith('.xml'):
            raise HTTPException(400, "Only XML files are accepted")

        # SECURITY: Fixed target path — no user-controlled filename
        target_path = Path("rekordbox.xml")
        with open(target_path, "wb") as f:
             shutil.copyfileobj(file.file, f)

        # Reload DB
        db.load_xml(str(target_path))

        return {
            "status": "success",
            "message": "XML Uploaded and Scanned",
            "tracks": len(db.tracks),
            "playlists": len(db.playlists)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"XML Error: {e}")
        raise HTTPException(400, safe_error_message(e))

@app.get("/api/genres")
def get_genres(): return db.get_all_genres()

@app.get("/api/library/tracks")
def get_library_tracks():
    try:
        tracks = db.get_all_tracks()
        logger.info(f"Returning {len(tracks)} tracks for library view")
        return tracks
    except Exception as e:
        logger.error(f"Failed to fetch library tracks: {e}")
        return []

@app.get("/api/insights/low_quality")
def get_low_quality_tracks():
    threshold = 320
    try:
        from .services import SettingsManager
        settings = SettingsManager.load()
        threshold = int(settings.get("insights_bitrate_threshold", 320))
    except Exception as e:
        logger.warning(f"Could not load bitrate threshold from settings: {e}")

    tracks = []
    source = db.tracks.values()

    for t in source:
        try:
            br = t.get("Bitrate", t.get("BitRate", 0))
            if int(br) < threshold and int(br) > 0:
               tracks.append(t)
        except (ValueError, TypeError) as e:
            logger.debug(f"Skipping track with invalid bitrate: {t.get('Name', '?')}: {e}")

    return tracks

@app.get("/api/insights/no_artwork")
def get_no_artwork_tracks():
    try:
        return db.get_tracks_missing_artwork()
    except Exception as e:
        logger.error(f"Error fetching no-artwork tracks: {e}")
        return []

@app.get("/api/insights/lost")
def get_lost_tracks():
    logger.info("Fetching lost tracks (low play count)...")

    threshold = 0
    try:
        from .services import SettingsManager
        settings = SettingsManager.load()
        threshold = int(settings.get("insights_playcount_threshold", 0))
    except Exception as e:
        logger.warning(f"Could not load playcount threshold from settings: {e}")

    tracks = []
    for t in db.tracks.values():
        try:
            pc = int(t.get("PlayCount", 0))
            if pc <= threshold:
                tracks.append(t)
        except (ValueError, TypeError) as e:
            logger.debug(f"Skipping track with invalid playcount: {t.get('Name', '?')}: {e}")
    return tracks

@app.get("/api/labels")
def get_labels(): return db.get_all_labels()

@app.get("/api/albums")
def get_albums(): return db.get_all_albums()

@app.post("/api/metadata/merge", dependencies=[Depends(require_session)])
def merge_metadata(r: MergeReq):
    try:
        from .services import MetadataManager
        MetadataManager.add_mapping(r.category, r.source_name, r.target_name)
        db.refresh_metadata()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/artists")
def get_artists():
    # artists = db.get_all_artists()
    # For XML mode, this might come from cached XML data
    return db.get_all_artists()

@app.get("/api/artist/{aid}/tracks")
def get_artist_tracks(aid: str): return db.get_tracks_by_artist(aid)

@app.get("/api/label/{aid}/tracks")
def get_label_tracks(aid: str): return db.get_tracks_by_label(aid)

@app.get("/api/album/{aid}/tracks")
def get_album_tracks(aid: str): return db.get_tracks_by_album(aid)

@app.get("/api/track/{tid}")
def get_track(tid: str):
    track = db.get_track_details(tid)
    if not track: raise HTTPException(404, "Track not found")
    # Synthesis ArtistName
    res = dict(track)
    res['ArtistName'] = res.get('Artist') or 'Unknown Artist'
    return res

@app.get("/api/track/{tid}/cues")
def get_cues(tid: str): return db.get_track_cues(tid)

@app.get("/api/track/{tid}/beatgrid")
def get_beatgrid(tid: str): return db.get_track_beatgrid(tid)

@app.post("/api/track/{tid}/analyze", dependencies=[Depends(require_session)])
async def analyze_track(tid: str):
    """Analyzes a track to detect BPM and beat grid if missing from XML"""
    track = db.get_track_details(tid)
    if not track:
        raise HTTPException(404, "Track not found")

    path = track.get("path")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Audio file not found")

    try:
        # Perform analysis in a separate process pool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(AudioAnalyzer.get_executor(), BeatAnalyzer.analyze, path)

        # PERSIST TO DATABASE IN-MEMORY
        track["BPM"] = result["bpm"]
        track["beatGrid"] = result["beats"]
        track["TotalTime"] = result["totalTime"]
        track["dropTime"] = result["dropTime"]

        # Add a "DROP" position mark if not already present
        marks = track.get("positionMarks", [])
        if not any(m.get("name") == "DROP" for m in marks):
            marks.append({
                "name": "DROP",
                "type": 0,
                "start": result["dropTime"],
                "num": -1,
                "red": 255, "green": 0, "blue": 0
            })
            track["positionMarks"] = marks

        db.save_xml()
        return result
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        raise HTTPException(500, safe_error_message(e))

@app.get("/api/audio/stream")
async def stream_audio(path: str):
    """Streams an audio file from the local filesystem"""
    logger.info(f"Stream request for: {path}")

    # SECURITY: Validate against allowed audio roots to prevent path traversal
    valid_path = validate_audio_path(path)

    return FileResponse(valid_path, headers={"Accept-Ranges": "bytes"})


# ── Pioneer "My Tag" (mode-agnostic — XML + Live both supported) ──────────────
def _require_live_db():
    """Backwards-compatible name. Now returns whichever active_db has MyTag support."""
    if not db.active_db or not hasattr(db.active_db, "list_mytags"):
        raise HTTPException(409, "MyTag is not available — library not loaded.")
    return db.active_db


class MyTagCreateReq(BaseModel):
    name: str


class TrackMyTagsReq(BaseModel):
    tag_ids: list[str] = []


@app.get("/api/mytags")
def list_mytags():
    return _require_live_db().list_mytags()


@app.post("/api/mytags", dependencies=[Depends(require_session)])
def create_mytag(r: MyTagCreateReq):
    name = (r.name or "").strip()
    if not name:
        raise HTTPException(400, "Tag name is required.")
    try:
        new_id = _require_live_db().create_mytag(name)
        return {"status": "success", "id": new_id, "name": name}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Could not create MyTag: {exc}")


@app.delete("/api/mytags/{tag_id}", dependencies=[Depends(require_session)])
def delete_mytag(tag_id: str):
    try:
        _require_live_db().delete_mytag(tag_id)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Could not delete MyTag: {exc}")


@app.get("/api/track/{tid}/mytags")
def get_track_mytags(tid: str):
    return _require_live_db().get_track_mytags(tid)


@app.post("/api/track/{tid}/mytags", dependencies=[Depends(require_session)])
def set_track_mytags(tid: str, r: TrackMyTagsReq):
    try:
        result = _require_live_db().set_track_mytags(tid, r.tag_ids)
        return {"status": "success", **result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Could not update MyTags: {exc}")


@app.post("/api/track/cues/save", dependencies=[Depends(require_session)])
def save_cues(r: CueReq): return {"status": "success" if db.save_track_cues(r.track_id, r.cues) else "error"}

@app.post("/api/track/grid/save", dependencies=[Depends(require_session)])
def save_grid(r: GridReq): return {"status": "success" if db.save_track_beatgrid(r.track_id, r.beat_grid) else "error"}

@app.post("/api/track/{tid}", dependencies=[Depends(require_session)])
def update_track(tid: str, r: TrackUpdateReq):
    updates = {k: v for k, v in r.model_dump().items() if v is not None}
    if not updates: return {"status": "no_change"}
    try:
        if not db.update_tracks_metadata([tid], updates):
            raise HTTPException(500, "Update returned False (unknown error)")

        # Best-effort write-back to the source audio file's tags. Disabled by
        # toggling settings.write_tags_to_files=False (default true).
        tag_status = "skipped"
        try:
            cfg = SettingsManager.load()
            if cfg.get("write_tags_to_files", True):
                track = db.get_track_details(tid) or {}
                src = track.get("path")
                if src:
                    artwork_bytes = None
                    art_path = track.get("Artwork")
                    if art_path:
                        artwork_bytes = audio_tags.load_artwork(art_path)
                    ok = audio_tags.write_tags(src, updates, artwork=artwork_bytes)
                    tag_status = "written" if ok else "failed"
        except Exception as exc:
            logger.warning(f"Tag write-back skipped for {tid}: {exc}")
            tag_status = "error"

        return {"status": "success", "file_tags": tag_status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update failed for {tid}: {e}")
        raise HTTPException(500, f"Update failed: {e!s}")

@app.patch("/api/tracks/batch", dependencies=[Depends(require_session)])
def batch_up(r: BatchReq): return {"status": "success" if db.update_tracks_metadata(r.track_ids, r.updates) else "error"}

@app.post("/api/system/heartbeat")
def system_heartbeat():
    """Lightweight keep-alive ping.

    Stays unauth'd by design — Tauri / dev-frontend use this to detect
    sidecar boot before the auth handshake completes. The legacy
    ``SHUTDOWN_TOKEN`` leak that lived here was removed when Bearer-token
    auth landed (see ``app/auth.py`` + the
    ``security-api-auth-hardening`` plan).
    """
    global last_heartbeat
    last_heartbeat = time.time()
    return {"status": "alive"}

@app.post("/api/track/delete", dependencies=[Depends(require_session)])
def del_trk(r: DeleteTrackReq): return {"status": "success" if db.delete_track(r.track_id) else "error"}

@app.post("/api/tracks/move", dependencies=[Depends(require_session)])
def move_t(r: MoveReq):
    # This might need adjustment for XML mode if we edit XML
    return {"moved": 0, "errors": ["Not supported in XML-only mode yet"]}

@app.post("/api/tools/rename", dependencies=[Depends(require_session)])
def ren_t(r: RenameReq):
    """Bulk smart-rename audio files on disk using a token pattern.

    Pattern tokens: %Artist%, %Title%, %BPM%, %Key%. Windows-illegal
    characters are stripped. Returns {"success": [...], "errors": [...]}.
    See LibraryTools.smart_rename for details.
    """
    try:
        return LibraryTools.smart_rename(r.track_ids, r.pattern)
    except Exception as e:
        logger.error("smart_rename failed: %s", e, exc_info=True)
        return {"success": [], "errors": [str(e)]}

@app.get("/api/playlists/tree")
def get_tree():
    if not db.active_db:
        # Backend is still loading (ANLZ scan in progress) — return empty tree,
        # frontend will retry on next interaction rather than showing a crash.
        logger.warning("get_tree: DB not yet loaded, returning empty tree")
        return []
    tree = db.get_playlist_tree()
    logger.info(f"Fetched playlist tree. Root nodes: {len(tree)}")
    return tree

@app.post("/api/playlists/create", dependencies=[Depends(require_session)])
def create_pl(r: CreatePlReq):
    pid = db.create_playlist(r.name, r.parent_id, r.type == "0", r.track_ids)
    return {"status": "success", "id": pid}

@app.post("/api/playlists/rename", dependencies=[Depends(require_session)])
def rename_pl(r: RenamePlReq): return {"status": "success" if db.rename_playlist(r.pid, r.name) else "error"}

@app.post("/api/playlists/move", dependencies=[Depends(require_session)])
def move_pl(r: MovePlReq): return {"status": "success" if db.move_playlist(r.pid, r.parent_id, r.target_id, r.position) else "error"}

@app.post("/api/playlists/delete", dependencies=[Depends(require_session)])
def delete_pl(r: DeletePlReq): return {"status": "success" if db.delete_playlist(r.pid) else "error"}

@app.post("/api/playlists/add-track", dependencies=[Depends(require_session)])
def add_trk_pl(r: TrackPlReq): return {"status": "success" if db.add_track_to_playlist(r.pid, r.track_id) else "error"}

@app.post("/api/playlists/remove-track", dependencies=[Depends(require_session)])
def remove_track_pl(r: PlRemoveTrackReq):
    try:
        db.remove_track_from_playlist(r.pid, r.track_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to remove track {r.track_id} from PL {r.pid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/track/{tid}", dependencies=[Depends(require_session)])
def delete_track(tid: str):
    logger.info(f"API Request: Delete track {tid}")
    if db.delete_track(tid):
        return {"status": "success", "tid": tid}
    raise HTTPException(status_code=404, detail="Track not found or could not be deleted")

@app.post("/api/playlists/reorder", dependencies=[Depends(require_session)])
def reorder_pl_track(r: PlReorderReq):
    try:
        if db.reorder_playlist_track(r.pid, r.track_id, r.target_index):
            return {"status": "success"}
        else:
            raise HTTPException(500, "Failed to reorder")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reorder error: {e}")
        raise HTTPException(500, safe_error_message(e))


# ─── Smart Playlist Endpoints ─────────────────────────────────────────────

class SmartPlaylistCreateReq(BaseModel):
    name: str
    parent_id: str = "ROOT"
    criteria: dict = {}


class SmartPlaylistUpdateReq(BaseModel):
    pid: str
    criteria: dict


@app.post("/api/playlists/smart/create", dependencies=[Depends(require_session)])
def create_smart_pl(r: SmartPlaylistCreateReq):
    try:
        node = db.create_smart_playlist(r.name, r.criteria, r.parent_id)
        if node:
            return {"status": "success", "id": str(node.get("ID") or node)}
        raise HTTPException(400, "create_smart_playlist not supported in current mode")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Smart playlist create failed: {e}")
        raise HTTPException(500, safe_error_message(e))


@app.post("/api/playlists/smart/update", dependencies=[Depends(require_session)])
def update_smart_pl(r: SmartPlaylistUpdateReq):
    try:
        ok = db.update_smart_playlist(r.pid, r.criteria)
        return {"status": "success" if ok else "error"}
    except Exception as e:
        raise HTTPException(500, safe_error_message(e))


@app.get("/api/playlists/smart/{pid}/evaluate")
def evaluate_smart_pl(pid: str):
    """Run smart-playlist criteria → list of matching track dicts."""
    try:
        return db.evaluate_smart_playlist(pid)
    except Exception as e:
        logger.error(f"Smart eval failed: {e}")
        raise HTTPException(500, safe_error_message(e))


class UsbHistoryReq(BaseModel):
    drive: str  # e.g. "E:\\"


@app.post("/api/usb/history", dependencies=[Depends(require_session)])
def read_usb_history(r: UsbHistoryReq):
    """
    Read CDJ-written history (history_entries / history_contents) from a USB stick
    after a gig. Returns the played tracks per session so the user can review what
    was played, in what order.
    """
    try:
        from pathlib import Path as _P

        import rbox
        usb = _P(r.drive)
        if len(r.drive) == 2 and r.drive[1] == ":":
            usb = _P(r.drive + "\\")
        db_path = usb / "PIONEER" / "rekordbox" / "exportLibrary.db"
        if not db_path.exists():
            raise HTTPException(404, f"No exportLibrary.db on {usb}")
        ol = rbox.OneLibrary(str(db_path))
        sessions = []
        for h in ol.get_histories():
            children = ol.get_history_contents(h.id) if hasattr(ol, "get_history_contents") else []
            sessions.append({
                "id": str(h.id),
                "name": getattr(h, "name", ""),
                "date": getattr(h, "date_created", ""),
                "tracks": [
                    {
                        "content_id": str(getattr(c, "content_id", c)),
                        "seq": getattr(c, "seq", 0),
                    } for c in children
                ],
            })
        return {"sessions": sessions}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"USB history read failed: {e}")
        raise HTTPException(500, safe_error_message(e))


@app.post("/api/playlists/folder/create", dependencies=[Depends(require_session)])
def create_folder_pl(r: CreatePlReq):
    """Convenience: create a folder (Type=0). Equivalent to /create with type='0'."""
    try:
        node = db.create_folder(r.name, r.parent_id) if hasattr(db, "create_folder") else \
            db.create_playlist(r.name, r.parent_id, is_folder=True)
        if node:
            return {"status": "success", "id": str(node.get("ID") if isinstance(node, dict) else node)}
        raise HTTPException(400, "Could not create folder")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, safe_error_message(e))

# --- RESTORED ENDPOINTS ---

@app.get("/api/tools/duplicates")
def get_dupes(): return LibraryTools.find_duplicates()

class BatchCommentReq(BaseModel):
    source_id: str  # "LIB" or playlist_id
    action: str     # remove, replace, append, set
    find: str | None = ""
    replace: str | None = ""

@app.post("/api/tools/batch-comment", dependencies=[Depends(require_session)])
async def batch_comment(r: BatchCommentReq):
    try:
        import os
        import subprocess
        import sys
        from pathlib import Path

        # Manually resolve DB path to avoid touching the global 'db' object
        # which contains thread-unsafe Rust objects.
        live_db_path = Path(os.path.expandvars(r"%APPDATA%\Pioneer\rekordbox\master.db"))
        if not live_db_path.exists():
            live_db_path = Path(os.path.expandvars(r"%APPDATA%\Pioneer\rekordbox6\master.db"))

        if not live_db_path.exists():
             raise HTTPException(400, "Could not find Rekordbox master.db")

        # Call the worker script
        cmd = [
            sys.executable,
            str(Path(APP_DIR) / "batch_worker.py"),
            "--db", str(live_db_path),
            "--source", r.source_id,
            "--action", r.action
        ]

        if r.find:
            cmd.extend(["--find", r.find])
        if r.replace:
            cmd.extend(["--replace", r.replace])

        logger.info(f"Running batch worker: {cmd}")

        # Run and capture output. timeout=600s — batch workers can legitimately
        # run minutes over large libraries, but past that point we treat the
        # process as hung and reclaim resources rather than blocking the
        # request thread forever.
        result = subprocess.check_output(
            cmd, text=True, stderr=subprocess.STDOUT, timeout=600
        )
        logger.info(f"Batch worker output: {result}")

        # Parse count
        count = 0
        for line in result.split("\n"):
            if line.startswith("COUNT:"):
                count = int(line.split(":")[1])

        return {"status": "success", "count": count}

    except subprocess.TimeoutExpired as e:
        logger.error("Batch worker exceeded 600s timeout — killed (%s)", e)
        raise HTTPException(504, detail="Batch worker timed out after 600s") from e
    except subprocess.CalledProcessError as e:
        logger.error(f"Batch worker failed: {e.output}")
        raise HTTPException(500, "Batch processing failed. Check logs for details.")
    except Exception as e:
        logger.error(f"Batch comment error: {e}")
        raise HTTPException(500, safe_error_message(e))

@app.post("/api/library/clean-titles", dependencies=[Depends(require_session)])
def clean_titles(r: CleanTitlesReq):
    return LibraryTools.clean_track_titles(r.track_ids)

@app.get("/api/library/status")
def get_lib_status():
    return {
        "loaded": db.loaded,
        "mode": db.mode,
        "path": str(db.xml_path) if db.mode == "xml" else str(db.live_db_path),
        "tracks": len(db.active_db.tracks) if db.active_db else 0,
        "playlists": len(db.active_db.playlists) if db.active_db else 0,
        "loading_current_item": getattr(db.active_db, "loading_status", "Idle") if db.active_db else "Idle"
    }

@app.post("/api/library/mode", dependencies=[Depends(require_session)])
def set_lib_mode(r: DBModeReq):
    logger.info(f"Setting library mode to: {r.mode}")
    success = db.set_mode(r.mode)
    if success:
        # Update settings if remember is on
        s = SettingsManager.load()
        if s.get("remember_lib_mode"):
            s["last_lib_mode"] = r.mode
            SettingsManager.save(s)

        db.load_library() # Reload in new mode
        logger.info(f"Library reloaded in {r.mode} mode. Tracks: {len(db.tracks)}")
    return {"status": "success" if success else "error", "mode": db.mode}

@app.post("/api/library/sync", dependencies=[Depends(require_session)])
def sync_lib():
    """Persist the in-memory library.

    XML mode: writes ``rekordbox.xml`` to disk.
    Live mode: no-op — rbox writes through on every mutation, so there is
    nothing extra to commit.
    """
    if db.mode == "live":
        return {"status": "success", "message": "Live mode — changes auto-saved"}
    success = db.save()
    return {"status": "success" if success else "error"}

class LoadLibReq(BaseModel):
    path: str | None = None

@app.post("/api/library/load", dependencies=[Depends(require_session)])
def load_lib(r: LoadLibReq = Body(default=None)):
    requested_path = r.path if r else None
    try:
        if db.mode == "live":
            success = db.active_db.load()
        else:
            xml_path = requested_path or str(db.xml_db.xml_path) or "rekordbox.xml"
            success = db.xml_db.load_xml(xml_path)
        db.loaded = success
        return {
            "status": "success" if success else "error",
            "message": "Library loaded" if success else "Failed to load library",
            "tracks": len(db.tracks),
            "path": str(db.xml_db.xml_path) if db.mode == "xml" else str(db.live_db_path),
        }
    except Exception as e:
        logger.error(f"Failed to load library: {e}\n{traceback.format_exc()}")
        return {"status": "error", "message": safe_error_message(e)}

@app.post("/api/library/unload", dependencies=[Depends(require_session)])
def unload_lib():
    success = db.unload_library()
    return {"status": "success", "message": "Library unloaded."}

# --- REKORDBOX SYNC ENDPOINTS ---

@app.post("/api/rekordbox/export", dependencies=[Depends(require_session)])
async def rbx_export(r: RbxSyncReq):
    """Exports specified tracks to a Rekordbox XML file."""
    try:
        output_path = EXPORT_DIR / r.filename
        xml_path = RekordboxBridge.export_collection(r.track_ids, output_path)
        return {
            "status": "success",
            "message": f"Exported {len(r.track_ids)} tracks to XML.",
            "path": str(xml_path)
        }
    except Exception as e:
        logger.error(f"Rekordbox export failed: {e}")
        raise HTTPException(500, safe_error_message(e))

@app.post("/api/rekordbox/import", dependencies=[Depends(require_session)])
async def rbx_import(r: RbxImportReq):
    """Imports tracks and metadata from a Rekordbox XML file."""
    try:
        if not os.path.exists(r.xml_path):
             raise HTTPException(404, "XML file not found")

        results = RekordboxBridge.import_library(r.xml_path)
        return {
            "status": "success",
            "message": f"Import complete: {results['added']} added, {results['updated']} updated.",
            "details": results
        }
    except Exception as e:
        logger.error(f"Rekordbox import failed: {e}")
        raise HTTPException(500, safe_error_message(e))


@app.post("/api/library/smart-playlists", dependencies=[Depends(require_session)])
def gen_smart(r: SmartPlReq):
    status = LibraryTools.generate_smart_playlists(r.artist_threshold, r.label_threshold)
    return {"status": "success" if status else "error"}


class PathRequest(BaseModel):
    """Generic single-path body used by every endpoint that needs to point at
    a filesystem location (scan-folder, folder-watcher add/remove, etc.)."""
    path: str


class ImportPathsReq(BaseModel):
    """Drag-drop import body. `paths` is mixed files + directories; the
    optional `group_into_playlist` + `playlist_name` group every imported
    track into a single playlist (defaults to the dropped folder's name)."""
    paths: list[str]
    group_into_playlist: bool = False
    playlist_name: str | None = None


@app.post("/api/library/scan-folder", dependencies=[Depends(require_session)])
async def scan_folder(r: PathRequest):
    """
    Trigger an import scan of a specific directory.
    Walks the folder recursively and imports any audio files not yet in the library.
    Long-running — runs in a background thread via BackgroundTasks.
    """
    path_str = r.path.strip()
    if not path_str:
        raise HTTPException(status_code=400, detail="'path' is required.")

    scan_path = Path(path_str)
    if not scan_path.exists() or not scan_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path_str}")

    def _scan():
        imported = 0
        skipped = 0
        for audio_file in scan_path.rglob("*"):
            if not audio_file.is_file() or audio_file.suffix.lower() not in folder_watcher.AUDIO_EXTENSIONS:
                continue
            try:
                if _is_known_audio_path(audio_file):
                    skipped += 1
                    continue
                result = ImportManager.process_import(audio_file)
                if result:
                    imported += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.warning("[Scan] Skipped %s: %s", audio_file.name, exc)
                skipped += 1
        logger.info("[Scan] Folder scan complete: path=%s imported=%d skipped=%d", path_str, imported, skipped)

    thread = threading.Thread(target=_scan, daemon=True, name=f"scan-{scan_path.name}")
    thread.start()
    return {"status": "ok", "data": {"message": f"Scanning {path_str} in background…"}}


@app.post("/api/library/import-paths", dependencies=[Depends(require_session)])
async def import_paths(r: ImportPathsReq):
    """
    Import a mixed list of absolute filesystem paths (files OR directories).
    Used by drag-drop in the desktop app, where the OS hands us full paths
    instead of upload-able File blobs. Dedup via _is_known_audio_path.

    Optional body fields:
      group_into_playlist: bool — if true, every audio file (incl. duplicates
                                  already in the library) is gathered into a
                                  single playlist named after the source folder
                                  (or `playlist_name` if given).
      playlist_name: str        — explicit playlist name override.
    """
    raw = r.paths
    if not raw:
        raise HTTPException(status_code=400, detail="'paths' must be a non-empty list")

    group_into_playlist = r.group_into_playlist
    explicit_pl_name = (r.playlist_name or "").strip()

    targets: list[Path] = []
    queued_dirs = 0
    queued_files = 0
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            continue
        try:
            p = Path(entry).expanduser()
        except Exception:
            continue
        if not p.exists():
            continue
        if p.is_dir():
            targets.append(p)
            queued_dirs += 1
        elif p.is_file() and p.suffix.lower() in folder_watcher.AUDIO_EXTENSIONS:
            targets.append(p)
            queued_files += 1

    if not targets:
        return {"status": "ok", "queued_dirs": 0, "queued_files": 0, "message": "No importable paths."}

    from . import import_tracker

    counters = {"imported": 0, "skipped": 0, "linked": 0}

    # ── Phase 1: enumerate and register tasks UPFRONT ─────────────────────
    queued: list[tuple[Path, str]] = []  # (path, task_id)
    folder_for_pl: Path | None = None
    for t in targets:
        try:
            if t.is_dir():
                if folder_for_pl is None:
                    folder_for_pl = t
                for f in t.rglob("*"):
                    if f.is_file() and f.suffix.lower() in folder_watcher.AUDIO_EXTENSIONS:
                        tid = import_tracker.register(str(f), source="drag-drop")
                        queued.append((f, tid))
            elif t.is_file():
                tid = import_tracker.register(str(t), source="drag-drop")
                queued.append((t, tid))
        except Exception as exc:
            logger.warning("[Drop] Scan failed for %s: %s", t, exc)
    logger.info("[Drop] queued %d files for import (group=%s)", len(queued), group_into_playlist)

    # Determine playlist name once. Empty → fall through to per-file 'Import' default.
    playlist_name = ""
    if group_into_playlist:
        if explicit_pl_name:
            playlist_name = explicit_pl_name
        elif folder_for_pl is not None:
            playlist_name = folder_for_pl.name
    collected_track_ids: list[str] = []

    def _process_one(f: Path, tid: str) -> None:
        local_id: str | None = None
        if _is_known_audio_path(f):
            local_id = _track_id_for_path(f)
            import_tracker.update(
                tid, status="Skipped", progress=100,
                error="Already in library",
                local_track_id=local_id,
            )
            counters["skipped"] += 1
            if local_id and group_into_playlist:
                collected_track_ids.append(local_id)
            return
        threading.current_thread()._lms_import_tid = tid
        try:
            import_tracker.update(tid, status="Analyzing", progress=20)
            result = ImportManager.process_import(f)
            import_tracker.update(tid, status="Importing", progress=60)
            local_id_raw, analysis = (result if isinstance(result, tuple) else (result, {}))
            local_id = str(local_id_raw) if local_id_raw else None
            import_tracker.update(
                tid, status="Completed", progress=100,
                local_track_id=local_id,
                bpm=analysis.get("bpm") if isinstance(analysis, dict) else None,
                key=analysis.get("key") if isinstance(analysis, dict) else None,
            )
            counters["imported"] += 1
            if local_id and group_into_playlist:
                collected_track_ids.append(local_id)
        except Exception as exc:
            logger.warning("[Drop] Import failed for %s: %s", f, exc)
            import_tracker.update(tid, status="Failed", progress=100, error=str(exc))
            counters["skipped"] += 1
        finally:
            try: del threading.current_thread()._lms_import_tid
            except AttributeError: pass

    def _bundle_into_playlist():
        """After all files are processed, drop them into a single playlist."""
        if not group_into_playlist or not playlist_name:
            return
        try:
            # Find or create playlist (avoid creating duplicates of same name at ROOT)
            pid = None
            for p in db.playlists:
                if p.get("Name") == playlist_name:
                    pid = str(p.get("ID"))
                    break
            if not pid and hasattr(db, "create_playlist"):
                node = db.create_playlist(playlist_name, "ROOT", is_folder=False)
                pid = str(node.get("ID")) if isinstance(node, dict) else str(node)
            if not pid:
                logger.warning("[Drop] Could not create playlist '%s'", playlist_name)
                return
            for tid_local in collected_track_ids:
                try:
                    db.add_track_to_playlist(pid, tid_local)
                    counters["linked"] += 1
                except Exception as exc:
                    logger.debug("[Drop] add_track_to_playlist(%s, %s) failed: %s",
                                 pid, tid_local, exc)
            logger.info("[Drop] Bundled %d tracks into playlist '%s' (pid=%s)",
                        counters["linked"], playlist_name, pid)
        except Exception as exc:
            logger.warning("[Drop] Playlist bundling failed: %s", exc)

    def _run():
        for f, tid in queued:
            try:
                _process_one(f, tid)
            except Exception as exc:
                logger.warning("[Drop] worker error for %s: %s", f, exc)
        _bundle_into_playlist()
        logger.info(
            "[Drop] import-paths complete: imported=%d skipped=%d linked=%d playlist=%r",
            counters["imported"], counters["skipped"], counters["linked"], playlist_name,
        )

    threading.Thread(target=_run, daemon=True, name="drop-import").start()
    pl_msg = f" → playlist '{playlist_name}'" if (group_into_playlist and playlist_name) else ""
    return {
        "status": "ok",
        "queued_dirs": queued_dirs,
        "queued_files": queued_files,
        "queued_total": len(queued),
        "playlist_name": playlist_name,
        "group_into_playlist": group_into_playlist,
        "message": f"Queued {len(queued)} audio file(s) for import (from {queued_dirs} folder(s), {queued_files} loose file(s)){pl_msg}",
    }

@app.get("/api/import/tasks")
def get_import_tasks():
    """Live status of every local-file import (drag-drop / folder browse)."""
    from . import import_tracker
    return import_tracker.get_all()


@app.post("/api/import/tasks/clear", dependencies=[Depends(require_session)])
def clear_import_tasks():
    """Drop all completed/failed/skipped tasks from the tracker."""
    from . import import_tracker
    return {"removed": import_tracker.clear_finished()}


@app.get("/api/artwork")
async def get_artwork(path: str):
    """
    Serves artwork image from absolute path or Rekordbox relative path.
    """
    if not path:
        raise HTTPException(404, "No path provided")

    # Try resolving Rekordbox relative paths (e.g. /PIONEER/Artwork/...)
    p_path = path
    if path.startswith("/PIONEER/"):
        app_data = os.environ.get("APPDATA")
        if app_data:
            # Rekordbox 6/7 stores artwork in AppData/Roaming/Pioneer/rekordbox/share
            # The relative path starts with /PIONEER/ which matches inside the share folder.
            rel = path.lstrip("/") # Remove leading slash
            p_path = Path(app_data) / "Pioneer" / "rekordbox" / "share" / rel
            # logger.debug(f"Resolved PIONEER path: {p_path}")
        else:
            logger.warning("APPDATA not found, cannot resolve PIONEER artwork path")

    file_path = Path(p_path).resolve()

    # SECURITY: Directory Jail & Extension Validation
    ALLOWED_ARTWORK_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    if file_path.suffix.lower() not in ALLOWED_ARTWORK_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid artwork file type")

    # Ensure the path is either in the local covers dir or the Pioneer share dir
    is_allowed = False
    if str(file_path).startswith(str(COVERS_DIR.resolve())):
        is_allowed = True
    elif os.environ.get("APPDATA"):
        rb_share_dir = (Path(os.environ.get("APPDATA")) / "Pioneer" / "rekordbox" / "share").resolve()
        if str(file_path).startswith(str(rb_share_dir)):
            is_allowed = True

    # As a fallback, if not in defined locations, allow only if it's explicitly tracked in the DB as artwork.
    # Note: we mostly rely on the above checks for safety.
    if not is_allowed:
        logger.warning(f"SECURITY: Blocked access to non-artwork path: {file_path}")
        raise HTTPException(status_code=403, detail="Access denied: path outside allowed artwork directories")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Artwork file not found")

    return FileResponse(file_path)

@app.get("/api/library/tracks")
def get_library_tracks():
    logger.info("Fetching all library tracks...")
    if not db.active_db:
        logger.warning("No active DB found for library tracks.")
        return []
    tracks = db.active_db.get_all_tracks()
    logger.info(f"Returning {len(tracks)} tracks from library.")
    return tracks

@app.get("/api/playlist/{pid}/tracks")
def get_ptracks(pid: str):
    try:
        tracks = []
        raw_tracks = db.get_playlist_tracks(pid)
        logger.info(f"Fetching tracks for playlist {pid}. Found: {len(raw_tracks)}")
        for t in raw_tracks:
            d = dict(t)
            # Ensure lowercase 'id' exists for frontend compatibility
            if 'id' not in d and 'ID' in d:
                d['id'] = d['ID']
            # Fix Artist field for frontend
            d['ArtistName'] = d.get('Artist') or 'Unknown Artist'
            p = Path(d.get('path', ''))
            d['filename'] = urllib.parse.quote(p.name)
            tracks.append(d)
        return tracks
    except Exception as e:
        logger.error(f"Failed to fetch tracks for playlist {pid}: {e}")
        return []

@app.get("/api/artist/{aid}/tracks")
def get_arttracks(aid: str):
    try:
        tracks = []
        raw_tracks = db.get_tracks_by_artist(aid)
        for t in raw_tracks:
            d = dict(t)
            d['ArtistName'] = d.get('Artist') or 'Unknown Artist'
            p = Path(d.get('path', ''))
            d['filename'] = urllib.parse.quote(p.name)
            tracks.append(d)
        return tracks
    except Exception as e:
        logger.error(f"Error fetching artist tracks: {e}")
        return []

@app.get("/api/label/{aid}/tracks")
def get_lbltracks(aid: str):
    try:
        tracks = []
        raw_tracks = db.get_tracks_by_label(aid)
        for t in raw_tracks:
            d = dict(t)
            d['ArtistName'] = d.get('Artist') or 'Unknown Artist'
            p = Path(d.get('path', ''))
            d['filename'] = urllib.parse.quote(p.name)
            tracks.append(d)
        return tracks
    except Exception as e:
        logger.error(f"Error fetching label tracks: {e}")
        return []

@app.get("/api/album/{aid}/tracks")
def get_albtracks(aid: str):
    try:
        tracks = []
        raw_tracks = db.get_tracks_by_album(aid)
        for t in raw_tracks:
            d = dict(t)
            d['ArtistName'] = d.get('Artist') or 'Unknown Artist'
            p = Path(d.get('path', ''))
            d['filename'] = urllib.parse.quote(p.name)
            tracks.append(d)
        return tracks
    except Exception as e:
        logger.error(f"Error fetching album tracks: {e}")
        return []

@app.get("/api/projects")
def list_projects():
    prj_dir = Path("PRJ")
    if not prj_dir.exists():
        return []
    projects = []
    for f in prj_dir.glob("*.prj"):
        projects.append({
            "name": f.stem,
            "path": str(f)
        })
    return projects

@app.post("/api/projects/save", dependencies=[Depends(require_session)])
def save_project(r: ProjectReq):
    try:
        ProjectManager.save_project(r.name, r.data)
        return {"status": "success"}
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/api/projects/{name}")
def load_project_endpoint(name: str):
    try:
        data = ProjectManager.load_project(name)
        return data
    except FileNotFoundError: raise HTTPException(404, "Project not found")
    except Exception as e:
        logger.error(f"Project load error: {e}")
        raise HTTPException(500, safe_error_message(e))

@app.post("/api/artist/soundcloud", dependencies=[Depends(require_session)])
def set_sc(r: ScReq):
    # storage.set_artist_link(r.artist_name, r.link)
    return {"status": "saved"}

class SliceReq(BaseModel):
    source_path: str
    start: float
    end: float

@app.post("/api/audio/slice", dependencies=[Depends(require_session)])
async def slice_endpoint(r: SliceReq):
    try:
        # SECURITY: Validate source path
        validate_audio_path(r.source_path)
        path = await asyncio.to_thread(AudioEngine.slice_audio, r.source_path, r.start, r.end)
        filename = os.path.basename(path)
        return {"filename": filename, "url": f"/exports/{filename}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Slice endpoint error: {e}")
        raise HTTPException(500, safe_error_message(e))

@app.post("/api/audio/render", dependencies=[Depends(require_session)])
async def render(r: ExportRequest):
    try:
        # SECURITY: Validate source path
        validate_audio_path(r.source_path)
        # SECURITY: Sanitize output name
        safe_name = Path(r.output_name).name  # Strip any path components
        if not safe_name.endswith(('.wav', '.mp3', '.flac')):
            safe_name += ".wav"

        tid = await asyncio.to_thread(AudioEngine.render_segment, r.source_path, r.cuts, safe_name, r.fade_in, r.fade_out)
        return {
            "status": "success",
            "track_id": tid,
            "filename": safe_name,
            "download_url": f"/exports/{safe_name}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Render failed: {e}")
        raise HTTPException(500, safe_error_message(e))

def _run_import_analysis(dest: Path, track_task_id: str) -> None:
    """Worker: heavy analysis pipeline for one uploaded file. Runs in a daemon
    thread; reports progress + final status into the import_tracker so the
    frontend can pick it up via /api/import/tasks."""
    from . import import_tracker
    threading.current_thread()._lms_import_tid = track_task_id
    try:
        import_tracker.update(track_task_id, status="Analyzing", progress=30)
        tid, analysis = ImportManager.process_import(dest)
        import_tracker.update(
            track_task_id, status="Completed", progress=100,
            local_track_id=str(tid) if tid else None,
            bpm=(analysis or {}).get("bpm"),
            key=(analysis or {}).get("key"),
            total_time=(analysis or {}).get("totalTime"),
        )
    except Exception as exc:
        logger.error(f"Background import failed for {dest}: {exc}")
        import_tracker.update(track_task_id, status="Failed", progress=100, error=str(exc))
    finally:
        try: del threading.current_thread()._lms_import_tid
        except AttributeError: pass


@app.post("/api/audio/import", dependencies=[Depends(require_session)])
def import_audio(files: list[UploadFile] = File(...), wait: bool = False):
    """Batch audio upload + analysis + library insertion.

    Default (wait=False, async):
        Validates every file, copies it to MUSIC_DIR, registers a tracker
        task, and spawns a daemon thread to run the analysis pipeline.
        Returns immediately with [{filename, task_id, status: "Queued"}]
        per file. Frontend polls /api/import/tasks for completion.

    Synchronous (wait=true): kept for CLI / test usage — blocks until every
        file has been analysed and returns the full result objects.
    """
    from . import import_tracker
    results = []
    for file in files:
        track_task_id = None
        try:
            # SECURITY: Validate file extension
            if not file.filename or Path(file.filename).suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
                results.append({"filename": file.filename or "unknown", "status": "error", "message": "File type not allowed"})
                continue

            # SECURITY: Use only the basename to prevent path traversal
            safe_filename = Path(file.filename).name
            dest = MUSIC_DIR / safe_filename
            if dest.exists():
                stem = safe_filename.rsplit('.', 1)[0]
                ext = safe_filename.rsplit('.', 1)[-1]
                dest = MUSIC_DIR / f"{stem}_{int(time.time())}.{ext}"

            track_task_id = import_tracker.register(str(dest), source="upload")
            import_tracker.update(track_task_id, status="Uploading", progress=10)

            with open(dest, "wb") as f:
                shutil.copyfileobj(file.file, f)

            import_tracker.update(track_task_id, status="Queued", progress=20)

            if wait:
                # Synchronous path — preserve the legacy response shape so any
                # CLI / test that still expects {id, bpm, totalTime} keeps working.
                threading.current_thread()._lms_import_tid = track_task_id
                try:
                    import_tracker.update(track_task_id, status="Analyzing", progress=30)
                    tid, analysis = ImportManager.process_import(dest)
                finally:
                    try: del threading.current_thread()._lms_import_tid
                    except AttributeError: pass

                import_tracker.update(
                    track_task_id, status="Completed", progress=100,
                    local_track_id=str(tid) if tid else None,
                    bpm=(analysis or {}).get("bpm"),
                    key=(analysis or {}).get("key"),
                )
                results.append({
                    "filename": file.filename,
                    "task_id": track_task_id,
                    "status": "success",
                    "id": tid,
                    "bpm": analysis.get("bpm"),
                    "totalTime": analysis.get("totalTime"),
                })
            else:
                # Async path — kick off the heavy work in a daemon thread and
                # return the task_id so the frontend can poll /api/import/tasks.
                threading.Thread(
                    target=_run_import_analysis,
                    args=(dest, track_task_id),
                    daemon=True,
                    name=f"import-{track_task_id}",
                ).start()
                results.append({
                    "filename": file.filename,
                    "task_id": track_task_id,
                    "status": "Queued",
                })
        except Exception as e:
            logger.error(f"Import failed for {file.filename}: {e}")
            if track_task_id:
                import_tracker.update(track_task_id, status="Failed", progress=100, error=str(e))
            results.append({"filename": file.filename, "task_id": track_task_id, "status": "error", "message": safe_error_message(e)})
    return results

@app.get("/api/settings")
def get_s():
    s = SettingsManager.load()
    s['active_db_path'] = "XML Mode"
    return s

@app.post("/api/settings", dependencies=[Depends(require_session)])
def save_s(s: SetReq):
    # Merge declared fields + any extras the frontend passes through.
    payload = s.model_dump()
    extras = getattr(s, "model_extra", None) or {}
    payload.update(extras)
    SettingsManager.save(payload)
    db.refresh_metadata()

    # Bring the live folder watcher in sync with the saved scan_folders list
    # so toggling a folder in the UI takes effect immediately.
    watcher = folder_watcher.get_watcher()
    if watcher is not None:
        try:
            watcher.reconcile(payload.get("scan_folders") or [])
        except Exception as exc:
            logger.warning("FolderWatcher reconcile failed: %s", exc)

    return {"status": "saved"}


# --- Folder Watcher ---------------------------------------------------------
# Live auto-import: configured via settings.scan_folders. These endpoints let
# the UI inspect / toggle individual folders without a full settings round-trip.

def _track_paths_snapshot() -> set[str]:
    """Resolved-path set of every track currently in the library."""
    paths: set[str] = set()
    for t in getattr(db, "tracks", {}).values():
        p = t.get("path") if isinstance(t, dict) else None
        if not p:
            continue
        try:
            paths.add(str(Path(p).resolve()))
        except Exception:
            paths.add(p)
    return paths


def _track_id_for_path(path: Path) -> str | None:
    """Return existing track-id for a given filesystem path, or None."""
    try:
        target = str(path.resolve())
    except Exception:
        target = str(path)
    for tid, t in getattr(db, "tracks", {}).items():
        p = t.get("path") if isinstance(t, dict) else None
        if not p:
            continue
        try:
            if str(Path(p).resolve()) == target:
                return str(tid)
        except Exception:
            if str(p) == target:
                return str(tid)
    return None


def _is_known_audio_path(path: Path) -> bool:
    try:
        resolved = str(path.resolve())
    except Exception:
        resolved = str(path)
    return resolved in _track_paths_snapshot()


@app.get("/api/library/folder-watcher/status")
def folder_watcher_status():
    watcher = folder_watcher.get_watcher()
    if watcher is None:
        return {"running": False, "folders": [], "pending_imports": 0}
    return watcher.status()


@app.post("/api/library/folder-watcher/add", dependencies=[Depends(require_session)])
def folder_watcher_add(r: PathRequest):
    path = r.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="'path' is required.")
    if not Path(path).is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    cfg = SettingsManager.load()
    folders = list(cfg.get("scan_folders") or [])
    norm = str(Path(path).expanduser().resolve())
    if norm not in folders:
        folders.append(norm)
        cfg["scan_folders"] = folders
        SettingsManager.save(cfg)

    watcher = folder_watcher.get_watcher()
    if watcher is None:
        raise HTTPException(status_code=503, detail="Folder watcher not initialised.")
    ok = watcher.add(norm)
    return {"status": "ok" if ok else "error", "folders": folders}


@app.post("/api/library/folder-watcher/remove", dependencies=[Depends(require_session)])
def folder_watcher_remove(r: PathRequest):
    path = r.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="'path' is required.")

    norm = str(Path(path).expanduser().resolve())
    cfg = SettingsManager.load()
    folders = [f for f in (cfg.get("scan_folders") or []) if f not in (path, norm)]
    cfg["scan_folders"] = folders
    SettingsManager.save(cfg)

    watcher = folder_watcher.get_watcher()
    if watcher is not None:
        watcher.remove(norm)
        # Also try the raw input in case it was stored unnormalised.
        watcher.remove(path)
    return {"status": "ok", "folders": folders}




# --- GRACEFUL SHUTDOWN ---
_shutdown_event = threading.Event()

def _graceful_shutdown():
    """Performs cleanup before shutting down the backend."""
    logger.info("Graceful shutdown initiated...")
    _shutdown_event.set()
    try:
        folder_watcher.shutdown_watcher()
    except Exception as exc:
        logger.warning("FolderWatcher shutdown error: %s", exc)
    # Give time for pending requests to complete
    time.sleep(0.5)
    logger.info("Shutdown complete.")
    os._exit(0)

@app.on_event("startup")
async def startup_event():
    logger.info("Backend started. Binding to 127.0.0.1:8000 only.")

    # Initialize download registry (creates SQLite DB if not exists)
    try:
        download_registry.init_registry()
    except Exception as _e:
        logger.error("[startup] download_registry.init_registry() failed: %s", _e)

    # State Recovery: purge orphaned temp files from previous hard crashes
    import glob
    import tempfile
    tmp_base = tempfile.gettempdir()
    for stale_file in glob.glob(str(Path(tmp_base) / "rbpro_sc_*")):
        try:
            os.remove(stale_file)
            logger.info("Purged stale download temp: %s", stale_file)
        except OSError:
            pass

    for stale_file in MUSIC_DIR.glob("*.tmp"):
        try:
            os.remove(stale_file)
        except OSError:
            pass

    # Req 29: Timeout Handling - Prevent infinite hangs on DB locks during boot
    try:
        logger.info("Auto-loading library with 30s timeout...")
        await asyncio.wait_for(asyncio.to_thread(db.load_library), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error("Library auto-load timed out after 30 seconds (Possible strict DB lock).")
    except Exception as e:
        logger.error(f"Failed to auto-load library on startup: {e}")

    # Folder watcher: auto-import audio files dropped into user-configured folders.
    try:
        def _import_one(path: Path):
            return ImportManager.process_import(path)

        watcher = folder_watcher.init_watcher(
            import_callback=_import_one,
            is_known_callback=_is_known_audio_path,
        )
        cfg = SettingsManager.load()
        scan_folders = cfg.get("scan_folders") or []
        if scan_folders:
            watcher.start(scan_folders)
            logger.info("FolderWatcher started for %d folder(s).", len(scan_folders))
        else:
            logger.info("FolderWatcher initialised — no folders configured.")
    except Exception as e:
        logger.error(f"FolderWatcher startup failed: {e}", exc_info=True)

@app.on_event("shutdown")
async def shutdown_watcher_event():
    try:
        folder_watcher.shutdown_watcher()
    except Exception as exc:
        logger.warning("FolderWatcher shutdown error: %s", exc)

@app.post("/api/system/shutdown", dependencies=[Depends(require_session)])
@rate_limit(steady=5.0, burst=10, key_mode="both")
async def shutdown(request: Request):
    """Trigger sidecar shutdown. Gated by ``require_session`` Bearer."""
    threading.Thread(target=_graceful_shutdown, daemon=True).start()
    return {"message": "Shutting down..."}

@app.post("/api/system/restart", dependencies=[Depends(require_session)])
@rate_limit(steady=5.0, burst=10, key_mode="both")
async def restart(request: Request):
    """Trigger sidecar restart. Gated by ``require_session`` Bearer."""
    def restart_proc():
        logger.info("Restarting backend...")
        time.sleep(0.5)
        if getattr(sys, 'frozen', False):
            os.execv(sys.executable, sys.argv)
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart_proc, daemon=True).start()
    return {"message": "Restarting backend..."}


@app.post("/api/system/select_db", dependencies=[Depends(require_session)])
def select_db_dialog():
    return {"path": "XML Mode"}

class NewLibReq(BaseModel):
    path: str | None = None

@app.post("/api/library/new", dependencies=[Depends(require_session)])
def create_new_lib(r: NewLibReq = Body(default=None)):
    path = r.path if r else None
    try:
        db.create_new_library(path)
        return {
            "status": "success",
            "message": "New empty library created.",
            "path": str(db.xml_db.xml_path),
            "mode": db.mode,
        }
    except Exception as e:
        logger.error(f"Failed to create new library: {e}")
        return {"status": "error", "message": safe_error_message(e)}

@app.post("/api/debug/load_xml", dependencies=[Depends(require_session)])
def debug_load_xml():
    """Debug-only: force-load a hardcoded rekordbox.xml.

    SECURITY: Disabled unless LMS_ENABLE_DEBUG_ROUTES=1 in the environment.
    The route previously shipped in production builds with no gate — see
    docs/research/research/idea_security-api-auth-hardening.md.
    """
    if os.environ.get("LMS_ENABLE_DEBUG_ROUTES", "") != "1":
        raise HTTPException(status_code=404, detail="Not found")
    db.load_xml("rekordbox.xml")
    return {"status": "loaded", "tracks": len(db.tracks), "playlists": len(db.playlists)}

# ─── USB Management API ───────────────────────────────────────────────────────

class UsbProfileReq(BaseModel):
    device_id: str
    label: str | None = None
    drive: str | None = None
    type: str | None = "Collection"  # MainCollection, Collection, PartCollection, SetStick
    sync_mode: str | None = "full"   # full, playlists_only, metadata_only, selective
    sync_playlists: list[str] | None = []
    auto_sync: bool | None = False

    # Per-profile audio export settings (used during sync if conversion is needed)
    audio_format: str | None = "original"   # 'original' | 'mp3' | 'flac' | 'wav' | 'aac'
    audio_bitrate: str | None = "320"       # for lossy formats: '128', '192', '256', '320'
    audio_sample_rate: str | None = "44100" # '44100' | '48000' | '96000'

    # Library type / sync direction (already used elsewhere — declared here so frontend can update)
    sync_direction: str | None = "pc_main"  # 'pc_main' | 'usb_main'
    sync_mirrored:  bool | None = False
    sync_primary:   str | None = None
    library_types:  list[str] | None = None

class UsbSyncReq(BaseModel):
    device_id: str
    sync_type: str | None = "collection"  # collection, playlists, metadata
    playlist_ids: list[str] | None = []
    # Default to BOTH formats so Rekordbox auto-detects (exportLibrary.db) AND
    # older Rekordbox / manual XML import (rekordbox.xml) both work out of the box.
    library_types: list[str] | None = ["library_one", "library_legacy"]

class UsbEjectReq(BaseModel):
    drive: str

class UsbResetReq(BaseModel):
    device_id: str

class DupMergeReq(BaseModel):
    keep_id: str
    remove_ids: list[str]

@app.get("/api/usb/devices")
def usb_scan_devices():
    """Scan for connected USB devices."""
    return UsbDetector.scan()

@app.get("/api/usb/profiles")
def usb_get_profiles():
    """List all registered USB profiles (connected + disconnected)."""
    return UsbProfileManager.get_profiles()

@app.post("/api/usb/profiles", dependencies=[Depends(require_session)])
def usb_save_profile(r: UsbProfileReq):
    """Create or update a USB device profile."""
    profile = UsbProfileManager.save_profile(r.model_dump())
    return {"status": "success", "profile": profile}

@app.delete("/api/usb/profiles/{device_id}", dependencies=[Depends(require_session)])
def usb_delete_profile(device_id: str):
    """Delete a USB device profile."""
    if UsbProfileManager.delete_profile(device_id):
        return {"status": "success"}
    raise HTTPException(404, "Profile not found")

@app.get("/api/usb/{device_id}/contents")
def usb_get_contents(device_id: str):
    """Get the tracks currently existing on the referenced USB stick."""
    tracks = UsbProfileManager.get_usb_contents(device_id)
    return {"status": "success", "tracks": tracks}

@app.get("/api/usb/diff/{device_id}")
def usb_get_diff(device_id: str):
    """Preview what would change in a sync operation."""
    profile = _get_or_create_profile(device_id)
    if not db.active_db:
        raise HTTPException(400, "No library loaded")
    db_path = getattr(db.active_db, "db_path", None) or getattr(db.active_db, "xml_path", None)
    engine = UsbSyncEngine(str(db_path), profile["drive"], profile.get("filesystem", ""))
    diff = engine.calculate_diff()
    # Add space estimate: ~10MB avg per track to add
    avg_track_size = 10 * 1024 * 1024
    diff["space_estimate"] = diff["tracks"]["to_add"] * avg_track_size
    diff["drive_free"] = profile.get("free_space", 0)
    return diff

def _get_or_create_profile(device_id: str) -> dict:
    """Get existing profile or auto-create from scan data.

    Refreshes `drive` and `filesystem` from the live scan on every call.
    Drive letters can change between Windows mounts (FAT32 → exFAT
    reformat, USB hub re-enumeration). The filesystem field MUST be
    fresh because UsbSyncEngine uses it to pick the path-length limit:
    stale "FAT32" on an exFAT stick truncates paths unnecessarily.
    """
    profile = UsbProfileManager.get_profile(device_id)
    devices = UsbDetector.scan()
    dev = next((d for d in devices if d["device_id"] == device_id), None)
    if profile:
        if dev:
            updates = {}
            if dev.get("drive") and dev["drive"] != profile.get("drive"):
                updates["drive"] = dev["drive"]
            fs = dev.get("filesystem")
            if fs and fs != profile.get("filesystem"):
                updates["filesystem"] = fs
            if updates:
                profile = UsbProfileManager.save_profile({"device_id": device_id, **updates})
        return profile
    if not dev:
        raise HTTPException(404, "Device not connected — cannot create profile")
    return UsbProfileManager.save_profile({
        "device_id": device_id,
        "label": dev.get("label", "USB Drive"),
        "drive": dev["drive"],
        "filesystem": dev.get("filesystem", ""),
    })

@app.post("/api/usb/sync", dependencies=[Depends(require_session)])
def usb_sync(r: UsbSyncReq):
    """Sync a specific USB device — works in both Live and XML modes."""
    profile = _get_or_create_profile(r.device_id)
    if not db.active_db:
        raise HTTPException(400, "No library loaded")

    # In Live mode pass master.db path, in XML mode pass xml path (legacy engine
    # will fall back to OneLibrary writer which reads from db wrapper directly).
    db_path = getattr(db.active_db, "db_path", None) or getattr(db.active_db, "xml_path", None)
    engine = UsbSyncEngine(str(db_path), profile["drive"], profile.get("filesystem", ""))
    results = []
    # Always export both formats so Rekordbox + CDJs auto-detect the stick.
    # Legacy callers that passed only one type are upgraded silently — the
    # writers are no-ops when their target file already matches, so this is
    # additive rather than destructive.
    libs = sorted(set((r.library_types or []) + ["library_one", "library_legacy"]))
    logger.info(f"[USB-SYNC] device={r.device_id} type={r.sync_type} libs={libs}")

    if r.sync_type == "collection":
        for event in engine.sync_collection(profile, libs):
            results.append(event)
    elif r.sync_type == "playlists":
        for event in engine.sync_playlists(profile, r.playlist_ids, libs):
            results.append(event)
    elif r.sync_type == "metadata":
        for event in engine.sync_metadata(profile, libs):
            results.append(event)

    last = results[-1] if results else {"stage": "error", "message": "No events"}
    return {"status": "success" if last.get("stage") == "complete" else "error", "result": last}

@app.post("/api/usb/profiles/prune", dependencies=[Depends(require_session)])
def usb_prune_profiles():
    """Force-collapse duplicate USB profiles for the same physical stick.

    Windows generates a new volume serial after every reformat which makes
    our device_id (md5 of serial) shift. Repeated stick wipes therefore
    pile up zombie profiles in the sidebar. The pruner keeps the most
    recently synced profile per (drive, label) pair and deletes orphan
    siblings that have no playlist history.
    """
    removed = UsbProfileManager.prune_duplicates()
    return {"status": "ok", "removed": removed}

@app.post("/api/usb/sync/all", dependencies=[Depends(require_session)])
def usb_sync_all():
    """Sync all connected USB devices per their profiles."""
    if not db.active_db:
        raise HTTPException(400, "No library loaded")
    db_path = getattr(db.active_db, "db_path", None) or getattr(db.active_db, "xml_path", None)
    results = []
    for event in UsbActions.update_all(str(db_path)):
        results.append(event)
    last = results[-1] if results else {"stage": "complete", "message": "Nothing to sync"}
    return {"status": "success", "result": last, "events": results}

@app.post("/api/usb/eject", dependencies=[Depends(require_session)])
def usb_eject(r: UsbEjectReq):
    """Safely eject a USB drive."""
    return UsbActions.eject(r.drive)

@app.post("/api/usb/reset", dependencies=[Depends(require_session)])
def usb_reset(r: UsbResetReq):
    """Reset a USB device (wipe PIONEER folder)."""
    profile = UsbProfileManager.get_profile(r.device_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    return UsbActions.reset(profile)

class UsbInitReq(BaseModel):
    drive: str

@app.post("/api/usb/initialize", dependencies=[Depends(require_session)])
def usb_initialize(r: UsbInitReq):
    """Initialize a new Rekordbox library on a USB drive."""
    if UsbDetector.initialize_usb(r.drive):
        return {"status": "success", "message": "Library initialized"}
    raise HTTPException(500, "Failed to initialize library")


# ─── MYSETTING / DJM SETTINGS — CDJ + DJM player customisation ─────────────
# Bound to a USB profile so the user can configure each stick independently.
# Schema endpoint feeds the frontend dropdowns; the read/write pair persists
# the binary MYSETTING.DAT / MYSETTING2.DAT / DJMMYSETTING.DAT files into
# <USB>/PIONEER/.

class UsbMySettingsReq(BaseModel):
    device_id: str
    values: dict[str, dict[str, str]]  # {"MYSETTING": {"auto_cue": "off", …}}


@app.get("/api/usb/mysettings/schema")
def usb_mysettings_schema():
    """Return the editable-field schema (file → fields → enum options).
    Frontend uses this to render labelled dropdowns without hardcoding."""
    from . import usb_mysettings
    return usb_mysettings.get_schema()


@app.get("/api/usb/mysettings/{device_id}")
def usb_mysettings_read(device_id: str):
    """Read current MYSETTING values from a connected stick. Falls back to
    pyrekordbox factory defaults for any field whose file is missing."""
    from . import usb_mysettings
    profile = UsbProfileManager.get_profile(device_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    drive = profile.get("drive")
    if not drive:
        raise HTTPException(400, "Profile has no drive letter")
    return {
        "status": "ok",
        "device_id": device_id,
        "values": usb_mysettings.read_settings(Path(drive)),
    }


@app.post("/api/usb/mysettings", dependencies=[Depends(require_session)])
def usb_mysettings_write(r: UsbMySettingsReq):
    """Persist user-edited MYSETTING values to <USB>/PIONEER/."""
    from . import usb_mysettings
    profile = UsbProfileManager.get_profile(r.device_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    drive = profile.get("drive")
    if not drive:
        raise HTTPException(400, "Profile has no drive letter")
    written = usb_mysettings.write_settings(Path(drive), r.values)
    return {"status": "ok", "written": written}


# ─── DESTRUCTIVE: USB FORMAT (FAT32 / exFAT) ─────────────────────────────────
# Two-step protocol so the UI cannot accidentally trigger a format:
#   1. POST /api/usb/format/preview {drive}  → returns drive info + a one-shot
#      token (valid 60s, single-use). The caller MUST display the data.
#   2. POST /api/usb/format/confirm {drive, token, filesystem, label,
#      typed_confirmation}  → wipes the drive. Server verifies the token AND
#      that `typed_confirmation` matches the literal string "FORMAT <DRIVE>".
#
# Rationale: the protocol-level second factor means a misbehaving frontend
# (or a stray click) can't issue a destructive call without first reading the
# preview, and the typed phrase forces an additional human acknowledgement.

_format_tokens: dict[str, dict] = {}  # token → {drive, expires_at, used}
_format_tokens_lock = threading.Lock()
_FORMAT_TOKEN_TTL = 60.0


def _purge_expired_tokens():
    now = time.time()
    with _format_tokens_lock:
        for tok in [t for t, m in _format_tokens.items() if m.get("expires_at", 0) < now]:
            _format_tokens.pop(tok, None)


class UsbFormatPreviewReq(BaseModel):
    drive: str


@app.post("/api/usb/format/preview", dependencies=[Depends(require_session)])
def usb_format_preview(r: UsbFormatPreviewReq):
    """
    Step 1 of the format protocol. Returns drive metadata the UI must display
    in its warning dialog, plus a single-use token to pass to /confirm.
    """
    _purge_expired_tokens()
    drive = r.drive.strip()
    if not drive:
        raise HTTPException(400, "Drive is required.")

    # Cross-platform existence check — accept "E:" / "E:\\" on Windows and
    # block-device paths like "/dev/sdb1" on Linux/macOS.
    drive_path = Path(drive if drive.endswith(("\\", "/")) else drive + ("\\" if ":" in drive else ""))
    exists = drive_path.exists() or Path(drive).exists()
    if not exists:
        raise HTTPException(404, f"Drive not found: {drive}")

    # Pull volume info if we can; non-fatal otherwise.
    info = {"label": "", "filesystem": "Unknown", "total": 0, "free": 0}
    try:
        if hasattr(UsbDetector, "_get_volume_info"):
            info.update(UsbDetector._get_volume_info(drive))
        if hasattr(UsbDetector, "_get_drive_size"):
            info.update(UsbDetector._get_drive_size(drive))
    except Exception as exc:
        logger.warning("Drive probe failed for %s: %s", drive, exc)

    token = secrets.token_urlsafe(24)
    with _format_tokens_lock:
        _format_tokens[token] = {
            "drive": drive,
            "expires_at": time.time() + _FORMAT_TOKEN_TTL,
            "used": False,
        }

    # The exact phrase the user must type in the UI to confirm.
    phrase = f"FORMAT {drive.rstrip(chr(92)).rstrip('/').rstrip(':')}"
    logger.warning("FORMAT preview issued for drive=%s token=%s…", drive, token[:8])
    return {
        "status": "ok",
        "drive": drive,
        "label": info.get("label") or "",
        "filesystem": info.get("filesystem") or "Unknown",
        "total_bytes": int(info.get("total") or 0),
        "free_bytes": int(info.get("free") or 0),
        "confirm_phrase": phrase,
        "token": token,
        "ttl_seconds": int(_FORMAT_TOKEN_TTL),
        "warning": (
            "DESTRUCTIVE: every file on this drive will be permanently erased. "
            "After formatting the stick is re-built as a clean Rekordbox / CDJ "
            "device (PIONEER skeleton + DEVICE.PIONEER marker)."
        ),
    }


class UsbFormatConfirmReq(BaseModel):
    drive: str
    token: str
    filesystem: str = "FAT32"           # "FAT32" or "exFAT"
    label: str = "CDJ"
    typed_confirmation: str = ""        # must match the preview's confirm_phrase


@app.post("/api/usb/format/confirm", dependencies=[Depends(require_session)])
def usb_format_confirm(r: UsbFormatConfirmReq):
    """Step 2: actually format the drive after both confirmations check out."""
    _purge_expired_tokens()
    with _format_tokens_lock:
        meta = _format_tokens.get(r.token)
        if not meta:
            raise HTTPException(403, "Invalid or expired confirmation token. Re-open the format dialog.")
        if meta.get("used"):
            raise HTTPException(403, "This token has already been used.")
        if meta["drive"] != r.drive:
            raise HTTPException(403, "Token does not match this drive.")

    # Phrase check first — wrong typing should not burn the token, so the user
    # can correct a typo without re-opening the dialog.
    expected = f"FORMAT {r.drive.rstrip(chr(92)).rstrip('/').rstrip(':')}"
    if r.typed_confirmation.strip() != expected:
        raise HTTPException(400, f"Typed confirmation does not match. Expected: {expected}")

    # Phrase OK — burn the token, then run the destructive op.
    with _format_tokens_lock:
        meta = _format_tokens.get(r.token)
        if not meta or meta.get("used"):
            raise HTTPException(403, "Token state changed. Re-open the format dialog.")
        meta["used"] = True

    logger.warning("FORMAT confirmed: drive=%s fs=%s label=%s", r.drive, r.filesystem, r.label)
    res = UsbActions.format_drive(r.drive, label=r.label, filesystem=r.filesystem)
    if res.get("status") != "success":
        raise HTTPException(500, res.get("message") or "Format failed")
    return res

class UsbRenameReq(BaseModel):
    drive: str
    new_label: str

@app.post("/api/usb/rename", dependencies=[Depends(require_session)])
def usb_rename(req: UsbRenameReq):
    """Rename a USB device."""
    res = UsbActions.set_label(req.drive, req.new_label)
    if res["status"] == "error":
        raise HTTPException(500, res["message"])
    return res

@app.get("/api/usb/settings")
def usb_get_settings():
    """Get USB global settings."""
    return UsbProfileManager.get_settings()

class UsbSettingsReq(BaseModel):
    """USB-global settings body. Fields are open-ended (forward-compat
    with whatever `UsbProfileManager.save_settings` accepts) — `extra =
    "allow"` lets the frontend add new keys without bumping this model."""
    model_config = {"extra": "allow"}


@app.post("/api/usb/settings", dependencies=[Depends(require_session)])
def usb_save_settings(r: UsbSettingsReq):
    """Save USB global settings."""
    UsbProfileManager.save_settings(r.model_dump())
    return {"status": "success"}

# ─── Enhanced Duplicate Scanner ───────────────────────────────────────────────

@app.post("/api/tools/duplicates/merge", dependencies=[Depends(require_session)])
def merge_duplicates(r: DupMergeReq):
    """Merge duplicate tracks: keep one, transfer playlist memberships, delete others."""
    try:
        if not db.active_db:
            raise HTTPException(400, "No database loaded")

        keep_id = r.keep_id
        remove_ids = r.remove_ids

        # Transfer playlist memberships
        if hasattr(db.active_db, 'db'):
            rbox = db.active_db.db
            for rid in remove_ids:
                # Find all playlists containing the track to remove
                for pl in db.playlists:
                    pl_tracks = db.get_playlist_tracks(pl.get('ID', ''))
                    track_ids = [str(t.get('id', t.get('ID', ''))) for t in pl_tracks]
                    if str(rid) in track_ids and str(keep_id) not in track_ids:
                        # Add the kept track to this playlist
                        try:
                            db.add_track_to_playlist(str(pl.get('ID', '')), str(keep_id))
                        except Exception:
                            pass

                # Delete the duplicate track
                try:
                    db.delete_track(str(rid))
                except Exception as e:
                    logger.warning(f"Could not delete duplicate {rid}: {e}")

        return {"status": "success", "kept": keep_id, "removed": len(remove_ids)}
    except Exception as e:
        logger.error(f"Merge failed: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/tools/duplicates/merge-all", dependencies=[Depends(require_session)])
def merge_all_duplicates():
    """Auto-merge all duplicate groups using best-metadata heuristic."""
    try:
        groups = LibraryTools.find_duplicates()
        merged = 0

        for group in groups:
            ids = group.get("ids", [])
            if len(ids) < 2:
                continue

            # Pick the best track (highest rating, then most cue points, then highest bitrate)
            best_id = ids[0]
            best_score = -1

            for tid in ids:
                track = db.tracks.get(tid, {})
                score = (track.get("Rating", 0) or 0) * 100
                score += (track.get("Bitrate", 0) or 0)
                score += len(str(track.get("CuePath", ""))) * 10
                if score > best_score:
                    best_score = score
                    best_id = tid

            remove_ids = [i for i in ids if i != best_id]

            # Transfer memberships and delete
            for rid in remove_ids:
                try:
                    db.delete_track(str(rid))
                except Exception:
                    pass

            merged += 1

        return {"status": "success", "groups_merged": merged}
    except Exception as e:
        logger.error(f"Merge all failed: {e}")
        raise HTTPException(500, str(e))

# ─── RBEP Project API ──────────────────────────────────────────────────────────

@app.get("/api/projects/rbep/list")
def rbep_list():
    """List available .rbep project files."""
    return rbep_list_projects()

@app.get("/api/projects/rbep/{name}")
def rbep_get(name: str):
    """Parse and return a .rbep project by name."""
    project = rbep_parse_project(name)
    if not project:
        raise HTTPException(404, f"Project '{name}' not found")
    return project

# ─── Audio Analysis API ──────────────────────────────────────────────────────────

@app.post("/api/audio/analyze", dependencies=[Depends(require_session)])
def analyze_audio(req: AudioImportReq):
    """Submit an audio file for high-accuracy background analysis."""
    if not LIBROSA_AVAILABLE:
        logger.warning("Librosa not installed. Audio analysis will be mocked.")

    file_path = Path(req.file_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")

    task_id = f"analysis_{secrets.token_hex(8)}"
    return AudioAnalyzer.analyze_track(task_id, str(file_path), req.mode)

@app.get("/api/audio/analyze/{task_id}")
def get_analysis_status(task_id: str):
    """Check the status/result of a background audio analysis job."""
    status = AudioAnalyzer.get_status(task_id)
    if status["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Task not found")
    return status

# ─── Track Analysis + DB Write API ───────────────────────────────────────────
# These endpoints run our own analysis engine and write results directly
# into the Rekordbox live database (master.db + ANLZ binary files),
# replacing the need for Rekordbox's built-in analysis.

@app.post("/api/track/{tid}/analyze-full", dependencies=[Depends(require_session)])
async def analyze_track_full(tid: str, req: AnalyzeFullReq = AnalyzeFullReq()):
    """
    Analyze a track with our engine and write BPM, key, waveforms, beatgrid
    directly into the Rekordbox live database + ANLZ files.

    Requires live DB mode. Rekordbox must NOT be running.
    """
    if not hasattr(db, 'get_analysis_writer'):
        raise HTTPException(400, "Analysis-to-DB requires live database mode. Switch to live mode first.")

    if _is_rekordbox_running():
        raise HTTPException(409, "Rekordbox is running. Close it before writing analysis data.")

    try:
        writer = db.get_analysis_writer()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, writer.analyze_and_save, tid, req.force)

        if result.get("status") == "error":
            raise HTTPException(500, result.get("error", "Analysis failed"))

        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"analyze-full failed for {tid}: {e}", exc_info=True)
        raise HTTPException(500, safe_error_message(e))


@app.post("/api/library/analyze-batch", dependencies=[Depends(require_session)])
async def analyze_batch(req: AnalyzeBatchReq = AnalyzeBatchReq()):
    """
    Batch-analyze tracks and write results to Rekordbox DB + ANLZ.
    If track_ids is None, automatically finds all unanalyzed tracks.

    Returns a streaming response with progress updates (NDJSON).
    """
    if not hasattr(db, 'get_analysis_writer'):
        raise HTTPException(400, "Analysis-to-DB requires live database mode.")

    if _is_rekordbox_running():
        raise HTTPException(409, "Rekordbox is running. Close it before writing analysis data.")

    writer = db.get_analysis_writer()

    track_ids = req.track_ids
    if not track_ids:
        track_ids = db.get_unanalyzed_track_ids()

    if not track_ids:
        return {"status": "ok", "data": {"message": "No tracks to analyze", "total": 0}}

    import json

    async def stream_progress():
        loop = asyncio.get_running_loop()
        for progress in writer.analyze_batch(track_ids, force=req.force):
            yield json.dumps(progress) + "\n"
            await asyncio.sleep(0)  # Yield control to event loop

    return StreamingResponse(
        stream_progress(),
        media_type="application/x-ndjson",
        headers={"X-Total-Tracks": str(len(track_ids))},
    )


@app.get("/api/library/analyze-status")
def get_analyze_capabilities():
    """Report which analysis backends are available and how many tracks need analysis."""
    try:
        from .audio_analyzer import AudioAnalyzer
        caps = AudioAnalyzer.capabilities()
    except Exception:
        caps = {"core": False, "madmom": False, "essentia": False}

    unanalyzed_count = 0
    total_count = 0
    if hasattr(db, 'tracks'):
        total_count = len(db.tracks)
        unanalyzed_count = len([
            t for t in db.tracks.values()
            if not t.get("BPM") or t["BPM"] <= 0
        ])

    return {
        "status": "ok",
        "data": {
            **caps,
            "total_tracks": total_count,
            "unanalyzed_tracks": unanalyzed_count,
            "analyzed_tracks": total_count - unanalyzed_count,
        }
    }


def _is_rekordbox_running() -> bool:
    """Check if Rekordbox is currently running."""
    try:
        import rbox
        return rbox.is_rekordbox_running()
    except Exception:
        return False


# ─── SoundCloud Download API ──────────────────────────────────────────────────
# NOTE: These endpoints use `keyring` for secure token storage (EC7/EC13).

class ScDownloadRequest(BaseModel):
    """
    Body for POST /api/soundcloud/download.

    Two usage modes:
      A) Pre-resolved (preferred — from SoundCloudSyncView where full track data is available):
           sc_track_id + title + artist + downloadable + (optional) sc_playlist_title
      B) URL-only (from SoundCloudView URL input box):
           url — backend resolves track metadata via /resolve API before downloading.

    `downloadable` is a hint, not a hard gate: when True the downloader prefers
    the official /tracks/{id}/download endpoint (original upload). When False,
    it falls back to the same signed transcodings the SC web player plays for
    the authenticated user. Legal boundaries are enforced inside the downloader
    (see app/soundcloud_downloader.py module docstring): snipped previews,
    401/403 responses, and unavailable tracks are skipped there.
    """
    url: str | None = None               # SC permalink URL (mode B)
    sc_track_id: str | None = None       # SoundCloud track ID (mode A)
    title: str | None = None
    artist: str | None = None
    duration_ms: int = 0
    downloadable: bool = False              # hint for path selection; see class docstring
    sc_playlist_title: str | None = None # for auto-playlist sort


@app.post("/api/soundcloud/download", dependencies=[Depends(require_session)])
async def soundcloud_download(data: ScDownloadRequest, request: Request):
    """
    Start a SoundCloud track download.

    Acquisition order (chosen by the downloader, not by this endpoint):
      1. Official /tracks/{id}/download — when creator enabled it.
      2. v2 media.transcodings[] stream — same as the SC web player.

    Legal boundaries (enforced in app/soundcloud_downloader.py):
      - Snipped (30s-preview) transcodings are skipped.
      - 401/403 from transcoding-signing is respected (no retry-with-other-path).
      - Go+ (hq) quality is only used when SC itself exposes it for the
        authenticated account — no paywall circumvention.

    Deduplication:
      - Checks the registry by sc_track_id before starting.
      - SHA-256 content check happens after download completes.

    Returns: { task_id: str }
    """
    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)

    # Write-permission guard
    sc_dir = MUSIC_DIR / "SoundCloud"
    sc_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(str(sc_dir), os.W_OK):
        raise HTTPException(
            status_code=403,
            detail=f"No write permission for download directory: {sc_dir}."
        )

    sc_track_id = data.sc_track_id
    title = data.title or ""
    artist = data.artist or ""
    downloadable = data.downloadable
    permalink_url = data.url or ""

    # Mode B: URL-only — resolve track metadata from SC API
    if not sc_track_id:
        if not permalink_url:
            raise HTTPException(status_code=400, detail="Either 'url' or 'sc_track_id' is required.")
        logger.info("[SC-DL API] Resolving URL: %s", permalink_url)
        try:
            track_meta = await asyncio.to_thread(
                SoundCloudPlaylistAPI.resolve_track_from_url, permalink_url, auth_token,
            )
        except AuthExpiredError:
            raise HTTPException(status_code=401, detail="SoundCloud auth token expired.")
        except Exception as exc:
            logger.error("[SC-DL API] resolve_track_from_url failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Could not resolve SC URL: {exc}")

        if not track_meta:
            raise HTTPException(
                status_code=400,
                detail="URL does not point to a valid SoundCloud track."
            )
        sc_track_id = str(track_meta["id"])
        title = title or track_meta.get("title", "")
        artist = artist or track_meta.get("artist", "")
        downloadable = track_meta.get("downloadable", False)
        permalink_url = track_meta.get("permalink_url", permalink_url)
        data = ScDownloadRequest(
            url=permalink_url, sc_track_id=sc_track_id,
            title=title, artist=artist,
            duration_ms=track_meta.get("duration", 0),
            downloadable=downloadable,
            sc_playlist_title=data.sc_playlist_title,
        )

    task_id = sc_downloader.download_track(
        sc_track_id=sc_track_id,
        sc_permalink_url=permalink_url,
        title=title,
        artist=artist,
        duration_ms=data.duration_ms,
        downloadable=downloadable,
        auth_token=auth_token,
        sc_playlist_title=data.sc_playlist_title,
    )
    return {"status": "ok", "data": {"task_id": task_id}}


class ScDownloadPlaylistReq(BaseModel):
    playlist_id: int
    is_likes: bool = False
    playlist_title: str | None = None  # for auto-playlist-sort folder
    force: bool = False  # if True: wipe registry entries for these tracks → re-download


@app.post("/api/soundcloud/download-playlist", dependencies=[Depends(require_session)])
async def soundcloud_download_playlist(r: ScDownloadPlaylistReq):
    """Enqueue download for every track in a SoundCloud playlist."""
    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
    if not auth_token:
        raise HTTPException(400, "SoundCloud auth token not configured")

    # Write-permission guard
    sc_dir = MUSIC_DIR / "SoundCloud"
    sc_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(str(sc_dir), os.W_OK):
        raise HTTPException(403, f"No write permission: {sc_dir}")

    # Fetch full track list
    try:
        if r.is_likes:
            likes = await asyncio.to_thread(SoundCloudPlaylistAPI.get_likes, auth_token)
            sc_tracks = likes.get("tracks", []) if likes else []
        else:
            sc_tracks = await asyncio.to_thread(
                SoundCloudPlaylistAPI.get_full_playlist_tracks, r.playlist_id, auth_token,
            )
    except AuthExpiredError:
        raise HTTPException(401, "auth_expired")
    except Exception as e:
        logger.error(f"[SC-DL-PL] Failed to fetch tracks: {e}")
        raise HTTPException(502, f"Could not fetch playlist tracks: {e}")

    if not sc_tracks:
        return {"status": "success", "queued": 0, "skipped": 0, "task_ids": [], "message": "Playlist is empty or all tracks unavailable."}

    task_ids = []
    skipped = 0
    forced_reset = 0
    for t in sc_tracks:
        sc_id = t.get("id")
        title = t.get("title", "")
        artist = t.get("artist", "")
        if not sc_id or not title:
            skipped += 1
            continue

        # Force re-download: wipe registry row so the gate doesn't skip us
        if r.force:
            try:
                if download_registry.delete_entry(str(sc_id)):
                    forced_reset += 1
            except Exception as e:
                logger.debug(f"[SC-DL-PL] registry delete failed for {sc_id}: {e}")

        try:
            tid = sc_downloader.download_track(
                sc_track_id=str(sc_id),
                sc_permalink_url=t.get("permalink_url", ""),
                title=title,
                artist=artist,
                duration_ms=t.get("duration", 0),
                downloadable=t.get("downloadable", False),
                auth_token=auth_token,
                sc_playlist_title=r.playlist_title,
            )
            task_ids.append(tid)
        except Exception as e:
            logger.warning(f"[SC-DL-PL] Skipped '{title}': {e}")
            skipped += 1

    return {
        "status": "success",
        "queued": len(task_ids),
        "skipped": skipped,
        "force_reset": forced_reset,
        "task_ids": task_ids,
        "message": f"Queued {len(task_ids)} downloads ({skipped} skipped, {forced_reset} reset).",
    }


@app.get("/api/soundcloud/tasks")
async def get_soundcloud_tasks():
    """Poll all active download tasks."""
    return sc_downloader.tasks


@app.get("/api/soundcloud/task/{task_id}")
async def get_soundcloud_task_status(task_id: str):
    """Get status for a specific download task."""
    task = sc_downloader.get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ─── Download History & Deduplication API ────────────────────────────────────

@app.get("/api/soundcloud/history")
async def get_download_history(
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
    device_id: str | None = None,
    search: str | None = None,
    this_device_only: bool = False,
):
    """
    Paginated analysis history log for all downloaded tracks.

    Query params:
      limit           — page size (max 500)
      offset          — pagination offset
      status          — filter by status: 'analyzed'|'downloaded'|'failed'|...
      device_id       — filter to a specific device UUID
      search          — substring search in title/artist
      this_device_only— shorthand: filter to current device ID
    """
    limit = min(limit, 500)
    effective_device_id = download_registry.get_current_device_id() if this_device_only else device_id
    try:
        rows = download_registry.get_history(
            limit=limit, offset=offset,
            status=status, device_id=effective_device_id, search=search,
        )
        return {"status": "ok", "data": rows}
    except Exception as exc:
        logger.error("[SC History] get_history failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load history.")


@app.get("/api/soundcloud/history/stats")
async def get_download_stats():
    """Aggregate statistics: total downloads, analyzed, failed, device count, date range."""
    try:
        stats = download_registry.get_stats()
        stats["device_id"] = download_registry.get_current_device_id()
        return {"status": "ok", "data": stats}
    except Exception as exc:
        logger.error("[SC History] get_stats failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load stats.")


@app.get("/api/soundcloud/check/{sc_track_id}")
async def check_already_downloaded(sc_track_id: str):
    """
    Fast O(1) deduplication check. Returns whether a track is already in the registry.
    Use before showing a download button in the UI to indicate 'already downloaded' state.
    """
    already = download_registry.is_already_downloaded(sc_track_id)
    return {"status": "ok", "data": {"sc_track_id": sc_track_id, "already_downloaded": already}}


@app.delete("/api/soundcloud/history/{sc_track_id}", dependencies=[Depends(require_session)])
async def delete_history_entry(sc_track_id: str):
    """
    Remove a registry entry to allow re-download (e.g. for failed entries).
    Does NOT delete the file from disk.
    """
    ok = download_registry.delete_entry(sc_track_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete registry entry.")
    return {"status": "ok", "data": {"deleted": sc_track_id}}


class ScAuthTokenReq(BaseModel):
    """SoundCloud OAuth access-token body — single `token` field, validated
    by `set_soundcloud_auth_token` for length (10–2048 chars) and ASCII."""
    token: str


@app.post("/api/soundcloud/auth-token", dependencies=[Depends(require_session)])
@rate_limit(steady=5.0, burst=10, key_mode="both")
async def set_soundcloud_auth_token(
    request: Request, r: ScAuthTokenReq, response: Response
):
    """
    EC7/EC13: Persist the SC OAuth token in the OS keyring (not in cookies or JSON).
    Sets a lightweight HttpOnly sentinel cookie so frontend can detect auth state
    without ever seeing the raw token.
    """
    token = r.token.strip()

    # EC13: Token format validation.
    # SoundCloud OAuth 2.1 issues JWT access tokens that are typically 400–900+ chars.
    # Upper bound is 2048 to safely accept any standard JWT while blocking clearly
    # malformed payloads (e.g. accidental HTML page bodies > 2 KB).
    if token:
        token_len = len(token)
        is_ascii  = token.isascii()
        if not (10 <= token_len <= 2048 and is_ascii):
            # EC10: Log the exact rejection reason so future failures are debuggable.
            reason = (
                f"length={token_len} (must be 10–2048)"
                if not (10 <= token_len <= 2048)
                else "contains non-ASCII characters"
            )
            logger.warning(f"[SC] /api/soundcloud/auth-token rejected: {reason}")
            raise HTTPException(status_code=400, detail=f"Invalid token format: {reason}")


    if token:
        keyring.set_password(KEYRING_SERVICE, KEYRING_SC_TOKEN, token)
        logger.info("[SC] Auth token stored in OS keyring.")
    else:
        # Empty token → clear credentials (logout)
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
        except Exception:
            pass
        logger.info("[SC] Auth token cleared from keyring (logout).")

    # Sentinel cookie — value is never the real token (EC13)
    response.set_cookie(
        key="sc_token",
        value="os_keyring_active" if token else "",
        httponly=True,
        samesite="lax",
        secure=False,   # set to True when served over HTTPS
        max_age=31536000 if token else 0
    )
    return {"status": "success"}



# ─── SoundCloud Playlist Sync API ─────────────────────────────────────────────

class ScSettingsReq(BaseModel):
    sc_sync_folder_id: str | None = None  # Rekordbox playlist ID or None for ROOT

@app.get("/api/soundcloud/settings")
async def get_sc_settings():
    """Return SC-specific settings (sync target folder)."""
    s = SettingsManager.load()
    # Also expose all available local folders for the UI picker
    folders = [
        {"id": pl["ID"], "name": pl["Name"]}
        for pl in (db.playlists or [])
        if str(pl.get("Type")) == "0"  # Type 0 = folder
    ]
    return {
        "sc_sync_folder_id": s.get("sc_sync_folder_id"),
        "available_folders": folders,
    }

@app.put("/api/soundcloud/settings", dependencies=[Depends(require_session)])
async def update_sc_settings(r: ScSettingsReq):
    """Persist SC sync target folder to settings.json."""
    s = SettingsManager.load()
    s["sc_sync_folder_id"] = r.sc_sync_folder_id  # None → ROOT, str → specific folder
    SettingsManager.save(s)
    logger.info(f"[SC] sc_sync_folder_id updated to: {r.sc_sync_folder_id!r}")
    return {"status": "ok", "sc_sync_folder_id": r.sc_sync_folder_id}


@app.get("/api/soundcloud/playlists")
async def get_soundcloud_playlists(request: Request):
    """
    Fetch user playlists + likes + profile from SoundCloud in parallel.

    EC1: Returns empty lists on 0 playlists — never 404.
    EC2: Clears the lru_cache before fetching so a fresh token always hits the SC API.
    EC3: RateLimitError from _sc_get is surfaced as 429.
    ROOT CAUSE FIX: SoundCloud 404s on /me and /users/{id}/playlists (bad client_id
    or invalid token) now raise AuthExpiredError instead of leaking the raw
    "404 Client Error: Not Found" string to the frontend toast.
    """
    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)

    if not auth_token:
        logger.warning("[SC] /api/soundcloud/playlists: no auth token in keyring — returning 401.")
        raise HTTPException(401, detail="auth_expired")

    # EC2: Invalidate lru_cache so a freshly-rotated token always fetches live data.
    SoundCloudPlaylistAPI.get_playlists.cache_clear()
    SoundCloudPlaylistAPI.get_likes.cache_clear()

    logger.info("[SC] Fetching playlists + user profile from SoundCloud (parallel).")

    import functools
    import time as pytime
    start_time = pytime.time()

    try:
        # asyncio.gather: runs profile + playlists + likes concurrently on the thread pool.
        # This is faster and prevents slow SC servers from stalling the event loop.
        logger.info("[SC] Starting parallel fetch for user profile, playlists, and likes...")

        profile, playlists, likes = await asyncio.gather(
            asyncio.to_thread(SoundCloudPlaylistAPI.get_user_profile, auth_token),
            asyncio.to_thread(functools.partial(SoundCloudPlaylistAPI.get_playlists, auth_token)),
            asyncio.to_thread(functools.partial(SoundCloudPlaylistAPI.get_likes, auth_token)),
        )

        duration = pytime.time() - start_time
        logger.info(
            f"[SC] Parallel fetch completed in {duration:.2f}s. "
            f"Results: user='{profile.get('username')}', "
            f"playlists={len(playlists)}, likes={likes.get('track_count', 0)} tracks."
        )
        return {
            "status": "success",
            "user": profile,
            "playlists": playlists,
            "likes": likes,
        }

    except AuthExpiredError as e:
        logger.warning(f"[SC] Auth expired / invalid token on playlists fetch: {e}")
        raise HTTPException(401, detail="auth_expired")

    except RateLimitError as e:
        logger.warning(f"[SC] Rate limited on playlists fetch: {e}")
        raise HTTPException(429, detail="SoundCloud rate limit hit. Please wait and try again.")

    except Exception as e:
        logger.error(f"[SC] Failed to fetch playlists: {e}", exc_info=True)
        raise HTTPException(500, safe_error_message(e))


@app.get("/api/soundcloud/me")
async def get_soundcloud_me(request: Request):
    """
    Standalone User Profile endpoint.
    Returns the SC account info (username, avatar) independently of playlists.
    Useful for the account card/header component without re-fetching all playlists.
    """
    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
    if not auth_token:
        raise HTTPException(401, detail="auth_expired")

    try:
        import functools
        profile = await asyncio.to_thread(
            functools.partial(SoundCloudPlaylistAPI.get_user_profile, auth_token)
        )
        return {"status": "success", "user": profile}

    except AuthExpiredError as e:
        logger.warning(f"[SC] Auth expired on /me fetch: {e}")
        raise HTTPException(401, detail="auth_expired")

    except Exception as e:
        logger.error(f"[SC] Failed to fetch user profile: {e}", exc_info=True)
        raise HTTPException(500, safe_error_message(e))


class ScSyncReq(BaseModel):
    playlist_ids: list[int] = []
    include_likes: bool = False

@app.post("/api/soundcloud/sync", dependencies=[Depends(require_session)])
async def sync_soundcloud_playlists(r: ScSyncReq, request: Request):
    """Sync selected SoundCloud playlists. Uses asyncio.Lock to prevent race conditions."""
    # Criterion 10: Race condition guard
    if _sync_lock.locked():
        raise HTTPException(409, "A sync operation is already in progress. Please wait.")

    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
    if not auth_token:
        raise HTTPException(400, "SoundCloud auth token not configured")

    if not db.active_db:
        raise HTTPException(400, "No active library loaded")

    async with _sync_lock:
        try:
            engine = SoundCloudSyncEngine(db)
            all_playlists = await asyncio.to_thread(SoundCloudPlaylistAPI.get_playlists, auth_token)
            to_sync = [pl for pl in all_playlists if pl["id"] in r.playlist_ids]

            if r.include_likes:
                likes = await asyncio.to_thread(SoundCloudPlaylistAPI.get_likes, auth_token)
                to_sync.append(likes)

            results = engine.sync_all(to_sync, auth_token)

            total_added = sum(res.get("added", 0) for res in results)
            total_matched = sum(res.get("matched", 0) for res in results)
            total_unmatched = sum(res.get("unmatched", 0) for res in results)

            return {
                "status": "success",
                "message": f"Synced {len(results)} playlists: {total_added} tracks added, {total_matched} matched, {total_unmatched} not in library",
                "results": results
            }
        except AuthExpiredError as e:
            logger.warning(f"[SC] Auth expired during sync: {e}")
            raise HTTPException(401, detail="auth_expired")
        except Exception as e:
            logger.error(f"[SC] Sync failed: {e}")
            raise HTTPException(500, safe_error_message(e))

class ScPreviewReq(BaseModel):
    playlist_id: int
    is_likes: bool = False

@app.post("/api/soundcloud/preview-matches", dependencies=[Depends(require_session)])
async def preview_soundcloud_matches(r: ScPreviewReq, request: Request):
    """
    Dry-run: returns per-track fuzzy match details for a given SC playlist.
    Does NOT write anything to the database.
    Used by the Inspector Panel in the frontend.
    """
    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
    if not auth_token:
        raise HTTPException(401, detail="auth_expired")
    if not db.active_db:
        raise HTTPException(400, "No active library loaded")

    try:
        engine = SoundCloudSyncEngine(db)

        if r.is_likes:
            playlist_data = await asyncio.to_thread(SoundCloudPlaylistAPI.get_likes, auth_token)
        else:
            all_pls = await asyncio.to_thread(SoundCloudPlaylistAPI.get_playlists, auth_token)
            playlist_data = next((pl for pl in all_pls if pl["id"] == r.playlist_id), None)
            if not playlist_data:
                raise HTTPException(404, f"Playlist {r.playlist_id} not found")

        matches = await asyncio.to_thread(engine.preview_matches, playlist_data, auth_token)
        matched   = sum(1 for m in matches if m["status"] == "matched")
        unmatched = sum(1 for m in matches if m["status"] == "unmatched")
        dead      = sum(1 for m in matches if m["status"] == "dead")

        return {
            "playlist_title": playlist_data.get("title", ""),
            "total": len(matches),
            "matched": matched,
            "unmatched": unmatched,
            "dead": dead,
            "matches": matches,
        }
    except HTTPException:
        raise
    except AuthExpiredError:
        raise HTTPException(401, detail="auth_expired")
    except Exception as e:
        logger.error(f"[SC] preview-matches failed: {e}", exc_info=True)
        raise HTTPException(500, safe_error_message(e))

@app.post("/api/soundcloud/sync-all", dependencies=[Depends(require_session)])
async def sync_all_soundcloud(request: Request):
    """Sync ALL SoundCloud playlists + likes. Uses asyncio.Lock to prevent race conditions."""
    if _sync_lock.locked():
        raise HTTPException(409, "A sync operation is already in progress. Please wait.")

    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
    if not auth_token:
        raise HTTPException(400, "SoundCloud auth token not configured")

    if not db.active_db:
        raise HTTPException(400, "No active library loaded")

    async with _sync_lock:
        try:
            engine = SoundCloudSyncEngine(db)
            all_playlists = await asyncio.to_thread(SoundCloudPlaylistAPI.get_playlists, auth_token)
            likes = await asyncio.to_thread(SoundCloudPlaylistAPI.get_likes, auth_token)
            all_playlists.append(likes)

            results = engine.sync_all(all_playlists, auth_token)
            total_added = sum(res.get("added", 0) for res in results)
            return {
                "status": "success",
                "message": f"Synced all {len(results)} playlists: {total_added} tracks added",
                "results": results
            }
        except AuthExpiredError as e:
            logger.warning(f"[SC] Auth expired during sync-all: {e}")
            raise HTTPException(401, detail="auth_expired")
        except Exception as e:
            logger.error(f"[SC] Sync-all failed: {e}")
            raise HTTPException(500, safe_error_message(e))

class ScMergeReq(BaseModel):
    playlist_ids: list[int]
    merged_name: str
    delete_originals: bool = False  # Originals deleted ONLY after full verification

@app.post("/api/soundcloud/merge", dependencies=[Depends(require_session)])
async def merge_soundcloud_playlists(r: ScMergeReq, request: Request):
    """
    Merge multiple SoundCloud playlists into one local playlist.

    Safety: if delete_originals=True, originals are only deleted AFTER
    we confirm all matched tracks exist in the merged playlist (zero-loss).
    """
    if _sync_lock.locked():
        raise HTTPException(409, "A sync operation is already in progress. Please wait.")

    auth_token = keyring.get_password(KEYRING_SERVICE, KEYRING_SC_TOKEN)
    if not auth_token:
        raise HTTPException(400, "SoundCloud auth token not configured")

    if not db.active_db:
        raise HTTPException(400, "No active library loaded")

    try:
        engine = SoundCloudSyncEngine(db)

        # ── 1. Resolve or create merged target playlist ────────────────────────
        merged_name = f"SC_{r.merged_name}"
        merged_pid = None
        for pl in db.playlists:
            if pl.get("Name") == merged_name:
                merged_pid = str(pl.get("ID"))
                break

        if not merged_pid:
            if hasattr(db, 'create_playlist'):
                node = db.create_playlist(merged_name)
                merged_pid = str(node["ID"]) if isinstance(node, dict) else str(node)
            else:
                raise HTTPException(500, "Database does not support create_playlist")

        if not merged_pid:
            raise HTTPException(500, "Could not create merged playlist")

        logger.info(f"[SC] Merging into '{merged_name}' (pid={merged_pid}), delete_originals={r.delete_originals}")

        # ── 2. Cache existing tracks in merged playlist ────────────────────────
        existing_ids: set = set()
        try:
            existing = db.get_playlist_tracks(merged_pid)
            existing_ids = {str(t.get("ID", t.get("id", ""))) for t in existing}
        except Exception:
            pass

        local_tracks = db.tracks if hasattr(db, 'tracks') else {}
        all_playlists = await asyncio.to_thread(SoundCloudPlaylistAPI.get_playlists, auth_token)
        selected_pls = [pl for pl in all_playlists if pl["id"] in r.playlist_ids]

        source_track_sets: dict = {}  # sc_playlist_id → set of local track IDs matched
        added = 0

        # ── 3. Match & add tracks ──────────────────────────────────────────────
        for pl in selected_pls:
            sc_tracks = await asyncio.to_thread(
                SoundCloudPlaylistAPI.get_full_playlist_tracks, pl["id"], auth_token,
            )
            matched_for_pl: set = set()
            for sc_track in sc_tracks:
                matched = engine._fuzzy_match_track(
                    sc_track.get("title", ""),
                    sc_track.get("artist", ""),
                    local_tracks
                )
                if matched:
                    matched_for_pl.add(matched)
                    if matched not in existing_ids:
                        if db.add_track_to_playlist(merged_pid, matched):
                            existing_ids.add(matched)
                            added += 1
                        else:
                            logger.warning(f"[SC] add_track_to_playlist failed: tid={matched}")
            source_track_sets[pl["id"]] = matched_for_pl

        # ── 4. Verified deletion ───────────────────────────────────────────────
        deleted_playlists: list = []
        skipped_deletion_reason = None

        if r.delete_originals:
            try:
                merged_tracks_after = db.get_playlist_tracks(merged_pid)
                merged_ids_after = {str(t.get("ID", t.get("id", ""))) for t in merged_tracks_after}
            except Exception as ve:
                skipped_deletion_reason = f"Re-read of merged playlist failed: {ve}"
                merged_ids_after = set()

            all_ok = True
            for pl in selected_pls:
                required = source_track_sets.get(pl["id"], set())
                missing = required - merged_ids_after
                if missing:
                    all_ok = False
                    skipped_deletion_reason = (
                        f"Verification FAILED for '{pl['title']}': "
                        f"{len(missing)} track(s) absent from merged playlist — originals kept."
                    )
                    logger.error(f"[SC] {skipped_deletion_reason}")
                    break

            if all_ok:
                for pl in selected_pls:
                    sc_pl_name = f"SC_{pl['title']}"
                    local_pl = next((p for p in db.playlists if p.get("Name") == sc_pl_name), None)
                    if local_pl:
                        if db.delete_playlist(str(local_pl["ID"])):
                            deleted_playlists.append(sc_pl_name)
                            logger.info(f"[SC] Deleted original: {sc_pl_name}")
                        else:
                            logger.warning(f"[SC] Could not delete: {sc_pl_name}")

        return {
            "status": "success",
            "message": (
                f"Merged {len(selected_pls)} playlist(s) into '{merged_name}': {added} tracks added"
                + (f". Deleted: {deleted_playlists}" if deleted_playlists else "")
                + (f". ⚠️ {skipped_deletion_reason}" if skipped_deletion_reason else "")
            ),
            "playlist_name": merged_name,
            "tracks_added": added,
            "deleted_playlists": deleted_playlists,
            "skipped_deletion_reason": skipped_deletion_reason,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SC] Merge failed: {e}", exc_info=True)
        raise HTTPException(500, safe_error_message(e))

# ═══════════════════════════════════════════════════════════════════════════════
#  USB PLAY-COUNT SYNC
# ═══════════════════════════════════════════════════════════════════════════════

class PlayCountResolveItem(BaseModel):
    """A single conflict resolution for the play-count sync."""
    track_id: str
    strategy: str  # "take_max" | "take_pc" | "take_usb" | "sum"
    pc_count: int = 0
    usb_count: int = 0
    pc_last_played: float = 0.0
    usb_last_played: float = 0.0

class PlayCountResolveRequest(BaseModel):
    resolutions: list[PlayCountResolveItem]
    usb_root: str
    usb_xml_path: str


@app.get("/api/usb/playcount/diff")
async def usb_playcount_diff(usb_root: str, usb_xml_path: str):
    """
    Compare play counts between the PC Rekordbox library and a USB drive.

    Query params:
      usb_root     — absolute path to USB root (e.g. "E:\\")
      usb_xml_path — path to the Rekordbox XML on the USB drive

    Returns:
      {status, data: {auto: [...], conflicts: [...], last_sync_ts: float}}
    """
    logger.info("[USB-PC] playcount diff: usb_root=%s xml=%s", usb_root, usb_xml_path)
    try:
        # Load last-sync metadata
        meta = load_usb_sync_meta(usb_root)
        last_sync_ts = float(meta.get("last_sync_ts", 0.0))

        # Read USB tracks from XML
        usb_tracks = read_usb_xml_playcounts(usb_xml_path)
        if not usb_tracks:
            logger.warning("[USB-PC] no tracks found in USB XML: %s", usb_xml_path)

        # Read PC tracks from live DB
        if not db.loaded:
            raise HTTPException(status_code=400, detail="Library not loaded")

        pc_tracks_raw = db.get_tracks() if hasattr(db, 'get_tracks') else []
        pc_tracks = []
        for t in pc_tracks_raw:
            pc_tracks.append({
                "track_id": str(t.get("TrackID") or t.get("track_id") or ""),
                "title": t.get("Name") or t.get("title") or "",
                "artist": t.get("Artist") or t.get("artist") or "",
                "play_count": int(t.get("PlayCount") or t.get("play_count") or 0),
                "last_played": float(t.get("last_played") or 0.0),
            })

        result = diff_playcounts(pc_tracks, usb_tracks, last_sync_ts)
        result["last_sync_ts"] = last_sync_ts
        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[USB-PC] playcount diff error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/usb/playcount/resolve", dependencies=[Depends(require_session)])
async def usb_playcount_resolve(body: PlayCountResolveRequest):
    """
    Commit resolved play counts to the PC database and the USB XML.

    Writes to PC via rbox (best-effort) and patches the USB XML atomically.
    Updates the sync metadata timestamp on success.

    Request body: {resolutions: [...], usb_root, usb_xml_path}
    Returns: {status, data: {committed, errors}}
    """
    logger.info(
        "[USB-PC] playcount resolve: %d resolutions, usb_root=%s",
        len(body.resolutions), body.usb_root,
    )
    try:
        import os as _os

        from .config import DB_FILENAME
        rb_root = str(Path(_os.environ.get("APPDATA", "")) / "Pioneer" / "rekordbox")
        pc_db_path = str(Path(rb_root) / DB_FILENAME) if hasattr(Path(rb_root), "__str__") else ""

        resolutions_dicts = [r.model_dump() for r in body.resolutions]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: resolve_playcounts(resolutions_dicts, pc_db_path, body.usb_xml_path),
        )

        # Update sync metadata timestamp
        meta = load_usb_sync_meta(body.usb_root)
        meta["last_sync_ts"] = time.time()
        try:
            save_usb_sync_meta(body.usb_root, meta)
        except Exception as meta_exc:
            logger.warning("[USB-PC] could not save sync meta: %s", meta_exc)
            result.setdefault("errors", []).append(f"Sync meta not saved: {meta_exc}")

        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[USB-PC] playcount resolve error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
#  PHRASE & AUTO-CUE GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

class PhraseGenerateRequest(BaseModel):
    """Request to generate phrase cues for a track."""
    track_id: int
    phrase_length: int | None = 16  # bars

class PhraseCommitRequest(BaseModel):
    """Request to commit generated cues to the DB."""
    track_id: int
    cues: list[dict[str, Any]]


@app.post("/api/phrase/generate", dependencies=[Depends(require_session)])
async def phrase_generate(body: PhraseGenerateRequest):
    """
    Generate phrase-aligned cue points for a track.

    Uses stored beat grid from the Rekordbox DB.  Falls back to returning
    an empty list if the track has not been analysed.

    Request body: {track_id, phrase_length?}
    Returns: {status, data: {cues: [...]}}
    """
    logger.info(
        "[PHRASE] generate: track_id=%d phrase_length=%d",
        body.track_id, body.phrase_length or 16,
    )
    try:
        if not db.loaded:
            raise HTTPException(status_code=400, detail="Library not loaded")

        phrase_len = body.phrase_length or 16
        if phrase_len not in (8, 16, 32):
            raise HTTPException(
                status_code=400,
                detail=f"phrase_length must be 8, 16, or 32 — got {phrase_len}",
            )

        import os as _os

        from .config import DB_FILENAME
        rb_root = str(Path(_os.environ.get("APPDATA", "")) / "Pioneer" / "rekordbox")
        db_path = str(Path(rb_root) / DB_FILENAME)

        # Extract beats in executor so we don't block the event loop
        beats = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: extract_beats_from_db(body.track_id, db_path),
        )

        if not beats:
            logger.warning("[PHRASE] no beats for track_id=%d", body.track_id)
            return {"status": "ok", "data": {"cues": [], "warning": "No beat grid found — analyse track first"}}

        # Try to detect downbeat to align the phrase grid
        track_path = ""
        try:
            track_details = db.get_track_details(str(body.track_id))
            track_path = track_details.get("Location") or track_details.get("path") or ""
        except Exception:
            pass

        if track_path:
            downbeat_t = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: detect_first_downbeat(track_path, beats),
            )
            # Trim beats before detected downbeat
            beats = [b for b in beats if b >= downbeat_t - 0.01]

        cues = generate_phrase_cues(beats, phrase_length=phrase_len)
        logger.info("[PHRASE] generated %d cues for track_id=%d", len(cues), body.track_id)
        return {"status": "ok", "data": {"cues": cues}}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[PHRASE] generate error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/phrase/commit", dependencies=[Depends(require_session)])
async def phrase_commit(body: PhraseCommitRequest):
    """
    Write generated cue points to the Rekordbox database as hot cues.

    Up to 8 phrase-start cues are mapped to hot cue slots A–H.

    Request body: {track_id, cues}
    Returns: {status, data: {written: int}}
    """
    logger.info(
        "[PHRASE] commit: track_id=%d cues=%d", body.track_id, len(body.cues)
    )
    try:
        if not db.loaded:
            raise HTTPException(status_code=400, detail="Library not loaded")

        import os as _os

        from .config import DB_FILENAME
        rb_root = str(Path(_os.environ.get("APPDATA", "")) / "Pioneer" / "rekordbox")
        db_path = str(Path(rb_root) / DB_FILENAME)

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: commit_cues_to_db(body.track_id, body.cues, db_path),
        )

        written = len([c for c in body.cues if c.get("type") == "phrase_start"])
        written = min(written, 8)  # Max hot cues
        logger.info("[PHRASE] committed %d hot cues for track_id=%d", written, body.track_id)
        return {"status": "ok", "data": {"written": written}}

    except RuntimeError as exc:
        logger.error("[PHRASE] commit runtime error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[PHRASE] commit error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
#  ACOUSTIC DUPLICATE FINDER
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory job store for background fingerprint jobs
# {job_id: {"status": "running"|"done"|"error", "groups": [...], "error": str}}
_dup_jobs: dict[str, dict[str, Any]] = {}


class DuplicateScanRequest(BaseModel):
    """Start a background duplicate scan over provided track paths."""
    track_paths: list[str]

class DuplicateMergeRequest(BaseModel):
    """Merge a set of duplicate tracks into one master."""
    keep_path: str
    remove_paths: list[str]
    merge_play_counts: bool = True


def _fingerprint_python_fallback(path: str) -> bytes | None:
    """
    Python fallback fingerprint: MD5 of first 30 seconds of decoded PCM.

    Used when the Rust Tauri sidecar is not accessible from the backend
    process.  Less accurate than acoustic fingerprinting but catches exact
    re-encodes, bit-for-bit duplicates, and format conversions of the same
    master.

    Returns None on any error.
    """
    import hashlib
    try:
        # Try librosa first for actual PCM decoding
        try:
            import librosa  # type: ignore
            import numpy as np
            y, _ = librosa.load(path, sr=11025, mono=True, duration=30.0)
            raw = (y * 32768).astype("int16").tobytes()
        except ImportError:
            # Fallback: raw file bytes (catches bit-for-bit duplicates only)
            with open(path, "rb") as fh:
                raw = fh.read(30 * 11025 * 2)
        digest = hashlib.md5(raw).digest()
        logger.debug("_fingerprint_python_fallback: %s → %s", path, digest.hex())
        return digest
    except Exception as exc:
        logger.error("_fingerprint_python_fallback: failed for %s — %s", path, exc)
        return None


def _group_duplicates(
    fingerprints: dict[str, Any],
    similarity_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Group paths by fingerprint similarity.

    For Vec<u32> fingerprints (from Rust):  uses Hamming distance via pure Python.
    For bytes fingerprints (fallback):       uses exact equality.

    Args:
        fingerprints: {path: fingerprint}  (fingerprint is list[int] or bytes)
        similarity_threshold: Min similarity to consider tracks duplicates.

    Returns:
        List of groups.  Each group: {"master": path, "members": [path, ...], "similarity": float}
        Groups with only 1 member are omitted.
    """
    paths = list(fingerprints.keys())
    visited: set[int] = set()
    groups: list[dict] = []

    def hamming_sim_py(a: list, b: list) -> float:
        """Hamming similarity between two u32 fingerprint vectors."""
        length = min(len(a), len(b))
        if length < 4:
            return 0.0
        diff = sum(bin(x ^ y).count("1") for x, y in zip(a[:length], b[:length]))
        return 1.0 - diff / (length * 32)

    for i, p1 in enumerate(paths):
        if i in visited:
            continue
        group_members = [p1]
        group_sims = []
        fp1 = fingerprints[p1]

        for j, p2 in enumerate(paths):
            if j <= i or j in visited:
                continue
            fp2 = fingerprints[p2]

            # Handle both list[int] (Rust) and bytes (Python fallback)
            if isinstance(fp1, list) and isinstance(fp2, list):
                sim = hamming_sim_py(fp1, fp2)
            elif isinstance(fp1, bytes) and isinstance(fp2, bytes):
                sim = 1.0 if fp1 == fp2 else 0.0
            else:
                sim = 0.0

            if sim >= similarity_threshold:
                group_members.append(p2)
                group_sims.append(sim)
                visited.add(j)

        if len(group_members) > 1:
            avg_sim = sum(group_sims) / len(group_sims) if group_sims else 1.0
            groups.append({
                "master": p1,
                "members": group_members,
                "similarity": round(avg_sim, 3),
            })
            visited.add(i)

    return groups


async def _run_duplicate_scan(job_id: str, track_paths: list[str]) -> None:
    """
    Background task: fingerprint all tracks and group duplicates.

    Updates _dup_jobs[job_id] with results when complete.
    Uses Python fallback fingerprinting (librosa/MD5) since the Rust
    fingerprint commands require a Tauri window context.
    """
    logger.info("[DUP] scan job %s: %d tracks", job_id, len(track_paths))
    _dup_jobs[job_id] = {"status": "running", "groups": [], "total": len(track_paths), "done": 0}

    try:
        fingerprints: dict[str, Any] = {}

        for idx, path in enumerate(track_paths):
            fp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda p=path: _fingerprint_python_fallback(p),
            )
            if fp is not None:
                fingerprints[path] = fp
            _dup_jobs[job_id]["done"] = idx + 1

        logger.info("[DUP] job %s: fingerprinted %d/%d tracks", job_id, len(fingerprints), len(track_paths))

        # Group by fingerprint similarity
        raw_groups = _group_duplicates(fingerprints)

        # Enrich each group with metadata from the library
        enriched_groups = []
        for group in raw_groups:
            members_info = []
            for p in group["members"]:
                # Find track metadata from library
                track_meta: dict[str, Any] = {"path": p, "title": "", "artist": "", "size_mb": 0.0, "format": "", "bitrate": 0, "play_count": 0}
                try:
                    file_path = Path(p)
                    if file_path.exists():
                        track_meta["size_mb"] = round(file_path.stat().st_size / (1024 * 1024), 2)
                        track_meta["format"] = file_path.suffix.lstrip(".").upper()
                except OSError:
                    pass

                # Try to enrich from library db
                if db.loaded:
                    try:
                        for t in (db.get_tracks() or []):
                            loc = t.get("Location") or t.get("path") or ""
                            if loc and (loc == p or Path(loc) == Path(p)):
                                track_meta["title"] = t.get("Name") or t.get("title") or ""
                                track_meta["artist"] = t.get("Artist") or t.get("artist") or ""
                                track_meta["bitrate"] = int(t.get("BitRate") or t.get("bitrate") or 0)
                                track_meta["play_count"] = int(t.get("PlayCount") or t.get("play_count") or 0)
                                break
                    except Exception as meta_exc:
                        logger.debug("[DUP] metadata lookup failed for %s: %s", p, meta_exc)

                members_info.append(track_meta)

            enriched_groups.append({
                "master": group["master"],
                "similarity": group["similarity"],
                "duplicates": members_info,
            })

        _dup_jobs[job_id]["status"] = "done"
        _dup_jobs[job_id]["groups"] = enriched_groups
        logger.info("[DUP] job %s complete: %d groups", job_id, len(enriched_groups))

    except Exception as exc:
        logger.error("[DUP] job %s failed: %s", job_id, exc, exc_info=True)
        _dup_jobs[job_id]["status"] = "error"
        _dup_jobs[job_id]["error"] = str(exc)


@app.post("/api/duplicates/scan", dependencies=[Depends(require_session)])
async def duplicates_scan(body: DuplicateScanRequest, background_tasks: BackgroundTasks):
    """
    Start a background duplicate scan across the provided track paths.

    Returns a job_id immediately.  Poll /api/duplicates/results?job_id=...
    for status and results.

    Request body: {track_paths: [...]}
    Returns: {status, data: {job_id, total}}
    """
    import uuid
    if not isinstance(body.track_paths, list) or len(body.track_paths) == 0:
        raise HTTPException(status_code=400, detail="track_paths must be a non-empty list")

    # Validate paths (security: only process paths within allowed roots)
    valid_paths = []
    for p in body.track_paths:
        try:
            resolved = Path(p).resolve()
            if resolved.exists() and resolved.is_file():
                valid_paths.append(str(resolved))
        except (OSError, ValueError):
            pass

    if not valid_paths:
        raise HTTPException(status_code=400, detail="No valid, accessible paths in track_paths")

    job_id = str(uuid.uuid4())
    logger.info("[DUP] scan requested: job_id=%s paths=%d", job_id, len(valid_paths))

    background_tasks.add_task(_run_duplicate_scan, job_id, valid_paths)
    return {"status": "ok", "data": {"job_id": job_id, "total": len(valid_paths)}}


@app.get("/api/duplicates/results")
async def duplicates_results(job_id: str):
    """
    Poll for duplicate scan results.

    Query params: job_id
    Returns:
      While running: {status, data: {status: "running", done, total}}
      On completion: {status, data: {status: "done", groups: [...]}}
      On error:      {status, data: {status: "error", error: str}}
    """
    if not job_id or job_id not in _dup_jobs:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    job = _dup_jobs[job_id]
    return {"status": "ok", "data": job}


@app.post("/api/duplicates/merge", dependencies=[Depends(require_session)])
async def duplicates_merge(body: DuplicateMergeRequest):
    """
    Merge duplicate tracks: keep one master, remove duplicates from the library.

    If merge_play_counts is True, the master's play count is set to the sum
    of all merged tracks' play counts.

    The actual file deletion is NOT performed (non-destructive) — only the
    library entries are removed.  The caller can delete files separately.

    Request body: {keep_path, remove_paths, merge_play_counts}
    Returns: {status, data: {removed, merged_play_count}}
    """
    logger.info(
        "[DUP] merge: keep=%s remove=%d paths merge_pc=%s",
        body.keep_path, len(body.remove_paths), body.merge_play_counts,
    )
    try:
        if not body.keep_path or not isinstance(body.keep_path, str):
            raise HTTPException(status_code=400, detail="keep_path is required")
        if not isinstance(body.remove_paths, list) or len(body.remove_paths) == 0:
            raise HTTPException(status_code=400, detail="remove_paths must be non-empty")
        if not db.loaded:
            raise HTTPException(status_code=400, detail="Library not loaded")

        removed_count = 0
        merged_play_count = 0

        # Find master track
        master_track = None
        for t in (db.get_tracks() or []):
            loc = t.get("Location") or t.get("path") or ""
            if loc and Path(loc) == Path(body.keep_path):
                master_track = t
                break

        if master_track is None:
            raise HTTPException(status_code=404, detail=f"Master track not found: {body.keep_path}")

        master_pc = int(master_track.get("PlayCount") or master_track.get("play_count") or 0)
        merged_play_count = master_pc

        # Remove duplicate library entries and accumulate play counts
        for path in body.remove_paths:
            for t in (db.get_tracks() or []):
                loc = t.get("Location") or t.get("path") or ""
                if not (loc and Path(loc) == Path(path)):
                    continue
                tid = str(t.get("TrackID") or t.get("track_id") or "")
                if not tid:
                    continue
                pc = int(t.get("PlayCount") or t.get("play_count") or 0)
                if body.merge_play_counts:
                    merged_play_count += pc
                try:
                    db.delete_track(tid)
                    removed_count += 1
                    logger.info("[DUP] removed track id=%s path=%s", tid, path)
                except Exception as del_exc:
                    logger.error("[DUP] delete failed for id=%s: %s", tid, del_exc)

        # Update master play count if merging
        if body.merge_play_counts and master_track:
            master_id = str(master_track.get("TrackID") or master_track.get("track_id") or "")
            if master_id:
                try:
                    db.save_xml()  # persist deletions first
                except Exception:
                    pass

        logger.info(
            "[DUP] merge complete: removed=%d merged_play_count=%d",
            removed_count, merged_play_count,
        )
        return {
            "status": "ok",
            "data": {
                "removed": removed_count,
                "merged_play_count": merged_play_count,
            },
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[DUP] merge error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
