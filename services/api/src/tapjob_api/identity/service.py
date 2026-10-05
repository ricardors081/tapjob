"""Use-case layer for WP1.1 (docs/adr/0009-identity-auth-user-account.md). Every function here is
plain, ownership-scoped SQLAlchemy over an already-authenticated `User` -- nothing here ever
accepts a caller-supplied `user_id`; the only `User` a caller can act on is the one resolved by
`tapjob_api.identity.dependencies.get_current_user` from a verified token (docs/adr/0009-...md
"current user / ownership").

Transaction convention: every function here defers the final `session.commit()` to its caller
(`tapjob_api.identity.router`), which stages the matching audit event first so both land in the
same transaction (docs/adr/0009-...md "audit transaction semantics") -- including `bootstrap_user`,
whose own concurrency retry uses a SAVEPOINT (`session.begin_nested()`), not a full
commit/rollback, specifically so a concurrent-insert conflict never forces an early, audit-less
commit of the new `User` row (2026 independent-review "account-created audit transaction").
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.identity.firebase import VerifiedIdentity
from tapjob_api.identity.models import (
    DEFAULT_TIMEZONE,
    AccountAuditEvent,
    AccountDeletionRequest,
    AuditOutcome,
    ConsentDecision,
    ConsentRecord,
    DeletionRequestStatus,
    DevicePlatform,
    ExternalIdentity,
    IdentityProvider,
    User,
    UserDevice,
)
from tapjob_product_config import get_product_config

# Security-sensitive actions (session revoke-all, deletion request/cancel) require the verified
# token's own `auth_time` to be recent, not merely the token itself to be currently valid
# (docs/adr/0009-...md "sensitive action / recent auth"). A named constant, not a literal repeated
# at each call site (docs/ENGINEERING_STANDARDS.md §2).
REAUTH_MAX_AGE_SECONDS = 15 * 60

# A future `auth_time` must never be treated as "recent" (2026 independent-review round 2 finding
# 6a): `current - auth_time` would otherwise be negative, which is `<= REAUTH_MAX_AGE_SECONDS` and
# would wrongly pass. The real Firebase-issued claim should never be meaningfully ahead of our
# clock; this is a small, explicit, documented allowance for ordinary clock skew between this
# server and Firebase/the client, not a general "trust future timestamps" policy -- anything
# further ahead than this is rejected, never silently accepted.
MAX_CLOCK_SKEW_SECONDS = 60


class ReauthenticationRequiredError(Exception):
    """Raised when a security-sensitive action's verified token is older than
    `REAUTH_MAX_AGE_SECONDS` (or its `auth_time` is implausibly in the future -- beyond
    `MAX_CLOCK_SKEW_SECONDS`). The caller must re-authenticate with the provider (mobile requests a
    fresh credential; TapJob never implements its own reauthentication) and retry with the
    resulting fresh ID token."""


def is_reauthentication_recent(identity: VerifiedIdentity, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    age_seconds = (current - identity.auth_time).total_seconds()
    return -MAX_CLOCK_SKEW_SECONDS <= age_seconds <= REAUTH_MAX_AGE_SECONDS


def require_recent_authentication(
    identity: VerifiedIdentity, *, now: datetime | None = None
) -> None:
    if not is_reauthentication_recent(identity, now=now):
        raise ReauthenticationRequiredError


def _find_firebase_identity(session: Session, *, subject: str) -> ExternalIdentity | None:
    return session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == IdentityProvider.FIREBASE,
            ExternalIdentity.provider_subject == subject,
        )
    )


def find_user_by_firebase_subject(session: Session, *, subject: str) -> User | None:
    """Resolves a verified Firebase UID to its linked TapJob `User`, or `None` if this identity has
    never called `bootstrap_user` (docs/adr/0009-...md "account bootstrap") -- the router maps
    `None` to `ACCOUNT_NOT_BOOTSTRAPPED`, never creating a `User` implicitly from this read path."""
    identity = _find_firebase_identity(session, subject=subject)
    return identity.user if identity is not None else None


def bootstrap_user(
    session: Session, *, identity: VerifiedIdentity, now: datetime | None = None
) -> tuple[User, bool]:
    """Resolves the TapJob `User` for a verified Firebase identity, creating one (plus its
    `ExternalIdentity` link) only if none exists yet. Concurrency-safe
    (docs/adr/0009-...md "concurrency"): `UNIQUE(provider, provider_subject)` is the actual source
    of truth, not this function's own read-then-write sequence -- two concurrent first logins for
    the same Firebase UID (each in its OWN session/transaction) race to insert, exactly one wins,
    and the loser re-queries the now-committed row instead of raising or creating a second `User`.
    The loser's conflict is caught at a SAVEPOINT (`session.begin_nested()`), not a full
    transaction rollback: only the speculative insert is undone, so the caller can still stage and
    commit an audit event in the SAME outer transaction regardless of which branch was taken.
    Never auto-links by email (docs/adr/0009-...md "never auto-link by email"): a match is found
    ONLY by `(provider, provider_subject)`, never by `email_snapshot`.

    Returns `(user, created)` -- `created` is `True` only when this call minted a new `User`. Never
    commits -- the caller commits once, after staging any audit event for this call's outcome.
    """
    current = now or datetime.now(UTC)

    existing = _find_firebase_identity(session, subject=identity.subject)
    if existing is not None:
        existing.last_seen_at = current
        existing.email_snapshot = identity.email
        existing.email_verified = identity.email_verified
        session.flush()
        return existing.user, False

    user = User(locale=get_product_config().default_locale, timezone=DEFAULT_TIMEZONE)
    external_identity = ExternalIdentity(
        user=user,
        provider=IdentityProvider.FIREBASE,
        provider_subject=identity.subject,
        email_snapshot=identity.email,
        email_verified=identity.email_verified,
    )
    try:
        with session.begin_nested():
            session.add(user)
            session.add(external_identity)
            session.flush()
    except IntegrityError:
        # Lost the create race to a concurrent bootstrap for the same subject. `begin_nested()`
        # already rolled back to the SAVEPOINT on this exception (the outer transaction is
        # otherwise untouched); the unique constraint guarantees the row now exists, so re-query it
        # rather than retrying the insert (an unbounded retry loop is never needed -- a second
        # concurrent CREATE for the same subject is structurally impossible once the constraint has
        # fired once).
        resolved = _find_firebase_identity(session, subject=identity.subject)
        if resolved is None:
            raise
        resolved.last_seen_at = current
        session.flush()
        return resolved.user, False
    return user, True


def get_firebase_subject(session: Session, *, user: User) -> str | None:
    """The user's own linked Firebase UID, if any -- `None` for a (future) channel-only user with
    no Firebase identity yet (docs/adr/0009-...md "identity invariant")."""
    identity = session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.user_id == user.id,
            ExternalIdentity.provider == IdentityProvider.FIREBASE,
        )
    )
    return identity.provider_subject if identity is not None else None


def update_user_settings(
    session: Session, *, user: User, locale: str | None, timezone: str | None
) -> User:
    if locale is not None:
        user.locale = locale
    if timezone is not None:
        user.timezone = timezone
    session.flush()
    return user


def register_device(
    session: Session,
    *,
    user: User,
    installation_id: str,
    platform: DevicePlatform,
    app_version: str | None,
    push_token: str | None,
    now: datetime | None = None,
) -> tuple[UserDevice, bool]:
    """Idempotent: re-registering the same `(user, installation_id)` refreshes metadata rather than
    creating a second row. Does NOT un-revoke an already-revoked device just because it
    re-registered -- revocation is a deliberate operator/user action, not something a client can
    silently undo by calling this endpoint again."""
    current = now or datetime.now(UTC)
    existing = session.scalar(
        select(UserDevice).where(
            UserDevice.user_id == user.id, UserDevice.installation_id == installation_id
        )
    )
    if existing is not None:
        existing.platform = platform
        existing.app_version = app_version
        existing.push_token = push_token
        existing.last_seen_at = current
        session.flush()
        return existing, False

    device = UserDevice(
        user_id=user.id,
        installation_id=installation_id,
        platform=platform,
        app_version=app_version,
        push_token=push_token,
    )
    session.add(device)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        resolved = session.scalar(
            select(UserDevice).where(
                UserDevice.user_id == user.id, UserDevice.installation_id == installation_id
            )
        )
        if resolved is None:
            raise
        return resolved, False
    return device, True


def find_owned_device(session: Session, *, user: User, device_id: object) -> UserDevice | None:
    """Ownership-scoped lookup: a device belonging to a different user is indistinguishable from a
    nonexistent one (docs/adr/0009-...md "ownership / IDOR testing") -- the router maps `None` to
    `DEVICE_NOT_FOUND` either way, never a separate "forbidden" response that would confirm the id
    exists."""
    return session.scalar(
        select(UserDevice).where(UserDevice.id == device_id, UserDevice.user_id == user.id)
    )


def revoke_device(
    session: Session, *, device: UserDevice, now: datetime | None = None
) -> UserDevice:
    device.revoked_at = device.revoked_at or (now or datetime.now(UTC))
    session.flush()
    return device


def record_consent(
    session: Session,
    *,
    user: User,
    purpose_key: str,
    policy_version: str,
    decision: ConsentDecision,
    source: str,
) -> ConsentRecord:
    """Always inserts a NEW row -- append-only, never an update of a prior decision
    (docs/adr/0009-...md "consent model")."""
    record = ConsentRecord(
        user_id=user.id,
        purpose_key=purpose_key,
        policy_version=policy_version,
        decision=decision,
        source=source,
    )
    session.add(record)
    session.flush()
    return record


def list_consents(session: Session, *, user: User) -> list[ConsentRecord]:
    return list(
        session.scalars(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == user.id)
            .order_by(ConsentRecord.recorded_at.desc())
        )
    )


def request_deletion(
    session: Session, *, user: User, now: datetime | None = None
) -> tuple[AccountDeletionRequest, bool]:
    """Idempotent: a second request while one is already `REQUESTED`/`PROCESSING` returns the
    existing row rather than creating a duplicate (the partial unique index on
    `account_deletion_requests` is the structural backstop, not just this read-before-write
    check)."""
    existing = session.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id,
            AccountDeletionRequest.status.in_(
                (DeletionRequestStatus.REQUESTED, DeletionRequestStatus.PROCESSING)
            ),
        )
    )
    if existing is not None:
        return existing, False

    request = AccountDeletionRequest(user_id=user.id)
    session.add(request)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        resolved = session.scalar(
            select(AccountDeletionRequest).where(
                AccountDeletionRequest.user_id == user.id,
                AccountDeletionRequest.status.in_(
                    (DeletionRequestStatus.REQUESTED, DeletionRequestStatus.PROCESSING)
                ),
            )
        )
        if resolved is None:
            raise
        return resolved, False
    return request, True


def get_latest_deletion_request(session: Session, *, user: User) -> AccountDeletionRequest | None:
    return session.scalar(
        select(AccountDeletionRequest)
        .where(AccountDeletionRequest.user_id == user.id)
        .order_by(AccountDeletionRequest.requested_at.desc())
        .limit(1)
    )


def cancel_deletion_request(
    session: Session, *, user: User, now: datetime | None = None
) -> AccountDeletionRequest | None:
    """Cancellation is allowed only while still `REQUESTED` -- once `PROCESSING` has begun,
    cancellation is refused (returns `None`, same as "nothing cancellable exists") rather than
    racing a deletion orchestrator that may already be acting on it (docs/adr/0009-...md "deletion
    tests").

    This is a single atomic conditional `UPDATE ... WHERE status = REQUESTED`, not a SELECT
    followed by a separate UPDATE (2026 independent-review round 2 finding 6b): a read-then-write
    here would race a future deletion orchestrator transitioning the SAME row to `PROCESSING`
    between this function's own read and write, letting a cancellation silently overwrite that
    transition. The `WHERE` clause is itself the concurrency guarantee -- the row is matched (and
    updated) only if it is STILL `REQUESTED` at the instant the `UPDATE` executes, atomically; a
    row that has already moved to `PROCESSING` (by this or a future caller) matches zero rows and
    this returns `None`, exactly like "nothing to cancel"."""
    result = session.execute(
        update(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.user_id == user.id,
            AccountDeletionRequest.status == DeletionRequestStatus.REQUESTED,
        )
        .values(status=DeletionRequestStatus.CANCELLED, cancelled_at=now or datetime.now(UTC))
        .returning(AccountDeletionRequest)
    )
    return result.scalars().one_or_none()


def record_audit_event(
    session: Session,
    *,
    user: User,
    event_type: str,
    outcome: AuditOutcome,
    metadata: dict[str, object] | None = None,
    correlation_id: str | None = None,
) -> AccountAuditEvent:
    """Caller-staged, not auto-committed (see module docstring): `metadata` must already be
    allowlisted, safe data -- never args/kwargs, never a token, never a raw request body
    (docs/SECURITY_PRIVACY.md)."""
    event = AccountAuditEvent(
        user_id=user.id,
        event_type=event_type,
        outcome=outcome,
        metadata_=metadata or {},
        correlation_id=correlation_id,
    )
    session.add(event)
    session.flush()
    return event
