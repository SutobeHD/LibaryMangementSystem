"""Tests for ``app/security_compare.py::safe_compare``.

7 cases per the evaluated research doc's Implementation Plan Step 3
(``docs/research/research/evaluated_security-secrets-compare-digest-codebase-audit.md``).

Every case asserts a ``bool`` return — none assert ``raises``. The
helper's whole purpose is to absorb the ``TypeError`` that
:func:`secrets.compare_digest` raises on non-ASCII ``str`` or mixed
``str``/``bytes`` inputs, returning ``False`` instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.security_compare import safe_compare


class TestCase1_Equal:
    def test_equal_str(self) -> None:
        result = safe_compare("abc", "abc")
        assert isinstance(result, bool)
        assert result is True

    def test_equal_bytes(self) -> None:
        result = safe_compare(b"abc", b"abc")
        assert isinstance(result, bool)
        assert result is True


class TestCase2_UnequalSameLength:
    def test_unequal_same_length_str(self) -> None:
        result = safe_compare("abc", "abd")
        assert isinstance(result, bool)
        assert result is False

    def test_unequal_same_length_bytes(self) -> None:
        result = safe_compare(b"abc", b"abd")
        assert isinstance(result, bool)
        assert result is False


class TestCase3_LengthMismatch:
    def test_length_mismatch_str(self) -> None:
        result = safe_compare("a", "abc")
        assert isinstance(result, bool)
        assert result is False

    def test_length_mismatch_bytes(self) -> None:
        result = safe_compare(b"abcd", b"abc")
        assert isinstance(result, bool)
        assert result is False


class TestCase4_EmptyVsNonEmpty:
    def test_empty_presented_str(self) -> None:
        result = safe_compare("", "abc")
        assert isinstance(result, bool)
        assert result is False

    def test_empty_expected_bytes(self) -> None:
        result = safe_compare(b"abc", b"")
        assert isinstance(result, bool)
        assert result is False

    def test_both_empty(self) -> None:
        # Both empty == equal-length-zero bytes pair == compare_digest True.
        # Documents current behavior; callers that need to reject this
        # case must do so before calling safe_compare.
        result = safe_compare("", "")
        assert isinstance(result, bool)
        assert result is True


class TestCase5_NonAsciiStr:
    def test_non_ascii_presented(self) -> None:
        result = safe_compare("café", "cafe")
        assert isinstance(result, bool)
        assert result is False

    def test_non_ascii_expected(self) -> None:
        result = safe_compare("cafe", "café")
        assert isinstance(result, bool)
        assert result is False

    def test_non_ascii_both(self) -> None:
        # Even when both sides match byte-for-byte at the UTF-8 level,
        # safe_compare rejects on ASCII gate — by design.
        result = safe_compare("café", "café")
        assert isinstance(result, bool)
        assert result is False


class TestCase6_MixedStrBytes:
    def test_str_vs_bytes(self) -> None:
        # Both sides are coerced via .encode("ascii"); equal content
        # therefore compares equal even across the str/bytes boundary.
        result = safe_compare("abc", b"abc")
        assert isinstance(result, bool)
        assert result is True

    def test_bytes_vs_str_unequal(self) -> None:
        result = safe_compare(b"abc", "abd")
        assert isinstance(result, bool)
        assert result is False


class TestCase7_NonStrOrBytesInput:
    @pytest.mark.parametrize(
        "bad_value",
        [123, None, 12.5, ["a", "b"], {"a": 1}, object()],
    )
    def test_non_str_or_bytes_presented(self, bad_value: Any) -> None:
        result = safe_compare(bad_value, "abc")
        assert isinstance(result, bool)
        assert result is False

    @pytest.mark.parametrize(
        "bad_value",
        [123, None, 12.5, ["a", "b"], {"a": 1}, object()],
    )
    def test_non_str_or_bytes_expected(self, bad_value: Any) -> None:
        result = safe_compare("abc", bad_value)
        assert isinstance(result, bool)
        assert result is False

    def test_both_none(self) -> None:
        result = safe_compare(None, None)  # type: ignore[arg-type]
        assert isinstance(result, bool)
        assert result is False
