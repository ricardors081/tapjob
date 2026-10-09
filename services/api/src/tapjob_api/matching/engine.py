"""Deterministic Match Engine Core for WP2.2 (ADR-0014 Decisions 3 & 6).

Coordinates candidate and job signals, hard filter gates, pure integer scoring,
and grounded explainability into an invariant DeterministicMatchCore.
"""

from __future__ import annotations

from datetime import date

from tapjob_api.jobs.models import Job
from tapjob_api.matching.explain import generate_grounded_explanation
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.gate import evaluate_hard_filters
from tapjob_api.matching.scorer import compute_scores_and_eligibility, compute_title_score
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    CandidateMatchSignals,
    DerivedJobMatchSignals,
    DeterministicMatchCore,
    compute_deterministic_result_digest,
)


def evaluate_match(
    candidate: CandidateMatchSignals,
    job_signals: DerivedJobMatchSignals,
    job: Job,
    evaluation_as_of_date: date,
    profile_version: int,
    policy_version: str = MATCHING_POLICY_VERSION,
) -> DeterministicMatchCore:
    """Evaluates match compatibility deterministically, returning DeterministicMatchCore."""
    title_score = compute_title_score(candidate, job)
    hard_filter_results, critical_unknowns = evaluate_hard_filters(
        candidate, job_signals, job, title_score
    )

    overall_score, eligibility, score_components = compute_scores_and_eligibility(
        candidate, job_signals, job, hard_filter_results, critical_unknowns
    )

    strengths, blockers, missing_requirements, grounded_fact_ids, explanation = (
        generate_grounded_explanation(
            candidate,
            job_signals,
            job,
            hard_filter_results,
            overall_score,
            eligibility,
        )
    )

    fingerprint = compute_job_match_fingerprint(job)

    # Construct provisional core without digest to calculate bitwise deterministic digest
    provisional_core = DeterministicMatchCore(
        overall_score=overall_score,
        eligibility=eligibility,
        hard_filter_results=hard_filter_results,
        score_components=score_components,
        blockers=blockers,
        strengths=strengths,
        missing_requirements=missing_requirements,
        critical_unknowns=critical_unknowns,
        deterministic_template_explanation=explanation,
        grounded_fact_ids=grounded_fact_ids,
        profile_version=profile_version,
        job_match_fingerprint=fingerprint,
        policy_version=policy_version,
        evaluation_as_of_date=evaluation_as_of_date,
        deterministic_result_digest="",
    )

    digest = compute_deterministic_result_digest(provisional_core)

    return DeterministicMatchCore(
        overall_score=overall_score,
        eligibility=eligibility,
        hard_filter_results=hard_filter_results,
        score_components=score_components,
        blockers=blockers,
        strengths=strengths,
        missing_requirements=missing_requirements,
        critical_unknowns=critical_unknowns,
        deterministic_template_explanation=explanation,
        grounded_fact_ids=grounded_fact_ids,
        profile_version=profile_version,
        job_match_fingerprint=fingerprint,
        policy_version=policy_version,
        evaluation_as_of_date=evaluation_as_of_date,
        deterministic_result_digest=digest,
    )
