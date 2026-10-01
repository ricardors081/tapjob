import pytest

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.core.observability import init_sentry


def test_init_sentry_is_a_noop_without_a_dsn(
    monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    settings = ApiSettings(
        app_env=Environment.LOCAL,
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=redis_url,  # type: ignore[arg-type]
    )
    init_sentry(settings, service="api")

    assert calls == []


def test_init_sentry_initializes_the_sdk_when_a_dsn_is_configured(
    monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    settings = ApiSettings(
        app_env=Environment.STAGING,
        database_url=database_url,  # type: ignore[arg-type]
        redis_url=redis_url,  # type: ignore[arg-type]
        sentry_dsn="https://public@o0.ingest.sentry.io/1",
        sentry_traces_sample_rate=0.25,
    )
    init_sentry(settings, service="api")

    assert len(calls) == 1
    call = calls[0]
    assert call["dsn"] == "https://public@o0.ingest.sentry.io/1"
    assert call["environment"] == Environment.STAGING
    assert call["server_name"] == "api"
    assert call["traces_sample_rate"] == 0.25
    assert call["send_default_pii"] is False
