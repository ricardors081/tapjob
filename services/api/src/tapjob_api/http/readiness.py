"""Readiness endpoint: probes PostgreSQL and Redis so an orchestrator can route traffic only to
instances that can actually serve it. ``/health`` (liveness) intentionally does not do this.

Each dependency is checked with a bounded timeout and reported as ok/unavailable only: the
response body and logs never include a DSN, host or exception message, just the dependency name
and the failure's exception class (docs/ENGINEERING_STANDARDS.md §18, AGENTS.md §8).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from enum import StrEnum
from http import HTTPStatus

import redis
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import Engine, text

READY_PATH = "/ready"

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class DependencyName(StrEnum):
    POSTGRESQL = "postgresql"
    REDIS = "redis"


class DependencyStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


class ReadinessStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class ReadinessResponse(BaseModel):
    status: ReadinessStatus
    service: str
    dependencies: dict[DependencyName, DependencyStatus]


def _ping_postgresql(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _ping_redis(redis_url: str, *, timeout_seconds: float) -> None:
    client = redis.Redis.from_url(
        redis_url, socket_connect_timeout=timeout_seconds, socket_timeout=timeout_seconds
    )
    try:
        client.ping()
    finally:
        client.close()


async def _check_dependency(
    name: DependencyName, probe: Callable[[], None], *, timeout_seconds: float
) -> tuple[DependencyName, DependencyStatus]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(probe), timeout=timeout_seconds)
    except Exception as error:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.warning(
            "readiness dependency check failed",
            extra={
                "dependency": name,
                "duration_ms": duration_ms,
                "error_type": type(error).__name__,
            },
        )
        return name, DependencyStatus.UNAVAILABLE
    return name, DependencyStatus.OK


@router.get(READY_PATH, response_model=ReadinessResponse)
async def get_readiness(request: Request, response: Response) -> ReadinessResponse:
    settings = request.app.state.settings
    timeout_seconds = settings.readiness_timeout_seconds

    results = dict(
        await asyncio.gather(
            _check_dependency(
                DependencyName.POSTGRESQL,
                lambda: _ping_postgresql(request.app.state.db_engine),
                timeout_seconds=timeout_seconds,
            ),
            _check_dependency(
                DependencyName.REDIS,
                lambda: _ping_redis(str(settings.redis_url), timeout_seconds=timeout_seconds),
                timeout_seconds=timeout_seconds,
            ),
        )
    )

    is_ready = all(status is DependencyStatus.OK for status in results.values())
    response.status_code = HTTPStatus.OK if is_ready else HTTPStatus.SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status=ReadinessStatus.OK if is_ready else ReadinessStatus.DEGRADED,
        service=request.app.state.service_name,
        dependencies=results,
    )
