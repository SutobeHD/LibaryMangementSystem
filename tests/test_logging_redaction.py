"""Unit tests for `app.logging_utils.RedactingFormatter`.

Covers the four behaviors required by
`docs/research/research/evaluated_security-error-handler-exc-info-leak.md`:

1. `exc_info` traceback scrubbed end-to-end.
2. Chained exception ("During handling of the above") preserves chain
   markers while scrubbing both frames.
3. `args`-style interpolation (`logger.error("path=%s", APP_DIR)`)
   produces a scrubbed final string.
4. Non-exception log lines are byte-identical to a plain `Formatter`
   (modulo the always-on scrub pass, which is a no-op on path-free input).
"""

from __future__ import annotations

import io
import logging
import os

import pytest

from app.logging_utils import RedactingFormatter

_APP_DIR = os.path.dirname(os.path.abspath(__import__("app").__file__))
_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


@pytest.fixture()
def stream_logger() -> tuple[logging.Logger, io.StringIO]:
    """Yield a freshly configured logger + the StringIO its handler writes to.

    Each test gets its own logger name so handlers don't accumulate
    across the suite.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(RedactingFormatter(fmt=_FMT))

    # Unique logger per call avoids handler bleed across tests; setting
    # propagate=False keeps the root logger (which test harness may
    # configure with its own handlers) out of the captured stream.
    logger = logging.getLogger(f"test_logging_redaction.{id(buf)}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = [handler]
    logger.propagate = False
    return logger, buf


def test_exc_info_traceback_scrubbed(stream_logger: tuple[logging.Logger, io.StringIO]) -> None:
    logger, buf = stream_logger
    try:
        raise ValueError(f"bad path: {_APP_DIR}/foo.py")
    except ValueError:
        logger.error("boom", exc_info=True)

    out = buf.getvalue()
    assert "[...]" in out, f"expected '[...]' marker in {out!r}"
    assert _APP_DIR not in out, f"APP_DIR leaked into log output: {out!r}"
    # Traceback structure preserved (header line present).
    assert "Traceback (most recent call last)" in out


def test_chained_exception_preserves_chain_marker(
    stream_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, buf = stream_logger
    try:
        try:
            raise OSError(f"primary {_APP_DIR}/inner.py")
        except OSError as inner:
            raise RuntimeError(f"wrap {_APP_DIR}/outer.py") from inner
    except RuntimeError:
        logger.error("wrapped", exc_info=True)

    out = buf.getvalue()
    # "During handling of the above" appears only when chained via implicit
    # __context__; "direct cause" appears when chained via `from`. We use
    # `from`, so expect the direct-cause marker.
    assert (
        "The above exception was the direct cause" in out or "During handling of the above" in out
    ), f"chain marker missing in {out!r}"
    assert _APP_DIR not in out, f"APP_DIR leaked through chained traceback: {out!r}"
    assert "[...]" in out


def test_args_interpolation_scrubbed(stream_logger: tuple[logging.Logger, io.StringIO]) -> None:
    logger, buf = stream_logger
    logger.error("path=%s", _APP_DIR)

    out = buf.getvalue()
    assert _APP_DIR not in out, f"args path leaked: {out!r}"
    assert "path=[...]" in out, f"expected scrubbed path interpolation in {out!r}"


def test_non_exception_log_format_unchanged() -> None:
    """A path-free `logger.info("hello")` must be byte-identical via
    `RedactingFormatter` vs a plain `Formatter` — the scrub is a no-op
    when no sensitive prefix appears.
    """
    record = logging.LogRecord(
        name="probe",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    plain = logging.Formatter(fmt=_FMT).format(record)
    redacted = RedactingFormatter(fmt=_FMT).format(record)
    assert plain == redacted, (
        f"non-exception format diverged:\n  plain={plain!r}\n  redacted={redacted!r}"
    )
