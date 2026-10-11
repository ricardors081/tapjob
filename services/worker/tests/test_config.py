"""Unit tests for WorkerSettings configuration and fail-fast validation (Finding 6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tapjob_api.core.config import Environment
from tapjob_worker.config import WorkerSettings


@pytest.fixture(autouse=True)
def base_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/tapjob")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


def test_worker_settings_development_allows_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    settings = WorkerSettings()
    assert settings.app_env is Environment.DEVELOPMENT
    assert settings.evolution_api_url == "http://localhost:8080"
    assert settings.evolution_api_key == "test-key"
    assert settings.evolution_instance_name == "tapjob"


def test_worker_settings_production_rejects_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EVOLUTION_API_KEY", "real-prod-key")
    monkeypatch.setenv("EVOLUTION_INSTANCE_NAME", "real-instance")
    monkeypatch.setenv("EVOLUTION_API_URL", "http://localhost:8080")

    with pytest.raises(ValidationError, match="evolution_api_url cannot point to localhost"):
        WorkerSettings()


def test_worker_settings_production_rejects_placeholder_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EVOLUTION_API_URL", "https://evolution.prod.internal")
    monkeypatch.setenv("EVOLUTION_INSTANCE_NAME", "real-instance")
    monkeypatch.setenv("EVOLUTION_API_KEY", "test-key")

    with pytest.raises(ValidationError, match="evolution_api_key cannot use default"):
        WorkerSettings()


def test_worker_settings_production_rejects_default_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EVOLUTION_API_URL", "https://evolution.prod.internal")
    monkeypatch.setenv("EVOLUTION_API_KEY", "real-prod-key")
    monkeypatch.setenv("EVOLUTION_INSTANCE_NAME", "tapjob")

    with pytest.raises(
        ValidationError, match="evolution_instance_name must be explicitly configured"
    ):
        WorkerSettings()


def test_worker_settings_production_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EVOLUTION_API_URL", "https://evolution.prod.internal")
    monkeypatch.setenv("EVOLUTION_API_KEY", "real-prod-key")
    monkeypatch.setenv("EVOLUTION_INSTANCE_NAME", "tapjob-prod-cluster")

    settings = WorkerSettings()
    assert settings.app_env is Environment.PRODUCTION
    assert settings.evolution_api_url == "https://evolution.prod.internal"
    assert settings.evolution_api_key == "real-prod-key"
    assert settings.evolution_instance_name == "tapjob-prod-cluster"
