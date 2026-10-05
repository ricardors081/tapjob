"""Integration tests: real PostgreSQL/Redis (docs/ENGINEERING_STANDARDS.md §27). Covers the auth
token matrix, account bootstrap, concurrency and the never-auto-link-by-email invariant
(docs/adr/0009-identity-auth-user-account.md).
"""

import threading
from http import HTTPStatus

import httpx
import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, func, select, text

from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.dependencies import get_identity_verifier
from tapjob_api.identity.models import AccountAuditEvent, ExternalIdentity, User
from tapjob_api.main import create_app

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"


def _bootstrap(client: TestClient, token: str) -> httpx.Response:
    return client.post(BOOTSTRAP_PATH, headers=bearer_header(token))  # type: ignore[no-any-return]


# -- auth token matrix (missing/malformed/invalid/expired/revoked/disabled) -----------------------


def test_missing_authorization_header_is_rejected(identity_client: TestClient) -> None:
    response = identity_client.post(BOOTSTRAP_PATH)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_MISSING"


def test_wrong_auth_scheme_is_rejected(identity_client: TestClient) -> None:
    response = identity_client.post(BOOTSTRAP_PATH, headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_MISSING"


def test_empty_bearer_token_is_rejected(identity_client: TestClient) -> None:
    response = identity_client.post(BOOTSTRAP_PATH, headers={"Authorization": "Bearer "})
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_overlong_bearer_token_is_rejected_before_verification(
    identity_client: TestClient,
) -> None:
    response = _bootstrap(identity_client, "a" * 5000)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_INVALID"


def test_unknown_token_is_invalid(identity_client: TestClient) -> None:
    response = _bootstrap(identity_client, "never-issued-token")
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_INVALID"


def test_expired_token_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.fail_token("expired-token", "expired")
    response = _bootstrap(identity_client, "expired-token")
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_INVALID"


def test_revoked_token_is_rejected_with_its_own_code(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.fail_token("revoked-token", "revoked")
    response = _bootstrap(identity_client, "revoked-token")
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_REVOKED"


def test_disabled_user_token_is_rejected_with_its_own_code(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.fail_token("disabled-token", "disabled")
    response = _bootstrap(identity_client, "disabled-token")
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "ACCOUNT_DISABLED"


def test_provider_unavailable_is_service_unavailable(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.fail_token("flaky-token", "unavailable")
    response = _bootstrap(identity_client, "flaky-token")
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"


def test_no_malformed_header_ever_crashes_the_endpoint(identity_client: TestClient) -> None:
    for header in ["Bearer", "Bearer\tx", "bearer lowercase-scheme", "Authorization"]:
        response = identity_client.post(BOOTSTRAP_PATH, headers={"Authorization": header})
        assert response.status_code == HTTPStatus.UNAUTHORIZED


# -- bootstrap: first login creates, second resolves -----------------------------------------


def test_first_bootstrap_creates_a_new_user(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))

    response = _bootstrap(identity_client, "token-1")

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["created"] is True
    assert body["user"]["status"] == "active"


def test_second_bootstrap_for_the_same_identity_resolves_the_same_user(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))

    first = _bootstrap(identity_client, "token-1").json()
    second = _bootstrap(identity_client, "token-1").json()

    assert second["created"] is False
    assert second["user"]["id"] == first["user"]["id"]


def test_two_different_identities_get_two_different_users(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))
    fake_identity_verifier.issue_token("token-2", make_identity(subject="uid-2"))

    first = _bootstrap(identity_client, "token-1").json()
    second = _bootstrap(identity_client, "token-2").json()

    assert first["user"]["id"] != second["user"]["id"]


def test_bootstrap_never_auto_links_a_second_firebase_uid_sharing_the_same_email(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Security-critical (docs/adr/0009-...md "never auto-link by email"): two DIFFERENT Firebase
    UIDs that happen to report the SAME email must resolve to two SEPARATE TapJob Users, never
    merged just because the email matches -- even when email_verified=True on both."""
    fake_identity_verifier.issue_token(
        "token-a", make_identity(subject="uid-a", email="shared@example.com", email_verified=True)
    )
    fake_identity_verifier.issue_token(
        "token-b", make_identity(subject="uid-b", email="shared@example.com", email_verified=True)
    )

    first = _bootstrap(identity_client, "token-a").json()
    second = _bootstrap(identity_client, "token-b").json()

    assert first["user"]["id"] != second["user"]["id"]


def test_bootstrap_is_rate_limited_per_identity_not_shared_globally(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))
    fake_identity_verifier.issue_token("token-2", make_identity(subject="uid-2"))

    # Same identity bootstrapping repeatedly is idempotent and does not itself prove anything
    # about the rate limit boundary (covered in test_identity_rate_limit_integration.py); this
    # only proves a second, unrelated identity is not blocked by the first's own counter.
    for _ in range(3):
        _bootstrap(identity_client, "token-1")
    response = _bootstrap(identity_client, "token-2")

    assert response.status_code == HTTPStatus.OK


def test_bootstrap_commits_the_new_user_and_its_account_created_audit_event_together(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves the `ACCOUNT_CREATED` audit event lands in the SAME transaction as the new `User`/
    `ExternalIdentity` rows, not a separate later commit (2026 independent-review "account-created
    audit transaction"): exactly one such row exists after the first bootstrap, and a second
    (non-creating) bootstrap for the same identity never adds a duplicate."""
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))

    first = _bootstrap(identity_client, "token-1").json()
    _bootstrap(identity_client, "token-1")  # repeat login: created=False, must not re-audit

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with engine.connect() as connection:
            audit_count = connection.execute(
                select(func.count())
                .select_from(AccountAuditEvent)
                .where(
                    AccountAuditEvent.user_id == first["user"]["id"],
                    AccountAuditEvent.event_type == "account_created",
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert audit_count == 1


# -- concurrency -----------------------------------------------------------------------------


def test_concurrent_first_bootstraps_for_the_same_identity_create_exactly_one_user(
    fake_identity_verifier: FakeIdentityVerifier,
) -> None:
    """Two requests racing to bootstrap the SAME Firebase UID for the first time must never
    produce two Users (docs/adr/0009-...md "concurrency") -- `UNIQUE(provider, provider_subject)`
    is the real backstop; this proves the retry-on-conflict path actually converges to one row
    under real concurrent load against a real database, not just in the single-threaded case the
    other bootstrap tests exercise. Builds its own app/client rather than using `identity_client`:
    this test needs several concurrent independent client instances against the SAME database."""
    settings = ApiSettings()
    fake_identity_verifier.issue_token("race-token", make_identity(subject="race-uid"))
    app = create_app(settings)
    app.dependency_overrides[get_identity_verifier] = lambda: fake_identity_verifier

    results: list[int] = []
    lock = threading.Lock()

    with TestClient(app) as client:

        def _call() -> None:
            response = client.post(BOOTSTRAP_PATH, headers=bearer_header("race-token"))
            with lock:
                results.append(response.status_code)

        threads = [threading.Thread(target=_call) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    engine = create_engine(str(settings.database_url))
    try:
        assert all(status == HTTPStatus.OK for status in results)

        with engine.connect() as connection:
            identity_count = connection.execute(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(ExternalIdentity.provider_subject == "race-uid")
            ).scalar_one()
            user_count = connection.execute(
                select(func.count())
                .select_from(User)
                .where(
                    User.id.in_(
                        select(ExternalIdentity.user_id).where(
                            ExternalIdentity.provider_subject == "race-uid"
                        )
                    )
                )
            ).scalar_one()

        assert identity_count == 1
        assert user_count == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE account_audit_events, account_deletion_requests, "
                    "consent_records, user_devices, external_identities, users CASCADE"
                )
            )
        engine.dispose()
