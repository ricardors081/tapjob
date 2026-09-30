"""Liveness endpoint. It reports that the process is up; it does not probe dependencies."""

from enum import StrEnum

from fastapi import APIRouter, Request
from pydantic import BaseModel

from tapjob_api.core.config import Environment

HEALTH_PATH = "/health"


class HealthStatus(StrEnum):
    OK = "ok"


class HealthResponse(BaseModel):
    status: HealthStatus
    service: str
    version: str
    environment: Environment


router = APIRouter(tags=["health"])


@router.get(HEALTH_PATH, response_model=HealthResponse)
def get_health(request: Request) -> HealthResponse:
    state = request.app.state
    return HealthResponse(
        status=HealthStatus.OK,
        service=state.service_name,
        version=state.version,
        environment=state.settings.app_env,
    )
