"""Integration tests: `/api/v1/me/profile-facts*` -- fact immutability, lineage/supersession,
retraction, ownership/IDOR, provenance (no source spoofing), input bounds
(docs/adr/0010-career-profile-truth-store.md). Real PostgreSQL/Redis.
"""

from http import HTTPStatus
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import AccountAuditEvent

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
FACTS_PATH = "/api/v1/me/profile-facts"

SKILL_VALUE = {"kind": "skill", "name": "Python"}
OTHER_SKILL_VALUE = {"kind": "skill", "name": "Go"}
WORK_AUTH_VALUE = {
    "kind": "work_authorization",
    "jurisdiction": "US",
    "questionKey": "requires_sponsorship",
    "answer": "no",
}

# One representative, successfully-creatable payload per fact type/preference kind -- every
# multi-word field exercised through the REAL wire (camelCase), not constructed directly against
# the Python model. This is what actually caught the missing-alias bug this PR fixed
# (questionKey/linkType/etc. silently 422ing) -- round 2 self-review flagged that only skill/
# language/work_authorization had real end-to-end success coverage before this was added.
_EVERY_FACT_TYPE_PAYLOAD: list[tuple[str, dict[str, Any], str, object]] = [
    (
        "experience",
        {
            "kind": "experience",
            "employer": "Acme",
            "title": "Engineer",
            "start": {"year": 2022, "month": 3},
            "current": True,
            "employmentType": "full_time",
        },
        "employmentType",
        "full_time",
    ),
    (
        "education",
        {
            "kind": "education",
            "institution": "State University",
            "fieldOfStudy": "Computer Science",
            "start": {"year": 2018},
            "end": {"year": 2022},
        },
        "fieldOfStudy",
        "Computer Science",
    ),
    (
        "certification",
        {
            "kind": "certification",
            "name": "AWS SAA",
            "issuer": "AWS",
            "credentialId": "ABC123",
            "credentialUrl": "https://aws.amazon.com/verify",
        },
        "credentialId",
        "ABC123",
    ),
    (
        "project",
        {
            "kind": "project",
            "name": "Side project",
            "technologies": ["Python", "PostgreSQL"],
        },
        "technologies",
        ["Python", "PostgreSQL"],
    ),
    (
        "link",
        {"kind": "link", "linkType": "portfolio", "url": "https://example.com"},
        "linkType",
        "portfolio",
    ),
    (
        "preference",
        {
            "kind": "preference",
            "preference": {
                "preferenceKind": "salary",
                "minorUnitMin": 500000,
                "minorUnitMax": 800000,
                "currency": "BRL",
                "period": "monthly",
            },
        },
        "preference",
        {
            "preferenceKind": "salary",
            "minorUnitMin": 500000,
            "minorUnitMax": 800000,
            "currency": "BRL",
            "period": "monthly",
        },
    ),
    (
        "preference",
        {
            "kind": "preference",
            "preference": {"preferenceKind": "workplace_type", "workplaceTypes": ["remote"]},
        },
        "preference",
        {"preferenceKind": "workplace_type", "workplaceTypes": ["remote"]},
    ),
    (
        "preference",
        {
            "kind": "preference",
            "preference": {
                "preferenceKind": "employment_type",
                "employmentTypes": ["full_time", "contract"],
            },
        },
        "preference",
        {"preferenceKind": "employment_type", "employmentTypes": ["full_time", "contract"]},
    ),
]


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def _add_fact(
    client: TestClient, token: str, *, expected_version: int, fact_type: str, value: dict[str, Any]
) -> httpx.Response:
    return client.post(  # type: ignore[no-any-return]
        FACTS_PATH,
        headers=bearer_header(token),
        json={"expectedProfileVersion": expected_version, "factType": fact_type, "value": value},
    )


def _replace_fact(
    client: TestClient,
    token: str,
    fact_id: str,
    *,
    expected_version: int,
    value: dict[str, Any],
) -> httpx.Response:
    return client.post(  # type: ignore[no-any-return]
        f"{FACTS_PATH}/{fact_id}/replace",
        headers=bearer_header(token),
        json={"expectedProfileVersion": expected_version, "value": value},
    )


def _retract_fact(
    client: TestClient, token: str, fact_id: str, *, expected_version: int
) -> httpx.Response:
    return client.post(  # type: ignore[no-any-return]
        f"{FACTS_PATH}/{fact_id}/retract",
        headers=bearer_header(token),
        json={"expectedProfileVersion": expected_version},
    )


def _get_fact(client: TestClient, token: str, fact_id: str) -> httpx.Response:
    return client.get(f"{FACTS_PATH}/{fact_id}", headers=bearer_header(token))  # type: ignore[no-any-return]


def _audit_event_count(event_type: str) -> int:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            return session.execute(
                select(func.count())
                .select_from(AccountAuditEvent)
                .where(AccountAuditEvent.event_type == event_type)
            ).scalar_one()
    finally:
        engine.dispose()


# -- every fact type round-trips through the real wire (camelCase aliases) ---------------------


@pytest.mark.parametrize(("fact_type", "value", "check_key", "expected"), _EVERY_FACT_TYPE_PAYLOAD)
def test_every_fact_type_is_creatable_through_the_real_http_api(
    identity_client: TestClient,
    fake_identity_verifier: FakeIdentityVerifier,
    fact_type: str,
    value: dict[str, Any],
    check_key: str,
    expected: object,
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type=fact_type, value=value
    )

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["factType"] == fact_type
    assert body["value"][check_key] == expected


# -- lifecycle / immutability ------------------------------------------------------------------


def test_add_fact_creates_an_active_fact_and_bumps_the_profile_version_once(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["status"] == "active"
    assert body["factType"] == "skill"
    assert body["activeFromProfileVersion"] == 1
    assert body["inactiveFromProfileVersion"] is None
    assert body["lineageId"] == body["id"]
    assert body["supersedesFactId"] is None


def test_replacing_a_fact_never_mutates_the_original_value_and_links_the_lineage(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()

    response = _replace_fact(
        identity_client, "token-1", original["id"], expected_version=1, value=OTHER_SKILL_VALUE
    )

    assert response.status_code == HTTPStatus.OK
    replacement = response.json()
    assert replacement["id"] != original["id"]
    assert replacement["value"]["name"] == "Go"
    assert replacement["status"] == "active"
    assert replacement["supersedesFactId"] == original["id"]
    assert replacement["lineageId"] == original["lineageId"]

    # The old fact is retrievable by its own id, STILL carrying its ORIGINAL (un-mutated) value.
    old_after = _get_fact(identity_client, "token-1", original["id"]).json()
    assert old_after["status"] == "superseded"
    assert old_after["value"]["name"] == "Python"
    assert old_after["inactiveFromProfileVersion"] == 2


def test_replacing_a_fact_bumps_the_profile_version_exactly_once(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()

    replacement = _replace_fact(
        identity_client, "token-1", original["id"], expected_version=1, value=OTHER_SKILL_VALUE
    ).json()

    assert replacement["activeFromProfileVersion"] == 2


def test_retracting_a_fact_keeps_the_row_but_marks_it_retracted(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    fact = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()

    response = _retract_fact(identity_client, "token-1", fact["id"], expected_version=1)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "retracted"
    assert body["inactiveFromProfileVersion"] == 2
    assert body["value"]["name"] == "Python"  # retraction never touches the content either.


def test_retracted_fact_no_longer_appears_in_the_active_list_but_is_still_readable_by_id(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    fact = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()
    _retract_fact(identity_client, "token-1", fact["id"], expected_version=1)

    listing = identity_client.get(FACTS_PATH, headers=bearer_header("token-1")).json()
    assert fact["id"] not in [item["id"] for item in listing]

    direct = _get_fact(identity_client, "token-1", fact["id"])
    assert direct.status_code == HTTPStatus.OK
    assert direct.json()["status"] == "retracted"


def test_retracting_an_already_retracted_fact_is_a_conflict_not_a_silent_no_op(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    fact = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()
    _retract_fact(identity_client, "token-1", fact["id"], expected_version=1)

    response = _retract_fact(identity_client, "token-1", fact["id"], expected_version=2)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "PROFILE_FACT_ALREADY_RETRACTED"


def test_replacing_an_already_superseded_fact_is_a_conflict(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()
    _replace_fact(
        identity_client, "token-1", original["id"], expected_version=1, value=OTHER_SKILL_VALUE
    )

    response = _replace_fact(
        identity_client, "token-1", original["id"], expected_version=2, value=SKILL_VALUE
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "PROFILE_FACT_ALREADY_RETRACTED"


def test_replace_with_a_mismatched_fact_type_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()

    response = _replace_fact(
        identity_client,
        "token-1",
        original["id"],
        expected_version=1,
        value={"kind": "language", "language": "English"},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_add_fact_with_a_stale_expected_version_is_a_conflict_and_creates_nothing(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _add_fact(identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE)

    response = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=OTHER_SKILL_VALUE
    )

    assert response.status_code == HTTPStatus.CONFLICT
    listing = identity_client.get(FACTS_PATH, headers=bearer_header("token-1")).json()
    assert len(listing) == 1


def test_filtering_the_list_by_fact_type(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _add_fact(identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE)
    _add_fact(
        identity_client,
        "token-1",
        expected_version=1,
        fact_type="language",
        value={"kind": "language", "language": "English"},
    )

    response = identity_client.get(
        FACTS_PATH, params={"factType": "skill"}, headers=bearer_header("token-1")
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert len(body) == 1
    assert body[0]["factType"] == "skill"


# -- ownership / IDOR ---------------------------------------------------------------------------


def test_user_b_cannot_read_user_as_fact(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    fact = _add_fact(
        identity_client, "token-a", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")

    response = _get_fact(identity_client, "token-b", fact["id"])

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "PROFILE_FACT_NOT_FOUND"


def test_user_b_cannot_replace_user_as_fact(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    fact = _add_fact(
        identity_client, "token-a", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")
    before_events = _audit_event_count("profile_fact_replaced")

    response = _replace_fact(
        identity_client, "token-b", fact["id"], expected_version=0, value=OTHER_SKILL_VALUE
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "PROFILE_FACT_NOT_FOUND"
    assert _audit_event_count("profile_fact_replaced") == before_events  # no success audit
    # Still intact for the real owner.
    assert _get_fact(identity_client, "token-a", fact["id"]).json()["status"] == "active"


def test_user_b_cannot_retract_user_as_fact(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    fact = _add_fact(
        identity_client, "token-a", expected_version=0, fact_type="skill", value=SKILL_VALUE
    ).json()
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")
    before_events = _audit_event_count("profile_fact_retracted")

    response = _retract_fact(identity_client, "token-b", fact["id"], expected_version=0)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "PROFILE_FACT_NOT_FOUND"
    assert _audit_event_count("profile_fact_retracted") == before_events  # no success audit
    assert _get_fact(identity_client, "token-a", fact["id"]).json()["status"] == "active"


def test_user_b_never_sees_user_as_facts_in_their_own_list(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    _add_fact(identity_client, "token-a", expected_version=0, fact_type="skill", value=SKILL_VALUE)
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")

    listing = identity_client.get(FACTS_PATH, headers=bearer_header("token-b")).json()

    assert listing == []


# -- provenance: no source spoofing ---------------------------------------------------------------


def test_client_cannot_spoof_source_kind_or_confirmation_metadata(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    # Extra fields a hostile client might try -- the request schema has no such fields at all, so
    # Pydantic silently drops them; the server unconditionally assigns USER_DIRECT/confirmed=True.
    response = identity_client.post(
        FACTS_PATH,
        headers=bearer_header("token-1"),
        json={
            "expectedProfileVersion": 0,
            "factType": "skill",
            "value": SKILL_VALUE,
            "sourceKind": "agent_suggestion",
            "sourceRef": "spoofed",
            "confidence": 0.1,
            "userConfirmed": False,
        },
    )

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["sourceKind"] == "user_direct"
    assert body["userConfirmed"] is True
    assert body["sourceRef"] is None
    assert body["confidence"] is None


def test_work_authorization_fact_is_only_ever_created_via_explicit_user_write(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="work_authorization",
        value=WORK_AUTH_VALUE,
    )

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["sourceKind"] == "user_direct"
    assert body["userConfirmed"] is True
    assert body["value"]["answer"] == "no"


# -- bounds / validation --------------------------------------------------------------------------


def test_empty_skill_name_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="skill",
        value={"kind": "skill", "name": ""},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_overlong_skill_name_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="skill",
        value={"kind": "skill", "name": "x" * 101},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unicode_and_emoji_skill_name_is_accepted(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="skill",
        value={"kind": "skill", "name": "Atendimento ao cliente 🤝"},
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["value"]["name"] == "Atendimento ao cliente 🤝"


def test_dangerous_link_scheme_is_rejected_end_to_end(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="link",
        value={"kind": "link", "linkType": "portfolio", "url": "javascript:alert(1)"},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_mismatched_value_kind_and_fact_type_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _add_fact(
        identity_client,
        "token-1",
        expected_version=0,
        fact_type="language",
        value=SKILL_VALUE,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_nonexistent_fact_id_is_not_found_not_a_500(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _get_fact(identity_client, "token-1", "00000000-0000-0000-0000-000000000000")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "PROFILE_FACT_NOT_FOUND"
