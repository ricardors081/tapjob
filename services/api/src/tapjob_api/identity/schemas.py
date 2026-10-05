"""Request/response DTOs for `/api/v1/auth` and `/api/v1/me` (docs/adr/0009-identity-auth-user-
account.md). CamelCase wire format via explicit per-field `Field(alias=...)`, mirroring
`tapjob_api.http.pagination`'s convention -- not an `alias_generator`, so every wire field name is
reviewable at its own definition.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, field_validator

from tapjob_api.identity.models import (
    ConsentDecision,
    DeletionRequestStatus,
    DevicePlatform,
    UserStatus,
)

# Simplified BCP-47 (language + optional region) -- intentionally the same shape
# `tapjob_product_config.ProductConfig.default_locale` already validates against, so a user's own
# locale override is held to the same bar as the product default it starts from.
LOCALE_PATTERN = r"^[a-z]{2,3}(-[A-Z]{2})?$"

_VALID_TIMEZONES = available_timezones()


def _validate_timezone(value: str) -> str:
    if value not in _VALID_TIMEZONES:
        raise ValueError("timezone must be a valid IANA time zone name")
    return value


class UserResponse(BaseModel):
    model_config = {"populate_by_name": True}

    id: UUID
    status: UserStatus
    locale: str
    timezone: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class BootstrapResponse(BaseModel):
    model_config = {"populate_by_name": True}

    user: UserResponse
    # Whether THIS call created the User (vs. resolved an existing one) -- lets the mobile client
    # distinguish first-ever sign-up from a returning sign-in without a second round trip.
    created: bool


class UpdateUserSettingsRequest(BaseModel):
    model_config = {"populate_by_name": True}

    locale: str | None = Field(default=None, pattern=LOCALE_PATTERN)
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value) if value is not None else None


class DeviceRegistrationRequest(BaseModel):
    model_config = {"populate_by_name": True}

    # A TapJob-minted, privacy-preserving random identifier the app generates and persists --
    # never a hardware serial, IMEI or advertising ID (docs/adr/0009-...md "device registration").
    installation_id: str = Field(alias="installationId", min_length=8, max_length=64)
    platform: DevicePlatform
    app_version: str | None = Field(default=None, alias="appVersion", max_length=32)
    # Future FCM delivery, not wired to anything in WP1.1 -- stored, never sent, never logged.
    push_token: str | None = Field(default=None, alias="pushToken", max_length=4096)


class DeviceResponse(BaseModel):
    model_config = {"populate_by_name": True}

    id: UUID
    installation_id: str = Field(alias="installationId")
    platform: DevicePlatform
    app_version: str | None = Field(default=None, alias="appVersion")
    created_at: datetime = Field(alias="createdAt")
    last_seen_at: datetime = Field(alias="lastSeenAt")
    revoked_at: datetime | None = Field(default=None, alias="revokedAt")


class ConsentRecordRequest(BaseModel):
    model_config = {"populate_by_name": True}

    # No fixed purpose-key catalog is enforced here -- no final legal-purpose list is approved yet
    # (docs/OPEN_QUESTIONS.md); this is the recording infrastructure, not a legal conclusion.
    purpose_key: str = Field(alias="purposeKey", min_length=1, max_length=64)
    policy_version: str = Field(alias="policyVersion", min_length=1, max_length=32)
    decision: ConsentDecision


class ConsentRecordResponse(BaseModel):
    model_config = {"populate_by_name": True}

    id: UUID
    purpose_key: str = Field(alias="purposeKey")
    policy_version: str = Field(alias="policyVersion")
    decision: ConsentDecision
    recorded_at: datetime = Field(alias="recordedAt")


class DeletionRequestResponse(BaseModel):
    model_config = {"populate_by_name": True}

    id: UUID
    status: DeletionRequestStatus
    requested_at: datetime = Field(alias="requestedAt")
    cancelled_at: datetime | None = Field(default=None, alias="cancelledAt")


# Re-exported so a caller doesn't need to import `zoneinfo` just to pre-validate a
# device-suggested timezone before sending it.
def is_valid_timezone(value: str) -> bool:
    try:
        _validate_timezone(value)
    except ValueError:
        return False
    return True


__all__ = [
    "LOCALE_PATTERN",
    "BootstrapResponse",
    "ConsentRecordRequest",
    "ConsentRecordResponse",
    "DeletionRequestResponse",
    "DeviceRegistrationRequest",
    "DeviceResponse",
    "UpdateUserSettingsRequest",
    "UserResponse",
    "is_valid_timezone",
]
