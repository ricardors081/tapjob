"""Integration tests: `GET /api/v1/me/truth-snapshot` -- the one shape a future AI/resume/matching
consumer may ground on (docs/adr/0010-career-profile-truth-store.md "truth snapshot filter").
Confirms the default filter (active, confirmed, not retracted/superseded, owned), fact-content
immutability across a replace, and historical reconstruction by `profileVersion`. Real PostgreSQL/
Redis.
"""

from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
FACTS_PATH = "/api/v1/me/profile-facts"
PROFILE_PATH = "/api/v1/me/career-profile"
SNAPSHOT_PATH = "/api/v1/me/truth-snapshot"

SKILL_VALUE = {"kind": "skill", "name": "Python"}
OTHER_SKILL_VALUE = {"kind": "skill", "name": "Rust"}


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> str:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return str(response.json()["user"]["id"])


def _add_fact(
    client: TestClient,
    token: str,
    *,
    expected_version: int,
    fact_type: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        FACTS_PATH,
        headers=bearer_header(token),
        json={"expectedProfileVersion": expected_version, "factType": fact_type, "value": value},
    )
    assert response.status_code == HTTPStatus.CREATED
    return dict(response.json())


def _snapshot(
    client: TestClient, token: str, *, profile_version: int | None = None
) -> dict[str, Any]:
    params = {} if profile_version is None else {"profileVersion": profile_version}
    response = client.get(SNAPSHOT_PATH, params=params, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return dict(response.json())


def _patch_profile(client: TestClient, token: str, **body: object) -> dict[str, Any]:
    response = client.patch(PROFILE_PATH, headers=bearer_header(token), json=body)
    assert response.status_code == HTTPStatus.OK
    return dict(response.json())


def test_snapshot_for_a_brand_new_user_is_empty_at_version_zero(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    body = _snapshot(identity_client, "token-1")

    assert body["profileVersion"] == 0
    assert body["facts"] == []
    assert body["headline"] is None


def test_snapshot_includes_only_active_confirmed_facts(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    kept = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )
    retracted = _add_fact(
        identity_client, "token-1", expected_version=1, fact_type="skill", value=OTHER_SKILL_VALUE
    )
    identity_client.post(
        f"{FACTS_PATH}/{retracted['id']}/retract",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 2},
    )

    body = _snapshot(identity_client, "token-1")

    fact_ids = [fact["id"] for fact in body["facts"]]
    assert kept["id"] in fact_ids
    assert retracted["id"] not in fact_ids


def test_snapshot_reflects_the_profile_headline_summary_and_target_roles(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    identity_client.patch(
        PROFILE_PATH,
        headers=bearer_header("token-1"),
        json={
            "expectedProfileVersion": 0,
            "headline": "Senior Backend Engineer",
            "targetRoles": ["backend engineer"],
        },
    )

    body = _snapshot(identity_client, "token-1")

    assert body["headline"] == "Senior Backend Engineer"
    assert body["targetRoles"] == ["backend engineer"]
    assert body["profileVersion"] == 1


def test_replacing_a_fact_swaps_it_in_the_snapshot_without_duplicating_the_lineage(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )
    identity_client.post(
        f"{FACTS_PATH}/{original['id']}/replace",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 1, "value": OTHER_SKILL_VALUE},
    )

    body = _snapshot(identity_client, "token-1")

    assert len(body["facts"]) == 1
    assert body["facts"][0]["value"]["name"] == "Rust"
    assert body["facts"][0]["id"] != original["id"]


def test_historical_snapshot_reconstructs_what_was_true_at_an_earlier_version(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    original = _add_fact(
        identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE
    )
    # original became active at profile_version=1; the replace below bumps it to 2.
    identity_client.post(
        f"{FACTS_PATH}/{original['id']}/replace",
        headers=bearer_header("token-1"),
        json={"expectedProfileVersion": 1, "value": OTHER_SKILL_VALUE},
    )

    historical = _snapshot(identity_client, "token-1", profile_version=1)
    current = _snapshot(identity_client, "token-1")

    assert historical["profileVersion"] == 1
    assert len(historical["facts"]) == 1
    assert historical["facts"][0]["id"] == original["id"]
    assert historical["facts"][0]["value"]["name"] == "Python"

    assert current["profileVersion"] == 2
    assert len(current["facts"]) == 1
    assert current["facts"][0]["value"]["name"] == "Rust"


def test_historical_snapshot_before_any_fact_existed_is_empty(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _add_fact(identity_client, "token-1", expected_version=0, fact_type="skill", value=SKILL_VALUE)

    body = _snapshot(identity_client, "token-1", profile_version=0)

    assert body["facts"] == []


# -- historical top-level truth (round-1 independent review "BLOCKER 1") -------------------------


def test_historical_headline_reflects_what_was_true_at_that_version_not_current(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="Backend Engineer"
    )
    _patch_profile(identity_client, "token-1", expectedProfileVersion=1, headline="Staff Engineer")

    v1 = _snapshot(identity_client, "token-1", profile_version=1)
    current = _snapshot(identity_client, "token-1")

    assert v1["headline"] == "Backend Engineer"
    assert current["headline"] == "Staff Engineer"
    assert current["profileVersion"] == 2


def test_historical_summary_reflects_what_was_true_at_that_version(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, summary="Summary A")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=1, summary="Summary B")

    v1 = _snapshot(identity_client, "token-1", profile_version=1)
    current = _snapshot(identity_client, "token-1")

    assert v1["summary"] == "Summary A"
    assert current["summary"] == "Summary B"


def test_historical_target_roles_reflect_what_was_true_at_that_version(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, targetRoles=["backend"])
    _patch_profile(identity_client, "token-1", expectedProfileVersion=1, targetRoles=["platform"])

    v1 = _snapshot(identity_client, "token-1", profile_version=1)
    current = _snapshot(identity_client, "token-1")

    assert v1["targetRoles"] == ["backend"]
    assert current["targetRoles"] == ["platform"]


def test_a_fact_only_mutation_never_changes_the_historical_top_level_state(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """A fact mutation bumps `profileVersion` but never touches headline/summary/targetRoles -- the
    historical top-level state at any version between two REAL top-level changes must keep
    reflecting the earlier change, not reset or drift (docs/adr/0010-...md "historical top-level
    revision")."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="Backend Engineer"
    )
    # Fact-only mutation: bumps profileVersion from 1 to 2, but headline is untouched.
    _add_fact(identity_client, "token-1", expected_version=1, fact_type="skill", value=SKILL_VALUE)

    at_version_1 = _snapshot(identity_client, "token-1", profile_version=1)
    at_version_2 = _snapshot(identity_client, "token-1", profile_version=2)
    current = _snapshot(identity_client, "token-1")

    assert at_version_1["headline"] == "Backend Engineer"
    assert at_version_2["headline"] == "Backend Engineer"
    assert current["headline"] == "Backend Engineer"
    assert current["profileVersion"] == 2


def test_facts_and_top_level_fields_are_reconstructed_together_at_the_same_version(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="Backend Engineer"
    )
    fact = _add_fact(
        identity_client, "token-1", expected_version=1, fact_type="skill", value=SKILL_VALUE
    )
    _patch_profile(identity_client, "token-1", expectedProfileVersion=2, headline="Staff Engineer")

    at_version_2 = _snapshot(identity_client, "token-1", profile_version=2)

    assert at_version_2["headline"] == "Backend Engineer"
    assert [f["id"] for f in at_version_2["facts"]] == [fact["id"]]


def test_requesting_a_profile_version_greater_than_current_fails_closed(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="h")

    response = identity_client.get(
        SNAPSHOT_PATH, params={"profileVersion": 999}, headers=bearer_header("token-1")
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "PROFILE_VERSION_NOT_FOUND"


def test_requesting_any_version_when_no_profile_exists_at_all_fails_closed_except_zero(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    zero = identity_client.get(
        SNAPSHOT_PATH, params={"profileVersion": 0}, headers=bearer_header("token-1")
    )
    future = identity_client.get(
        SNAPSHOT_PATH, params={"profileVersion": 1}, headers=bearer_header("token-1")
    )

    assert zero.status_code == HTTPStatus.OK
    assert zero.json()["facts"] == []
    assert future.status_code == HTTPStatus.NOT_FOUND
    assert future.json()["code"] == "PROFILE_VERSION_NOT_FOUND"


def test_snapshot_never_leaks_another_users_facts(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    _add_fact(identity_client, "token-a", expected_version=0, fact_type="skill", value=SKILL_VALUE)
    user_b_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-b", subject="uid-b"
    )

    body = _snapshot(identity_client, "token-b")

    assert body["facts"] == []
    assert body["userId"] == user_b_id
