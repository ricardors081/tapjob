import pytest
from pydantic import ValidationError

from tapjob_api.core.config import Environment
from tapjob_worker.celery_app import create_celery_app
from tapjob_worker.config import WorkerSettings

REDIS_URL = "redis://localhost:6379/0"
DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/test"


def _settings() -> WorkerSettings:
    return WorkerSettings(
        app_env=Environment.LOCAL,
        redis_url=REDIS_URL,  # type: ignore[arg-type]
        database_url=DATABASE_URL,  # type: ignore[arg-type]
        # `create_storage_adapter` fails fast in local/development with neither R2 nor a shared
        # local directory configured (round-2 independent review "Finding 5") -- these tests only
        # exercise `create_celery_app`'s own Celery config, not real storage I/O, so a dummy
        # directory value is enough to satisfy the config gate.
        resume_import_local_storage_dir="/tmp/resume-imports-test",  # noqa: S108 -- never touched
    )


def test_celery_app_uses_configured_broker() -> None:
    app = create_celery_app(_settings())

    assert app.conf.broker_url == REDIS_URL


def test_celery_app_only_accepts_json_payloads() -> None:
    conf = create_celery_app(_settings()).conf

    assert conf.task_serializer == "json"
    assert list(conf.accept_content) == ["json"]


def test_celery_app_prefers_redelivery_over_task_loss() -> None:
    conf = create_celery_app(_settings()).conf

    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1


def test_worker_settings_require_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError):
        WorkerSettings()


def test_celery_app_configures_periodic_beat_schedule() -> None:
    from tapjob_api.jobs.tasks import (
        DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS,
        SWEEP_SCHEDULED_INGESTION_TASK_NAME,
    )

    app = create_celery_app(_settings())
    beat_conf = app.conf.beat_schedule
    assert "jobs-scheduled-ingestion-sweep" in beat_conf
    entry = beat_conf["jobs-scheduled-ingestion-sweep"]
    assert entry["task"] == SWEEP_SCHEDULED_INGESTION_TASK_NAME
    assert entry["schedule"] == float(DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS)


def test_celery_app_loads_real_registry_from_worker_settings() -> None:
    from unittest.mock import patch

    sources_json = """
    [
        {"provider": "greenhouse", "token": "acme", "company_name": "Acme Corp"},
        {"provider": "lever", "token": "netflix", "company_name": "Netflix"}
    ]
    """
    settings = WorkerSettings(
        app_env=Environment.LOCAL,
        redis_url=REDIS_URL,  # type: ignore[arg-type]
        database_url=DATABASE_URL,  # type: ignore[arg-type]
        resume_import_local_storage_dir="/tmp/resume-imports-test",  # noqa: S108
        hosted_ats_sources=sources_json,
    )

    with patch("tapjob_worker.celery_app.configure_jobs_worker") as mock_configure:
        create_celery_app(settings)
        assert mock_configure.called
        kwargs = mock_configure.call_args.kwargs
        registry = kwargs["registry"]
        assert len(registry.list_all()) == 2
        assert len(registry.list_active()) == 2
        assert registry.get("greenhouse:acme") is not None
        assert registry.get("lever:netflix") is not None


def test_celery_app_fails_closed_on_invalid_hosted_ats_sources() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    bad_settings = WorkerSettings(
        app_env=Environment.LOCAL,
        redis_url=REDIS_URL,  # type: ignore[arg-type]
        database_url=DATABASE_URL,  # type: ignore[arg-type]
        resume_import_local_storage_dir="/tmp/resume-imports-test",  # noqa: S108
        hosted_ats_sources='[{"provider": "invalid_ats", "token": "foo", "company_name": "Bar"}]',
    )
    with pytest.raises(JobSourceConfigError):
        create_celery_app(bad_settings)
