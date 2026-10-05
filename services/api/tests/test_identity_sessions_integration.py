"""Integration tests: `/api/v1/me/sessions/revoke-all` -- real provider-outcome-reflecting audit,
recent-auth, revoked-token rejection afterward (docs/adr/0009-identity-auth-user-account.md
"session revocation", "audit transaction semantics"). Real PostgreSQL/Redis.
"""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
REVOKE_ALL_PATH = "/api/v1/me/sessions/revoke-all"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def test_revoke_all_succeeds_and_calls_the_provider_with_the_right_subject(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.post(REVOKE_ALL_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert fake_identity_verifier.revoke_calls == ["uid-1"]


def test_revoke_all_requires_recent_authentication(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    stale_auth = datetime.now(UTC) - timedelta(hours=1)
    fake_identity_verifier.issue_token(
        "stale-token", make_identity(subject="uid-1", auth_time=stale_auth)
    )
    assert (
        identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("stale-token")).status_code
        == HTTPStatus.OK
    )

    response = identity_client.post(REVOKE_ALL_PATH, headers=bearer_header("stale-token"))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "REAUTHENTICATION_REQUIRED"
    # Recent-auth is checked before the provider is ever contacted.
    assert fake_identity_verifier.revoke_calls == []


def test_a_token_revoked_by_the_provider_is_rejected_on_the_next_request(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))
    identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("token-1"))

    identity_client.post(REVOKE_ALL_PATH, headers=bearer_header("token-1"))
    response = identity_client.get("/api/v1/me", headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "AUTH_TOKEN_REVOKED"


def test_provider_failure_during_revoke_all_is_service_unavailable_and_never_claims_success(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    fake_identity_verifier.set_revoke_failure("unavailable")

    response = identity_client.post(REVOKE_ALL_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    # The provider was genuinely contacted and genuinely failed -- not silently skipped.
    assert fake_identity_verifier.revoke_calls == ["uid-1"]
