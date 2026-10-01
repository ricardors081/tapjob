import pytest
from pydantic import ValidationError

from tapjob_api.core.config import ApiSettings, Environment


@pytest.fixture(autouse=True)
def valid_environment(monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("READINESS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_URL", redis_url)


def test_settings_are_read_from_environment(database_url: str, redis_url: str) -> None:
    settings = ApiSettings()

    assert settings.app_env is Environment.STAGING
    assert str(settings.database_url) == database_url
    assert str(settings.redis_url) == redis_url
    assert settings.log_level == "INFO"


def test_sentry_and_readiness_timeout_have_safe_defaults() -> None:
    settings = ApiSettings()

    assert settings.sentry_dsn is None
    assert settings.sentry_traces_sample_rate == 0.0
    assert settings.readiness_timeout_seconds > 0


def test_sentry_dsn_and_readiness_timeout_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "5")

    settings = ApiSettings()

    assert settings.sentry_dsn == "https://public@o0.ingest.sentry.io/1"
    assert settings.sentry_traces_sample_rate == 0.1
    assert settings.readiness_timeout_seconds == 5.0


@pytest.mark.parametrize("missing", ["APP_ENV", "DATABASE_URL", "REDIS_URL"])
def test_missing_required_setting_fails_fast(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        ApiSettings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "prod"),
        ("DATABASE_URL", "mysql://tapjob@localhost/tapjob"),
        ("REDIS_URL", "mysql://tapjob@localhost/tapjob"),
        ("LOG_LEVEL", "VERBOSE"),
        ("SENTRY_TRACES_SAMPLE_RATE", "1.5"),
        ("READINESS_TIMEOUT_SECONDS", "0"),
    ],
)
def test_invalid_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        ApiSettings()
