"""FastAPI application factory.

Run with ``uvicorn --factory tapjob_api.main:create_app`` so settings are loaded (and validated) at
process start rather than at import time.
"""

from importlib.metadata import version

from fastapi import FastAPI

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.core.logging import configure_logging
from tapjob_api.http.health import router as health_router
from tapjob_api.http.request_context import RequestContextMiddleware
from tapjob_product_config import get_product_config

SERVICE_NAME = "api"
DISTRIBUTION_NAME = "tapjob-api"
OPENAPI_PATH = "/openapi.json"


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    configure_logging(level=settings.log_level, service=SERVICE_NAME, environment=settings.app_env)

    # Interactive schema/docs are a development aid; production does not expose them.
    expose_openapi = settings.app_env is not Environment.PRODUCTION
    app_version = version(DISTRIBUTION_NAME)
    app = FastAPI(
        title=f"{get_product_config().product_name} API",
        version=app_version,
        openapi_url=OPENAPI_PATH if expose_openapi else None,
    )
    app.state.settings = settings
    app.state.service_name = SERVICE_NAME
    app.state.version = app_version

    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    return app
