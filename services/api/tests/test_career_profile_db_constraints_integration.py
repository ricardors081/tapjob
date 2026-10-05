"""Integration tests: real-PostgreSQL-enforced invariants that the service layer already respects
but that the schema itself must also guarantee (docs/adr/0010-career-profile-truth-store.md
"referential consistency", round-1 independent review "BLOCKER 2"). These tests deliberately bypass
`tapjob_api.career_profile.service` and write directly through a raw ORM `Session` -- the point is
to prove the DATABASE itself rejects an invariant violation, not merely that the service layer
happens to avoid constructing one. Real PostgreSQL/Redis.
"""

import uuid
from datetime import UTC, datetime
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import (
    CareerProfile,
    FactSourceKind,
    FactStatus,
    FactType,
    ProfileFact,
)
from tapjob_api.core.config import ApiSettings

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
PROFILE_PATH = "/api/v1/me/career-profile"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> str:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return str(response.json()["user"]["id"])


def _ensure_career_profile_row(client: TestClient, token: str) -> None:
    response = client.patch(
        PROFILE_PATH,
        headers=bearer_header(token),
        json={"expectedProfileVersion": 0, "headline": "h"},
    )
    assert response.status_code == HTTPStatus.OK


def _career_profile_id(user_id: str) -> uuid.UUID:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            profile_id = session.scalar(
                select(CareerProfile.id).where(CareerProfile.user_id == uuid.UUID(user_id))
            )
    finally:
        engine.dispose()
    assert profile_id is not None
    return profile_id


def _make_bare_fact(*, user_id: uuid.UUID, career_profile_id: uuid.UUID) -> ProfileFact:
    fact_id = uuid.uuid4()
    return ProfileFact(
        id=fact_id,
        user_id=user_id,
        career_profile_id=career_profile_id,
        fact_type=FactType.SKILL,
        value_json={"kind": "skill", "name": "Python"},
        source_kind=FactSourceKind.USER_DIRECT,
        user_confirmed=True,
        confirmed_at=datetime.now(UTC),
        status=FactStatus.ACTIVE,
        lineage_id=fact_id,
        active_from_profile_version=1,
    )


def test_a_fact_whose_user_matches_its_career_profiles_real_owner_is_accepted(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_a_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-a", subject="uid-a"
    )
    _ensure_career_profile_row(identity_client, "token-a")
    profile_a_id = _career_profile_id(user_a_id)

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            session.add(
                _make_bare_fact(user_id=uuid.UUID(user_a_id), career_profile_id=profile_a_id)
            )
            session.commit()  # must not raise -- the pair genuinely belongs together.
    finally:
        engine.dispose()


def test_a_fact_whose_user_does_not_match_its_career_profiles_real_owner_is_rejected_by_the_db(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """The structural gap round-1 independent review's BLOCKER 2 found: `user_id` and
    `career_profile_id` were previously two INDEPENDENT FKs, so nothing in the schema itself
    rejected a fact whose `user_id` belongs to User A but whose `career_profile_id` points at User
    B's `CareerProfile`. The composite FK added to fix this must make Postgres reject the insert
    outright, never merely rely on `tapjob_api.career_profile.service` never constructing one."""
    user_a_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-a", subject="uid-a"
    )
    _ensure_career_profile_row(identity_client, "token-a")
    profile_a_id = _career_profile_id(user_a_id)

    user_b_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-b", subject="uid-b"
    )
    _ensure_career_profile_row(identity_client, "token-b")

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            # User B's id, paired with User A's career_profile_id -- a mismatched pair.
            session.add(
                _make_bare_fact(user_id=uuid.UUID(user_b_id), career_profile_id=profile_a_id)
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()
