import pytest
from pydantic import ValidationError

from tapjob_api.core.config import ApiSettings, Environment


@pytest.fixture(autouse=True)
def valid_environment(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", database_url)


def test_settings_are_read_from_environment(database_url: str) -> None:
    settings = ApiSettings()

    assert settings.app_env is Environment.STAGING
    assert str(settings.database_url) == database_url
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("missing", ["APP_ENV", "DATABASE_URL"])
def test_missing_required_setting_fails_fast(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        ApiSettings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "prod"),
        ("DATABASE_URL", "mysql://tapjob@localhost/tapjob"),
        ("LOG_LEVEL", "VERBOSE"),
    ],
)
def test_invalid_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        ApiSettings()
