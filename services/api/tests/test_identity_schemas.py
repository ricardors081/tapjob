"""Unit tests for `tapjob_api.identity.schemas` validation rules."""

import pytest
from pydantic import ValidationError

from tapjob_api.identity.models import DevicePlatform
from tapjob_api.identity.schemas import (
    DeviceRegistrationRequest,
    UpdateUserSettingsRequest,
    is_valid_timezone,
)


def test_valid_locale_passes() -> None:
    body = UpdateUserSettingsRequest.model_validate({"locale": "pt-BR"})
    assert body.locale == "pt-BR"


def test_bare_language_locale_passes() -> None:
    body = UpdateUserSettingsRequest.model_validate({"locale": "en"})
    assert body.locale == "en"


@pytest.mark.parametrize("bad_locale", ["PT-br", "pt_BR", "portuguese", "", "pt-BRA"])
def test_malformed_locale_is_rejected(bad_locale: str) -> None:
    with pytest.raises(ValidationError):
        UpdateUserSettingsRequest.model_validate({"locale": bad_locale})


def test_valid_iana_timezone_passes() -> None:
    body = UpdateUserSettingsRequest.model_validate({"timezone": "America/Sao_Paulo"})
    assert body.timezone == "America/Sao_Paulo"


@pytest.mark.parametrize("bad_timezone", ["Mars/Phobos", "not-a-timezone", "", "GMT+25"])
def test_invalid_timezone_is_rejected(bad_timezone: str) -> None:
    with pytest.raises(ValidationError):
        UpdateUserSettingsRequest.model_validate({"timezone": bad_timezone})


def test_is_valid_timezone_helper_matches_schema_validation() -> None:
    assert is_valid_timezone("Etc/UTC") is True
    assert is_valid_timezone("not-a-real-zone") is False


def test_omitting_both_fields_is_valid_a_partial_update() -> None:
    body = UpdateUserSettingsRequest.model_validate({})
    assert body.locale is None
    assert body.timezone is None


def test_device_registration_accepts_camelcase_wire_fields() -> None:
    body = DeviceRegistrationRequest.model_validate(
        {
            "installationId": "a" * 32,
            "platform": "ios",
            "appVersion": "1.2.3",
            "pushToken": None,
        }
    )
    assert body.installation_id == "a" * 32
    assert body.platform is DevicePlatform.IOS
    assert body.app_version == "1.2.3"


@pytest.mark.parametrize("installation_id", ["short", "a" * 65])
def test_device_registration_rejects_out_of_bounds_installation_id(installation_id: str) -> None:
    with pytest.raises(ValidationError):
        DeviceRegistrationRequest.model_validate(
            {"installationId": installation_id, "platform": "android"}
        )


def test_device_registration_rejects_unknown_platform() -> None:
    with pytest.raises(ValidationError):
        DeviceRegistrationRequest.model_validate(
            {"installationId": "a" * 32, "platform": "windows-phone"}
        )
