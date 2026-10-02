"""Primitives shared by every internal port (docs/adr/0005-internal-ports-capability-model.md).

Kept intentionally small: a port receives a `CallContext` (correlation + deadline) and raises
`PortError` (a normalized, provider-neutral failure) rather than leaking a provider SDK's own
exception types or shapes across the boundary (docs/ENGINEERING_STANDARDS.md §13-14).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum


def require_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    """The one normalization rule every timestamp field in a port DTO applies
    (docs/ENGINEERING_STANDARDS.md §10): reject a naive datetime outright -- never silently assume
    a timezone -- and normalize an aware-but-non-UTC datetime to UTC, so every normalized port
    timestamp is directly comparable without a caller re-converting it first.

    A datetime is aware, per Python's own definition, only when BOTH `tzinfo is not None` AND
    `tzinfo.utcoffset(value) is not None` -- a `tzinfo` object that returns `None` from
    `utcoffset()` makes the datetime naive/ambiguous despite `tzinfo` being set, so checking
    `tzinfo is None` alone is not sufficient."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware (docs/ENGINEERING_STANDARDS.md §10)")
    return value.astimezone(UTC)


def normalize_utc_field(instance: object, field_name: str, *, optional: bool = False) -> None:
    """Call from a frozen dataclass's `__post_init__` to validate+normalize one datetime field to
    UTC in place. Centralizes the `object.__setattr__` frozen-dataclass workaround so it is not
    repeated at every call site across every port module."""
    value = getattr(instance, field_name)
    if value is None:
        if optional:
            return
        raise ValueError(f"{field_name} must not be None")
    object.__setattr__(instance, field_name, require_utc_datetime(value, field_name=field_name))


@dataclass(frozen=True, slots=True)
class Deadline:
    """An absolute point in time by which a call must complete or be abandoned.

    Deliberately NOT a retry policy: a `CallContext` carries at most one deadline, covering the
    caller's whole operation (including any retries the caller itself performs). An adapter
    decides, within whatever budget remains, whether and how to attempt the call -- it does not
    own a separate retry-count/backoff concept here.
    """

    at: datetime

    def __post_init__(self) -> None:
        normalize_utc_field(self, "at")

    @classmethod
    def after(cls, seconds: float, *, now: datetime | None = None) -> "Deadline":
        if seconds <= 0:
            raise ValueError("Deadline.after requires a positive budget in seconds")
        base = now if now is not None else datetime.now(UTC)
        return cls(at=base + timedelta(seconds=seconds))

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        base = now if now is not None else datetime.now(UTC)
        return (self.at - base).total_seconds()

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return self.remaining_seconds(now=now) <= 0


@dataclass(frozen=True, slots=True)
class CallContext:
    """Cross-cutting, provider-neutral context passed into every port call. Carries only
    justified cross-cutting concerns -- never business/domain data (docs/ENGINEERING_STANDARDS.md
    §2: that belongs to the request DTO, not here)."""

    correlation_id: str
    deadline: Deadline

    @classmethod
    def with_timeout(
        cls, correlation_id: str, timeout_seconds: float, *, now: datetime | None = None
    ) -> "CallContext":
        return cls(correlation_id=correlation_id, deadline=Deadline.after(timeout_seconds, now=now))


class PortErrorCode(StrEnum):
    """Minimal, provider-neutral failure taxonomy shared by every port. An adapter translates
    whatever its provider/SDK actually raised into one of these -- the provider's own exception
    type/message never crosses the boundary as domain logic input."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    # The external side effect may or may not have happened and cannot be proven either way (e.g.
    # a submission request timed out after the provider may already have received it). Callers
    # must never treat this as success, and must not blindly retry a non-idempotent operation on
    # the strength of this code alone (docs/APPLY_ENGINE.md §8-9).
    OUTCOME_UNKNOWN = "outcome_unknown"
    # Catch-all for a real provider-side failure that does not fit a more specific code above --
    # including a provider's structured response failing this caller's own output validation
    # (docs/adr/0005-internal-ports-capability-model.md §7: AiGateway never trusts a cast alone).
    PROVIDER_ERROR = "provider_error"


# Whether a code is transient by default (safe to consider retryable) absent a more specific
# signal from the adapter. A caller may still override this per-instance when the adapter knows
# better (e.g. a provider that reports this specific failure as permanent).
_TRANSIENT_BY_DEFAULT: frozenset[PortErrorCode] = frozenset(
    {
        PortErrorCode.TIMEOUT,
        PortErrorCode.UNAVAILABLE,
        PortErrorCode.RATE_LIMITED,
        PortErrorCode.PROVIDER_ERROR,
    }
)


class PortError(Exception):
    """Raised by any port implementation for a boundary failure. `details` is for safe,
    already-redacted diagnostics only -- never a raw provider exception message, stack trace or
    secret (AGENTS.md §8, docs/ENGINEERING_STANDARDS.md §18)."""

    def __init__(
        self,
        code: PortErrorCode,
        *,
        provider: str,
        message: str,
        transient: bool | None = None,
        retry_after_seconds: float | None = None,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.message = message
        self.transient = transient if transient is not None else code in _TRANSIENT_BY_DEFAULT
        self.retry_after_seconds = retry_after_seconds
        self.details: Mapping[str, str] = dict(details) if details else {}


class AdapterHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    """Reports the adapter's OWN operational health -- never conflated with product-facing
    `CapabilityStatus` (capabilities.py): health is "can we currently reach this provider at all",
    capability is "does this provider support this operation in principle". `/ready`
    (docs/adr/0002-mobile-foundation.md) only probes TapJob's own infrastructure and never calls
    this; a future health-aggregation surface, if any, is a separate concern from this
    checkpoint."""

    status: AdapterHealthStatus
    checked_at: datetime
    reason_code: str | None = None

    def __post_init__(self) -> None:
        normalize_utc_field(self, "checked_at")


@dataclass(frozen=True, slots=True)
class ResultMetadata:
    """Small, generic provenance a port result may carry when it originated from (or was
    processed by) an external provider. Not every port method needs one; a port composes this in
    only where provenance genuinely matters, instead of every DTO inventing its own fields."""

    provider: str
    observed_at: datetime
    correlation_id: str | None = None
    external_ref: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalize_utc_field(self, "observed_at")
