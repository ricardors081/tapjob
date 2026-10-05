"""Integration tests: every Truth Store mutation is audited, and never with the fact's own content
(docs/adr/0010-career-profile-truth-store.md "audit", docs/SECURITY_PRIVACY.md §10). Real
PostgreSQL/Redis.
"""

from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import AccountAuditEvent

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
FACTS_PATH = "/api/v1/me/profile-facts"

SKILL_VALUE = {"kind": "skill", "name": "Python"}
WORK_AUTH_VALUE = {
    "kind": "work_authorization",
    "jurisdiction": "US",
    "questionKey": "requires_sponsorship",
    "answer": "confidential legal answer text",
}


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def _add_fact(
    client: TestClient, token: str, *, expected_version: int, fact_type: str, value: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(
        FACTS_PATH,
        headers=bearer_header(token),
        json={"expectedProfileVersion": expected_version, "factType": fact_type, "value": value},
    )
    assert response.status_code == HTTPStatus.CREATED
    return dict(response.json())


def _events(event_type: str) -> list[AccountAuditEvent]:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            return list(
                session.scalars(
                    select(AccountAuditEvent).where(AccountAuditEvent.event_type == event_type)
                )
            )
    finally:
        engine.dispose()


def test_add_fact_is_audited_without_the_facts_own_content(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    fact = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )

    rows = _events("profile_fact_added")
    assert len(rows) == 1
    metadata = rows[0].metadata_
    assert metadata == {
        "fact_type": "skill",
        "fact_id": fact["id"],
        "source_kind": "user_direct",
        "profile_version_before": 0,
        "profile_version_after": 1,
    }
    assert "Python" not in str(metadata)


def test_work_authorization_answer_is_never_recorded_in_audit_metadata(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="work_authorization",
        value=WORK_AUTH_VALUE,
    )

    rows = _events("profile_fact_added")
    metadata = rows[0].metadata_
    assert "confidential legal answer text" not in str(metadata)
    assert set(metadata.keys()) == {
        "fact_type",
        "fact_id",
        "source_kind",
        "profile_version_before",
        "profile_version_after",
    }


def test_replace_fact_is_audited_with_lineage_ids_only_not_either_value(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )

    response = identity_client.post(
        f"{FACTS_PATH}/{original['id']}/replace",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 1, "value": {"kind": "skill", "name": "Rust"}},
    )
    replacement = response.json()

    rows = _events("profile_fact_replaced")
    assert len(rows) == 1
    metadata = rows[0].metadata_
    assert metadata == {
        "fact_type": "skill",
        "fact_id": replacement["id"],
        "supersedes_fact_id": original["id"],
        "source_kind": "user_direct",
        "profile_version_before": 1,
        "profile_version_after": 2,
    }
    assert "Python" not in str(metadata)
    assert "Rust" not in str(metadata)


def test_retract_fact_is_audited_without_its_content(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    fact = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )

    identity_client.post(
        f"{FACTS_PATH}/{fact['id']}/retract",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 1},
    )

    rows = _events("profile_fact_retracted")
    assert len(rows) == 1
    assert rows[0].metadata_ == {
        "fact_type": "skill",
        "fact_id": fact["id"],
        "source_kind": "user_direct",
        "profile_version_before": 1,
        "profile_version_after": 2,
    }


def test_a_version_conflict_on_add_fact_is_never_audited_as_a_success(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _add_fact(identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE)

    conflict = identity_client.post(
        FACTS_PATH,
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 0, "factType": "skill", "value": SKILL_VALUE},
    )
    assert conflict.status_code == HTTPStatus.CONFLICT

    rows = _events("profile_fact_added")
    assert len(rows) == 1  # only the first, successful add -- the conflict added nothing.


def test_retracting_an_already_retracted_fact_is_never_audited_a_second_time(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    fact = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )
    identity_client.post(
        f"{FACTS_PATH}/{fact['id']}/retract",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 1},
    )

    conflict = identity_client.post(
        f"{FACTS_PATH}/{fact['id']}/retract",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 2},
    )
    assert conflict.status_code == HTTPStatus.CONFLICT

    rows = _events("profile_fact_retracted")
    assert len(rows) == 1
