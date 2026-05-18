"""Log redaction helpers — scrub absolute paths from log lines + tracebacks.

`safe_error_message_str` is the string-input variant of `safe_error_message`
(`app/main.py`); the two share the replacement list so widening one widens both.

`RedactingFormatter` is the wire-time interception point: it overrides
`Formatter.format` so the persisted log line AND the cached
`LogRecord.exc_text` (used by sibling handlers per CPython
`Logger.callHandlers` iteration order) are both scrubbed.

Invariant: must be the formatter on EVERY handler attached to the root
logger; otherwise the first formatter to run populates `record.exc_text`
with raw paths, and any handler whose formatter doesn't also scrub the
cached string will leak them.

Do NOT flip `capture_locals=True` on `traceback.TracebackException`
anywhere without widening `safe_error_message_str` scope — locals freely
embed paths (e.g. `file_path` in `validate_audio_path`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import EXPORT_DIR, MUSIC_DIR, TEMP_DIR

# Process-local app root; mirrors `APP_DIR` in `app/main.py` so both
# helpers strip the same install-layout prefix.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def safe_error_message_str(msg: str) -> str:
    """Strip absolute paths from a rendered log/error string.

    Operates on already-rendered text — call sites pass `str(exc)`,
    `Formatter.format` output, or `record.exc_text`. Replaces each
    sensitive prefix with ``[...]``.
    """
    if not msg:
        return msg
    for sensitive in [
        _APP_DIR,
        str(Path.home()),
        os.environ.get("APPDATA", ""),
        str(EXPORT_DIR.resolve()),
        str(MUSIC_DIR.resolve()),
        str(TEMP_DIR.resolve()),
    ]:
        if sensitive:
            msg = msg.replace(sensitive, "[...]")
    return msg


class RedactingFormatter(logging.Formatter):
    """`logging.Formatter` that scrubs paths from message + cached traceback.

    Override semantics:

    1. `super().format(record)` runs the standard pipeline, which (when
       `record.exc_info` is set) populates `record.exc_text` via
       `self.formatException(record.exc_info)` on the first formatter
       call across all handlers (CPython `Lib/logging/__init__.py`
       `Formatter.format`).
    2. We then mutate `record.exc_text` in place so any sibling handler
       whose formatter reuses the cache sees the scrubbed version.
    3. The returned string is scrubbed unconditionally so the calling
       handler's emit gets clean output regardless of what was cached.
    """

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        if record.exc_text:
            record.exc_text = safe_error_message_str(record.exc_text)
        return safe_error_message_str(s)
