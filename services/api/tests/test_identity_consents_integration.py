"""Integration tests: `/api/v1/me/consents` -- append-only history, ownership
(docs/adr/0009-identity-auth-user-account.md "consent model"). Real PostgreSQL/Redis.
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
CONSENTS_PATH = "/api/v1/me/consents"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def _record_consent(client: TestClient, token: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "purposeKey": "analytics",
        "policyVersion": "2026-01-01",
        "decision": "granted",
        **overrides,
    }
    response = client.post(CONSENTS_PATH, headers=bearer_header(token), json=payload)
    return dict(response.json())


def test_recording_a_consent_succeeds(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    body = _record_consent(identity_client, "token-1")

    assert body["purposeKey"] == "analytics"
    assert body["decision"] == "granted"


def test_withdrawal_adds_a_new_row_never_overwrites_the_grant(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    granted = _record_consent(identity_client, "token-1", decision="granted")
    withdrawn = _record_consent(identity_client, "token-1", decision="withdrawn")

    history = identity_client.get(CONSENTS_PATH, headers=bearer_header("token-1")).json()

    assert granted["id"] != withdrawn["id"]
    assert len(history) == 2
    decisions_by_id = {record["id"]: record["decision"] for record in history}
    assert decisions_by_id[granted["id"]] == "granted"
    assert decisions_by_id[withdrawn["id"]] == "withdrawn"


def test_list_consents_is_ordered_most_recent_first(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _record_consent(identity_client, "token-1", purposeKey="analytics")
    _record_consent(identity_client, "token-1", purposeKey="marketing")

    history = identity_client.get(CONSENTS_PATH, headers=bearer_header("token-1")).json()

    assert history[0]["purposeKey"] == "marketing"
    assert history[1]["purposeKey"] == "analytics"


def test_malformed_decision_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = identity_client.post(
        CONSENTS_PATH,
        headers=bearer_header("token-1"),
        json={"purposeKey": "analytics", "policyVersion": "2026-01-01", "decision": "maybe"},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_user_a_never_sees_user_bs_consent_history(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    _record_consent(identity_client, "token-a", purposeKey="analytics")
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")

    history_b = identity_client.get(CONSENTS_PATH, headers=bearer_header("token-b")).json()

    assert history_b == []
