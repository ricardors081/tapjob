"""FastAPI dependencies for the verified-token trust boundary (docs/adr/0009-identity-auth-user-
account.md "current user / ownership"). Every `/api/v1/me/...` route derives `user_id` from here --
never from a client-supplied value -- which is what makes a self-service route structurally immune
to IDOR rather than merely tested against it (docs/adr/0009-...md "ownership / IDOR testing").
"""

from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from tapjob_api.core.rate_limit import (
    RateLimiterUnavailableError,
    RateLimitPolicy,
    RedisFixedWindowRateLimiter,
)
from tapjob_api.db.session import get_db_session
from tapjob_api.http.errors import ApiError, ErrorCode
from tapjob_api.identity import service
from tapjob_api.identity.firebase import (
    FirebaseIdentityProvider,
    FirebaseTokenError,
    IdentityTokenVerifier,
    TokenFailureReason,
    VerifiedIdentity,
    create_firebase_app,
)
from tapjob_api.identity.models import User, UserStatus

_BEARER_PREFIX = "Bearer "
# A real Firebase ID token observed in practice is roughly 900-1200 characters; bounded generously
# above that so an oversized header is rejected before any verification work is attempted (the same
# defensive pattern tapjob_api.http.request_context applies to X-Request-ID).
MAX_BEARER_TOKEN_LENGTH = 4096

# Named, reusable policies (docs/ENGINEERING_STANDARDS.md §2: no magic numbers at each call site).
# Every one of these guards a security-sensitive mutation, so Redis being unreachable fails the
# whole request closed (enforce_rate_limit below), never silently open
# (docs/adr/0009-...md "rate limit failure mode").
BOOTSTRAP_RATE_LIMIT = RateLimitPolicy(key_prefix="rl:auth:bootstrap", limit=10, window_seconds=60)
SESSION_REVOKE_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:sessions:revoke-all", limit=5, window_seconds=60
)
DEVICE_MUTATION_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:devices", limit=20, window_seconds=60
)
CONSENT_MUTATION_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:consents", limit=20, window_seconds=60
)
DELETION_REQUEST_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:deletion-request", limit=3, window_seconds=3600
)

_REASON_TO_ERROR: dict[str, tuple[ErrorCode, int]] = {
    TokenFailureReason.INVALID: (ErrorCode.AUTH_TOKEN_INVALID, 401),
    TokenFailureReason.EXPIRED: (ErrorCode.AUTH_TOKEN_INVALID, 401),
    TokenFailureReason.REVOKED: (ErrorCode.AUTH_TOKEN_REVOKED, 401),
    TokenFailureReason.DISABLED: (ErrorCode.ACCOUNT_DISABLED, 403),
    TokenFailureReason.UNAVAILABLE: (ErrorCode.SERVICE_UNAVAILABLE, 503),
}


def _extract_bearer_token(authorization_header: str | None) -> str:
    """Strict `Authorization: Bearer <token>` parsing. Never reads `uid`/`email`/`provider` from
    anywhere else (query string, body, a custom header) to determine identity -- the verified
    token is the only source (docs/adr/0009-...md "token verification")."""
    if not authorization_header or not authorization_header.startswith(_BEARER_PREFIX):
        raise ApiError(ErrorCode.AUTH_TOKEN_MISSING, status_code=401)
    token = authorization_header[len(_BEARER_PREFIX) :]
    if not token or len(token) > MAX_BEARER_TOKEN_LENGTH:
        raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, status_code=401)
    return token


@lru_cache(maxsize=1)
def _default_identity_verifier() -> IdentityTokenVerifier:
    # Lazy, process-wide singleton (docs/adr/0009-...md "Firebase config server-side"): a route
    # that never authenticates anything (health/readiness, or any future unauthenticated route)
    # never triggers Application Default Credentials lookup. Real staging/production deployments
    # instead get a request.app.state.identity_verifier set eagerly during the app's lifespan
    # (tapjob_api.main), which fails fast at process start if ADC is missing -- this fallback exists
    # for local/manual testing against a real project outside that eager path.
    return FirebaseIdentityProvider(create_firebase_app())


def get_identity_verifier(request: Request) -> IdentityTokenVerifier:
    verifier: IdentityTokenVerifier | None = getattr(request.app.state, "identity_verifier", None)
    if verifier is not None:
        return verifier
    return _default_identity_verifier()


def get_verified_identity(
    request: Request, verifier: IdentityTokenVerifier = Depends(get_identity_verifier)
) -> VerifiedIdentity:
    token = _extract_bearer_token(request.headers.get("authorization"))
    try:
        return verifier.verify_id_token(token)
    except FirebaseTokenError as error:
        code, status_code = _REASON_TO_ERROR.get(error.reason, (ErrorCode.AUTH_TOKEN_INVALID, 401))
        raise ApiError(code, status_code=status_code) from error


def get_current_user(
    identity: VerifiedIdentity = Depends(get_verified_identity),
    session: Session = Depends(get_db_session),
) -> User:
    """The ONE place `user_id` is derived from a verified token rather than accepted from the
    client. A valid token for an identity that never bootstrapped resolves to
    `ACCOUNT_NOT_BOOTSTRAPPED`, never an implicit account creation on a read path
    (docs/adr/0009-...md "account bootstrap")."""
    user = service.find_user_by_firebase_subject(session, subject=identity.subject)
    if user is None:
        raise ApiError(ErrorCode.ACCOUNT_NOT_BOOTSTRAPPED, status_code=404)
    if user.status is UserStatus.DISABLED:
        raise ApiError(ErrorCode.ACCOUNT_DISABLED, status_code=403)
    return user


def get_rate_limiter(request: Request) -> RedisFixedWindowRateLimiter:
    limiter: RedisFixedWindowRateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, status_code=503)
    return limiter


def enforce_rate_limit(
    limiter: RedisFixedWindowRateLimiter, policy: RateLimitPolicy, principal_key: str
) -> None:
    try:
        result = limiter.check(policy, principal_key)
    except RateLimiterUnavailableError as error:
        raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, status_code=503) from error
    if not result.allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED, status_code=429, retry_after_seconds=result.retry_after_seconds
        )


def require_recent_authentication(identity: VerifiedIdentity) -> None:
    try:
        service.require_recent_authentication(identity)
    except service.ReauthenticationRequiredError as error:
        raise ApiError(ErrorCode.REAUTHENTICATION_REQUIRED, status_code=401) from error
