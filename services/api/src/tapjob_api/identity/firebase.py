"""Firebase Admin integration boundary (docs/adr/0009-identity-auth-user-account.md §"Firebase
backend adapter"). This is the ONLY module allowed to import `firebase_admin` -- the SDK never
leaks into the service/route layer, which depends on `IdentityTokenVerifier` (a `Protocol`) instead
(docs/ENGINEERING_STANDARDS.md §13, mirroring `tapjob_api.ports`'s provider-boundary style without
adopting the full `Port`/`CallContext`/`PortError` machinery for a single, non-pluggable provider).

Every verification call uses ``check_revoked=True`` (2026 WP1.1 review; corrected 2026 independent
review finding 5 -- the paragraph below previously had this backwards). Without
``check_revoked``, `verify_id_token` only checks the JWT's own signature and expiry: an
already-issued ID token a client still holds would keep passing verification until its own natural
(~1 hour) expiry, EVEN after `revoke_refresh_tokens(uid)` -- the revoke-all endpoint would then be
a no-op against any such token. ``check_revoked=True`` closes that gap: it makes Firebase Admin
additionally compare the token's issue time against the account's server-side revocation boundary
(set by `revoke_refresh_tokens`) via a round trip to the Firebase Auth backend, rejecting the token
immediately if it was issued before that boundary -- stronger revocation enforcement, at the cost
of that extra per-request network dependency on Firebase (a real, documented trade-off, not a free
local check).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from firebase_admin import exceptions as firebase_exceptions

# Firebase ID tokens always carry a numeric `auth_time` (seconds since epoch) -- the last time the
# end user actively authenticated with the provider, distinct from `iat`/`exp` (docs/adr/0009-
# ...md "sensitive action / recent auth").
_AUTH_TIME_CLAIM = "auth_time"
_SUBJECT_CLAIM = "uid"
_EMAIL_CLAIM = "email"
_EMAIL_VERIFIED_CLAIM = "email_verified"


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """The only shape the rest of this codebase ever sees for an authenticated Firebase principal
    -- a raw decoded-JWT claims dict never crosses this boundary."""

    subject: str
    email: str | None
    email_verified: bool | None
    auth_time: datetime


class TokenFailureReason:
    """Not a `StrEnum` of its own: these map directly onto `tapjob_api.http.errors.ErrorCode`
    members (`AUTH_TOKEN_INVALID`/`AUTH_TOKEN_REVOKED`/`ACCOUNT_DISABLED`) one-for-one, so the
    dependency layer that catches `FirebaseTokenError` is the single place translating a reason
    into an `ApiError` -- this module stays free of any import from `tapjob_api.http`."""

    INVALID = "invalid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class FirebaseTokenError(Exception):
    """Normalized failure from this adapter boundary. The real `firebase_admin` exception's own
    type/message never crosses it (AGENTS.md §8) -- only `reason`, one of `TokenFailureReason`'s
    values."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class IdentityTokenVerifier(Protocol):
    """What the identity service layer depends on -- satisfied by `FirebaseIdentityProvider` in
    production and by a small in-memory fake in tests (never a real Firebase project is contacted
    in CI, per docs/adr/0009-...md "operational prerequisites")."""

    def verify_id_token(self, token: str) -> VerifiedIdentity:
        """Raises `FirebaseTokenError` for any invalid/expired/revoked/disabled/unreachable case --
        never raises the underlying SDK's own exception type."""
        ...

    def revoke_refresh_tokens(self, subject: str) -> None:
        """Raises `FirebaseTokenError(UNAVAILABLE)` if the provider call itself fails; never
        silently succeeds without the SDK actually confirming the revocation request."""
        ...


class FirebaseIdentityProvider:
    """Thin adapter over `firebase_admin.auth`, backed by one process-wide `firebase_admin.App`
    created via `create_firebase_app` below."""

    def __init__(self, app: firebase_admin.App) -> None:
        self._app = app

    def verify_id_token(self, token: str) -> VerifiedIdentity:
        try:
            claims = firebase_auth.verify_id_token(token, app=self._app, check_revoked=True)
        except firebase_auth.ExpiredIdTokenError as error:
            raise FirebaseTokenError(TokenFailureReason.EXPIRED) from error
        except firebase_auth.RevokedIdTokenError as error:
            raise FirebaseTokenError(TokenFailureReason.REVOKED) from error
        except firebase_auth.UserDisabledError as error:
            raise FirebaseTokenError(TokenFailureReason.DISABLED) from error
        except firebase_auth.CertificateFetchError as error:
            raise FirebaseTokenError(TokenFailureReason.UNAVAILABLE) from error
        except (firebase_auth.InvalidIdTokenError, ValueError) as error:
            raise FirebaseTokenError(TokenFailureReason.INVALID) from error

        auth_time = claims.get(_AUTH_TIME_CLAIM)
        subject = claims.get(_SUBJECT_CLAIM)
        if not isinstance(auth_time, int) or not isinstance(subject, str) or not subject:
            # A verified token missing its own mandatory claims is not something this adapter can
            # trust further, regardless of what the SDK itself accepted (fail-closed).
            raise FirebaseTokenError(TokenFailureReason.INVALID)

        email = claims.get(_EMAIL_CLAIM)
        email_verified = claims.get(_EMAIL_VERIFIED_CLAIM)
        return VerifiedIdentity(
            subject=subject,
            email=email if isinstance(email, str) else None,
            email_verified=email_verified if isinstance(email_verified, bool) else None,
            auth_time=datetime.fromtimestamp(auth_time, tz=UTC),
        )

    def revoke_refresh_tokens(self, subject: str) -> None:
        try:
            firebase_auth.revoke_refresh_tokens(subject, app=self._app)
        except (firebase_exceptions.FirebaseError, ValueError) as error:
            raise FirebaseTokenError(TokenFailureReason.UNAVAILABLE) from error


def create_firebase_app() -> firebase_admin.App:
    """Application Default Credentials only (docs/adr/0009-...md "Firebase config server-side") --
    never a committed service-account key file, private key or other secret blob
    (docs/DEPLOYMENT.md §7). The operator is responsible for making ADC available to the process
    (e.g. `GOOGLE_APPLICATION_CREDENTIALS` pointing at a key file held outside this repository, or
    the ambient credentials of a managed runtime) -- this call fails fast if none is configured,
    which is the correct fail-fast behavior for staging/production (docs/adr/0009-...md
    "operational prerequisites"); local/unit tests never call this function at all, using
    `IdentityTokenVerifier`'s fake implementation instead."""
    return firebase_admin.initialize_app(credentials.ApplicationDefault())
