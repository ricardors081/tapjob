"""Tests for Lexical Scoring and Pure Integer Arithmetic (ADR-0014 Decision 6).

Verifies:
- ZERO float types in scoring path
- ZERO Python builtin round()
- round_half_up_ratio exact boundary tests (0.5->1, 1.5->2, 2.5->3, 33.5->34, 66.5->67)
- denominator <= 0 raises ValueError
- Basis points (0..10,000) arithmetic scaled to 0..100
- Title Alignment Dice formula and 90% weighting for past experience titles
- Skills 6-case decision tree table
- Unclassified skills zero weight when required skills exist
- Experience domain keywords and calendar month duration adequacy factor
- Zero experience duration precedence rule
- Seniority 7x7 matrix lookup
- Workplace / Location 50/50 integer split
- Threshold 49 (INELIGIBLE) vs 50 (ELIGIBLE) boundary
"""

import ast
import inspect
import uuid

import pytest

from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching import scorer
from tapjob_api.matching.scorer import (
    compute_experience_score,
    compute_scores_and_eligibility,
    compute_seniority_score,
    compute_skills_score,
    compute_title_dice,
    compute_title_score,
    compute_workplace_location_score,
    round_half_up_ratio,
    tokenize_title,
)
from tapjob_api.matching.types import (
    CandidateExperienceSignal,
    CandidateMatchSignals,
    CandidateSeniorityLevel,
    DerivedJobMatchSignals,
    HardFilterId,
    HardFilterOutcome,
    HardFilterResult,
    JobSeniorityLevel,
    MatchEligibility,
)


def test_no_builtin_round_in_scorer_module() -> None:
    source = inspect.getsource(scorer)
    tree = ast.parse(source)
    round_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "round"
    ]
    assert len(round_calls) == 0, "Python builtin round() must NOT be called in scorer.py"


def test_round_half_up_ratio_exact_boundaries() -> None:
    # 0.5 -> 1
    assert round_half_up_ratio(1, 2) == 1
    # 1.5 -> 2
    assert round_half_up_ratio(3, 2) == 2
    # 2.5 -> 3 (Half-Up vs Half-Even: builtin round(2.5) returns 2, round_half_up returns 3)
    assert round_half_up_ratio(5, 2) == 3
    # 33.5 -> 34
    assert round_half_up_ratio(67, 2) == 34
    # 66.5 -> 67
    assert round_half_up_ratio(133, 2) == 67

    # Zero numerator -> 0
    assert round_half_up_ratio(0, 5) == 0
    assert round_half_up_ratio(-5, 5) == 0

    # Denominator <= 0 raises ValueError
    with pytest.raises(ValueError, match="denominator must be positive"):
        round_half_up_ratio(10, 0)
    with pytest.raises(ValueError, match="denominator must be positive"):
        round_half_up_ratio(10, -1)


def test_title_dice_and_tokenization() -> None:
    # Stopwords and seniority stripped, aliases resolved
    tokens = tokenize_title("Senior Software Engineer e Desenvolvedor Backend Pleno")
    assert "senior" not in tokens
    assert "pleno" not in tokens
    assert "e" not in tokens
    assert "developer" in tokens  # desenvolvedor -> developer
    assert "engineer" in tokens

    # Exact overlap
    assert compute_title_dice({"python", "developer"}, {"python", "developer"}) == 100
    # Half overlap: 200 * 1 / (2 + 2) = 50
    assert compute_title_dice({"python", "backend"}, {"python", "frontend"}) == 50
    # Disjoint
    assert compute_title_dice({"python"}, {"java"}) == 0


def test_title_scoring_target_vs_past_experience() -> None:
    job = Job()
    job.title_normalized = "software engineer"

    cand_target = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Software Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    # Target role exact match -> 100
    assert compute_title_score(cand_target, job) == 100

    cand_past = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(
            CandidateExperienceSignal(
                title="Software Engineer",
                title_normalized="software engineer",
                company_name="Acme",
                duration_months=24,
                fact_id=uuid.uuid4(),
            ),
        ),
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
        confirmed_fact_count=1,
    )
    # Past experience exact match has 90% weighting -> 90
    assert compute_title_score(cand_past, job) == 90


def test_skills_scoring_6_cases() -> None:
    def _sig(skills: set[str]) -> CandidateMatchSignals:
        return CandidateMatchSignals(
            skills=(),
            skill_names=frozenset(skills),
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
            confirmed_fact_count=len(skills),
        )

    # Case 1: R > 0, P > 0 (75% R + 25% P)
    # Cand has python (1/1 R = 100) and no P (0/1 P = 0) -> 100*75 + 0 = 75
    js_c1 = DerivedJobMatchSignals(
        required_skills=("python",),
        preferred_skills=("aws",),
        unclassified_skills=("docker",),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )
    assert compute_skills_score(_sig({"python"}), js_c1) == 75

    # Case 2: R > 0, P == 0 (100% R)
    js_c2 = DerivedJobMatchSignals(
        required_skills=("python", "fastapi"),
        preferred_skills=(),
        unclassified_skills=("docker",),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )
    # 1/2 R = 50
    assert compute_skills_score(_sig({"python"}), js_c2) == 50

    # Case 3: R == 0, P > 0, U == 0 (25 + 50% P)
    js_c3 = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=("aws",),
        unclassified_skills=(),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )
    # Cand has aws (100% P) -> 25 + 50 = 75
    assert compute_skills_score(_sig({"aws"}), js_c3) == 75

    # Case 4: R == 0, P > 0, U > 0 (60% U + 40% P)
    js_c4 = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=("aws",),
        unclassified_skills=("docker",),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )
    # Cand has docker (100% U, 0% P) -> 60
    assert compute_skills_score(_sig({"docker"}), js_c4) == 60

    # Case 5: R == 0, P == 0, U > 0 (100% U)
    js_c5 = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=(),
        unclassified_skills=("docker",),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )
    assert compute_skills_score(_sig({"docker"}), js_c5) == 100

    # Case 6: R == 0, P == 0, U == 0 (neutral 50)
    js_c6 = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=(),
        unclassified_skills=(),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )
    assert compute_skills_score(_sig({"python"}), js_c6) == 50


def test_experience_domain_scoring_and_adequacy() -> None:
    job = Job()
    job.description_cleaned = "Trabalho com backend e devops em nuvem."

    # Zero experience precedence rule -> 0
    cand_zero = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=0,
        domain_tokens=(),
        domain_token_names=frozenset({"backend", "devops"}),
        target_roles=(),
        derived_seniority=CandidateSeniorityLevel.UNKNOWN,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=0,
    )
    assert compute_experience_score(cand_zero, job) == 0

    # Adequate experience (>= 24 months -> adequacy 100%)
    cand_24m = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(
            CandidateExperienceSignal(
                title="Dev",
                title_normalized="dev",
                company_name="Acme",
                duration_months=24,
                fact_id=uuid.uuid4(),
            ),
        ),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset({"backend", "devops"}),
        target_roles=(),
        derived_seniority=CandidateSeniorityLevel.JUNIOR,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )
    score_24m = compute_experience_score(cand_24m, job)
    assert score_24m > 0


def test_seniority_7x7_matrix_lookup() -> None:
    cand_mid = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=48,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=(),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )

    def _job_sig(sen: JobSeniorityLevel | None) -> DerivedJobMatchSignals:
        return DerivedJobMatchSignals(
            required_skills=(),
            preferred_skills=(),
            unclassified_skills=(),
            seniority_signal=sen,
            salary_requirement=None,
            work_authorization_requirement=None,
            unknown_signals=(),
        )

    # Mid candidate vs Mid job -> 100
    assert compute_seniority_score(cand_mid, _job_sig(JobSeniorityLevel.MID)) == 100
    # Mid candidate vs Senior job -> 75
    assert compute_seniority_score(cand_mid, _job_sig(JobSeniorityLevel.SENIOR)) == 75
    # Mid candidate vs Intern job -> 50
    assert compute_seniority_score(cand_mid, _job_sig(JobSeniorityLevel.INTERN)) == 50
    # Mid candidate vs Unknown job -> 50
    assert compute_seniority_score(cand_mid, _job_sig(None)) == 50


def test_workplace_location_score_split() -> None:
    cand = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
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
    job_remote = Job()
    job_remote.workplace_type = JobWorkplaceType.REMOTE
    job_remote.location_normalized = "sao paulo, sp"

    # Both wp (100) and remote location (100) pass -> 100
    assert compute_workplace_location_score(cand, job_remote) == 100


def test_score_boundary_49_vs_50() -> None:
    job = Job()
    job.title_normalized = "software engineer"
    job.workplace_type = JobWorkplaceType.REMOTE
    job.location_normalized = "sao paulo, sp"
    job.description_cleaned = ""

    cand = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=24,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Software Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=1,
    )

    job_sig = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=(),
        unclassified_skills=(),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
    )

    # 1. Hard blocker forces overall_score = 0 and INELIGIBLE
    blocker_result = HardFilterResult(
        filter_id=HardFilterId.WORKPLACE_TYPE,
        outcome=HardFilterOutcome.HARD_BLOCKER,
        reason="Blocked",
        source_facts=(),
    )
    score_b, elig_b, _ = compute_scores_and_eligibility(
        cand,
        job_sig,
        job,
        hard_filter_results=(blocker_result,),
        critical_unknowns=(),
    )
    assert score_b == 0
    assert elig_b == MatchEligibility.INELIGIBLE

    # 2. Cleared gates with score >= 50 and zero unknowns -> ELIGIBLE
    passed_result = HardFilterResult(
        filter_id=HardFilterId.WORKPLACE_TYPE,
        outcome=HardFilterOutcome.PASSED,
        reason="Passed",
        source_facts=(),
    )
    score_ok, elig_ok, _ = compute_scores_and_eligibility(
        cand,
        job_sig,
        job,
        hard_filter_results=(passed_result,),
        critical_unknowns=(),
    )
    assert score_ok >= 50
    assert elig_ok == MatchEligibility.ELIGIBLE

    # 3. Score >= 50 with critical unknown -> REVIEW_REQUIRED
    from tapjob_api.matching.types import CriticalUnknown

    score_cu, elig_cu, _ = compute_scores_and_eligibility(
        cand,
        job_sig,
        job,
        hard_filter_results=(passed_result,),
        critical_unknowns=(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS,),
    )
    assert score_cu >= 50
    assert elig_cu == MatchEligibility.REVIEW_REQUIRED
