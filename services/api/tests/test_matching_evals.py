"""15 Golden Match Evaluations for WP2.2 Matching Engine (ADR-0014 §4 Part A).

Covers all 15 canonical regression scenarios:
1. perfect_match
2. missing_mandatory_skill
3. wrong_work_authorization
4. remote_preference_mismatch
5. salary_same_currency_same_period
6. salary_different_currency_or_period
7. missing_salary
8. relocation_unknown
9. free_form_work_authorization
10. seniority_mismatch
11. synonyms_and_aliases
12. bilingual_pt_en
13. sparse_candidate_profile
14. job_without_extracted_skills
15. workplace_type_change_fingerprint
"""

import uuid
from datetime import date

from tapjob_api.career_profile.fact_values import SalaryPeriod
from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.engine import evaluate_match
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.signals import (
    derive_job_match_signals,
)
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    CandidateExperienceSignal,
    CandidateLocationPreference,
    CandidateMatchSignals,
    CandidateSalaryPreference,
    CandidateSeniorityLevel,
    CandidateSkillSignal,
    CriticalUnknown,
    HardFilterId,
    HardFilterOutcome,
    MatchEligibility,
    WorkAuthSemanticValue,
)


def _make_job(
    *,
    title: str = "Senior Backend Engineer",
    workplace_type: JobWorkplaceType = JobWorkplaceType.REMOTE,
    location_normalized: str | None = "sao paulo, sp",
    description_cleaned: str = "Requisitos: python, postgresql, docker",
) -> Job:
    job = Job()
    job.id = uuid.uuid4()
    job.title = title
    job.title_normalized = title.lower()
    job.workplace_type = workplace_type
    job.location_normalized = location_normalized
    job.description_cleaned = description_cleaned
    return job


def test_eval_01_perfect_match() -> None:
    # All required/preferred skills matched, matching title, remote preference satisfied
    cand = CandidateMatchSignals(
        skills=(
            CandidateSkillSignal("python", (uuid.uuid4(),)),
            CandidateSkillSignal("postgresql", (uuid.uuid4(),)),
            CandidateSkillSignal("docker", (uuid.uuid4(),)),
        ),
        skill_names=frozenset({"python", "postgresql", "docker"}),
        experiences=(
            CandidateExperienceSignal(
                "Senior Backend Engineer", "senior backend engineer", "Acme", 72, uuid.uuid4()
            ),
        ),
        total_experience_months=72,
        domain_tokens=(),
        domain_token_names=frozenset({"backend", "python"}),
        target_roles=("Senior Backend Engineer",),
        derived_seniority=CandidateSeniorityLevel.SENIOR,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=4,
    )
    job = _make_job(
        title="Senior Backend Engineer",
        workplace_type=JobWorkplaceType.REMOTE,
        description_cleaned="""
        Requisitos:
        - python
        - postgresql

        Diferenciais:
        - docker
        """,
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert core.eligibility == MatchEligibility.ELIGIBLE
    assert core.overall_score >= 90
    assert len(core.blockers) == 0


def test_eval_02_missing_mandatory_skill() -> None:
    # Core requirements missing -> missing skills populated, partial score
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(
            CandidateExperienceSignal(
                "Backend Engineer", "backend engineer", "Acme", 36, uuid.uuid4()
            ),
        ),
        total_experience_months=36,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Backend Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=2,
    )
    job = _make_job(
        title="Backend Engineer",
        workplace_type=JobWorkplaceType.REMOTE,
        description_cleaned="""
        Requisitos:
        - python
        - rust
        """,
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    # Missing mandatory skill rust is populated
    missing_names = {m.name for m in core.missing_requirements}
    assert "rust" in missing_names
    assert 55 <= core.overall_score <= 85


def test_eval_03_wrong_work_authorization() -> None:
    # Confirmed negative work authorization for required country -> INELIGIBLE, score 0, blocker
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(CandidateExperienceSignal("Engineer", "engineer", "Acme", 36, uuid.uuid4()),),
        total_experience_months=36,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={
            ("BR", "legally_authorized_to_work"): WorkAuthSemanticValue.NOT_AUTHORIZED
        },
        confirmed_fact_count=2,
    )
    job = _make_job(
        description_cleaned=(
            "Must be legally authorized to work in Brazil. We do not sponsor visas."
        ),
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert core.overall_score == 0
    assert core.eligibility == MatchEligibility.INELIGIBLE
    assert any(b.filter_id == HardFilterId.WORK_AUTHORIZATION for b in core.blockers)


def test_eval_04_remote_preference_mismatch() -> None:
    # Acceptable workplace_types=[REMOTE] vs on-site role in another city -> INELIGIBLE, score 0
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job = _make_job(
        workplace_type=JobWorkplaceType.ON_SITE,
        location_normalized="curitiba, pr",
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert core.overall_score == 0
    assert core.eligibility == MatchEligibility.INELIGIBLE
    assert any(b.filter_id == HardFilterId.WORKPLACE_TYPE for b in core.blockers)


def test_eval_05_salary_same_currency_same_period() -> None:
    # Candidate min > job max -> SOFT_MISMATCH with zero score penalty, not blocked
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(CandidateExperienceSignal("Engineer", "engineer", "Corp", 36, uuid.uuid4()),),
        total_experience_months=36,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=CandidateSalaryPreference(
            minor_unit_min=1500000,
            minor_unit_max=2000000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
        ),
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=2,
    )
    job = _make_job(
        title="Engineer",
        workplace_type=JobWorkplaceType.REMOTE,
        description_cleaned="Salário: R$ 8.000 a R$ 12.000 por mês. Requisitos: python",
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    # Not hard blocked
    assert len(core.blockers) == 0
    assert core.overall_score >= 50
    # Soft mismatch noted in hard filter results
    sal_filter = next(
        f for f in core.hard_filter_results if f.filter_id == HardFilterId.SALARY_MINIMUM
    )
    assert sal_filter.outcome == HardFilterOutcome.SOFT_MISMATCH


def test_eval_06_salary_different_currency_or_period() -> None:
    # Cross currency -> UNKNOWN (neutral, zero penalty, no blocker)
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=CandidateSalaryPreference(
            minor_unit_min=10000000,
            minor_unit_max=12000000,
            currency="USD",
            period=SalaryPeriod.ANNUAL,
        ),
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job = _make_job(
        description_cleaned="Salário: R$ 10.000 por mês",
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    sal_filter = next(
        f for f in core.hard_filter_results if f.filter_id == HardFilterId.SALARY_MINIMUM
    )
    assert sal_filter.outcome == HardFilterOutcome.UNKNOWN
    assert len(core.blockers) == 0


def test_eval_07_missing_salary() -> None:
    # Job omits salary -> UNKNOWN
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=CandidateSalaryPreference(1000000, 1500000, "BRL", SalaryPeriod.MONTHLY),
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job = _make_job(description_cleaned="Ótimo ambiente de trabalho.")
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    sal_filter = next(
        f for f in core.hard_filter_results if f.filter_id == HardFilterId.SALARY_MINIMUM
    )
    assert sal_filter.outcome == HardFilterOutcome.UNKNOWN


def test_eval_08_relocation_unknown() -> None:
    # On-site job in another city -> SOFT_MISMATCH on location, never hard blocker
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.ON_SITE}),
        acceptable_locations=(CandidateLocationPreference("BR", "SP", "sao paulo"),),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job = _make_job(
        workplace_type=JobWorkplaceType.ON_SITE,
        location_normalized="belo horizonte, mg",
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    loc_filter = next(f for f in core.hard_filter_results if f.filter_id == HardFilterId.LOCATION)
    assert loc_filter.outcome == HardFilterOutcome.SOFT_MISMATCH
    assert len(core.blockers) == 0


def test_eval_09_free_form_work_authorization() -> None:
    # Unrecognized question key -> UNKNOWN (neutral, never falsely blocks)
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={("BR", "unrecognized_custom_key"): WorkAuthSemanticValue.UNKNOWN},
        confirmed_fact_count=1,
    )
    job = _make_job(description_cleaned="Vaga com requisitos flexíveis.")
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert len(core.blockers) == 0


def test_eval_10_seniority_mismatch() -> None:
    # Junior candidate vs Principal/Lead role -> heavy seniority penalty
    cand = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(
            CandidateExperienceSignal("Junior Dev", "junior dev", "Acme", 12, uuid.uuid4()),
        ),
        total_experience_months=12,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Junior Developer",),
        derived_seniority=CandidateSeniorityLevel.JUNIOR,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job = _make_job(title="Principal Architect / Staff Engineer")
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    # Junior vs Staff/Principal in matrix yields 0
    assert core.score_components.seniority == 0
    assert core.overall_score < 40


def test_eval_11_synonyms_and_aliases() -> None:
    # k8s matches kubernetes, postgres matches postgresql -> 100% skill match
    cand = CandidateMatchSignals(
        skills=(
            CandidateSkillSignal("kubernetes", (uuid.uuid4(),)),
            CandidateSkillSignal("postgresql", (uuid.uuid4(),)),
        ),
        skill_names=frozenset({"kubernetes", "postgresql"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=(),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=2,
    )
    job = _make_job(
        description_cleaned="""
        Requisitos:
        - k8s
        - postgres
        """,
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert core.score_components.skills == 100


def test_eval_12_bilingual_pt_en() -> None:
    # Desenvolvedor matches Developer
    cand = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Desenvolvedor de Software",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job = _make_job(title="Software Developer")
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    # 100% title match via alias resolution
    assert core.score_components.title == 100


def test_eval_13_sparse_candidate_profile() -> None:
    # 0 confirmed skills AND 0 confirmed experiences -> SPARSE_PROFILE & REVIEW_REQUIRED
    cand_sparse = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=0,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Software Engineer",),
        derived_seniority=CandidateSeniorityLevel.UNKNOWN,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=0,
    )
    job = _make_job(title="Software Engineer", description_cleaned="")
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand_sparse, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert CriticalUnknown.SPARSE_PROFILE in core.critical_unknowns
    assert core.eligibility == MatchEligibility.REVIEW_REQUIRED


def test_eval_14_job_without_extracted_skills() -> None:
    # Job posting with 0 extracted technical skills -> neutral skills score (50), NOT SPARSE_PROFILE
    cand = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(CandidateExperienceSignal("Engineer", "engineer", "Corp", 36, uuid.uuid4()),),
        total_experience_months=36,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=2,
    )
    job = _make_job(
        title="Engineer",
        description_cleaned=(
            "Vaga para pessoa comunicativa com espírito de liderança e trabalho em equipe."
        ),
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    core = evaluate_match(cand, job_sig, job, date(2026, 1, 1), profile_version=1)

    assert core.score_components.skills == 50
    assert CriticalUnknown.SPARSE_PROFILE not in core.critical_unknowns


def test_eval_15_workplace_type_change_fingerprint() -> None:
    # Workplace hint change alters fingerprint and triggers INELIGIBLE when candidate is remote
    cand_remote_only = CandidateMatchSignals(
        skills=(CandidateSkillSignal("python", (uuid.uuid4(),)),),
        skill_names=frozenset({"python"}),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=(),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    job_v1 = _make_job(workplace_type=JobWorkplaceType.REMOTE)
    job_v2 = _make_job(workplace_type=JobWorkplaceType.ON_SITE)
    job_v2.id = job_v1.id  # Same job ID, but workplace updated

    fp1 = compute_job_match_fingerprint(job_v1)
    fp2 = compute_job_match_fingerprint(job_v2)
    assert fp1 != fp2

    sig_v2 = derive_job_match_signals(job_v2, MATCHING_POLICY_VERSION)
    core_v2 = evaluate_match(cand_remote_only, sig_v2, job_v2, date(2026, 1, 1), profile_version=1)

    assert core_v2.eligibility == MatchEligibility.INELIGIBLE
    assert core_v2.overall_score == 0
    assert any(b.filter_id == HardFilterId.WORKPLACE_TYPE for b in core_v2.blockers)
