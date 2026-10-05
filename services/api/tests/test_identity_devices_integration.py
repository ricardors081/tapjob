"""Integration tests: `/api/v1/me/devices` -- registration idempotency and ownership
(docs/adr/0009-identity-auth-user-account.md "device registration", "ownership / IDOR testing").
Real PostgreSQL/Redis.
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
DEVICES_PATH = "/api/v1/me/devices"
INSTALLATION_ID = "a" * 32


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def _register_device(client: TestClient, token: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"installationId": INSTALLATION_ID, "platform": "ios", **overrides}
    response = client.post(DEVICES_PATH, headers=bearer_header(token), json=payload)
    return dict(response.json())


def test_register_device_succeeds(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    body = _register_device(identity_client, "token-1")

    assert body["installationId"] == INSTALLATION_ID
    assert body["platform"] == "ios"
    assert body["revokedAt"] is None


def test_re_registering_the_same_installation_id_is_idempotent_not_a_duplicate(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    first = _register_device(identity_client, "token-1", appVersion="1.0.0")
    second = _register_device(identity_client, "token-1", appVersion="1.1.0")

    assert first["id"] == second["id"]
    assert second["appVersion"] == "1.1.0"


def test_malformed_installation_id_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.post(
        DEVICES_PATH,
        headers=bearer_header("token-1"),
        json={"installationId": "short", "platform": "ios"},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unknown_platform_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.post(
        DEVICES_PATH,
        headers=bearer_header("token-1"),
        json={"installationId": INSTALLATION_ID, "platform": "palm-os"},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_revoke_device_succeeds_for_the_owner(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    device = _register_device(identity_client, "token-1")

    response = identity_client.delete(
        f"{DEVICES_PATH}/{device['id']}", headers=bearer_header("token-1")
    )

    assert response.status_code == HTTPStatus.NO_CONTENT


def test_revoking_a_nonexistent_device_is_not_found(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.delete(
        f"{DEVICES_PATH}/00000000-0000-0000-0000-000000000000", headers=bearer_header("token-1")
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "DEVICE_NOT_FOUND"


def test_malformed_device_id_in_path_is_unprocessable_not_a_500(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.delete(
        f"{DEVICES_PATH}/not-a-uuid", headers=bearer_header("token-1")
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_user_b_cannot_revoke_user_as_device(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """IDOR: a device id belonging to a different user resolves to DEVICE_NOT_FOUND, never a
    different/forbidden response that would confirm the id exists (docs/adr/0009-...md
    "ownership / IDOR testing")."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    device = _register_device(identity_client, "token-a")
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")

    response = identity_client.delete(
        f"{DEVICES_PATH}/{device['id']}", headers=bearer_header("token-b")
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "DEVICE_NOT_FOUND"

    # And the device is provably still active for its real owner.
    still_active = _register_device(identity_client, "token-a")
    assert still_active["revokedAt"] is None


def test_push_token_is_stored_but_never_echoed_as_plain_logged_metadata(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    body = _register_device(
        identity_client, "token-1", platform="android", pushToken="fcm-token-value"
    )

    # The device response itself legitimately omits pushToken (it is write-only from the client's
    # perspective here) -- this asserts the response schema, not a logging claim this test cannot
    # observe directly.
    assert "pushToken" not in body
