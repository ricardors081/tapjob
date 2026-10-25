"""Tests for natural conversational cross-document conflict resolution (Blocker 2).

Validates:
1. test_language_conflict_natural_choose_proposed
2. test_language_conflict_natural_keep_existing
3. test_experience_conflict_natural_choose_resume_value
4. test_conflict_resolution_preserves_lineage
5. test_conflict_resolution_bumps_profile_version_once
6. test_conflict_resolution_finalizes_import
7. test_multiple_conflicts_resolve_one_at_a_time
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock, patch

from sqlalchemy import Table, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.career_profile.models import (
    CareerProfile,
    CareerProfileRevision,
    FactSourceKind,
    FactStatus,
    FactType,
    ProfileFact,
)
from tapjob_api.db.base import Base
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.orchestration import dispatch_inbound_message
from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.ports.messaging_channel import ChannelIdentity, InboundMessage
from tapjob_api.resume_import.models import (
    CandidateReviewDraft,
    CandidateReviewState,
    ImportStatus,
    Resume,
    ResumeImport,
    ResumeImportCandidate,
    ResumeVersion,
)


def _setup_sqlite_db() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_con: Any, con_record: Any) -> None:
        dbapi_con.create_function("now", 0, lambda: datetime.now(UTC).isoformat())

    tables = [
        User.__table__,
        CareerProfile.__table__,
        CareerProfileRevision.__table__,
        ProfileFact.__table__,
        Resume.__table__,
        ResumeVersion.__table__,
        ResumeImport.__table__,
        ResumeImportCandidate.__table__,
        CandidateReviewDraft.__table__,
        OutboundMessageQueue.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=cast(list[Table], tables))
    return sessionmaker(bind=engine, expire_on_commit=False)


def _create_base_env(session: Session) -> tuple[User, CareerProfile, MagicMock]:
    user = User(
        id=uuid.uuid4(),
        status=UserStatus.ACTIVE,
        locale="pt-BR",
        timezone="America/Cuiaba",
    )
    session.add(user)
    session.flush()

    profile = CareerProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        profile_version=1,
    )
    session.add(profile)
    session.commit()

    link = MagicMock()
    link.id = str(uuid.uuid4())
    link.user_id = user.id
    return user, profile, link


def test_language_conflict_natural_choose_proposed() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        user_id = user.id

        # Existing fact: English Intermediate
        fact_id = uuid.uuid4()
        existing_fact = ProfileFact(
            id=fact_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.LANGUAGE,
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_id,
            active_from_profile_version=1,
        )
        session.add(existing_fact)

        # Import 2: English Advanced
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=2000,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        session.flush()

        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.flush()

        conflict_payload = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand2.id),
            "existing_fact_id": str(existing_fact.id),
            "fact_type": "LANGUAGE",
            "field": "proficiency",
            "existing_value": "Intermediário",
            "proposed_value": "Avançado",
            "semantic_identity": ["language", "english"],
        }
        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_payload],
            "active_conflict": conflict_payload,
        }

        # Outbox entry pointing to active conflict
        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": (
                    "No seu perfil consta Inglês intermediário, mas este currículo "
                    "indica Inglês avançado. Qual informação está correta?"
                ),
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_payload,
            },
        )
        session.add(outbox)
        session.commit()

        imp2_id = imp2.id

    # User sends natural message: "avançado"
    inbound = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="avançado",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        # Import is finalized
        imp_db = session.get(ResumeImport, imp2_id)
        assert imp_db is not None
        assert imp_db.status == ImportStatus.FINALIZED

        # Facts check: Old is SUPERSEDED, new is ACTIVE
        old_f = session.get(ProfileFact, fact_id)
        assert old_f is not None
        assert old_f.status == FactStatus.SUPERSEDED
        assert old_f.inactive_from_profile_version == 2

        new_f = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.fact_type == FactType.LANGUAGE,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        )
        assert new_f is not None
        assert new_f.value_json.get("proficiency") == "advanced"
        assert new_f.supersedes_fact_id == fact_id
        assert new_f.lineage_id == fact_id
        assert new_f.active_from_profile_version == 2


def test_language_conflict_natural_keep_existing() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        user_id = user.id

        fact_id = uuid.uuid4()
        existing_fact = ProfileFact(
            id=fact_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.LANGUAGE,
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_id,
            active_from_profile_version=1,
        )
        session.add(existing_fact)

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=2000,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        session.flush()

        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.flush()

        conflict_payload = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand2.id),
            "existing_fact_id": str(existing_fact.id),
            "fact_type": "LANGUAGE",
            "field": "proficiency",
            "existing_value": "Intermediário",
            "proposed_value": "Avançado",
            "semantic_identity": ["language", "english"],
        }
        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_payload],
            "active_conflict": conflict_payload,
        }

        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": (
                    "No seu perfil consta Inglês intermediário, mas este currículo "
                    "indica Inglês avançado. Qual informação está correta?"
                ),
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_payload,
            },
        )
        session.add(outbox)
        session.commit()

        imp2_id = imp2.id

    # User sends natural message: "o que já estava"
    inbound = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="o que já estava",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        # Import is finalized
        imp_db = session.get(ResumeImport, imp2_id)
        assert imp_db is not None
        assert imp_db.status == ImportStatus.FINALIZED

        # Existing fact remains strictly ACTIVE
        existing_f = session.get(ProfileFact, fact_id)
        assert existing_f is not None
        assert existing_f.status == FactStatus.ACTIVE
        assert existing_f.inactive_from_profile_version is None
        assert existing_f.value_json.get("proficiency") == "intermediate"


def test_experience_conflict_natural_choose_resume_value() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        user_id = user.id

        fact_id = uuid.uuid4()
        existing_exp = ProfileFact(
            id=fact_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.EXPERIENCE,
            value_json={
                "kind": "experience",
                "company": "Inpasa Brasil",
                "title": "Backend Lead",
                "start_date": "2021-01",
                "end_date": None,
                "current": True,
            },
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_id,
            active_from_profile_version=1,
        )
        session.add(existing_exp)

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=2000,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        session.flush()

        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="experience",
            value_json={
                "kind": "experience",
                "company": "Inpasa Brasil",
                "title": "Backend Lead",
                "start_date": "2021-01",
                "end_date": "2024-05",
                "current": False,
            },
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.flush()

        conflict_payload = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand2.id),
            "existing_fact_id": str(existing_exp.id),
            "fact_type": "EXPERIENCE",
            "field": "period",
            "existing_value": "Atual (desde 2021-01)",
            "proposed_value": "2021-01 até 2024-05",
            "semantic_identity": ["experience", "inpasa brasil", "backend lead", "2021-01"],
        }
        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_payload],
            "active_conflict": conflict_payload,
        }

        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": (
                    "No seu perfil consta Inpasa Brasil - Backend Lead Atual (desde 2021-01), "
                    "mas este currículo indica Inpasa Brasil - Backend Lead 2021-01 até 2024-05. "
                    "Qual informação está correta?"
                ),
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_payload,
            },
        )
        session.add(outbox)
        session.commit()

        imp2_id = imp2.id

    # User sends natural message: "o do currículo"
    inbound = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="o do currículo",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        imp_db = session.get(ResumeImport, imp2_id)
        assert imp_db is not None
        assert imp_db.status == ImportStatus.FINALIZED

        old_e = session.get(ProfileFact, fact_id)
        assert old_e is not None
        assert old_e.status == FactStatus.SUPERSEDED

        new_e = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.fact_type == FactType.EXPERIENCE,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        )
        assert new_e is not None
        assert new_e.value_json.get("current") is False
        assert new_e.value_json.get("end_date") == "2024-05"
        assert new_e.supersedes_fact_id == fact_id
        assert new_e.lineage_id == fact_id


def test_conflict_resolution_preserves_lineage() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        user_id = user.id

        original_lineage = uuid.uuid4()
        existing_fact = ProfileFact(
            id=original_lineage,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.LANGUAGE,
            value_json={"kind": "language", "language": "Spanish", "proficiency": "basic"},
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=original_lineage,
            active_from_profile_version=1,
        )
        session.add(existing_fact)

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv2.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="ck_spanish",
            storage_key="k_spanish",
        )
        session.add(imp2)
        session.flush()

        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "Spanish", "proficiency": "fluent"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.flush()

        conflict_payload = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand2.id),
            "existing_fact_id": str(existing_fact.id),
            "fact_type": "LANGUAGE",
            "field": "proficiency",
            "existing_value": "Básico",
            "proposed_value": "Fluente",
            "semantic_identity": ["language", "spanish"],
        }
        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_payload],
            "active_conflict": conflict_payload,
        }

        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": (
                    "No seu perfil consta Espanhol básico, mas este currículo "
                    "indica Espanhol fluente. Qual informação está correta?"
                ),
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_payload,
            },
        )
        session.add(outbox)
        session.commit()

    inbound = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="fluente",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        active_f = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.fact_type == FactType.LANGUAGE,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        )
        assert active_f is not None
        assert active_f.lineage_id == original_lineage
        assert active_f.supersedes_fact_id == original_lineage


def test_conflict_resolution_bumps_profile_version_once() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        assert profile.profile_version == 1
        user_id = user.id

        fact_id = uuid.uuid4()
        existing_fact = ProfileFact(
            id=fact_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.LANGUAGE,
            value_json={"kind": "language", "language": "French", "proficiency": "basic"},
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_id,
            active_from_profile_version=1,
        )
        session.add(existing_fact)

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_fr.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="ck_fr",
            storage_key="k_fr",
        )
        session.add(imp2)
        session.flush()

        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "French", "proficiency": "intermediate"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.flush()

        conflict_payload = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand2.id),
            "existing_fact_id": str(existing_fact.id),
            "fact_type": "LANGUAGE",
            "field": "proficiency",
            "existing_value": "Básico",
            "proposed_value": "Intermediário",
            "semantic_identity": ["language", "french"],
        }
        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_payload],
            "active_conflict": conflict_payload,
        }

        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": (
                    "No seu perfil consta Francês básico, mas este currículo "
                    "indica Francês intermediário. Qual informação está correta?"
                ),
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_payload,
            },
        )
        session.add(outbox)
        session.commit()

    inbound = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="intermediário",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        prof = session.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id))
        assert prof is not None
        # BUMPED EXACTLY ONCE: 1 -> 2
        assert prof.profile_version == 2


def test_conflict_resolution_finalizes_import() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        user_id = user.id

        fact_id = uuid.uuid4()
        existing_fact = ProfileFact(
            id=fact_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.SKILL,
            value_json={"kind": "skill", "name": "Python"},
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_id,
            active_from_profile_version=1,
        )
        session.add(existing_fact)

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_py.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="ck_py",
            storage_key="k_py",
        )
        session.add(imp2)
        session.flush()

        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.flush()

        conflict_payload = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand2.id),
            "existing_fact_id": str(existing_fact.id),
            "fact_type": "SKILL",
            "field": "name",
            "existing_value": "Python",
            "proposed_value": "Python 3",
            "semantic_identity": ["skill", "python"],
        }
        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_payload],
            "active_conflict": conflict_payload,
        }

        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": "Qual informação está correta?",
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_payload,
            },
        )
        session.add(outbox)
        session.commit()

        imp2_id = imp2.id

    inbound = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="o do currículo",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        imp_db = session.get(ResumeImport, imp2_id)
        assert imp_db is not None
        assert imp_db.status == ImportStatus.FINALIZED
        if imp_db.review_metadata:
            assert "cross_document_conflicts" not in imp_db.review_metadata


def test_multiple_conflicts_resolve_one_at_a_time() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user, profile, link = _create_base_env(session)
        user_id = user.id

        # 1. Existing Language: English Intermediate
        fact_lang_id = uuid.uuid4()
        existing_lang = ProfileFact(
            id=fact_lang_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.LANGUAGE,
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_lang_id,
            active_from_profile_version=1,
        )
        session.add(existing_lang)

        # 2. Existing Experience: Inpasa Brasil (current=True)
        fact_exp_id = uuid.uuid4()
        existing_exp = ProfileFact(
            id=fact_exp_id,
            user_id=user.id,
            career_profile_id=profile.id,
            fact_type=FactType.EXPERIENCE,
            value_json={
                "kind": "experience",
                "company": "Inpasa Brasil",
                "title": "Backend Lead",
                "start_date": "2021-01",
                "end_date": None,
                "current": True,
            },
            source_kind=FactSourceKind.RESUME_IMPORT,
            source_ref="cv1.pdf",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=fact_exp_id,
            active_from_profile_version=1,
        )
        session.add(existing_exp)

        # Import 2 with 2 conflicting candidates
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_multi.pdf",
            media_type="application/pdf",
            byte_size=3000,
            checksum_sha256="ck_multi",
            storage_key="k_multi",
        )
        session.add(imp2)
        session.flush()

        cand_lang = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand_lang)

        cand_exp = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="experience",
            value_json={
                "kind": "experience",
                "company": "Inpasa Brasil",
                "title": "Backend Lead",
                "start_date": "2021-01",
                "end_date": "2024-05",
                "current": False,
            },
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand_exp)
        session.flush()

        conflict_1 = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand_lang.id),
            "existing_fact_id": str(existing_lang.id),
            "fact_type": "LANGUAGE",
            "field": "proficiency",
            "existing_value": "Intermediário",
            "proposed_value": "Avançado",
            "semantic_identity": ["language", "english"],
        }
        conflict_2 = {
            "import_id": str(imp2.id),
            "candidate_id": str(cand_exp.id),
            "existing_fact_id": str(existing_exp.id),
            "fact_type": "EXPERIENCE",
            "field": "period",
            "existing_value": "Atual (desde 2021-01)",
            "proposed_value": "2021-01 até 2024-05",
            "semantic_identity": ["experience", "inpasa brasil", "backend lead", "2021-01"],
        }

        imp2.review_metadata = {
            "cross_document_conflicts": [conflict_1, conflict_2],
            "active_conflict": conflict_1,
        }

        outbox = OutboundMessageQueue(
            idempotency_key=f"out_{uuid.uuid4()}",
            channel_identity_link_id=link.id,
            status=OutboundMessageStatus.PENDING,
            message_content={
                "message_text": (
                    "No seu perfil consta Inglês intermediário, mas este currículo "
                    "indica Inglês avançado. Qual informação está correta?"
                ),
                "resume_import_id": str(imp2.id),
                "active_conflict": conflict_1,
            },
        )
        session.add(outbox)
        session.commit()

        imp2_id = imp2.id

    # STEP 1: User resolves Conflict 1 (Language -> Choose proposed: "avançado")
    inbound_1 = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="avançado",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_1, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        # Import is NOT yet finalized because Conflict 2 is still pending!
        imp_step1 = session.get(ResumeImport, imp2_id)
        assert imp_step1 is not None
        assert imp_step1.status == ImportStatus.READY_FOR_REVIEW

        # Active conflict is now Conflict 2
        assert imp_step1.review_metadata is not None
        active_c = imp_step1.review_metadata.get("active_conflict")
        assert active_c is not None
        assert active_c["candidate_id"] == str(cand_exp.id)

    # STEP 2: User resolves Conflict 2 (Experience -> Keep existing: "o que já estava")
    inbound_2 = InboundMessage(
        provider_message_id=str(uuid.uuid4()),
        received_at=datetime.now(UTC),
        sender=ChannelIdentity(channel="whatsapp", external_id="5565999999999"),
        text="o que já estava",
    )

    with (
        session_factory() as session,
        patch(
            "tapjob_api.messaging.orchestration.resolve_user_by_channel_identity",
            side_effect=lambda s, *args, **kwargs: s.get(User, user_id),
        ),
        patch(
            "tapjob_api.messaging.orchestration.resolve_link_by_channel_identity", return_value=link
        ),
        patch("tapjob_api.messaging.orchestration.resolve_user_target_job", return_value=None),
        patch("tapjob_api.messaging.orchestration.get_inbound_effect", return_value=None),
        patch("tapjob_api.messaging.orchestration.reserve_inbound_effect"),
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):
        dispatch_inbound_message(
            session, redis_client=None, inbound=inbound_2, provider_id=uuid.uuid4()
        )
        session.commit()

    with session_factory() as session:
        # Now Import IS FINALIZED!
        imp_final = session.get(ResumeImport, imp2_id)
        assert imp_final is not None
        assert imp_final.status == ImportStatus.FINALIZED

        # Language: proposed won (advanced is active)
        active_lang = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.fact_type == FactType.LANGUAGE,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        )
        assert active_lang is not None
        assert active_lang.value_json.get("proficiency") == "advanced"

        # Experience: existing won (current=True is active)
        active_exp = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.fact_type == FactType.EXPERIENCE,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        )
        assert active_exp is not None
        assert active_exp.value_json.get("current") is True
        assert active_exp.value_json.get("end_date") is None
