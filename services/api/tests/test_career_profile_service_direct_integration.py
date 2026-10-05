"""Integration tests calling `tapjob_api.career_profile.service` DIRECTLY -- never through the
HTTP router (docs/adr/0010-career-profile-truth-store.md "channel-first design", round-2
independent review "BLOCKER 1"/"SERVICE TEST MATRIX"). The point of this file is to prove a future
non-HTTP channel (WhatsApp, the Career Agent, a future Resume Import reviewer) gets the exact same
safety guarantees the HTTP adapter gets -- WITHOUT re-implementing them itself. Real PostgreSQL/
Redis.
"""

import uuid
from http import HTTPStatus
from typing import cast

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from tapjob_api.career_profile import service
from tapjob_api.career_profile.commands import CareerProfilePatchCommand
from tapjob_api.career_profile.fact_values import SkillFactValue
from tapjob_api.career_profile.models import (
    CareerProfile,
    CareerProfileRevision,
    FactType,
    ProfileFact,
)
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import User

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> uuid.UUID:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return uuid.UUID(response.json()["user"]["id"])


def _engine() -> Engine:
    settings = ApiSettings()
    return create_engine(str(settings.database_url))


def _load_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    assert user is not None
    return user


# -- typed value boundary -------------------------------------------------------------------------


def test_add_profile_fact_rejects_a_fact_type_value_kind_mismatch_in_the_domain_itself(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves the invariant lives in `service.py`, not only in `router.py`'s Pydantic DTOs -- a
    caller skipping the HTTP layer entirely still cannot store a fact whose declared `fact_type`
    disagrees with its own typed `value.kind`."""
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            with pytest.raises(service.ProfileFactValueMismatchError):
                service.add_profile_fact(
                    session,
                    user=user,
                    expected_version=0,
                    fact_type=FactType.LANGUAGE,
                    value=SkillFactValue(name="Python"),
                )
            session.rollback()

            any_fact = session.scalar(select(ProfileFact).where(ProfileFact.user_id == user.id))
            assert any_fact is None  # nothing was ever committed
    finally:
        engine.dispose()


def test_add_profile_fact_cannot_be_bypassed_with_an_arbitrary_dict(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """A plain `dict` has no `.kind` attribute -- a caller that skips constructing a real
    `FactValue` fails immediately and loudly, never silently storing an unvalidated payload."""
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            with pytest.raises(AttributeError):
                service.add_profile_fact(
                    session,
                    user=user,
                    expected_version=0,
                    fact_type=FactType.SKILL,
                    value={"kind": "skill", "name": "Python"},  # type: ignore[arg-type]
                )
            session.rollback()
    finally:
        engine.dispose()


def test_add_profile_fact_succeeds_with_a_matching_typed_value(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            fact = service.add_profile_fact(
                session,
                user=user,
                expected_version=0,
                fact_type=FactType.SKILL,
                value=SkillFactValue(name="Python"),
            )
            session.commit()

            assert fact.source_kind.value == "user_direct"
            assert fact.user_confirmed is True
            assert fact.value_json["name"] == "Python"
    finally:
        engine.dispose()


# -- version guard still applies at the domain layer ----------------------------------------------


def test_add_profile_fact_still_enforces_the_version_guard_directly(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            service.add_profile_fact(
                session,
                user=user,
                expected_version=0,
                fact_type=FactType.SKILL,
                value=SkillFactValue(name="Python"),
            )
            session.commit()

        with Session(engine) as session:
            user = _load_user(session, user_id)
            with pytest.raises(service.ProfileVersionConflictError):
                service.add_profile_fact(
                    session,
                    user=user,
                    expected_version=0,  # stale -- already bumped to 1 above
                    fact_type=FactType.SKILL,
                    value=SkillFactValue(name="Rust"),
                )
    finally:
        engine.dispose()


# -- ownership is enforced INSIDE the service, not only by a caller's own prior lookup -------------


def test_replace_profile_fact_cannot_touch_another_users_fact_even_called_directly(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_a_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-a", subject="uid-a"
    )
    user_b_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-b", subject="uid-b"
    )
    engine = _engine()
    try:
        with Session(engine) as session:
            user_a = _load_user(session, user_a_id)
            fact_a = service.add_profile_fact(
                session,
                user=user_a,
                expected_version=0,
                fact_type=FactType.SKILL,
                value=SkillFactValue(name="Python"),
            )
            session.commit()
            fact_a_id = fact_a.id

        with Session(engine) as session:
            user_b = _load_user(session, user_b_id)
            with pytest.raises(service.ProfileFactNotFoundError):
                service.replace_profile_fact(
                    session,
                    user=user_b,
                    fact_id=fact_a_id,
                    expected_version=0,
                    value=SkillFactValue(name="Hijacked"),
                )
            session.rollback()

        # Still intact and unchanged for the real owner.
        with Session(engine) as session:
            fact = session.get(ProfileFact, fact_a_id)
            assert fact is not None
            assert fact.value_json["name"] == "Python"
            assert fact.status.value == "active"
    finally:
        engine.dispose()


def test_retract_profile_fact_cannot_touch_another_users_fact_even_called_directly(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_a_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-a", subject="uid-a"
    )
    user_b_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-b", subject="uid-b"
    )
    engine = _engine()
    try:
        with Session(engine) as session:
            user_a = _load_user(session, user_a_id)
            fact_a = service.add_profile_fact(
                session,
                user=user_a,
                expected_version=0,
                fact_type=FactType.SKILL,
                value=SkillFactValue(name="Python"),
            )
            session.commit()
            fact_a_id = fact_a.id

        with Session(engine) as session:
            user_b = _load_user(session, user_b_id)
            with pytest.raises(service.ProfileFactNotFoundError):
                service.retract_profile_fact(
                    session, user=user_b, fact_id=fact_a_id, expected_version=0
                )
            session.rollback()

        with Session(engine) as session:
            fact = session.get(ProfileFact, fact_a_id)
            assert fact is not None
            assert fact.status.value == "active"
    finally:
        engine.dispose()


def test_a_nonexistent_fact_id_behaves_the_same_as_a_cross_user_one(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            with pytest.raises(service.ProfileFactNotFoundError):
                service.replace_profile_fact(
                    session,
                    user=user,
                    fact_id=uuid.uuid4(),
                    expected_version=0,
                    value=SkillFactValue(name="Python"),
                )
    finally:
        engine.dispose()


# -- update_career_profile has no arbitrary-attribute escape hatch --------------------------------


def test_update_career_profile_has_no_keyword_for_restricted_attributes(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Structural proof, not a runtime check: `update_career_profile`'s signature simply has no
    parameter for `profile_version`/`user_id`/`id`/`created_at` -- Python itself raises `TypeError`
    for an unexpected keyword argument, there is no `**kwargs` catch-all a caller could smuggle one
    through (round-2 independent review "BLOCKER 1": the previous `fields: dict[str, object]` +
    `setattr` boundary had no such protection)."""
    with pytest.raises(TypeError):
        service.update_career_profile(  # type: ignore[call-arg]
            cast(Session, None),  # never reached -- the TypeError is a bind-time error
            user=cast(User, None),
            expected_version=0,
            profile_version=999,
        )


# -- same-value patch is a no-op, called directly --------------------------------------------------


def test_update_career_profile_same_value_patch_is_a_no_op_called_directly(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            seeded = service.update_career_profile(
                session,
                user=user,
                expected_version=0,
                command=CareerProfilePatchCommand(headline="Engineer"),
            )
            session.commit()
            assert seeded.fields_changed == ["headline"]
            assert seeded.profile is not None
            assert seeded.profile.profile_version == 1

        with Session(engine) as session:
            user = _load_user(session, user_id)
            result = service.update_career_profile(
                session,
                user=user,
                expected_version=1,
                command=CareerProfilePatchCommand(headline="Engineer"),
            )
            session.commit()

            assert result.fields_changed == []
            assert result.profile is not None
            assert result.profile.profile_version == 1  # unchanged -- no bump
    finally:
        engine.dispose()


# -- typed command validation lives in the domain, not only router.py (round-3 "BLOCKER 1") -------


def test_invalid_command_never_touches_the_database_at_all(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """A caller that tries to build an invalid `CareerProfilePatchCommand` (e.g. a whitespace-only
    headline) fails at CONSTRUCTION time -- it never even reaches `update_career_profile`, so there
    is no code path by which it could create a row, bump a version, or write a revision."""
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    with pytest.raises(ValueError):
        CareerProfilePatchCommand(headline="   ")

    engine = _engine()
    try:
        with Session(engine) as session:
            assert (
                session.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id))
                is None
            )
            assert (
                session.scalar(
                    select(CareerProfileRevision).where(CareerProfileRevision.user_id == user_id)
                )
                is None
            )
    finally:
        engine.dispose()


# -- virtual-profile same-value patch creates nothing (round-3 "BLOCKER 2") -----------------------


def test_virtual_profile_same_value_patch_creates_no_row_no_revision_called_directly(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    engine = _engine()
    try:
        with Session(engine) as session:
            user = _load_user(session, user_id)
            result = service.update_career_profile(
                session,
                user=user,
                expected_version=0,
                command=CareerProfilePatchCommand(headline=None),
            )
            session.commit()

            assert result.fields_changed == []
            assert result.profile is None

        with Session(engine) as session:
            assert (
                session.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id))
                is None
            )
            assert (
                session.scalar(
                    select(CareerProfileRevision).where(CareerProfileRevision.user_id == user_id)
                )
                is None
            )
    finally:
        engine.dispose()
