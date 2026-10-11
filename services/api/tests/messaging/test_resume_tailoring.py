"""Unit tests proving Finding 5: Claim-Level Grounding in Resume Tailoring.

Enforces:
- Every candidate claim carries exact source_fact_ids that support it
- Zero candidate claim without source_fact_ids
- Removal of fabricated qualifications ("Profissional qualificado para a vaga de...")
- Neutral editorial text distinguished from candidate claims
- ready_for_submission is True ONLY when all candidate claims are grounded
- Test scenarios:
  1. no headline
  2. no skills
  3. sparse profile
  4. experience only
  5. fabricated qualification regression
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import ProfileFact
from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs.models import Job, JobStatus, JobWorkplaceType
from tapjob_api.messaging.resume_tailoring import (
    TailoredClaim,
    generate_grounded_tailored_resume,
    validate_tailored_claims,
)


def _make_user_and_job() -> tuple[User, Job]:
    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    job = Job(
        id=uuid.uuid4(),
        title="Engenheiro de Software Backend",
        title_normalized="engenheiro de software backend",
        status=JobStatus.ACTIVE,
        workplace_type=JobWorkplaceType.REMOTE,
        description_raw="Vaga de engenharia backend com foco em APIs",
        description_cleaned="Vaga de engenharia backend com foco em APIs",
        similarity_fingerprint="fp_job_tailoring",
    )
    return user, job


def _make_skill_fact(fact_id: uuid.UUID, name: str) -> MagicMock:
    fact = MagicMock(spec=ProfileFact)
    fact.id = fact_id
    fact.value_json = {"kind": "skill", "name": name, "normalized_name": name.lower()}
    return fact


def _make_experience_fact(fact_id: uuid.UUID, title: str, employer: str) -> MagicMock:
    fact = MagicMock(spec=ProfileFact)
    fact.id = fact_id
    fact.value_json = {
        "kind": "experience",
        "title": title,
        "employer": employer,
        "current": True,
    }
    return fact


def _make_education_fact(fact_id: uuid.UUID, degree: str, institution: str) -> MagicMock:
    fact = MagicMock(spec=ProfileFact)
    fact.id = fact_id
    fact.value_json = {
        "kind": "education",
        "degree": degree,
        "institution": institution,
    }
    return fact


def test_tailoring_no_headline() -> None:
    """Proves tailoring handles absence of headline without fabricating qualification strings."""
    session = MagicMock(spec=Session)
    user, job = _make_user_and_job()

    fact1_id = uuid.uuid4()
    fact1 = _make_skill_fact(fact1_id, "Python")
    fact2_id = uuid.uuid4()
    fact2 = _make_experience_fact(fact2_id, "Desenvolvedor Backend", "Empresa Alpha")

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,  # No headline
        summary=None,
        target_roles=["Backend Developer"],
        facts=[fact1, fact2],
    )

    # Mock job lookup
    mock_job_result = MagicMock()
    mock_job_result.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_job_result

    with patch(
        "tapjob_api.messaging.resume_tailoring.get_truth_snapshot",
        return_value=snapshot,
    ):
        draft = generate_grounded_tailored_resume(session, user=user, job_id=job.id)

        assert draft.ready_for_submission is True
        assert "headline" not in draft.sections
        # Must not fabricate "Profissional qualificado para a vaga de..."
        assert "Profissional qualificado" not in draft.sections["summary"]
        assert "vaga de" not in draft.sections["summary"]
        assert all(len(c.source_fact_ids) > 0 for c in draft.claims)


def test_tailoring_no_skills() -> None:
    """Proves profile with experience only does not fabricate skills or 'áreas correlatas'."""
    session = MagicMock(spec=Session)
    user, job = _make_user_and_job()

    fact_exp_id = uuid.uuid4()
    fact_exp = _make_experience_fact(fact_exp_id, "Tech Lead", "Tech Solutions")

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline="Tech Lead",
        summary=None,
        target_roles=[],
        facts=[fact_exp],
    )

    mock_job_result = MagicMock()
    mock_job_result.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_job_result

    with patch(
        "tapjob_api.messaging.resume_tailoring.get_truth_snapshot",
        return_value=snapshot,
    ):
        draft = generate_grounded_tailored_resume(session, user=user, job_id=job.id)

        assert draft.ready_for_submission is True
        assert draft.sections["skills"] == "Nenhuma competência registrada"
        assert "áreas correlatas" not in draft.sections["summary"]
        # Only experience claim and summary claim produced
        assert any(c.claim_type == "experience" for c in draft.claims)
        assert not any(c.claim_type == "skill" for c in draft.claims)


def test_tailoring_sparse_profile() -> None:
    """Proves sparse profile (single fact) is strictly grounded and marked ready."""
    session = MagicMock(spec=Session)
    user, job = _make_user_and_job()

    single_skill_id = uuid.uuid4()
    single_skill = _make_skill_fact(single_skill_id, "PostgreSQL")

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,
        summary=None,
        target_roles=[],
        facts=[single_skill],
    )

    mock_job_result = MagicMock()
    mock_job_result.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_job_result

    with patch(
        "tapjob_api.messaging.resume_tailoring.get_truth_snapshot",
        return_value=snapshot,
    ):
        draft = generate_grounded_tailored_resume(session, user=user, job_id=job.id)

        assert draft.ready_for_submission is True
        assert len(draft.grounded_fact_ids) == 1
        assert draft.grounded_fact_ids[0] == single_skill_id
        # All claims must trace back to the single skill fact
        for claim in draft.claims:
            assert single_skill_id in claim.source_fact_ids


def test_tailoring_experience_only() -> None:
    """Proves profile with experience facts only produces valid, grounded draft."""
    session = MagicMock(spec=Session)
    user, job = _make_user_and_job()

    exp1_id = uuid.uuid4()
    exp1 = _make_experience_fact(exp1_id, "Dev 1", "Empresa A")
    exp2_id = uuid.uuid4()
    exp2 = _make_experience_fact(exp2_id, "Dev 2", "Empresa B")

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,
        summary=None,
        target_roles=[],
        facts=[exp1, exp2],
    )

    mock_job_result = MagicMock()
    mock_job_result.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_job_result

    with patch(
        "tapjob_api.messaging.resume_tailoring.get_truth_snapshot",
        return_value=snapshot,
    ):
        draft = generate_grounded_tailored_resume(session, user=user, job_id=job.id)

        assert draft.ready_for_submission is True
        assert draft.sections["skills"] == "Nenhuma competência registrada"
        assert draft.sections["education"] == "Nenhuma formação registrada"
        assert len(draft.claims) == 3  # 2 experience claims + 1 summary claim
        assert all(len(c.source_fact_ids) > 0 for c in draft.claims)


def test_tailoring_fabricated_qualification_regression() -> None:
    """Proves regression: claim with fabricated qualification text or missing facts fails."""
    fact_id = uuid.uuid4()
    fact = _make_skill_fact(fact_id, "Go")
    snapshot_facts = {fact_id: fact}

    # 1. Fabricated qualification claim rejected
    fabricated_claim = TailoredClaim(
        claim_type="headline",
        text="Profissional qualificado para a vaga de Engenheiro Go",
        source_fact_ids=[fact_id],
    )
    is_grounded, val = validate_tailored_claims([fabricated_claim], snapshot_facts)
    assert is_grounded is False
    assert any("fabricated_qualification_detected" in err for err in val["unverified_claims"])

    # 2. Claim without source_fact_ids rejected
    unlinked_claim = TailoredClaim(
        claim_type="skill",
        text="Go",
        source_fact_ids=[],
    )
    is_grounded, val = validate_tailored_claims([unlinked_claim], snapshot_facts)
    assert is_grounded is False
    assert any("missing_source_fact_ids" in err for err in val["unverified_claims"])

    # 3. Claim with non-existent fact ID rejected
    foreign_id = uuid.uuid4()
    foreign_claim = TailoredClaim(
        claim_type="skill",
        text="Go",
        source_fact_ids=[foreign_id],
    )
    is_grounded, val = validate_tailored_claims([foreign_claim], snapshot_facts)
    assert is_grounded is False
    assert any("ungrounded_fact_ids" in err for err in val["unverified_claims"])
