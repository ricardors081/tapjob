"""Liveness endpoint. It reports that the process is up; it does not probe dependencies."""

from enum import StrEnum

from fastapi import APIRouter, Request
from pydantic import BaseModel

from tapjob_api.core.config import Environment
from tapjob_api.http.errors import COMMON_OPERATIONAL_ERROR_RESPONSES

HEALTH_PATH = "/health"


class HealthStatus(StrEnum):
    OK = "ok"


class HealthResponse(BaseModel):
    status: HealthStatus
    service: str
    version: str
    environment: Environment


# `responses` set once here (not per-route) so a future operational endpoint added to this router
# inherits the documented 500/ErrorResponse case automatically instead of each route repeating it.
router = APIRouter(tags=["health"], responses=COMMON_OPERATIONAL_ERROR_RESPONSES)


@router.get(HEALTH_PATH, response_model=HealthResponse, operation_id="getHealth")
def get_health(request: Request) -> HealthResponse:
    state = request.app.state
    return HealthResponse(
        status=HealthStatus.OK,
        service=state.service_name,
        version=state.version,
        environment=state.settings.app_env,
    )
