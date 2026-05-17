"""Constant-time equality helper for tokens, secrets, HMAC outputs.

Single public symbol: :func:`safe_compare`. Wraps
:func:`secrets.compare_digest` with input validation so that callers
never have to hand-roll the ``isinstance`` / ``isascii`` / length-equal
pre-checks that the primitive otherwise requires. The primitive raises
``TypeError`` on non-ASCII ``str`` or mixed ``str``/``bytes``; this
helper returns ``False`` in every fragility case from the audit's
behavior matrix (see
``docs/research/research/evaluated_security-secrets-compare-digest-codebase-audit.md``,
Findings #2).

**Trust direction:** ``presented`` = untrusted (request-side);
``expected`` = canonical (server-side). Argument order is part of the
public contract.
"""

from __future__ import annotations

import secrets


def safe_compare(
    presented: str | bytes,
    expected: str | bytes,
) -> bool:
    """Constant-time equality for tokens/secrets/HMAC outputs.

    Returns ``False`` (never raises) for: non-``(str|bytes)`` inputs,
    non-ASCII ``str``, length mismatch. Returns
    :func:`secrets.compare_digest` result for a valid equal-length
    bytes pair. Untrusted side is ``presented``; canonical side is
    ``expected``.
    """
    if not isinstance(presented, (str, bytes)) or not isinstance(
        expected, (str, bytes)
    ):
        return False

    if isinstance(presented, str):
        if not presented.isascii():
            return False
        p_bytes: bytes = presented.encode("ascii")
    else:
        p_bytes = presented

    if isinstance(expected, str):
        if not expected.isascii():
            return False
        e_bytes: bytes = expected.encode("ascii")
    else:
        e_bytes = expected

    if len(p_bytes) != len(e_bytes):
        return False

    return secrets.compare_digest(p_bytes, e_bytes)
