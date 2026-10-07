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
