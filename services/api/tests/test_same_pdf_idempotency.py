"""3x Same-PDF Idempotency Integration Test.

Verifies:
1. Same resume PDF imported 3 times sequentially.
2. Finalizing each time bumps profile_version cleanly (1 -> 2 -> 3).
3. In the second and third imports, older RESUME_IMPORT facts from prior imports
   with the identical checksum_sha256 are marked SUPERSEDED in the same transaction
   with inactive_from_profile_version correctly set.
4. Active profile facts contain exactly one set of resume import facts (no duplicate accumulation).
5. Manual facts (source_kind=user_direct) are preserved across all 3 finalizations.
6. MasterResumeSnapshot correctly reflects the active facts.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import Table, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.career_profile import service as career_profile_service
from tapjob_api.career_profile.fact_values import SkillFactValue
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
from tapjob_api.resume_import import service as resume_import_service
from tapjob_api.resume_import.commands import (
    CandidateReviewDecision,
    FinalizeResumeImportCommand,
    ReviewDecisionKind,
)
from tapjob_api.resume_import.errors import CrossDocumentConflictError
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
    ]
    Base.metadata.create_all(bind=engine, tables=cast(list[Table], tables))
    return sessionmaker(bind=engine)


def test_three_times_same_pdf_idempotency_and_reconciliation() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        initial_user = User(
            id=uuid.uuid4(),
            status=UserStatus.ACTIVE,
            locale="pt-BR",
            timezone="America/Cuiaba",
        )
        session.add(initial_user)
        session.commit()
        user_id = initial_user.id

    pdf_bytes = b"%PDF-1.4 Mock resume content for Ricardo Silva with unique payload"
    checksum = hashlib.sha256(pdf_bytes).hexdigest()

    # --- IMPORT 1 ---
    with session_factory() as session:
        user1 = session.get(User, user_id)
        assert user1 is not None

        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user1.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_ricardo.pdf",
            media_type="application/pdf",
            byte_size=len(pdf_bytes),
            checksum_sha256=checksum,
            storage_key=f"resumes/{user1.id}/imp1.pdf",
            parser_identity="test-parser",
        )
        session.add(imp1)
        session.flush()

        imp1_id = imp1.id

        # Add 3 candidates: 2 skills, 1 language
        cand1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=user1.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=user1.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Gestão de Stakeholders"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand3 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=user1.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "Português", "proficiency": "Nativo"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add_all([cand1, cand2, cand3])
        session.commit()

        # Finalize import 1 (expected_profile_version=0)
        decisions1 = [
            CandidateReviewDecision(candidate_id=cand1.id, decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=cand2.id, decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=cand3.id, decision=ReviewDecisionKind.ACCEPT),
        ]
        res1 = resume_import_service.finalize_resume_import(
            session,
            user=user1,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(expected_profile_version=0, decisions=decisions1),
        )
        session.commit()
        assert res1.resume_version.profile_version == 1

    # Verify state after Import 1
    with session_factory() as session:
        user_check1 = session.get(User, user_id)
        assert user_check1 is not None
        profile1 = career_profile_service.get_career_profile(session, user=user_check1)
        assert profile1 is not None
        assert profile1.profile_version == 1

        active_facts = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_check1.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts) == 3

        # Add a MANUAL fact (source_kind=user_direct)
        manual_fact = career_profile_service.add_profile_fact(
            session,
            user=user_check1,
            expected_version=1,
            fact_type=FactType.SKILL,
            value=SkillFactValue(name="Liderança Estratégica"),
        )
        session.commit()
        assert manual_fact.source_kind == FactSourceKind.USER_DIRECT

        profile1_v2 = career_profile_service.get_career_profile(session, user=user_check1)
        assert profile1_v2 is not None
        assert profile1_v2.profile_version == 2

        active_facts_v2 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_check1.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts_v2) == 4  # 3 resume + 1 manual

    # --- IMPORT 2 (Same PDF, same checksum) ---
    with session_factory() as session:
        user2 = session.get(User, user_id)
        assert user2 is not None

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user2.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_ricardo.pdf",
            media_type="application/pdf",
            byte_size=len(pdf_bytes),
            checksum_sha256=checksum,
            storage_key=f"resumes/{user2.id}/imp2.pdf",
            parser_identity="test-parser",
        )
        session.add(imp2)
        session.flush()
        imp2_id = imp2.id
        assert imp2_id != imp1_id

        cand4 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user2.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand5 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user2.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Gestão de Stakeholders"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand6 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user2.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "Português", "proficiency": "Nativo"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add_all([cand4, cand5, cand6])
        session.commit()

        decisions2 = [
            CandidateReviewDecision(candidate_id=cand4.id, decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=cand5.id, decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=cand6.id, decision=ReviewDecisionKind.ACCEPT),
        ]
        res2 = resume_import_service.finalize_resume_import(
            session,
            user=user2,
            import_id=imp2.id,
            command=FinalizeResumeImportCommand(expected_profile_version=2, decisions=decisions2),
        )
        session.commit()
        assert res2.resume_version.profile_version == 3

    # Verify state after Import 2:
    with session_factory() as session:
        user_check2 = session.get(User, user_id)
        assert user_check2 is not None
        profile2 = career_profile_service.get_career_profile(session, user=user_check2)
        assert profile2 is not None
        assert profile2.profile_version == 3

        # Older facts from imp1 must be SUPERSEDED with inactive_from_profile_version == 3
        superseded_imp1 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_check2.id,
                    ProfileFact.source_ref == str(imp1_id),
                )
            )
        )
        assert len(superseded_imp1) == 3
        for f in superseded_imp1:
            assert f.status == FactStatus.SUPERSEDED
            assert f.inactive_from_profile_version == 3

        # Manual fact must remain ACTIVE
        manual = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_check2.id,
                ProfileFact.source_kind == FactSourceKind.USER_DIRECT,
            )
        )
        assert manual is not None
        assert manual.status == FactStatus.ACTIVE
        assert manual.inactive_from_profile_version is None

        # Total active facts must be 4 (3 from imp2 + 1 manual), NOT 7!
        active_facts_v3 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_check2.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts_v3) == 4

    # --- IMPORT 3 (Same PDF, third time) ---
    with session_factory() as session:
        user3 = session.get(User, user_id)
        assert user3 is not None

        imp3 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user3.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_ricardo.pdf",
            media_type="application/pdf",
            byte_size=len(pdf_bytes),
            checksum_sha256=checksum,
            storage_key=f"resumes/{user3.id}/imp3.pdf",
            parser_identity="test-parser",
        )
        session.add(imp3)
        session.flush()

        cand7 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp3.id,
            user_id=user3.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand8 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp3.id,
            user_id=user3.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Gestão de Stakeholders"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand9 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp3.id,
            user_id=user3.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "Português", "proficiency": "Nativo"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add_all([cand7, cand8, cand9])
        session.commit()

        decisions3 = [
            CandidateReviewDecision(candidate_id=cand7.id, decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=cand8.id, decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=cand9.id, decision=ReviewDecisionKind.ACCEPT),
        ]
        res3 = resume_import_service.finalize_resume_import(
            session,
            user=user3,
            import_id=imp3.id,
            command=FinalizeResumeImportCommand(expected_profile_version=3, decisions=decisions3),
        )
        session.commit()
        assert res3.resume_version.profile_version == 4

    # Final Verification after 3x imports
    with session_factory() as session:
        user_check3 = session.get(User, user_id)
        assert user_check3 is not None
        profile3 = career_profile_service.get_career_profile(session, user=user_check3)
        assert profile3 is not None
        assert profile3.profile_version == 4

        # Total active facts remains 4 (3 from imp3 + 1 manual)
        active_facts_v4 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_check3.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts_v4) == 4

        # Manual fact remains active
        manual = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_check3.id,
                ProfileFact.source_kind == FactSourceKind.USER_DIRECT,
            )
        )
        assert manual is not None
        assert manual.status == FactStatus.ACTIVE

        # Master resume version is at version 3
        master = resume_import_service.get_master_resume(session, user=user_check3)
        assert master is not None
        _resume, resume_version = master
        assert resume_version.version == 3
        assert resume_version.profile_version == 4


def test_cross_document_language_conflict_does_not_finalize_success() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # 1. Finalize import 1 with English (Intermediate)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v1.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_v1",
            storage_key="k1",
        )
        session.add(imp1)
        cand1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=cand1.id, decision=ReviewDecisionKind.ACCEPT
                    )
                ],
            ),
        )
        session.commit()

    # 2. Finalize import 2 (different checksum) with English (Advanced)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=1100,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.commit()

        # Fails closed with CrossDocumentConflictError -- does not report success
        with pytest.raises(CrossDocumentConflictError) as exc_info:
            resume_import_service.finalize_resume_import(
                session,
                user=u,
                import_id=imp2.id,
                command=FinalizeResumeImportCommand(
                    expected_profile_version=1,
                    decisions=[
                        CandidateReviewDecision(
                            candidate_id=cand2.id, decision=ReviewDecisionKind.ACCEPT
                        )
                    ],
                ),
            )
        session.commit()
        assert len(exc_info.value.conflicts) == 1
        imp2_id = imp2.id

    # Verification: Import remains READY_FOR_REVIEW (non-terminal)
    with session_factory() as session:
        imp2_db = session.get(ResumeImport, imp2_id)
        assert imp2_db is not None
        assert imp2_db.status == ImportStatus.READY_FOR_REVIEW
        assert imp2_db.review_metadata is not None
        conflicts = imp2_db.review_metadata.get("cross_document_conflicts", [])
        assert len(conflicts) == 1
        assert list(conflicts[0]["semantic_identity"]) == ["language", "english"]


def test_cross_document_experience_conflict_does_not_finalize_success() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # 1. Finalize import 1 with Current Role (end=None)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v1.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_v1",
            storage_key="k1",
        )
        session.add(imp1)
        cand1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="experience",
            value_json={
                "kind": "experience",
                "employer": "Inpasa Brasil",
                "title": "Backend Lead",
                "start": {"year": 2021, "month": 1},
                "end": None,
            },
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=cand1.id, decision=ReviewDecisionKind.ACCEPT
                    )
                ],
            ),
        )
        session.commit()

    # 2. Finalize import 2 (different checksum) with Ended Role (end=2024-12)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=1100,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="experience",
            value_json={
                "kind": "experience",
                "employer": "Inpasa Brasil",
                "title": "Backend Lead",
                "start": {"year": 2021, "month": 1},
                "end": {"year": 2024, "month": 12},
            },
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.commit()

        with pytest.raises(CrossDocumentConflictError) as exc_info:
            resume_import_service.finalize_resume_import(
                session,
                user=u,
                import_id=imp2.id,
                command=FinalizeResumeImportCommand(
                    expected_profile_version=1,
                    decisions=[
                        CandidateReviewDecision(
                            candidate_id=cand2.id, decision=ReviewDecisionKind.ACCEPT
                        )
                    ],
                ),
            )
        session.commit()
        assert len(exc_info.value.conflicts) == 1
        imp2_id = imp2.id

    with session_factory() as session:
        imp2_db = session.get(ResumeImport, imp2_id)
        assert imp2_db is not None
        assert imp2_db.status == ImportStatus.READY_FOR_REVIEW
        assert imp2_db.review_metadata is not None
        conflicts = imp2_db.review_metadata.get("cross_document_conflicts", [])
        assert len(conflicts) == 1


def test_conflict_preserves_existing_active_fact() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # Import 1 with English Intermediate
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v1.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_v1",
            storage_key="k1",
        )
        session.add(imp1)
        c1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(candidate_id=c1.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()

    # Import 2 with English Advanced (causes conflict)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=1100,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        c2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c2)
        session.commit()

        with pytest.raises(CrossDocumentConflictError):
            resume_import_service.finalize_resume_import(
                session,
                user=u,
                import_id=imp2.id,
                command=FinalizeResumeImportCommand(
                    expected_profile_version=1,
                    decisions=[
                        CandidateReviewDecision(
                            candidate_id=c2.id, decision=ReviewDecisionKind.ACCEPT
                        )
                    ],
                ),
            )
        session.commit()

    # Verification: Existing fact remains strictly ACTIVE with original values
    with session_factory() as session:
        active_facts = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_id,
                    ProfileFact.fact_type == FactType.LANGUAGE,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts) == 1
        assert active_facts[0].value_json.get("proficiency") == "intermediate"
        assert active_facts[0].status == FactStatus.ACTIVE
        assert active_facts[0].inactive_from_profile_version is None


def test_conflict_proposed_fact_not_active() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v1.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_v1",
            storage_key="k1",
        )
        session.add(imp1)
        c1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(candidate_id=c1.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()

    # Import 2 with English Advanced (conflict)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=1100,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        c2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c2)
        session.commit()

        with pytest.raises(CrossDocumentConflictError):
            resume_import_service.finalize_resume_import(
                session,
                user=u,
                import_id=imp2.id,
                command=FinalizeResumeImportCommand(
                    expected_profile_version=1,
                    decisions=[
                        CandidateReviewDecision(
                            candidate_id=c2.id, decision=ReviewDecisionKind.ACCEPT
                        )
                    ],
                ),
            )
        session.commit()

    # Verification: Proposed value "advanced" is NEVER in ACTIVE facts
    with session_factory() as session:
        all_user_facts = list(
            session.scalars(select(ProfileFact).where(ProfileFact.user_id == user_id))
        )
        advanced_facts = [
            f for f in all_user_facts if f.value_json.get("proficiency") == "advanced"
        ]
        assert len(advanced_facts) == 0


def test_conflict_remains_resolvable() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v1.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_v1",
            storage_key="k1",
        )
        session.add(imp1)
        c1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "intermediate"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(candidate_id=c1.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()

    # Import 2 enters conflict
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=1100,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        c2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="language",
            value_json={"kind": "language", "language": "English", "proficiency": "advanced"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c2)
        session.commit()
        imp2_id = imp2.id
        c2_id = c2.id

        with pytest.raises(CrossDocumentConflictError):
            resume_import_service.finalize_resume_import(
                session,
                user=u,
                import_id=imp2.id,
                command=FinalizeResumeImportCommand(
                    expected_profile_version=1,
                    decisions=[
                        CandidateReviewDecision(
                            candidate_id=c2.id, decision=ReviewDecisionKind.ACCEPT
                        )
                    ],
                ),
            )
        session.commit()

    # Conflict remains resolvable: user rejects the conflicting candidate
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        res = resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp2_id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=1,
                decisions=[
                    CandidateReviewDecision(candidate_id=c2_id, decision=ReviewDecisionKind.REJECT)
                ],
            ),
        )
        session.commit()
        assert res.resume_import.status == ImportStatus.FINALIZED

    # Verification: Import transitioned to FINALIZED, conflict resolved
    with session_factory() as session:
        imp2_db = session.get(ResumeImport, imp2_id)
        assert imp2_db is not None
        assert imp2_db.status == ImportStatus.FINALIZED
        if imp2_db.review_metadata:
            assert "cross_document_conflicts" not in imp2_db.review_metadata


def test_no_conflict_import_still_finalizes_normally() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # Import 1 with Python skill
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v1.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_v1",
            storage_key="k1",
        )
        session.add(imp1)
        c1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(candidate_id=c1.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()

    # Import 2 (different checksum) with Docker skill (different identity -> ADD, no conflict)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv_v2.pdf",
            media_type="application/pdf",
            byte_size=1100,
            checksum_sha256="checksum_v2",
            storage_key="k2",
        )
        session.add(imp2)
        c2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Docker"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c2)
        session.commit()

        res2 = resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp2.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=1,
                decisions=[
                    CandidateReviewDecision(candidate_id=c2.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()
        assert res2.resume_import.status == ImportStatus.FINALIZED
        assert res2.resume_version.profile_version == 2

    # Verification: Both Python and Docker are ACTIVE
    with session_factory() as session:
        active_skills = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_id,
                    ProfileFact.fact_type == FactType.SKILL,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        skill_names = {s.value_json.get("name") for s in active_skills}
        assert skill_names == {"Python", "Docker"}


def test_confirm_all_cannot_accept_structural_conflict() -> None:
    session_factory = _setup_sqlite_db()

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256="checksum_conflict",
            storage_key="k1",
            review_metadata={
                "ambiguities": [
                    {
                        "field": "category_conflict",
                        "term": "docker",
                        "kind": "structural_conflict",
                        "options": ["competência", "idioma"],
                    }
                ]
            },
        )
        session.add(imp)
        cand_python = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand_python)
        session.commit()

        # Finalize accepting all candidates
        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=cand_python.id, decision=ReviewDecisionKind.ACCEPT
                    )
                ],
            ),
        )
        session.commit()

    with session_factory() as session:
        active_facts = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        # Python is active, but conflicting "Docker" is NOT present
        fact_names = [
            f.value_json.get("name") for f in active_facts if f.fact_type == FactType.SKILL
        ]
        assert "Python" in fact_names
        assert "Docker" not in fact_names
        assert len(active_facts) == 1


def test_same_checksum_replacement_preserves_lineage() -> None:
    session_factory = _setup_sqlite_db()
    same_checksum = "shared_checksum_abc123"

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # 1. First import with Python skill
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256=same_checksum,
            storage_key="k1",
        )
        session.add(imp1)
        cand1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=cand1.id, decision=ReviewDecisionKind.ACCEPT
                    )
                ],
            ),
        )
        session.commit()

    with session_factory() as session:
        initial_fact = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id, ProfileFact.status == FactStatus.ACTIVE
            )
        )
        assert initial_fact is not None
        initial_fact_id = initial_fact.id
        initial_lineage_id = initial_fact.lineage_id
        assert initial_fact.lineage_id == initial_fact.id

    # 2. Second import of the EXACT SAME file (same checksum)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256=same_checksum,
            storage_key="k2",
        )
        session.add(imp2)
        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(cand2)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp2.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=1,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=cand2.id, decision=ReviewDecisionKind.ACCEPT
                    )
                ],
            ),
        )
        session.commit()

    # Verification: Old fact is SUPERSEDED, new fact inherits lineage_id and sets supersedes_fact_id
    with session_factory() as session:
        old_f = session.get(ProfileFact, initial_fact_id)
        assert old_f is not None
        assert old_f.status == FactStatus.SUPERSEDED
        assert old_f.inactive_from_profile_version == 2

        new_f = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id, ProfileFact.status == FactStatus.ACTIVE
            )
        )
        assert new_f is not None
        assert new_f.id != initial_fact_id
        assert new_f.lineage_id == initial_lineage_id  # LINEAGE IS PRESERVED!
        assert new_f.supersedes_fact_id == initial_fact_id  # CHAINED TO PREDECESSOR!
        assert new_f.active_from_profile_version == 2


def test_same_checksum_removed_fact_becomes_retracted() -> None:
    session_factory = _setup_sqlite_db()
    same_checksum = "shared_checksum_xyz789"

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # 1. First import with Python and Go skills
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256=same_checksum,
            storage_key="k1",
        )
        session.add(imp1)
        c_python = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        c_go = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Go"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add_all([c_python, c_go])
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=0,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=c_python.id, decision=ReviewDecisionKind.ACCEPT
                    ),
                    CandidateReviewDecision(
                        candidate_id=c_go.id, decision=ReviewDecisionKind.ACCEPT
                    ),
                ],
            ),
        )
        session.commit()

    with session_factory() as session:
        facts_v1 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_id, ProfileFact.status == FactStatus.ACTIVE
                )
            )
        )
        assert len(facts_v1) == 2
        go_fact_id = next(f.id for f in facts_v1 if f.value_json.get("name") == "Go")

    # 2. Second import of the same file, but this time only Python is accepted
    # (Go is removed/omitted)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256=same_checksum,
            storage_key="k2",
        )
        session.add(imp2)
        c2_python = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c2_python)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp2.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=1,
                decisions=[
                    CandidateReviewDecision(
                        candidate_id=c2_python.id, decision=ReviewDecisionKind.ACCEPT
                    )
                ],
            ),
        )
        session.commit()

    # Verification: Go fact without successor became RETRACTED (not SUPERSEDED without successor)
    with session_factory() as session:
        go_fact = session.get(ProfileFact, go_fact_id)
        assert go_fact is not None
        assert go_fact.status == FactStatus.RETRACTED
        assert go_fact.inactive_from_profile_version == 2
        assert go_fact.supersedes_fact_id is None


def test_manual_fact_survives_reimport() -> None:
    session_factory = _setup_sqlite_db()
    same_checksum = "same_checksum_manual_test"

    with session_factory() as session:
        user = User(id=uuid.uuid4(), status=UserStatus.ACTIVE, locale="pt-BR", timezone="UTC")
        session.add(user)
        session.commit()
        user_id = user.id

    # 1. User adds manual fact (USER_DIRECT)
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        profile = career_profile_service.bump_profile_version(session, user=u, expected_version=0)
        manual_id = uuid.uuid4()
        manual_fact = ProfileFact(
            id=manual_id,
            user_id=u.id,
            career_profile_id=profile.id,
            fact_type=FactType.SKILL,
            value_json={"kind": "skill", "name": "Leadership"},
            source_kind=FactSourceKind.USER_DIRECT,
            source_ref="manual_entry",
            user_confirmed=True,
            status=FactStatus.ACTIVE,
            lineage_id=manual_id,
            active_from_profile_version=1,
        )
        session.add(manual_fact)
        session.commit()

    # 2. User imports resume first time
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256=same_checksum,
            storage_key="k1",
        )
        session.add(imp1)
        c1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c1)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=1,
                decisions=[
                    CandidateReviewDecision(candidate_id=c1.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()

    # 3. User re-imports same resume second time
    with session_factory() as session:
        u = session.get(User, user_id)
        assert u is not None
        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=u.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="cv.pdf",
            media_type="application/pdf",
            byte_size=1000,
            checksum_sha256=same_checksum,
            storage_key="k2",
        )
        session.add(imp2)
        c2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=u.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        session.add(c2)
        session.commit()

        resume_import_service.finalize_resume_import(
            session,
            user=u,
            import_id=imp2.id,
            command=FinalizeResumeImportCommand(
                expected_profile_version=2,
                decisions=[
                    CandidateReviewDecision(candidate_id=c2.id, decision=ReviewDecisionKind.ACCEPT)
                ],
            ),
        )
        session.commit()

    # Verification: Manual fact remains strictly ACTIVE throughout
    with session_factory() as session:
        manual_check = session.get(ProfileFact, manual_id)
        assert manual_check is not None
        assert manual_check.status == FactStatus.ACTIVE
        assert manual_check.inactive_from_profile_version is None
