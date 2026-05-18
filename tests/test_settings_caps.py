"""Tests for `SetReq` payload caps + `SettingsManager.load` sanitizer.

Covers Recommendation in
`docs/research/research/evaluated_security-pydantic-extra-allow-blob-write.md`:

* per-value bytes (MAX_VALUE_BYTES = 8 KB)
* per-dict keys (MAX_DICT_KEYS = 64)
* per-key length (MAX_KEY_LEN = 64)
* per-list items (MAX_LIST_ITEMS = 256)
* per-path length (MAX_PATH_LEN = 1024)
* total payload bytes (MAX_TOTAL_BYTES = 256 KB)
* `strict: True` rejects string coercion of bools
* legacy invalid keys in stored settings.json are silently stripped
  on load with `logger.info` (load-path tolerance)

Direct Pydantic instantiation is used (not the FastAPI route) because
that's where the validator runs; the route handler is unchanged.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("rbox", reason="pyrekordbox not installed on this platform")

from pydantic import ValidationError

from app.main import (
    MAX_DICT_KEYS,
    MAX_KEY_LEN,
    MAX_LIST_ITEMS,
    MAX_PATH_LEN,
    MAX_TOTAL_BYTES,
    MAX_VALUE_BYTES,
    SetReq,
)
from app.services import SettingsManager


class TestSetReqHappyPath:
    """Realistic payloads must keep working."""

    def test_minimal_empty_payload_ok(self) -> None:
        SetReq()  # defaults must satisfy the validator.

    def test_typical_populated_payload_ok(self) -> None:
        SetReq(
            theme="dark",
            auto_snap=True,
            waveform_visual_mode="3band",
            waveform_color_low="#ef4444",
            waveform_color_mid="#22c55e",
            waveform_color_high="#3b82f6",
            locale="de",
            shortcuts={"play": "Space", "next": "Right"},
            scan_folders=["C:/Music/Library", "D:/Backup"],
            insights_bitrate_threshold=320,
        )

    def test_extras_passthrough_ok(self) -> None:
        # Unanticipated key the frontend may add mid-cycle: should flow
        # through under `extra: "allow"` as long as caps satisfied.
        m = SetReq(future_pref="experimental")
        assert (m.model_extra or {}).get("future_pref") == "experimental"


class TestSetReqValueBytesCap:
    def test_extras_value_over_cap_rejected(self) -> None:
        oversized = "x" * (MAX_VALUE_BYTES + 1)
        with pytest.raises(ValidationError, match=r"value_bytes>"):
            SetReq(big=oversized)

    def test_extras_value_at_cap_ok(self) -> None:
        # JSON encoding wraps with quotes (+2 bytes); leave a margin so
        # the serialized form lands within the cap.
        SetReq(big="x" * (MAX_VALUE_BYTES - 16))


class TestSetReqDictKeysCap:
    def test_extras_dict_over_keys_rejected(self) -> None:
        big_dict = {f"k{i}": "v" for i in range(MAX_DICT_KEYS + 1)}
        with pytest.raises(ValidationError, match=r"dict_keys>"):
            SetReq(blob=big_dict)


class TestSetReqKeyLenCap:
    def test_extras_long_key_rejected(self) -> None:
        long_key = "k" * (MAX_KEY_LEN + 1)
        with pytest.raises(ValidationError, match=r"key_len>"):
            SetReq(**{long_key: "v"})


class TestSetReqListItemsCap:
    def test_scan_folders_over_cap_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SetReq(scan_folders=[f"/p/{i}" for i in range(MAX_LIST_ITEMS + 1)])

    def test_extras_list_over_cap_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"list_items>"):
            SetReq(weird=[i for i in range(MAX_LIST_ITEMS + 1)])


class TestSetReqPathLenCap:
    def test_scan_folders_long_path_rejected(self) -> None:
        long_path = "C:/" + ("a" * (MAX_PATH_LEN + 10))
        with pytest.raises(ValidationError):
            SetReq(scan_folders=[long_path])


class TestSetReqTotalBytesCap:
    def test_payload_over_total_rejected(self) -> None:
        # One ~7.5 KB value across enough extras to blow past 256 KB.
        chunk = "x" * (MAX_VALUE_BYTES - 512)
        kwargs = {f"k{i:03d}": chunk for i in range(64)}  # 64*7.5KB ≈ 480 KB
        with pytest.raises(ValidationError):
            SetReq(**kwargs)


class TestSetReqStrictMode:
    def test_string_true_rejected_for_bool(self) -> None:
        with pytest.raises(ValidationError):
            SetReq(auto_snap="true")

    def test_string_int_rejected_for_int_field(self) -> None:
        with pytest.raises(ValidationError):
            SetReq(insights_bitrate_threshold="320")

    def test_negative_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SetReq(artist_view_threshold=-1)

    def test_unknown_waveform_visual_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SetReq(waveform_visual_mode="rainbow")

    def test_bad_hex_color_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SetReq(waveform_color_low="not-a-color")


class TestSetReqLoggingRedaction:
    def test_warning_logged_with_key_and_reason_not_value(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        oversized = "y" * (MAX_VALUE_BYTES + 1)
        with (
            caplog.at_level(logging.WARNING, logger="app.main"),
            pytest.raises(ValidationError),
        ):
            SetReq(secret_blob=oversized)
        assert any(
            "[settings] POST rejected" in rec.message and "secret_blob" in rec.message
            for rec in caplog.records
        )
        # NEVER log the value.
        assert not any("yyyy" in rec.message for rec in caplog.records)


class TestSettingsManagerLoadSanitizer:
    """`load` strips now-rejected keys silently + logs `info`."""

    def test_oversize_key_dropped_on_load(
        self, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = tmp_path / "settings.json"
        long_key = "x" * (MAX_KEY_LEN + 5)
        cfg.write_text(json.dumps({long_key: "v", "theme": "light"}), encoding="utf-8")
        monkeypatch.setattr(SettingsManager, "CONFIG", cfg)
        with caplog.at_level(logging.INFO, logger="app.services"):
            result = SettingsManager.load()
        assert long_key not in result
        assert result["theme"] == "light"
        assert any("dropped key=" in rec.message for rec in caplog.records)

    def test_oversize_value_dropped_on_load(
        self, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = tmp_path / "settings.json"
        big = "z" * (MAX_VALUE_BYTES + 100)
        cfg.write_text(json.dumps({"blob": big, "theme": "dark"}), encoding="utf-8")
        monkeypatch.setattr(SettingsManager, "CONFIG", cfg)
        with caplog.at_level(logging.INFO, logger="app.services"):
            result = SettingsManager.load()
        assert "blob" not in result
        assert result["theme"] == "dark"

    def test_oversize_list_dropped_on_load(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        cfg = tmp_path / "settings.json"
        big_list = [f"/p/{i}" for i in range(MAX_LIST_ITEMS + 5)]
        cfg.write_text(
            json.dumps({"old_paths": big_list, "theme": "dark"}), encoding="utf-8",
        )
        monkeypatch.setattr(SettingsManager, "CONFIG", cfg)
        result = SettingsManager.load()
        assert "old_paths" not in result
        assert result["theme"] == "dark"

    def test_load_legacy_clean_file_unchanged(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        cfg = tmp_path / "settings.json"
        clean = {"theme": "light", "auto_snap": False, "scan_folders": []}
        cfg.write_text(json.dumps(clean), encoding="utf-8")
        monkeypatch.setattr(SettingsManager, "CONFIG", cfg)
        result = SettingsManager.load()
        assert result["theme"] == "light"
        assert result["auto_snap"] is False
