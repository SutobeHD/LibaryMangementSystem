"""Regression tests for ``POST /api/file/reveal`` sandbox.

Tracks ``docs/research/research/evaluated_security-api-file-reveal-sandbox.md``.

The handler now routes the user-supplied path through
``validate_audio_path`` so any authenticated caller can only reveal
**audio files inside ``ALLOWED_AUDIO_ROOTS``**. Five cases pin the
contract:

1. outside-roots path     -> 403, subprocess.run NOT called
2. non-audio extension    -> 400, subprocess.run NOT called
3. missing file (in-root) -> 404, subprocess.run NOT called
4. directory path         -> 400/404, subprocess.run NOT called
5. valid in-root audio    -> 200, subprocess.run called once with
                              platform-correct argv (win32 / darwin /
                              linux variants via monkeypatched
                              ``sys.platform``).

Driving the app: same pattern as ``tests/test_security_hotfixes.py``
(httpx ``ASGITransport`` against the live FastAPI graph; no
``TestClient`` because the installed fastapi 0.109 + httpx 0.28 pair
mishandles the deprecated ``app=`` kwarg).
"""
from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from app.main import ALLOWED_AUDIO_ROOTS, app

# ---------------------------------------------------------------------------
# Helpers (mirror test_security_hotfixes.py to avoid one-off divergence)
# ---------------------------------------------------------------------------


def _post(
    url: str,
    json: dict | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Synchronous POST against the ASGI app.

    ``raise_app_exceptions=False`` keeps app-level exceptions inside the
    middleware stack so the global handler can convert them to a 500 —
    matching the live uvicorn behaviour.
    """
    async def _go() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app, client=("127.0.0.1", 12345), raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.post(url, json=json, headers=headers)

    return asyncio.run(_go())


@pytest.fixture
def sandbox_root(tmp_path: Path) -> Iterator[Path]:
    """Add a temp dir to ``ALLOWED_AUDIO_ROOTS`` for one test, then remove it."""
    root = (tmp_path / "lib").resolve()
    root.mkdir()
    ALLOWED_AUDIO_ROOTS.append(root)
    try:
        yield root
    finally:
        with contextlib.suppress(ValueError):
            ALLOWED_AUDIO_ROOTS.remove(root)


class _RunRecorder:
    """Callable stand-in for ``subprocess.run`` capturing every invocation."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object):
        self.calls.append((args, kwargs))

        class _Completed:
            returncode = 0
            stdout = b""
            stderr = b""

        return _Completed()


@pytest.fixture
def no_run(monkeypatch: pytest.MonkeyPatch) -> _RunRecorder:
    """Replace ``subprocess.run`` with a recorder that never spawns a process.

    The handler does ``import subprocess`` inside the function body so
    patching the live ``subprocess.run`` attribute is sufficient — the
    fresh import inside the handler resolves to the same module object
    we patched here.
    """
    rec = _RunRecorder()
    monkeypatch.setattr(subprocess, "run", rec)
    return rec


# ---------------------------------------------------------------------------
# Negative cases — subprocess MUST NOT be called
# ---------------------------------------------------------------------------


class TestFileRevealSandboxRejects:
    def test_outside_roots_path_is_forbidden(
        self,
        tmp_path: Path,
        sandbox_root: Path,
        no_run: _RunRecorder,
        auth_token: dict[str, str],
    ) -> None:
        outside = (tmp_path / "elsewhere").resolve()
        outside.mkdir()
        rogue = outside / "rogue.mp3"
        rogue.write_bytes(b"\x00")
        r = _post(
            "/api/file/reveal",
            json={"path": str(rogue)},
            headers=auth_token,
        )
        assert r.status_code == 403
        assert no_run.calls == []

    def test_non_audio_extension_is_rejected(
        self,
        sandbox_root: Path,
        no_run: _RunRecorder,
        auth_token: dict[str, str],
    ) -> None:
        note = sandbox_root / "note.txt"
        note.write_bytes(b"hello")
        r = _post(
            "/api/file/reveal",
            json={"path": str(note)},
            headers=auth_token,
        )
        assert r.status_code == 400
        assert no_run.calls == []

    def test_missing_file_in_root_is_404(
        self,
        sandbox_root: Path,
        no_run: _RunRecorder,
        auth_token: dict[str, str],
    ) -> None:
        ghost = sandbox_root / "does_not_exist.mp3"
        assert not ghost.exists()
        r = _post(
            "/api/file/reveal",
            json={"path": str(ghost)},
            headers=auth_token,
        )
        assert r.status_code == 404
        assert no_run.calls == []

    def test_directory_path_is_rejected(
        self,
        sandbox_root: Path,
        no_run: _RunRecorder,
        auth_token: dict[str, str],
    ) -> None:
        sub = sandbox_root / "some_folder"
        sub.mkdir()
        r = _post(
            "/api/file/reveal",
            json={"path": str(sub)},
            headers=auth_token,
        )
        # Dirs have no audio extension -> 400 from validate_audio_path.
        # If a future caller passes a dir WITH an audio suffix (e.g.
        # ``foo.mp3/``), the ``is_file()`` check still rejects as 404.
        # Either way the contract is "subprocess never runs".
        assert r.status_code in (400, 404)
        assert no_run.calls == []


# ---------------------------------------------------------------------------
# Positive case — valid in-root audio, platform-correct argv
# ---------------------------------------------------------------------------


class TestFileRevealSandboxAccepts:
    @pytest.mark.parametrize(
        ("platform", "expected_argv_builder"),
        [
            (
                "win32",
                lambda p: ["explorer", "/select,", str(p)],
            ),
            (
                "darwin",
                lambda p: ["open", "-R", str(p)],
            ),
            (
                "linux",
                lambda p: ["xdg-open", str(p.parent)],
            ),
        ],
    )
    def test_valid_audio_calls_subprocess_with_platform_argv(
        self,
        platform: str,
        expected_argv_builder,
        sandbox_root: Path,
        no_run: _RunRecorder,
        auth_token: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pin the platform branch before the request hits the handler.
        monkeypatch.setattr(sys, "platform", platform)

        mp3 = sandbox_root / "track.mp3"
        mp3.write_bytes(b"\x00")  # 1 byte; never decoded
        resolved = mp3.resolve()

        r = _post(
            "/api/file/reveal",
            json={"path": str(mp3)},
            headers=auth_token,
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "success"}

        assert len(no_run.calls) == 1, (
            f"expected exactly one subprocess.run call, got "
            f"{len(no_run.calls)}: {no_run.calls!r}"
        )
        (args, kwargs) = no_run.calls[0]
        # argv is the first positional arg.
        assert args[0] == expected_argv_builder(resolved)
        # check=False is the contract — never let explorer/open/xdg-open
        # raise CalledProcessError back into the handler.
        assert kwargs.get("check") is False
