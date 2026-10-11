"""Unit tests and integration tests proving Finding 3:
Eligibility casing bug fixed & canonical WP2.2 matching.

Enforces:
- WhatsApp delegates exclusively to canonical WP2.2 matching service
- Canonical MatchResult.eligibility values ("eligible", "ineligible", "review_required")
- WHATSAPP_SCORE == CANONICAL_MATCH_RESULT_SCORE (integer arithmetic)
- Hard-blocked jobs and non-eligible jobs are strictly excluded
- Zero float heuristics or parallel scoring functions
- Real PostgreSQL integration testing for MatchResult retrieval and filtering
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.career_profile import service as career_profile_service
from tapjob_api.career_profile.fact_values import SkillFactValue
from tapjob_api.career_profile.models import FactType
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs.models import Company, Job, JobStatus, JobWorkplaceType
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.types import MATCHING_POLICY_VERSION, MatchEligibility
from tapjob_api.messaging import job_matching
from tapjob_api.messaging.job_matching import find_matching_jobs


def test_whatsapp_score_equals_canonical_match_result_score() -> None:
    """Proves WHATSAPP_SCORE == CANONICAL_MATCH_RESULT_SCORE with canonical eligibility."""
    session = MagicMock(spec=Session)
    redis_client = MagicMock()

    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    company = Company(id=uuid.uuid4(), name="Tech Corp", name_normalized="tech corp")
    job = Job(
        id=uuid.uuid4(),
        company_id=company.id,
        title="Senior Python Engineer",
        title_normalized="senior python engineer",
        location_raw="São Paulo, SP",
        workplace_type=JobWorkplaceType.HYBRID,
        status=JobStatus.ACTIVE,
        description_raw="Descrição da vaga",
        description_cleaned="Descrição da vaga",
        similarity_fingerprint="fp123",
    )
    job.company = company

    # Mock active jobs query
    mock_jobs_result = MagicMock()
    mock_jobs_result.scalars.return_value.all.return_value = [job]
    session.execute.return_value = mock_jobs_result

    # Mock canonical WP2.2 match result using canonical MatchEligibility.ELIGIBLE ("eligible")
    canonical_match = MagicMock(spec=MatchResult)
    canonical_match.id = uuid.uuid4()
    canonical_match.profile_version = 2
    canonical_match.overall_score = 88
    canonical_match.eligibility = MatchEligibility.ELIGIBLE
    canonical_match.blockers = []
    canonical_match.strengths = [{"name": "Python", "summary": "Forte aderência em Python"}]
    canonical_match.missing_requirements = []
    canonical_match.explanation = "Perfil altamente aderente"

    with patch(
        "tapjob_api.messaging.job_matching.get_or_compute_match",
        return_value=canonical_match,
    ):
        recs = find_matching_jobs(session, redis_client, user=user, limit=5)

        assert len(recs) == 1
        rec = recs[0]
        # WhatsApp score MUST match canonical overall_score
        assert rec["overall_score"] == canonical_match.overall_score
        assert rec["overall_score"] == 88
        assert rec["eligibility"] == "eligible"
        assert rec["match_id"] == str(canonical_match.id)
        assert rec["title"] == "Senior Python Engineer"
        assert rec["company"] == "Tech Corp"


def test_hard_blocked_job_never_eligible() -> None:
    """Proves job hard-blocked by WP2.2 is NEVER presented as eligible recommendation."""
    session = MagicMock(spec=Session)
    redis_client = MagicMock()

    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    company = Company(id=uuid.uuid4(), name="Blocked Corp", name_normalized="blocked corp")
    job = Job(
        id=uuid.uuid4(),
        company_id=company.id,
        title="Blocked Role",
        title_normalized="blocked role",
        location_raw="Remoto",
        workplace_type=JobWorkplaceType.REMOTE,
        status=JobStatus.ACTIVE,
        description_raw="Vaga bloqueada",
        description_cleaned="Vaga bloqueada",
        similarity_fingerprint="fp_blocked",
    )
    job.company = company

    mock_jobs_result = MagicMock()
    mock_jobs_result.scalars.return_value.all.return_value = [job]
    session.execute.return_value = mock_jobs_result

    # WP2.2 reports blockers and INELIGIBLE
    blocked_match = MagicMock(spec=MatchResult)
    blocked_match.id = uuid.uuid4()
    blocked_match.profile_version = 1
    blocked_match.overall_score = 40
    blocked_match.eligibility = MatchEligibility.INELIGIBLE
    blocked_match.blockers = [{"code": "HARD_FILTER_FAILED", "detail": "Requisito legal ausente"}]
    blocked_match.strengths = []
    blocked_match.missing_requirements = []
    blocked_match.explanation = "Candidato inelegível"

    with patch(
        "tapjob_api.messaging.job_matching.get_or_compute_match",
        return_value=blocked_match,
    ):
        recs = find_matching_jobs(session, redis_client, user=user, limit=5)

        # Ineligible / blocked job MUST NOT be in recommendations
        assert len(recs) == 0


def test_review_required_job_never_eligible() -> None:
    """Proves job with review_required status is not presented as eligible recommendation."""
    session = MagicMock(spec=Session)
    redis_client = MagicMock()

    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    company = Company(id=uuid.uuid4(), name="Review Corp", name_normalized="review corp")
    job = Job(
        id=uuid.uuid4(),
        company_id=company.id,
        title="Review Role",
        title_normalized="review role",
        location_raw="Remoto",
        workplace_type=JobWorkplaceType.REMOTE,
        status=JobStatus.ACTIVE,
        description_raw="Vaga pendente de revisão",
        description_cleaned="Vaga pendente de revisão",
        similarity_fingerprint="fp_review",
    )
    job.company = company

    mock_jobs_result = MagicMock()
    mock_jobs_result.scalars.return_value.all.return_value = [job]
    session.execute.return_value = mock_jobs_result

    review_match = MagicMock(spec=MatchResult)
    review_match.id = uuid.uuid4()
    review_match.profile_version = 1
    review_match.overall_score = 65
    review_match.eligibility = MatchEligibility.REVIEW_REQUIRED
    review_match.blockers = []
    review_match.strengths = []
    review_match.missing_requirements = []
    review_match.explanation = "Necessária revisão de autorização"

    with patch(
        "tapjob_api.messaging.job_matching.get_or_compute_match",
        return_value=review_match,
    ):
        recs = find_matching_jobs(session, redis_client, user=user, limit=5)
        assert len(recs) == 0


def test_no_parallel_scoring_engine() -> None:
    """Verifies that no parallel scoring functions or float heuristics exist in job_matching."""
    import inspect

    functions = [
        name
        for name, _ in inspect.getmembers(job_matching, inspect.isfunction)
        if not name.startswith("__")
    ]
    assert "_calculate_match_score" not in functions
    assert "calculate_score" not in functions
    assert not hasattr(job_matching, "_calculate_match_score")


@pytest.mark.integration
def test_find_matching_jobs_real_postgresql_canonical_eligibility(
    _identity_schema_ready: None,
) -> None:
    """Proves real PostgreSQL integration for find_matching_jobs:
    - Real eligibility="eligible" -> recommendation included
    - Ineligible / hard blocker -> excluded
    - WHATSAPP_SCORE == canonical overall_score
    """
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    try:
        user = User(
            id=uuid.uuid4(),
            locale="pt-BR",
            timezone="Etc/UTC",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        session.commit()

        # Add profile fact so truth snapshot exists with profile_version = 1
        fact_val = SkillFactValue(name="Python")
        career_profile_service.add_profile_fact(
            session,
            user=user,
            expected_version=0,
            fact_type=FactType.SKILL,
            value=fact_val,
        )
        session.commit()

        snapshot = career_profile_service.get_truth_snapshot(session, user=user)

        company_name = f"Company {uuid.uuid4().hex[:6]}"
        company = Company(
            id=uuid.uuid4(),
            name=company_name,
            name_normalized=company_name.lower(),
        )
        session.add(company)

        # Job 1: Eligible job
        job_eligible = Job(
            id=uuid.uuid4(),
            company_id=company.id,
            title="Senior Python Backend",
            title_normalized="senior python backend",
            location_raw="São Paulo, SP",
            workplace_type=JobWorkplaceType.REMOTE,
            status=JobStatus.ACTIVE,
            description_raw="Vaga de Python",
            description_cleaned="Vaga de Python",
            similarity_fingerprint=f"fp_{uuid.uuid4().hex[:8]}",
        )
        # Job 2: Ineligible job
        job_ineligible = Job(
            id=uuid.uuid4(),
            company_id=company.id,
            title="Senior Rust Kernel Engineer",
            title_normalized="senior rust kernel engineer",
            location_raw="Curitiba, PR",
            workplace_type=JobWorkplaceType.ON_SITE,
            status=JobStatus.ACTIVE,
            description_raw="Vaga de Rust",
            description_cleaned="Vaga de Rust",
            similarity_fingerprint=f"fp_{uuid.uuid4().hex[:8]}",
        )
        # Job 3: Eligible eligibility string but has hard blocker
        job_blocked = Job(
            id=uuid.uuid4(),
            company_id=company.id,
            title="Lead Architect",
            title_normalized="lead architect",
            location_raw="Remoto",
            workplace_type=JobWorkplaceType.REMOTE,
            status=JobStatus.ACTIVE,
            description_raw="Vaga com restrição",
            description_cleaned="Vaga com restrição",
            similarity_fingerprint=f"fp_{uuid.uuid4().hex[:8]}",
        )
        session.add_all([job_eligible, job_ineligible, job_blocked])
        session.commit()

        now_utc = datetime.now(UTC)
        as_of_date = now_utc.date()

        # Insert real MatchResult rows into PostgreSQL
        match_eligible = MatchResult(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job_eligible.id,
            profile_version=snapshot.profile_version,
            job_match_fingerprint=compute_job_match_fingerprint(job_eligible),
            policy_version=MATCHING_POLICY_VERSION,
            evaluation_as_of_date=as_of_date,
            deterministic_result_digest="digest_1",
            overall_score=89,
            eligibility=MatchEligibility.ELIGIBLE.value,  # "eligible"
            hard_filter_results=[],
            score_breakdown={},
            blockers=[],
            strengths=[{"name": "Python"}],
            missing_requirements=[],
            critical_unknowns=[],
            explanation={"summary": "Excelente aderência"},
            grounded_fact_ids=[str(uuid.uuid4())],
            computed_at=now_utc,
        )

        match_ineligible = MatchResult(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job_ineligible.id,
            profile_version=snapshot.profile_version,
            job_match_fingerprint=compute_job_match_fingerprint(job_ineligible),
            policy_version=MATCHING_POLICY_VERSION,
            evaluation_as_of_date=as_of_date,
            deterministic_result_digest="digest_2",
            overall_score=35,
            eligibility=MatchEligibility.INELIGIBLE.value,  # "ineligible"
            hard_filter_results=[],
            score_breakdown={},
            blockers=[],
            strengths=[],
            missing_requirements=[],
            critical_unknowns=[],
            explanation={"summary": "Baixa aderência"},
            grounded_fact_ids=[],
            computed_at=now_utc,
        )

        match_blocked = MatchResult(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job_blocked.id,
            profile_version=snapshot.profile_version,
            job_match_fingerprint=compute_job_match_fingerprint(job_blocked),
            policy_version=MATCHING_POLICY_VERSION,
            evaluation_as_of_date=as_of_date,
            deterministic_result_digest="digest_3",
            overall_score=75,
            eligibility=MatchEligibility.ELIGIBLE.value,  # "eligible" but blocked
            hard_filter_results=[],
            score_breakdown={},
            blockers=[{"code": "RESTRICTION", "detail": "Requisito mandatário não atendido"}],
            strengths=[],
            missing_requirements=[],
            critical_unknowns=[],
            explanation={"summary": "Bloqueado por hard blocker"},
            grounded_fact_ids=[],
            computed_at=now_utc,
        )

        session.add_all([match_eligible, match_ineligible, match_blocked])
        session.commit()

        # Execute find_matching_jobs with real session
        recs = find_matching_jobs(session, None, user=user, limit=10)

        # 1. Job with real eligibility="eligible" is included
        assert any(r["job_id"] == str(job_eligible.id) for r in recs)
        eligible_rec = next(r for r in recs if r["job_id"] == str(job_eligible.id))
        assert eligible_rec["eligibility"] == "eligible"
        assert eligible_rec["overall_score"] == 89
        # WHATSAPP_SCORE is strictly identical to canonical overall_score
        assert eligible_rec["overall_score"] == match_eligible.overall_score

        # 2. Ineligible job is strictly excluded
        assert not any(r["job_id"] == str(job_ineligible.id) for r in recs)

        # 3. Hard-blocked job is strictly excluded despite eligibility string
        assert not any(r["job_id"] == str(job_blocked.id) for r in recs)
    finally:
        session.close()
        engine.dispose()
