"""Integration tests: `GET`/`PATCH /api/v1/me/career-profile` -- virtual empty profile, optimistic
concurrency, explicit-null vs. absent field semantics, bounds (docs/adr/0010-career-profile-truth-
store.md). Real PostgreSQL/Redis.
"""

from http import HTTPStatus
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import CareerProfile, CareerProfileRevision
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import AccountAuditEvent

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
PROFILE_PATH = "/api/v1/me/career-profile"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> None:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK


def _get_profile(client: TestClient, token: str) -> dict[str, Any]:
    response = client.get(PROFILE_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return dict(response.json())


def _patch_profile(client: TestClient, token: str, **body: object) -> httpx.Response:
    return client.patch(PROFILE_PATH, headers=bearer_header(token), json=body)  # type: ignore[no-any-return]


def _profile_updated_events() -> list[AccountAuditEvent]:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            return list(
                session.scalars(
                    select(AccountAuditEvent).where(
                        AccountAuditEvent.event_type == "profile_updated"
                    )
                )
            )
    finally:
        engine.dispose()


def _career_profile_row_count() -> int:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with engine.connect() as connection:
            return connection.execute(select(func.count()).select_from(CareerProfile)).scalar_one()
    finally:
        engine.dispose()


def _career_profile_revision_count() -> int:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with engine.connect() as connection:
            return connection.execute(
                select(func.count()).select_from(CareerProfileRevision)
            ).scalar_one()
    finally:
        engine.dispose()


def test_get_for_a_brand_new_user_returns_a_virtual_profile_at_version_zero(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    body = _get_profile(identity_client, "token-1")

    assert body["profileVersion"] == 0
    assert body["headline"] is None
    assert body["summary"] is None
    assert body["targetRoles"] == []
    assert body["createdAt"] is None


def test_get_never_creates_a_row_as_a_side_effect(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    _get_profile(identity_client, "token-1")
    _get_profile(identity_client, "token-1")

    assert _career_profile_row_count() == 0


def test_first_patch_creates_the_row_at_expected_version_zero(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="Senior Engineer"
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["headline"] == "Senior Engineer"
    assert body["profileVersion"] == 1
    assert body["createdAt"] is not None


def test_patch_with_a_stale_expected_version_is_a_conflict(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="v1")

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="v2-stale"
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "PROFILE_VERSION_CONFLICT"
    # The conflicting write never applied -- the profile still shows the winning value.
    assert _get_profile(identity_client, "token-1")["headline"] == "v1"


def test_patch_with_the_correct_expected_version_after_a_conflict_succeeds(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="v1")

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=1, headline="v2")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 2


def test_a_single_patch_never_bumps_the_version_more_than_once(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    body = _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        headline="h",
        summary="s",
        targetRoles=["backend", "platform"],
    ).json()

    assert body["profileVersion"] == 1


def test_a_failed_conflicting_patch_never_bumps_the_version(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="v1")

    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="stale")

    assert _get_profile(identity_client, "token-1")["profileVersion"] == 1


def test_omitted_field_is_left_untouched_but_explicit_null_clears_it(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="h", summary="s")

    # `headline` is entirely absent from this body -- must remain "h", not be reset to null.
    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=1, summary="s2")
    assert response.json()["headline"] == "h"
    assert response.json()["summary"] == "s2"

    # Now explicitly send summary: null -- this one MUST clear it.
    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=2, summary=None)
    assert response.json()["summary"] is None
    assert response.json()["headline"] == "h"


def test_a_patch_with_no_mutable_field_at_all_is_rejected_not_a_logical_mutation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    # `profile_version` represents a change in professional truth -- a body that only sets
    # `expectedProfileVersion` is not a logical mutation and must never create a row, bump the
    # version, or produce a success audit event (docs/adr/0010-...md "profile patch", round-1
    # independent review "BLOCKER 3").
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    before_events = len(_profile_updated_events())

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=0)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert _career_profile_row_count() == 0
    assert _get_profile(identity_client, "token-1")["profileVersion"] == 0
    assert len(_profile_updated_events()) == before_events


def test_explicit_null_alone_is_accepted_at_the_schema_level_even_though_it_is_a_no_op(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    # Distinguishes "no mutable field present at all" (rejected above, schema-level 422) from "a
    # mutable field was explicitly sent, even as null" (passes the schema's own "at least one
    # mutable field" check). Whether it ALSO bumps the version is a separate, content-based
    # decision the SERVICE makes (round-2 independent review "same-value profile patch") -- here
    # the field was already `None` by default, so the resulting state equals the current state and
    # this is correctly a no-op (see the dedicated no-op test below), not a schema rejection.
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline=None)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 0


# -- virtual-profile same-value patch creates NOTHING (round-3 independent review "BLOCKER 2") -----
# A patch against a `CareerProfile` that has NEVER been created, whose resulting state equals the
# VIRTUAL default (headline/summary=null, targetRoles=[]), must not create a row, a revision, or an
# audit event -- only a genuinely different value may materialize the aggregate.


@pytest.mark.parametrize(
    "body",
    [
        {"headline": None},
        {"summary": None},
        {"targetRoles": []},
        {"targetRoles": None},
    ],
)
def test_virtual_profile_same_value_patch_creates_nothing(
    identity_client: TestClient,
    fake_identity_verifier: FakeIdentityVerifier,
    body: dict[str, object],
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    before_audits = len(_profile_updated_events())

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=0, **body)

    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert payload["profileVersion"] == 0
    assert payload["createdAt"] is None
    assert payload["updatedAt"] is None
    assert _career_profile_row_count() == 0
    assert _career_profile_revision_count() == 0
    assert len(_profile_updated_events()) == before_audits


def test_a_real_mutation_after_a_virtual_no_op_still_creates_the_row_normally(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline=None)
    assert _career_profile_row_count() == 0  # the no-op above created nothing

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="Engineer"
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 1
    assert response.json()["createdAt"] is not None
    assert _career_profile_row_count() == 1
    # Two revisions for a user's first-ever real mutation, by design (docs/adr/0010-...md
    # "historical top-level revision"): one at profile_version=0 (the empty state, written when
    # the row is first materialized) and one at profile_version=1 (the real values, written by
    # this mutation) -- both are required for `get_truth_snapshot(profileVersion=0)` to correctly
    # reconstruct the empty state that genuinely preceded this change.
    assert _career_profile_revision_count() == 2


def test_a_stale_expected_version_against_a_never_created_profile_still_conflicts(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Even though the SUBMITTED content matches the virtual default, a caller claiming a version
    other than the real current one (`0`, since nothing was ever created) must still conflict --
    never silently treated as a no-op success just because the content happens to match."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=5, headline=None)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "PROFILE_VERSION_CONFLICT"
    assert _career_profile_row_count() == 0


def test_explicit_null_clears_an_existing_value_and_is_a_real_mutation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="Engineer")

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=1, headline=None)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 2
    assert response.json()["headline"] is None


def test_overlong_headline_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="x" * 201
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_too_many_target_roles_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        targetRoles=[f"role-{i}" for i in range(21)],
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unicode_and_whitespace_headline_is_accepted_unmodified(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    headline = "Engenheira de Software 👩‍💻 — São Paulo"
    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline=headline
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["headline"] == headline


def test_whitespace_only_headline_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    # The Truth Store must never accept a semantically empty claim -- `min_length` alone would
    # pass a whitespace-only string (docs/adr/0010-...md "truth store non-blank/normalization
    # policy", round-1 independent review "BLOCKER 4").
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="   ")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert _career_profile_row_count() == 0


def test_whitespace_only_summary_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, summary="\n\t  \n"
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_headline_is_trimmed(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="  Senior Engineer  "
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["headline"] == "Senior Engineer"


def test_summary_preserves_legitimate_internal_newlines(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        summary="Line one.\nLine two.",
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["summary"] == "Line one.\nLine two."


def test_headline_rejects_control_characters(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=0, headline="Engineer\x00Hacker"
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_target_roles_are_trimmed_and_deduplicated_case_insensitively(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        targetRoles=[
            "  Backend Engineer  ",
            "backend engineer",
            "BACKEND ENGINEER",
            "Data Scientist",
        ],
    )

    assert response.status_code == HTTPStatus.OK
    # The FIRST spelling/order is preserved -- never a semantic occupation taxonomy.
    assert response.json()["targetRoles"] == ["Backend Engineer", "Data Scientist"]


def test_target_roles_never_merges_semantically_different_titles(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        targetRoles=["JS Developer", "JavaScript Developer"],
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["targetRoles"] == ["JS Developer", "JavaScript Developer"]


def test_blank_target_role_is_rejected(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    response = _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        targetRoles=["Backend Engineer", "   "],
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# -- same-value patch is a no-op (round-2 independent review "ADDITIONAL REVIEW") ------------------


def test_resubmitting_the_current_value_is_a_no_op_not_a_fake_version_bump(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="Engineer")
    before_events = len(_profile_updated_events())

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=1, headline="Engineer"
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 1  # unchanged -- no bump, no fake history
    assert len(_profile_updated_events()) == before_events  # no success audit for a no-op


def test_a_stale_expected_version_is_still_a_conflict_even_when_values_happen_to_match(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """A submitted value equal to the CURRENT value must never mask a genuinely stale
    `expectedProfileVersion` -- the version check always runs first (docs/adr/0010-...md "same-
    value profile patch")."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="Engineer")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=1, headline="Staff Engineer")

    # token-1's profile is now at version 2 with headline="Staff Engineer". Resubmitting the OLD
    # value "Engineer" at the OLD (now stale) version 1 must conflict, never silently succeed just
    # because "Engineer" happens to match what version 1 itself once held.
    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=1, headline="Engineer"
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "PROFILE_VERSION_CONFLICT"
    assert _get_profile(identity_client, "token-1")["headline"] == "Staff Engineer"


def test_a_genuine_value_change_still_bumps_and_audits_normally(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="Engineer")
    before_events = len(_profile_updated_events())

    response = _patch_profile(
        identity_client, "token-1", expectedProfileVersion=1, headline="Staff Engineer"
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 2
    assert len(_profile_updated_events()) == before_events + 1


def test_each_user_has_an_independent_profile_version_sequence(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    _bootstrap(identity_client, fake_identity_verifier, token="token-b", subject="uid-b")

    _patch_profile(identity_client, "token-a", expectedProfileVersion=0, headline="a1")
    _patch_profile(identity_client, "token-a", expectedProfileVersion=1, headline="a2")

    # User B's own first write still expects version 0 -- unaffected by User A's version history.
    response = _patch_profile(identity_client, "token-b", expectedProfileVersion=0, headline="b1")
    assert response.status_code == HTTPStatus.OK
    assert response.json()["profileVersion"] == 1


def test_successful_patch_is_audited_without_the_field_content(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    _patch_profile(
        identity_client,
        "token-1",
        expectedProfileVersion=0,
        headline="Confidential Headline Text",
    )

    events = _profile_updated_events()

    assert len(events) == 1
    metadata = events[0].metadata_
    assert metadata == {
        "fields_changed": ["headline"],
        "profile_version_before": 0,
        "profile_version_after": 1,
    }
    assert "Confidential Headline Text" not in str(metadata)


def test_a_conflicting_patch_is_never_audited_as_a_success(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="v1")
    before = len(_profile_updated_events())

    _patch_profile(identity_client, "token-1", expectedProfileVersion=0, headline="stale")

    assert len(_profile_updated_events()) == before
