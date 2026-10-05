"""Unit tests for the recent-authentication policy
(`tapjob_api.identity.service.require_recent_authentication`, docs/adr/0009-identity-auth-user-
account.md "sensitive action / recent auth")."""

from datetime import UTC, datetime, timedelta

import pytest

from tapjob_api.identity.firebase import VerifiedIdentity
from tapjob_api.identity.service import (
    MAX_CLOCK_SKEW_SECONDS,
    REAUTH_MAX_AGE_SECONDS,
    ReauthenticationRequiredError,
    is_reauthentication_recent,
    require_recent_authentication,
)

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _identity(auth_time: datetime) -> VerifiedIdentity:
    return VerifiedIdentity(
        subject="firebase-uid-1", email=None, email_verified=None, auth_time=auth_time
    )


def test_an_auth_time_at_the_current_instant_is_recent() -> None:
    identity = _identity(_NOW)
    assert is_reauthentication_recent(identity, now=_NOW) is True
    require_recent_authentication(identity, now=_NOW)  # must not raise


def test_an_auth_time_exactly_at_the_max_age_boundary_is_still_recent() -> None:
    identity = _identity(_NOW - timedelta(seconds=REAUTH_MAX_AGE_SECONDS))
    assert is_reauthentication_recent(identity, now=_NOW) is True


def test_an_auth_time_one_second_past_the_max_age_is_rejected() -> None:
    identity = _identity(_NOW - timedelta(seconds=REAUTH_MAX_AGE_SECONDS + 1))

    assert is_reauthentication_recent(identity, now=_NOW) is False
    with pytest.raises(ReauthenticationRequiredError):
        require_recent_authentication(identity, now=_NOW)


def test_an_old_auth_time_from_hours_ago_is_rejected() -> None:
    identity = _identity(_NOW - timedelta(hours=3))

    assert is_reauthentication_recent(identity, now=_NOW) is False


# -- future auth_time (2026 independent-review round 2 finding 6a) -------------------------------


def test_an_auth_time_exactly_at_the_future_clock_skew_boundary_is_still_recent() -> None:
    identity = _identity(_NOW + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS))
    assert is_reauthentication_recent(identity, now=_NOW) is True


def test_an_auth_time_one_second_beyond_the_future_clock_skew_boundary_is_rejected() -> None:
    identity = _identity(_NOW + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS + 1))

    assert is_reauthentication_recent(identity, now=_NOW) is False
    with pytest.raises(ReauthenticationRequiredError):
        require_recent_authentication(identity, now=_NOW)


def test_an_auth_time_far_in_the_future_is_rejected_not_treated_as_negative_age_recent() -> None:
    """Without the lower bound, `current - auth_time` would be a large negative number, which
    satisfies a naive `<= REAUTH_MAX_AGE_SECONDS` check -- this proves that bug is actually
    closed."""
    identity = _identity(_NOW + timedelta(hours=3))

    assert is_reauthentication_recent(identity, now=_NOW) is False
