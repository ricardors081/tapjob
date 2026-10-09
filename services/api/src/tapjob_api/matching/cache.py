"""Redis caching and pre-cache visibility revalidation guard for WP2.2 (ADR-0014 Decision 8).

PostgreSQL is the canonical store of truth. Redis is transient cache (24h TTL).
Stale cache for closed, unavailable, expired or inaccessible jobs is immediately evicted.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any, cast

import redis

from tapjob_api.jobs.models import Job, JobStatus

logger = logging.getLogger(__name__)

MATCH_CACHE_TTL_SECONDS: int = 86400  # 24 hours


def build_match_cache_key(
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    profile_version: int,
    job_match_fingerprint: str,
    policy_version: str,
    evaluation_as_of_date: date,
) -> str:
    """Canonical Redis cache key format per ADR-0014."""
    return (
        f"match:{user_id}:{job_id}:{profile_version}:"
        f"{job_match_fingerprint}:{policy_version}:{evaluation_as_of_date.isoformat()}"
    )


def get_cached_match(
    redis_client: redis.Redis | None,
    key: str,
    job: Job,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Retrieves cached match result with pre-cache liveness and visibility revalidation.

    If the job is closed, expired, or private to another user, evicts the cache and returns None.
    """
    if redis_client is None:
        return None

    current_time = now or datetime.now(UTC)

    # 1. Ownership / Visibility Guard
    if job.created_by_user_id is not None and job.created_by_user_id != user_id:
        with contextlib.suppress(Exception):
            redis_client.delete(key)
        return None

    # 2. Liveness Guard
    if job.status not in (JobStatus.ACTIVE, JobStatus.UNKNOWN):
        with contextlib.suppress(Exception):
            redis_client.delete(key)
        return None

    if job.expires_at is not None and job.expires_at <= current_time:
        with contextlib.suppress(Exception):
            redis_client.delete(key)
        return None

    # 3. Read from Redis
    try:
        raw_val = redis_client.get(key)
    except Exception as exc:
        logger.warning("Redis cache read failed, failing open to database: %s", exc)
        return None

    if raw_val is None:
        return None

    try:
        raw_str = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else str(raw_val)
        parsed = json.loads(raw_str)
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)
        return None
    except Exception as exc:
        logger.warning("Corrupted Redis cache payload, evicting key %s: %s", key, exc)
        with contextlib.suppress(Exception):
            redis_client.delete(key)
        return None


def set_cached_match(
    redis_client: redis.Redis | None,
    key: str,
    payload: dict[str, Any],
    *,
    ttl_seconds: int = MATCH_CACHE_TTL_SECONDS,
) -> None:
    """Sets cached match result in Redis with 24h TTL."""
    if redis_client is None:
        return

    try:
        serialized = json.dumps(payload, separators=(",", ":"))
        redis_client.setex(key, ttl_seconds, serialized)
    except Exception as exc:
        logger.warning("Redis cache write failed: %s", exc)
