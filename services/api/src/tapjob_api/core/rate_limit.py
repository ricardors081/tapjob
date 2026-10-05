"""Small, reusable, Redis-backed fixed-window rate limiter (docs/ENGINEERING_STANDARDS.md §2: one
source of truth per concern). Generic and feature-neutral -- WP1.1 is its first caller
(`tapjob_api.identity.dependencies`), but nothing here is identity-specific, so a later feature
needing its own rate limit reuses this instead of growing a second implementation.

Deliberately NOT a token-bucket/sliding-window algorithm: a fixed window is simpler, uses one Redis
key per (policy, principal, window), and is accurate enough for abuse protection -- the caller
never needs sub-window precision.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import redis


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """A named, reusable limit. `key_prefix` must be unique per call site so two different
    operations never share a counter."""

    key_prefix: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiterUnavailableError(Exception):
    """Raised when Redis itself could not be reached -- the caller (not this module) decides the
    fail-open/fail-closed behavior appropriate to its own endpoint
    (docs/adr/0009-identity-auth-user-account.md "rate limit failure mode")."""


class RedisFixedWindowRateLimiter:
    """One Redis key per `(policy.key_prefix, principal_key, window_index)`, incremented with
    `INCR` and given a `window_seconds` TTL via `EXPIRE ... NX` (set only on the first increment of
    that window, so a request's own check never resets its neighbors' TTL): a value that never
    reaches `policy.limit` within one window simply expires and starts over."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def close(self) -> None:
        """Releases the underlying Redis connection pool. The caller (`tapjob_api.main`'s app
        lifespan) owns this limiter's lifecycle the same way it owns `db_engine`'s."""
        self._client.close()

    def check(
        self, policy: RateLimitPolicy, principal_key: str, *, now: datetime | None = None
    ) -> RateLimitResult:
        current = now or datetime.now(UTC)
        epoch_seconds = int(current.timestamp())
        window_index = epoch_seconds // policy.window_seconds
        redis_key = f"{policy.key_prefix}:{principal_key}:{window_index}"

        try:
            pipeline = self._client.pipeline()
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, policy.window_seconds, nx=True)
            count, _ = pipeline.execute()
        except redis.RedisError as error:
            raise RateLimiterUnavailableError(type(error).__name__) from error

        remaining = max(0, policy.limit - count)
        retry_after_seconds = policy.window_seconds - (epoch_seconds % policy.window_seconds)
        return RateLimitResult(
            allowed=count <= policy.limit,
            remaining=remaining,
            retry_after_seconds=retry_after_seconds,
        )
