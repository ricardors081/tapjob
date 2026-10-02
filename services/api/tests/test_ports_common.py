"""Invariants of the primitives every port shares
(docs/adr/0005-internal-ports-capability-model.md)."""

from datetime import UTC, datetime, timedelta

import pytest

from tapjob_api.ports import AdapterHealth, AdapterHealthStatus, CallContext, Deadline
from tapjob_api.ports._common import PortError, PortErrorCode

# -- Deadline --------------------------------------------------------------------------------


def test_deadline_requires_timezone_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Deadline(at=datetime(2026, 1, 1))  # noqa: DTZ001 (deliberately naive, to trigger the guard)


def test_deadline_after_requires_a_positive_budget() -> None:
    with pytest.raises(ValueError, match="positive budget"):
        Deadline.after(0)
    with pytest.raises(ValueError, match="positive budget"):
        Deadline.after(-1)


def test_deadline_after_is_in_the_future_relative_to_now() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    deadline = Deadline.after(30, now=now)

    assert deadline.at == now + timedelta(seconds=30)


def test_deadline_remaining_seconds_decreases_and_can_go_negative() -> None:
    deadline = Deadline(at=datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC))

    assert deadline.remaining_seconds(now=datetime(2026, 1, 1, tzinfo=UTC)) == 30.0
    assert deadline.remaining_seconds(now=datetime(2026, 1, 1, 0, 0, 40, tzinfo=UTC)) == -10.0


def test_deadline_is_expired_boundary() -> None:
    deadline = Deadline(at=datetime(2026, 1, 1, tzinfo=UTC))

    assert deadline.is_expired(now=datetime(2026, 1, 1, tzinfo=UTC)) is True
    assert deadline.is_expired(now=datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)) is False


# -- CallContext ------------------------------------------------------------------------------


def test_call_context_with_timeout_builds_a_consistent_deadline() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ctx = CallContext.with_timeout("corr-1", 5, now=now)

    assert ctx.correlation_id == "corr-1"
    assert ctx.deadline.at == now + timedelta(seconds=5)


def test_call_context_is_immutable() -> None:
    ctx = CallContext.with_timeout("corr-1", 5, now=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(AttributeError):
        ctx.correlation_id = "corr-2"  # type: ignore[misc]


# -- PortError / PortErrorCode ----------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        PortErrorCode.TIMEOUT,
        PortErrorCode.UNAVAILABLE,
        PortErrorCode.RATE_LIMITED,
        PortErrorCode.PROVIDER_ERROR,
    ],
)
def test_default_transient_codes_are_transient_unless_overridden(code: PortErrorCode) -> None:
    error = PortError(code, provider="fake", message="boom")
    assert error.transient is True


@pytest.mark.parametrize(
    "code",
    [
        PortErrorCode.AUTH_FAILED,
        PortErrorCode.INVALID_REQUEST,
        PortErrorCode.UNSUPPORTED_CAPABILITY,
        PortErrorCode.OUTCOME_UNKNOWN,
    ],
)
def test_default_non_transient_codes_are_not_transient_unless_overridden(
    code: PortErrorCode,
) -> None:
    error = PortError(code, provider="fake", message="boom")
    assert error.transient is False


def test_transient_can_be_explicitly_overridden() -> None:
    error = PortError(PortErrorCode.TIMEOUT, provider="fake", message="boom", transient=False)
    assert error.transient is False


def test_outcome_unknown_is_preserved_as_its_own_code_not_folded_into_unavailable() -> None:
    error = PortError(PortErrorCode.OUTCOME_UNKNOWN, provider="fake", message="ambiguous")
    assert error.code is PortErrorCode.OUTCOME_UNKNOWN


def test_port_error_details_never_defaults_to_a_mutable_shared_dict() -> None:
    first = PortError(PortErrorCode.PROVIDER_ERROR, provider="fake", message="x")
    second = PortError(PortErrorCode.PROVIDER_ERROR, provider="fake", message="y")

    assert first.details == {}
    assert first.details is not second.details


# -- AdapterHealth / ResultMetadata ------------------------------------------------------------


def test_adapter_health_statuses_are_distinct() -> None:
    assert (
        len(
            {
                AdapterHealthStatus.HEALTHY,
                AdapterHealthStatus.DEGRADED,
                AdapterHealthStatus.UNAVAILABLE,
            }
        )
        == 3
    )


def test_adapter_health_is_frozen() -> None:
    health = AdapterHealth(
        status=AdapterHealthStatus.HEALTHY, checked_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    with pytest.raises(AttributeError):
        health.status = AdapterHealthStatus.DEGRADED  # type: ignore[misc]
