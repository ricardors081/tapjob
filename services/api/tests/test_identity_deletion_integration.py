"""Integration tests: `/api/v1/me/deletion-request` -- idempotent request, cancellation rules,
recent-auth, never-claims-deleted (docs/adr/0009-identity-auth-user-account.md "deletion
endpoint", "deletion tests"). Real PostgreSQL/Redis.
"""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, text

from tapjob_api.core.config import ApiSettings

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
DELETION_PATH = "/api/v1/me/deletion-request"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def test_requesting_deletion_succeeds_with_202(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.post(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.ACCEPTED
    body = response.json()
    assert body["status"] == "requested"
    # Never claims data was actually deleted -- this is a skeleton/schedule, not a completion.
    assert body["status"] != "completed"


def test_duplicate_requests_are_idempotent_not_a_second_row(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    first = identity_client.post(DELETION_PATH, headers=bearer_header("token-1")).json()
    second = identity_client.post(DELETION_PATH, headers=bearer_header("token-1")).json()

    assert first["id"] == second["id"]


def test_get_deletion_request_reflects_the_current_state(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    identity_client.post(DELETION_PATH, headers=bearer_header("token-1"))

    response = identity_client.get(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "requested"


def test_get_deletion_request_with_no_request_yet_returns_null(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.get(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.OK
    assert response.json() is None


def test_cancelling_a_requested_deletion_succeeds(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    identity_client.post(DELETION_PATH, headers=bearer_header("token-1"))

    response = identity_client.delete(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "cancelled"


def test_cancelling_with_no_active_request_is_a_conflict(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.delete(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.CONFLICT


def test_cancelling_twice_the_second_time_is_a_conflict(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    identity_client.post(DELETION_PATH, headers=bearer_header("token-1"))
    identity_client.delete(DELETION_PATH, headers=bearer_header("token-1"))

    response = identity_client.delete(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.CONFLICT


def test_a_new_request_can_be_made_after_cancellation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    first = identity_client.post(DELETION_PATH, headers=bearer_header("token-1")).json()
    identity_client.delete(DELETION_PATH, headers=bearer_header("token-1"))

    second = identity_client.post(DELETION_PATH, headers=bearer_header("token-1")).json()

    assert second["id"] != first["id"]
    assert second["status"] == "requested"


def test_deletion_request_requires_recent_authentication(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    stale_auth = datetime.now(UTC) - timedelta(hours=2)
    fake_identity_verifier.issue_token(
        "stale-token", make_identity(subject="uid-1", auth_time=stale_auth)
    )
    assert (
        identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("stale-token")).status_code
        == HTTPStatus.OK
    )

    response = identity_client.post(DELETION_PATH, headers=bearer_header("stale-token"))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "REAUTHENTICATION_REQUIRED"


def test_cancellation_requires_recent_authentication(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    identity_client.post(DELETION_PATH, headers=bearer_header("token-1"))

    stale_auth = datetime.now(UTC) - timedelta(hours=2)
    fake_identity_verifier.issue_token(
        "stale-token", make_identity(subject="uid-1", auth_time=stale_auth)
    )

    response = identity_client.delete(DELETION_PATH, headers=bearer_header("stale-token"))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "REAUTHENTICATION_REQUIRED"


def test_a_cancellation_blocked_by_stale_auth_leaves_the_request_untouched(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """No partial mutation/audit when `require_recent_authentication` rejects the call
    (docs/adr/0009-...md "audit transaction semantics") -- the request is still exactly as it was
    before the blocked attempt."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    identity_client.post(DELETION_PATH, headers=bearer_header("token-1"))

    stale_auth = datetime.now(UTC) - timedelta(hours=2)
    fake_identity_verifier.issue_token(
        "stale-token", make_identity(subject="uid-1", auth_time=stale_auth)
    )
    identity_client.delete(DELETION_PATH, headers=bearer_header("stale-token"))

    still_requested = identity_client.get(DELETION_PATH, headers=bearer_header("token-1"))
    assert still_requested.json()["status"] == "requested"


def test_cancelling_a_request_already_moved_to_processing_is_refused_atomically(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """2026 independent-review round 2 finding 6b: a plain SELECT-then-UPDATE could race a future
    deletion orchestrator transitioning REQUESTED -> PROCESSING between the read and the write,
    letting a cancellation silently overwrite that transition. No orchestrator exists yet (out of
    scope for this WP), so this test moves the row to PROCESSING directly via SQL -- standing in
    for that future transition -- and proves the atomic conditional UPDATE's WHERE clause (not
    just application logic) refuses to touch a row that is no longer REQUESTED, leaving it exactly
    as PROCESSING."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    created = identity_client.post(DELETION_PATH, headers=bearer_header("token-1")).json()

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE account_deletion_requests SET status = 'processing' WHERE id = :id"),
                {"id": created["id"]},
            )
    finally:
        engine.dispose()

    response = identity_client.delete(DELETION_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.CONFLICT

    engine = create_engine(str(settings.database_url))
    try:
        with engine.connect() as connection:
            status: str = connection.execute(
                text("SELECT status FROM account_deletion_requests WHERE id = :id"),
                {"id": created["id"]},
            ).scalar_one()
    finally:
        engine.dispose()
    assert status == "processing"


def test_user_a_cannot_see_or_cancel_user_bs_deletion_request(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    identity_client.post(DELETION_PATH, headers=bearer_header("token-a"))
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")

    get_b = identity_client.get(DELETION_PATH, headers=bearer_header("token-b"))
    assert get_b.json() is None

    cancel_b = identity_client.delete(DELETION_PATH, headers=bearer_header("token-b"))
    assert cancel_b.status_code == HTTPStatus.CONFLICT

    # And user A's own request is provably untouched.
    still_requested = identity_client.get(DELETION_PATH, headers=bearer_header("token-a"))
    assert still_requested.json()["status"] == "requested"
