"""Integration tests: `/api/v1/me` get/patch and the ownership/IDOR boundary
(docs/adr/0009-identity-auth-user-account.md "current user / ownership",
"ownership / IDOR testing"). Real PostgreSQL/Redis.
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
ME_PATH = "/api/v1/me"


def _bootstrap_and_get_user(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> dict[str, object]:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return dict(response.json()["user"])


def test_get_me_without_bootstrapping_first_is_account_not_bootstrapped(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))

    response = identity_client.get(ME_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "ACCOUNT_NOT_BOOTSTRAPPED"


def test_get_me_does_not_implicitly_create_an_account(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))

    identity_client.get(ME_PATH, headers=bearer_header("token-1"))
    response = identity_client.get(ME_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_get_me_after_bootstrap_returns_the_same_user(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user = _bootstrap_and_get_user(
        identity_client, fake_identity_verifier, token="token-1", subject="uid-1"
    )

    response = identity_client.get(ME_PATH, headers=bearer_header("token-1"))

    assert response.status_code == HTTPStatus.OK
    assert response.json()["id"] == user["id"]


def test_patch_me_updates_locale_and_timezone(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap_and_get_user(
        identity_client, fake_identity_verifier, token="token-1", subject="uid-1"
    )

    response = identity_client.patch(
        ME_PATH,
        headers=bearer_header("token-1"),
        json={"locale": "en", "timezone": "America/Sao_Paulo"},
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["locale"] == "en"
    assert body["timezone"] == "America/Sao_Paulo"


def test_patch_me_with_invalid_timezone_is_rejected_and_does_not_change_state(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    before = _bootstrap_and_get_user(
        identity_client, fake_identity_verifier, token="token-1", subject="uid-1"
    )

    response = identity_client.patch(
        ME_PATH, headers=bearer_header("token-1"), json={"timezone": "Not/AZone"}
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    after = identity_client.get(ME_PATH, headers=bearer_header("token-1")).json()
    assert after["timezone"] == before["timezone"]


def test_patch_me_with_no_fields_changes_nothing(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    before = _bootstrap_and_get_user(
        identity_client, fake_identity_verifier, token="token-1", subject="uid-1"
    )

    response = identity_client.patch(ME_PATH, headers=bearer_header("token-1"), json={})

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["locale"] == before["locale"]
    assert body["timezone"] == before["timezone"]


def test_user_a_cannot_see_user_bs_settings_through_their_own_token(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """There is no `user_id` parameter anywhere on `/me` for a caller to manipulate
    (docs/adr/0009-...md "ownership / IDOR testing") -- each principal's own verified token is the
    only input, so this is really proving the dependency wiring, not a specific guard clause."""
    user_a = _bootstrap_and_get_user(
        identity_client, fake_identity_verifier, token="token-a", subject="uid-a"
    )
    user_b = _bootstrap_and_get_user(
        identity_client, fake_identity_verifier, token="token-b", subject="uid-b"
    )
    assert user_a["id"] != user_b["id"]

    identity_client.patch(ME_PATH, headers=bearer_header("token-a"), json={"locale": "en"})

    response_b = identity_client.get(ME_PATH, headers=bearer_header("token-b"))
    assert response_b.json()["locale"] == user_b["locale"]
    assert response_b.json()["locale"] != "en"
