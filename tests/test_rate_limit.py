"""Tests for ``app/rate_limit.py`` -- in-process token-bucket limiter.

Covers the seven cases enumerated in
``docs/research/research/evaluated_security-rate-limit-design.md``
Implementation Plan Step-by-step #2:

1. ``take_until_empty`` -- bucket drains to zero, then refuses.
2. ``refill_after_wait`` -- monkeypatched ``time.monotonic`` advances
   enough seconds to mint one fresh token; ``take`` allows again.
3. ``burst_allows_then_throttles`` -- first ``burst`` calls pass, the
   next one returns ``(False, retry_after_s>0)``.
4. ``whitelist_bypass`` -- loopback IP routes to the
   ``__whitelist__`` sentinel; ``BucketStore.take`` short-circuits
   without allocating a key.
5. ``concurrent_take`` -- 4 ``threading.Thread`` workers racing a
   10-burst bucket all see exactly 10 ``(True, ...)`` outcomes total.
6. ``ttl_purge`` -- bucket whose ``last_refill`` sits 700s in the past
   and is fully-refilled is evicted on the next ``take`` after the
   60s purge-interval has elapsed.
7. ``auth_before_ratelimit`` -- unauth request that would also be
   over-limit returns ``401`` (auth dependency runs before the
   ``@rate_limit`` body); marked ``no_auth``.

Same ``httpx.ASGITransport`` driver as ``tests/test_auth.py`` to keep
test infra consistent. Non-loopback ``client=("198.51.100.1", 4242)``
forces the bucket path; loopback default ``("127.0.0.1", 123)``
exercises the whitelist sentinel.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, ClassVar

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from app import rate_limit as rl
from app.auth import require_session

_NON_LOOPBACK = ("198.51.100.1", 4242)  # TEST-NET-2, never a real client


def _reset_store() -> None:
    """Drop every bucket from the module-singleton ``_store``."""
    with rl._store._lock:
        rl._store._buckets.clear()
        rl._store._last_purge = 0.0


@pytest.fixture(autouse=True)
def _clean_store():
    """Each test starts with an empty :class:`BucketStore`."""
    _reset_store()
    yield
    _reset_store()


def _request(
    app: FastAPI,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    client: tuple[str, int] = _NON_LOOPBACK,
) -> httpx.Response:
    async def _go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=client, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            return await ac.request(method, url, headers=headers, json=json)

    return asyncio.run(_go())


# --------------------------------------------------------------------------
# 1. take_until_empty
# --------------------------------------------------------------------------
def test_take_until_empty() -> None:
    b = rl.TokenBucket(steady_per_min=60.0, burst=3)
    assert b.take()[0] is True
    assert b.take()[0] is True
    assert b.take()[0] is True
    allowed, retry_after_s = b.take()
    assert allowed is False
    assert retry_after_s > 0.0


# --------------------------------------------------------------------------
# 2. refill_after_wait (monkeypatched clock)
# --------------------------------------------------------------------------
def test_refill_after_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    t = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: t[0])

    b = rl.TokenBucket(steady_per_min=60.0, burst=1)  # 1 token/sec
    assert b.take()[0] is True
    assert b.take()[0] is False  # drained

    t[0] += 2.0  # >=1s passes -> >=1 token refilled
    assert b.take()[0] is True


# --------------------------------------------------------------------------
# 3. burst_allows_then_throttles
# --------------------------------------------------------------------------
def test_burst_allows_then_throttles() -> None:
    b = rl.TokenBucket(steady_per_min=6.0, burst=10)  # 0.1 token/sec
    allowed_count = sum(1 for _ in range(10) if b.take()[0])
    assert allowed_count == 10
    allowed, retry_after_s = b.take()
    assert allowed is False
    assert retry_after_s > 0.0


# --------------------------------------------------------------------------
# 4. whitelist_bypass (loopback -> sentinel)
# --------------------------------------------------------------------------
def test_whitelist_bypass() -> None:
    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers: ClassVar[dict[str, str]] = {}

    key = rl.make_key(_Req(), mode="both")  # type: ignore[arg-type]
    assert key == "__whitelist__"

    # Repeated takes never allocate a bucket.
    for _ in range(50):
        allowed, retry_after_s = rl._store.take(key, steady=5.0, burst=1)
        assert (allowed, retry_after_s) == (True, 0.0)
    assert key not in rl._store._buckets


# --------------------------------------------------------------------------
# 5. concurrent_take (4 workers, 10 burst)
# --------------------------------------------------------------------------
def test_concurrent_take() -> None:
    bucket_key = "ip:198.51.100.1|b:none"
    results: list[bool] = []
    results_lock = threading.Lock()

    def worker() -> None:
        local: list[bool] = []
        for _ in range(25):  # 4 * 25 = 100 attempts >> 10 burst
            allowed, _ = rl._store.take(bucket_key, steady=0.0, burst=10)
            local.append(allowed)
        with results_lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 10


# --------------------------------------------------------------------------
# 6. ttl_purge
# --------------------------------------------------------------------------
def test_ttl_purge() -> None:
    key = "ip:203.0.113.7"
    rl._store.take(key, steady=60.0, burst=10)
    bucket = rl._store._buckets[key]

    # Force the bucket into the "fully-refilled, idle 700s" state.
    bucket.tokens = float(bucket.capacity)
    bucket.last_refill -= 700.0
    rl._store._last_purge -= 61.0  # next take crosses purge interval

    # Touching any other key triggers the lazy purge sweep.
    rl._store.take("ip:203.0.113.99", steady=60.0, burst=10)
    assert key not in rl._store._buckets


# --------------------------------------------------------------------------
# 7. auth_before_ratelimit (401 wins over 429)
# --------------------------------------------------------------------------
@pytest.mark.no_auth
def test_auth_before_ratelimit() -> None:
    """Unauth + over-limit MUST surface as 401, not 429.

    ``Depends(require_session)`` resolves in FastAPI's request pipeline
    *before* the handler body runs; ``@rate_limit`` wraps the handler,
    so the auth 401 fires first. The test bursts past the 1/min steady
    limit with a 2-burst cap to be sure the bucket would deny if reached.
    """
    app = FastAPI()

    @app.post("/gated", dependencies=[Depends(require_session)])
    @rl.rate_limit(steady=1.0, burst=2, key_mode="both")
    async def _gated(request: Request) -> dict[str, str]:
        return {"status": "ok"}

    for _ in range(5):
        r = _request(app, "POST", "/gated")
        assert r.status_code == 401, "auth dep must fire before rate-limit decorator body"
