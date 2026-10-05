"""Unit tests for `tapjob_api.core.rate_limit` against a tiny in-memory fake Redis client -- no
real Redis connection, deterministic `now` control for window-boundary behavior.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import redis

from tapjob_api.core.rate_limit import (
    RateLimiterUnavailableError,
    RateLimitPolicy,
    RedisFixedWindowRateLimiter,
)


class _FakePipeline:
    def __init__(self, store: dict[str, int], ttls: dict[str, int]) -> None:
        self._store = store
        self._ttls = ttls
        self._ops: list[Callable[[], object]] = []

    def incr(self, key: str) -> "_FakePipeline":
        def _run() -> object:
            self._store[key] = self._store.get(key, 0) + 1
            return self._store[key]

        self._ops.append(_run)
        return self

    def expire(self, key: str, seconds: int, *, nx: bool = False) -> "_FakePipeline":
        def _run() -> object:
            if not nx or key not in self._ttls:
                self._ttls[key] = seconds
            return True

        self._ops.append(_run)
        return self

    def execute(self) -> list[object]:
        results = [op() for op in self._ops]
        self._ops.clear()
        return results


class FakeRedisClient:
    """Mimics only the `redis.Redis` surface `RedisFixedWindowRateLimiter` actually calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    def pipeline(self) -> _FakePipeline:
        if self.fail:
            raise redis.ConnectionError("simulated Redis outage")
        return _FakePipeline(self.store, self.ttls)


def _policy(
    *, limit: int = 3, window_seconds: int = 60, prefix: str = "rl:test"
) -> RateLimitPolicy:
    return RateLimitPolicy(key_prefix=prefix, limit=limit, window_seconds=window_seconds)


def test_first_request_in_a_window_is_allowed() -> None:
    limiter = RedisFixedWindowRateLimiter(FakeRedisClient())  # type: ignore[arg-type]

    result = limiter.check(_policy(limit=3), "user-1")

    assert result.allowed is True
    assert result.remaining == 2


def test_requests_up_to_the_limit_are_allowed() -> None:
    client = FakeRedisClient()
    limiter = RedisFixedWindowRateLimiter(client)  # type: ignore[arg-type]
    policy = _policy(limit=3)

    results = [limiter.check(policy, "user-1") for _ in range(3)]

    assert all(result.allowed for result in results)
    assert results[-1].remaining == 0


def test_a_request_beyond_the_limit_is_rejected() -> None:
    client = FakeRedisClient()
    limiter = RedisFixedWindowRateLimiter(client)  # type: ignore[arg-type]
    policy = _policy(limit=3)
    for _ in range(3):
        limiter.check(policy, "user-1")

    result = limiter.check(policy, "user-1")

    assert result.allowed is False
    assert result.remaining == 0


def test_retry_after_seconds_is_within_the_window_bounds() -> None:
    limiter = RedisFixedWindowRateLimiter(FakeRedisClient())  # type: ignore[arg-type]
    policy = _policy(window_seconds=60)

    result = limiter.check(policy, "user-1", now=datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC))

    assert 1 <= result.retry_after_seconds <= 60


def test_different_principals_never_share_a_counter() -> None:
    client = FakeRedisClient()
    limiter = RedisFixedWindowRateLimiter(client)  # type: ignore[arg-type]
    policy = _policy(limit=1)

    first = limiter.check(policy, "user-1")
    second = limiter.check(policy, "user-2")

    assert first.allowed is True
    assert second.allowed is True


def test_different_policies_never_share_a_counter_even_for_the_same_principal() -> None:
    client = FakeRedisClient()
    limiter = RedisFixedWindowRateLimiter(client)  # type: ignore[arg-type]
    bootstrap_policy = _policy(limit=1, prefix="rl:bootstrap")
    devices_policy = _policy(limit=1, prefix="rl:devices")

    first = limiter.check(bootstrap_policy, "user-1")
    second = limiter.check(devices_policy, "user-1")

    assert first.allowed is True
    assert second.allowed is True


def test_a_new_window_resets_the_counter() -> None:
    client = FakeRedisClient()
    limiter = RedisFixedWindowRateLimiter(client)  # type: ignore[arg-type]
    policy = _policy(limit=1, window_seconds=60)
    first_window = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    next_window = datetime(2026, 1, 1, 12, 1, 0, tzinfo=UTC)

    exhausted = limiter.check(policy, "user-1", now=first_window)
    rejected = limiter.check(policy, "user-1", now=first_window)
    allowed_again = limiter.check(policy, "user-1", now=next_window)

    assert exhausted.allowed is True
    assert rejected.allowed is False
    assert allowed_again.allowed is True


def test_redis_unavailable_raises_rate_limiter_unavailable_error() -> None:
    limiter = RedisFixedWindowRateLimiter(FakeRedisClient(fail=True))  # type: ignore[arg-type]

    with pytest.raises(RateLimiterUnavailableError):
        limiter.check(_policy(), "user-1")
