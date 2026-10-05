"""Shared `IdentityTokenVerifier` test double (docs/adr/0009-identity-auth-user-account.md).
Not a `test_*.py` module -- imported by other test files, never collected by pytest itself.

No test in this suite ever contacts a real Firebase project (docs/adr/0009-...md "operational
prerequisites"): this fake is the only `IdentityTokenVerifier` implementation any test wires up,
via `app.dependency_overrides[get_identity_verifier]`.
"""

from datetime import UTC, datetime

from tapjob_api.identity.firebase import FirebaseTokenError, VerifiedIdentity


def bearer_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_identity(
    *,
    subject: str,
    email: str | None = "person@example.com",
    email_verified: bool | None = True,
    auth_time: datetime | None = None,
) -> VerifiedIdentity:
    return VerifiedIdentity(
        subject=subject,
        email=email,
        email_verified=email_verified,
        auth_time=auth_time or datetime.now(UTC),
    )


class FakeIdentityVerifier:
    def __init__(self) -> None:
        self._tokens: dict[str, VerifiedIdentity] = {}
        self._forced_failures: dict[str, str] = {}
        self.revoked_subjects: set[str] = set()
        self.revoke_calls: list[str] = []
        self._revoke_failure_reason: str | None = None

    def issue_token(self, token: str, identity: VerifiedIdentity) -> None:
        self._tokens[token] = identity

    def fail_token(self, token: str, reason: str) -> None:
        self._forced_failures[token] = reason

    def set_revoke_failure(self, reason: str | None) -> None:
        self._revoke_failure_reason = reason

    def verify_id_token(self, token: str) -> VerifiedIdentity:
        if token in self._forced_failures:
            raise FirebaseTokenError(self._forced_failures[token])
        identity = self._tokens.get(token)
        if identity is None:
            raise FirebaseTokenError("invalid")
        if identity.subject in self.revoked_subjects:
            raise FirebaseTokenError("revoked")
        return identity

    def revoke_refresh_tokens(self, subject: str) -> None:
        self.revoke_calls.append(subject)
        if self._revoke_failure_reason is not None:
            raise FirebaseTokenError(self._revoke_failure_reason)
        self.revoked_subjects.add(subject)
