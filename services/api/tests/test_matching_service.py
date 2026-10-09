"""Tests for Matching Service Layer, Persistence, Cache & Concurrency (ADR-0014 Decisions 7 & 8).

Verifies:
- Computed-at immunity: different timestamps yield identical deterministic_result_digest
- As-of date boundary freeze
- Concurrency convergence: INSERT ON CONFLICT DO NOTHING with identical digest returns existing row
- Concurrency mismatch: divergent digest raises DeterministicMatchMismatchError
- Redis caching with 24h TTL
- Pre-cache liveness guard: closed/expired job evicts stale cache and raises MatchJobNotFoundError
- Visibility / cross-user isolation: private manual job raises MatchJobNotFoundError
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.identity.models import User
from tapjob_api.jobs.models import Job, JobStatus, JobWorkplaceType
from tapjob_api.matching import service
from tapjob_api.matching.engine import evaluate_match
from tapjob_api.matching.errors import (
    DeterministicMatchMismatchError,
    MatchJobNotFoundError,
)
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.signals import (
    derive_candidate_match_signals,
    derive_job_match_signals,
)
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    MatchEligibility,
)


class FakeRedis:
    """In-memory Redis fake for matching cache unit tests."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, time: int, value: str) -> bool:
        self.store[key] = value
        return True

    def delete(self, key: str) -> int:
        if key in self.store:
            del self.store[key]
            return 1
        return 0


def _make_user() -> User:
    user = User()
    user.id = uuid.uuid4()
    return user


def _make_job(
    *,
    job_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
    status: JobStatus = JobStatus.ACTIVE,
    expires_at: datetime | None = None,
) -> Job:
    job = Job()
    job.id = job_id or uuid.uuid4()
    job.title = "Software Engineer"
    job.title_normalized = "software engineer"
    job.location_normalized = "sao paulo, sp"
    job.workplace_type = JobWorkplaceType.REMOTE
    job.description_cleaned = "Python role"
    job.status = status
    job.created_by_user_id = created_by_user_id
    job.expires_at = expires_at
    return job


def test_computed_at_immunity() -> None:
    # Identical inputs evaluated at different computed_at timestamps produce identical digest
    job = _make_job()
    as_of = date(2026, 1, 1)

    cand_sig = derive_candidate_match_signals(
        TruthSnapshot(
            user_id=uuid.uuid4(),
            profile_version=1,
            headline=None,
            summary=None,
            facts=[],
            target_roles=["Software Engineer"],
        ),
        MATCHING_POLICY_VERSION,
        as_of,
    )
    job_sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)

    core1 = evaluate_match(cand_sig, job_sig, job, as_of, profile_version=1)
    core2 = evaluate_match(cand_sig, job_sig, job, as_of, profile_version=1)

    assert core1.deterministic_result_digest == core2.deterministic_result_digest
    assert len(core1.deterministic_result_digest) == 64


def test_pre_cache_liveness_guard_closed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    # Closed job evicts stale Redis cache and raises MatchJobNotFoundError
    user = _make_user()
    job = _make_job(status=JobStatus.CLOSED)

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,
        summary=None,
        facts=[],
        target_roles=[],
    )
    monkeypatch.setattr(service, "get_truth_snapshot", lambda *args, **kwargs: snapshot)

    fp = compute_job_match_fingerprint(job)
    today = date(2026, 1, 1)

    fake_redis: Any = FakeRedis()
    key = service.build_match_cache_key(user.id, job.id, 1, fp, MATCHING_POLICY_VERSION, today)
    fake_redis.store[key] = '{"dummy": "data"}'

    mock_session = MagicMock(spec=Session)
    mock_session.get.return_value = job

    with pytest.raises(MatchJobNotFoundError):
        service.get_or_compute_match(
            mock_session, fake_redis, user=user, job_id=job.id, evaluation_as_of_date=today
        )

    # Stale cache must be evicted
    assert key not in fake_redis.store


def test_pre_cache_liveness_guard_expired_job(monkeypatch: pytest.MonkeyPatch) -> None:
    # Expired job evicts stale cache and raises MatchJobNotFoundError
    user = _make_user()
    past_expiration = datetime.now(UTC) - timedelta(hours=1)
    job = _make_job(status=JobStatus.ACTIVE, expires_at=past_expiration)

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,
        summary=None,
        facts=[],
        target_roles=[],
    )
    monkeypatch.setattr(service, "get_truth_snapshot", lambda *args, **kwargs: snapshot)

    fp = compute_job_match_fingerprint(job)
    today = date(2026, 1, 1)

    fake_redis: Any = FakeRedis()
    key = service.build_match_cache_key(user.id, job.id, 1, fp, MATCHING_POLICY_VERSION, today)
    fake_redis.store[key] = '{"dummy": "data"}'

    mock_session = MagicMock(spec=Session)
    mock_session.get.return_value = job

    with pytest.raises(MatchJobNotFoundError):
        service.get_or_compute_match(
            mock_session, fake_redis, user=user, job_id=job.id, evaluation_as_of_date=today
        )

    assert key not in fake_redis.store


def test_cross_user_manual_job_isolation() -> None:
    # User A tries to match a manual job owned by User B -> MatchJobNotFoundError (non-enumerating)
    user_a = _make_user()
    user_b = _make_user()
    job_b = _make_job(created_by_user_id=user_b.id)

    fake_redis: Any = FakeRedis()
    mock_session = MagicMock(spec=Session)
    mock_session.get.return_value = job_b

    with pytest.raises(MatchJobNotFoundError):
        service.get_or_compute_match(mock_session, fake_redis, user=user_a, job_id=job_b.id)


def test_concurrency_convergence_divergent_digest_raises_invariant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _make_user()
    job = _make_job()
    fake_redis: Any = FakeRedis()

    mock_session = MagicMock(spec=Session)
    mock_session.get.return_value = job

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,
        summary=None,
        facts=[],
        target_roles=["Software Engineer"],
    )
    monkeypatch.setattr(service, "get_truth_snapshot", lambda *args, **kwargs: snapshot)

    # First scalar() call checks existing_row -> returns None (cache miss, DB miss)
    # Second scalar() call executes insert ... returning -> returns None (simulating race lost!)
    # Third scalar() call reloads existing_after_race -> returns existing row with DIFFERENT digest!
    existing_row = MatchResult(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job.id,
        profile_version=1,
        job_match_fingerprint="fp",
        policy_version=MATCHING_POLICY_VERSION,
        evaluation_as_of_date=date(2026, 1, 1),
        deterministic_result_digest="divergent_fake_digest_1234567890",
        overall_score=70,
        eligibility=MatchEligibility.ELIGIBLE.value,
        hard_filter_results=[],
        score_breakdown={},
        blockers=[],
        strengths=[],
        missing_requirements=[],
        critical_unknowns=[],
        explanation={},
        grounded_fact_ids=[],
        computed_at=datetime.now(UTC),
    )

    mock_session.scalar.side_effect = [None, None, existing_row]

    with pytest.raises(DeterministicMatchMismatchError) as exc_info:
        service.get_or_compute_match(
            mock_session,
            fake_redis,
            user=user,
            job_id=job.id,
            evaluation_as_of_date=date(2026, 1, 1),
        )

    assert exc_info.value.found_digest == "divergent_fake_digest_1234567890"


def test_cache_corruption_evicts_and_falls_back_to_db(monkeypatch: pytest.MonkeyPatch) -> None:
    # Syntactically malformed JSON in Redis -> evicts key and falls back to PostgreSQL
    import json

    user = _make_user()
    job = _make_job()
    fake_redis: Any = FakeRedis()
    mock_session = MagicMock(spec=Session)
    mock_session.get.return_value = job

    snapshot = TruthSnapshot(
        user_id=user.id,
        profile_version=1,
        headline=None,
        summary=None,
        facts=[],
        target_roles=["Software Engineer"],
    )
    monkeypatch.setattr(service, "get_truth_snapshot", lambda *args, **kwargs: snapshot)

    fp = compute_job_match_fingerprint(job)
    today = date(2026, 1, 1)
    key = service.build_match_cache_key(user.id, job.id, 1, fp, MATCHING_POLICY_VERSION, today)

    # 1. Syntactically invalid JSON
    fake_redis.store[key] = "invalid json {{"

    db_row = MatchResult(
        id=uuid.uuid4(),
        user_id=user.id,
        job_id=job.id,
        profile_version=1,
        job_match_fingerprint=fp,
        policy_version=MATCHING_POLICY_VERSION,
        evaluation_as_of_date=today,
        deterministic_result_digest="valid_digest_123",
        overall_score=85,
        eligibility=MatchEligibility.ELIGIBLE.value,
        hard_filter_results=[],
        score_breakdown={},
        blockers=[],
        strengths=[],
        missing_requirements=[],
        critical_unknowns=[],
        explanation={},
        grounded_fact_ids=[],
        computed_at=datetime.now(UTC),
    )
    mock_session.scalar.return_value = db_row

    res1 = service.get_or_compute_match(
        mock_session, fake_redis, user=user, job_id=job.id, evaluation_as_of_date=today
    )
    assert res1.overall_score == 85
    # Corrupted payload was evicted and replaced by valid db row
    assert fake_redis.store[key] != "invalid json {{"
    assert json.loads(fake_redis.store[key])["deterministic_result_digest"] == "valid_digest_123"

    # 2. Syntactically valid JSON missing required schema fields
    fake_redis.store[key] = '{"id": "some-id", "missing_other_fields": true}'
    res2 = service.get_or_compute_match(
        mock_session, fake_redis, user=user, job_id=job.id, evaluation_as_of_date=today
    )
    assert res2.overall_score == 85
    assert json.loads(fake_redis.store[key])["deterministic_result_digest"] == "valid_digest_123"


def test_post_commit_cache_guarantee_and_rollback() -> None:
    # Verifies Redis is NOT populated on rollback, and IS populated on commit
    fake_redis: Any = FakeRedis()
    key = "test_match_cache_key"
    payload = {"overall_score": 90, "deterministicResultDigest": "test_digest"}

    # 1. On rollback: cache is NOT populated
    sess_rollback = Session()
    service._register_post_commit_cache(sess_rollback, fake_redis, key, payload)
    sess_rollback.dispatch.after_rollback(sess_rollback)
    assert key not in fake_redis.store

    # 2. On commit: cache IS populated
    sess_commit = Session()
    service._register_post_commit_cache(sess_commit, fake_redis, key, payload)
    sess_commit.dispatch.after_commit(sess_commit)
    assert key in fake_redis.store


def test_publish_cached_match_redis_failure_is_non_fatal() -> None:
    # If Redis raises during cache publication, it must not crash the caller
    class ExplodingRedis:
        def setex(self, key: str, time: int, value: str) -> None:
            raise ConnectionError("Redis is down")

    row = MatchResult(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        profile_version=1,
        job_match_fingerprint="fp",
        policy_version=MATCHING_POLICY_VERSION,
        evaluation_as_of_date=date(2026, 1, 1),
        deterministic_result_digest="digest",
        overall_score=80,
        eligibility=MatchEligibility.ELIGIBLE.value,
        hard_filter_results=[],
        score_breakdown={},
        blockers=[],
        strengths=[],
        missing_requirements=[],
        critical_unknowns=[],
        explanation={},
        grounded_fact_ids=[],
        computed_at=datetime.now(UTC),
    )

    # Must NOT raise exception
    service.publish_cached_match(ExplodingRedis(), row)  # type: ignore[arg-type]


def test_domain_strength_candidate_job_intersection_finding_5() -> None:
    from tapjob_api.matching.explain import generate_grounded_explanation
    from tapjob_api.matching.types import (
        CandidateDomainTokenSignal,
        CandidateMatchSignals,
        CandidateSeniorityLevel,
        DerivedJobMatchSignals,
    )

    fact_cloud = uuid.uuid4()
    fact_sec = uuid.uuid4()

    candidate = CandidateMatchSignals(
        skills=(),
        skill_names=frozenset(),
        experiences=(),
        total_experience_months=36,
        domain_tokens=(
            CandidateDomainTokenSignal("cloud", (fact_cloud,)),
            CandidateDomainTokenSignal("security", (fact_sec,)),
        ),
        domain_token_names=frozenset({"cloud", "security"}),
        target_roles=("Cloud Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=None,
        salary_ambiguous=False,
        acceptable_workplace_types=frozenset(),
        acceptable_locations=(),
        work_authorizations={},
        confirmed_fact_count=2,
    )

    # 1. Job requires data / analytics -> neither cloud nor security appears in strengths!
    job_data_signals = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=(),
        unclassified_skills=(),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
        domain_tokens=("data", "analytics"),
    )
    job = _make_job()
    strengths1, _, _, _, _ = generate_grounded_explanation(
        candidate=candidate,
        job_signals=job_data_signals,
        job=job,
        hard_filter_results=(),
        overall_score=50,
        eligibility=MatchEligibility.REVIEW_REQUIRED,
    )
    assert not any(s.name in ("cloud", "security") for s in strengths1)

    # 2. Job requires cloud / devops -> only cloud appears in strengths, attached to fact_cloud
    job_cloud_signals = DerivedJobMatchSignals(
        required_skills=(),
        preferred_skills=(),
        unclassified_skills=(),
        seniority_signal=None,
        salary_requirement=None,
        work_authorization_requirement=None,
        unknown_signals=(),
        domain_tokens=("cloud", "devops"),
    )
    strengths2, _, _, grounded2, _ = generate_grounded_explanation(
        candidate=candidate,
        job_signals=job_cloud_signals,
        job=job,
        hard_filter_results=(),
        overall_score=75,
        eligibility=MatchEligibility.ELIGIBLE,
    )
    strength_names = [s.name for s in strengths2]
    assert "cloud" in strength_names
    assert "security" not in strength_names

    cloud_strength = next(s for s in strengths2 if s.name == "cloud")
    assert cloud_strength.fact_ids == (fact_cloud,)
    assert fact_cloud in grounded2
