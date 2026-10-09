"""Performance benchmark test for WP2.2 Matching Engine (ADR-0014 Decision 10).

Target ADR: pure engine evaluation < 50ms p95.
Pure domain logic: zero LLM, zero network calls, pure integer arithmetic.
"""

import time
import uuid
from datetime import date

from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.engine import evaluate_match
from tapjob_api.matching.signals import (
    derive_job_match_signals,
)
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    CandidateExperienceSignal,
    CandidateMatchSignals,
    CandidateSeniorityLevel,
    CandidateSkillSignal,
)


def _make_benchmark_candidate() -> CandidateMatchSignals:
    return CandidateMatchSignals(
        skills=tuple(
            CandidateSkillSignal(skill, (uuid.uuid4(),))
            for skill in ("python", "postgresql", "docker", "fastapi", "redis")
        ),
        skill_names=frozenset({"python", "postgresql", "docker", "fastapi", "redis"}),
        experiences=(
            CandidateExperienceSignal(
                "Senior Software Engineer",
                "senior software engineer",
                "Acme Corp",
                48,
                uuid.uuid4(),
            ),
        ),
        total_experience_months=48,
        domain_tokens=(),
        domain_token_names=frozenset({"backend", "cloud"}),
        target_roles=("Senior Python Engineer", "Backend Developer"),
        derived_seniority=CandidateSeniorityLevel.SENIOR,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE, JobWorkplaceType.HYBRID}),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=8,
    )


def _make_benchmark_job() -> Job:
    return Job(
        id=uuid.uuid4(),
        title="Senior Python Engineer",
        title_normalized="senior python engineer",
        location_normalized="remote",
        workplace_type=JobWorkplaceType.REMOTE,
        description_cleaned="""
        Requirements:
        - Python
        - PostgreSQL
        - Docker
        - Redis

        Nice to have:
        - Kubernetes

        Seniority: Senior
        """,
    )


def test_pure_matching_engine_performance_under_50ms_p95() -> None:
    candidate = _make_benchmark_candidate()
    job = _make_benchmark_job()
    job_signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
    as_of = date(2026, 1, 1)

    # Warmup
    for _ in range(5):
        evaluate_match(candidate, job_signals, job, as_of, profile_version=1)

    latencies_ms: list[float] = []
    iterations = 100

    for _ in range(iterations):
        start = time.perf_counter()
        core = evaluate_match(candidate, job_signals, job, as_of, profile_version=1)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        latencies_ms.append(elapsed_ms)
        assert core.overall_score > 0

    latencies_ms.sort()
    p50 = latencies_ms[int(iterations * 0.50)]
    p95 = latencies_ms[int(iterations * 0.95)]
    p99 = latencies_ms[int(iterations * 0.99)]

    _ = (p50, p95, p99)

    # Pure engine target is strictly < 50ms p95 per ADR-0014
    assert p95 < 50.0, f"p95 latency {p95:.2f}ms exceeded 50ms target"
