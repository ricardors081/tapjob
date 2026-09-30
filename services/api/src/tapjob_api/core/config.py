"""Typed, validated environment configuration (docs/ENGINEERING_STANDARDS.md §3-5).

This is the only place backend code reads process environment variables. Settings are validated
when the process starts, so a missing or malformed required value fails fast.
"""

from enum import StrEnum
from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class RuntimeSettings(BaseSettings):
    """Settings every backend process needs.

    Values come from the process environment only: no implicit ``.env`` file loading, so there is
    no hidden local state. Docker Compose (or the deployment platform) injects the variables.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_env: Environment
    log_level: LogLevel = "INFO"


class ApiSettings(RuntimeSettings):
    """HTTP API and migration settings."""

    database_url: PostgresDsn
