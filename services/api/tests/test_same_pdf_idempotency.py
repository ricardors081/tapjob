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
        user = User(
            id=uuid.uuid4(),
            status=UserStatus.ACTIVE,
            locale="pt-BR",
            timezone="America/Cuiaba",
        )
        session.add(user)
        session.commit()
        user_id = user.id

    pdf_bytes = b"%PDF-1.4 Mock resume content for Ricardo Silva with unique payload"
    checksum = hashlib.sha256(pdf_bytes).hexdigest()

    # --- IMPORT 1 ---
    with session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None

        imp1 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_ricardo.pdf",
            media_type="application/pdf",
            byte_size=len(pdf_bytes),
            checksum_sha256=checksum,
            storage_key=f"resumes/{user.id}/imp1.pdf",
            parser_identity="test-parser",
        )
        session.add(imp1)
        session.flush()

        imp1_id = imp1.id

        # Add 3 candidates: 2 skills, 1 language
        cand1 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand2 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Gestão de Stakeholders"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand3 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp1.id,
            user_id=user.id,
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
            user=user,
            import_id=imp1.id,
            command=FinalizeResumeImportCommand(expected_profile_version=0, decisions=decisions1),
        )
        session.commit()
        assert res1.resume_version.profile_version == 1

    # Verify state after Import 1
    with session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        profile = career_profile_service.get_career_profile(session, user=user)
        assert profile.profile_version == 1

        active_facts = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts) == 3

        # Add a MANUAL fact (source_kind=user_direct)
        manual_fact = career_profile_service.add_profile_fact(
            session,
            user=user,
            expected_version=1,
            fact_type=FactType.SKILL,
            value=SkillFactValue(name="Liderança Estratégica"),
        )
        session.commit()
        assert manual_fact.source_kind == FactSourceKind.USER_DIRECT

        profile = career_profile_service.get_career_profile(session, user=user)
        assert profile.profile_version == 2

        active_facts_v2 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts_v2) == 4  # 3 resume + 1 manual

    # --- IMPORT 2 (Same PDF, same checksum) ---
    with session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None

        imp2 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_ricardo.pdf",
            media_type="application/pdf",
            byte_size=len(pdf_bytes),
            checksum_sha256=checksum,
            storage_key=f"resumes/{user.id}/imp2.pdf",
            parser_identity="test-parser",
        )
        session.add(imp2)
        session.flush()
        imp2_id = imp2.id
        assert imp2_id != imp1_id

        cand4 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand5 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Gestão de Stakeholders"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand6 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp2.id,
            user_id=user.id,
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
            user=user,
            import_id=imp2.id,
            command=FinalizeResumeImportCommand(expected_profile_version=2, decisions=decisions2),
        )
        session.commit()
        assert res2.resume_version.profile_version == 3

    # Verify state after Import 2:
    with session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        profile = career_profile_service.get_career_profile(session, user=user)
        assert profile.profile_version == 3

        # Older facts from imp1 must be SUPERSEDED with inactive_from_profile_version == 3
        superseded_imp1 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user.id,
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
                ProfileFact.user_id == user.id,
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
                    ProfileFact.user_id == user.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts_v3) == 4

    # --- IMPORT 3 (Same PDF, third time) ---
    with session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None

        imp3 = ResumeImport(
            id=uuid.uuid4(),
            user_id=user.id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_ricardo.pdf",
            media_type="application/pdf",
            byte_size=len(pdf_bytes),
            checksum_sha256=checksum,
            storage_key=f"resumes/{user.id}/imp3.pdf",
            parser_identity="test-parser",
        )
        session.add(imp3)
        session.flush()

        cand7 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp3.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Python"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand8 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp3.id,
            user_id=user.id,
            candidate_kind="skill",
            value_json={"kind": "skill", "name": "Gestão de Stakeholders"},
            confidence=1.0,
            review_state=CandidateReviewState.PENDING,
        )
        cand9 = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=imp3.id,
            user_id=user.id,
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
            user=user,
            import_id=imp3.id,
            command=FinalizeResumeImportCommand(expected_profile_version=3, decisions=decisions3),
        )
        session.commit()
        assert res3.resume_version.profile_version == 4

    # Final Verification after 3x imports
    with session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        profile = career_profile_service.get_career_profile(session, user=user)
        assert profile.profile_version == 4

        # Total active facts remains 4 (3 from imp3 + 1 manual)
        active_facts_v4 = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user.id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            )
        )
        assert len(active_facts_v4) == 4

        # Manual fact remains active
        manual = session.scalar(
            select(ProfileFact).where(
                ProfileFact.user_id == user.id,
                ProfileFact.source_kind == FactSourceKind.USER_DIRECT,
            )
        )
        assert manual is not None
        assert manual.status == FactStatus.ACTIVE

        # Master resume version is at version 3
        master = resume_import_service.get_master_resume(session, user=user)
        assert master is not None
        _resume, resume_version = master
        assert resume_version.version == 3
        assert resume_version.profile_version == 4
