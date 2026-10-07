"""FastAPI application factory.

Run with ``uvicorn --factory tapjob_api.main:create_app`` so settings are loaded (and validated) at
process start rather than at import time.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

import redis
from fastapi import FastAPI

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.core.database import create_database_engine
from tapjob_api.core.logging import configure_logging
from tapjob_api.core.observability import init_sentry
from tapjob_api.core.rate_limit import RedisFixedWindowRateLimiter
from tapjob_api.core.tasks import create_task_producer
from tapjob_api.db.session import create_session_factory
from tapjob_api.http.api_v1 import router as api_v1_router
from tapjob_api.http.errors import register_exception_handlers
from tapjob_api.http.health import router as health_router
from tapjob_api.http.readiness import router as readiness_router
from tapjob_api.http.request_context import RequestContextMiddleware
from tapjob_api.identity.firebase import FirebaseIdentityProvider, create_firebase_app
from tapjob_api.resume_import.storage import ObjectStoragePort, create_storage_adapter
from tapjob_product_config import get_product_config

SERVICE_NAME = "api"
DISTRIBUTION_NAME = "tapjob-api"
OPENAPI_PATH = "/openapi.json"


def create_app(
    settings: ApiSettings | None = None, *, resume_storage_override: ObjectStoragePort | None = None
) -> FastAPI:
    """`resume_storage_override` is the one, explicit dependency-injection point a caller uses to
    supply a test-only storage adapter (e.g. `InMemoryObjectStorage`) directly -- round-2
    independent review "Finding 5": `create_storage_adapter` itself never implicitly falls back to
    one any more, so a test process that wants one must ask for it explicitly, here, rather than
    relying on a runtime fallback a real deployment could accidentally hit too."""
    settings = settings or ApiSettings()
    configure_logging(level=settings.log_level, service=SERVICE_NAME, environment=settings.app_env)
    init_sentry(settings, service=SERVICE_NAME)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One pooled engine per process, reused by /ready and every WP1.1 identity route.
        app.state.db_engine = create_database_engine(
            str(settings.database_url),
            connect_timeout_seconds=settings.readiness_timeout_seconds,
        )
        app.state.db_session_factory = create_session_factory(app.state.db_engine)

        # One pooled Redis client for the rate limiter (docs/adr/0009-identity-auth-user-
        # account.md "rate limit"), separate from readiness.py's own short-lived probe client.
        app.state.rate_limiter = RedisFixedWindowRateLimiter(
            redis.Redis.from_url(str(settings.redis_url))
        )

        # WP1.3 (docs/adr/0011-resume-import.md): one storage adapter and one Celery PRODUCER per
        # process, same "construct once, reuse" discipline as every other process-scoped resource
        # above. The API process never runs a worker task itself -- it only enqueues by name.
        app.state.resume_storage = (
            resume_storage_override
            if resume_storage_override is not None
            else create_storage_adapter(settings)
        )
        app.state.celery_producer = create_task_producer(str(settings.redis_url))

        # Staging/production fail fast at process start if Application Default Credentials are
        # not configured (docs/adr/0009-...md "Firebase config server-side") -- local/development
        # and every test leave this None, so no real Firebase project is ever contacted
        # (tapjob_api.identity.dependencies falls back to a lazy singleton only when a route
        # actually needs it, which tests override via app.dependency_overrides instead).
        app.state.identity_verifier = (
            FirebaseIdentityProvider(create_firebase_app())
            if settings.app_env in (Environment.STAGING, Environment.PRODUCTION)
            else None
        )
        try:
            yield
        finally:
            app.state.db_engine.dispose()
            app.state.rate_limiter.close()

    # Interactive schema/docs are a development aid; production does not expose them.
    expose_openapi = settings.app_env is not Environment.PRODUCTION
    app_version = version(DISTRIBUTION_NAME)
    app = FastAPI(
        title=f"{get_product_config().product_name} API",
        version=app_version,
        openapi_url=OPENAPI_PATH if expose_openapi else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.service_name = SERVICE_NAME
    app.state.version = app_version

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(readiness_router)
    app.include_router(api_v1_router)
    return app
