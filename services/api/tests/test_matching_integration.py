"""Integration tests for WP2.2 Matching Engine (ADR-0014).

Requires real PostgreSQL and Redis (backend-integration CI job).
Exercises:
- End-to-end matching route POST /api/v1/jobs/{job_id}/match
- Real PostgreSQL persistence and unique constraints (uq_match_results_user_job_versions)
- Redis caching with pre-cache liveness & visibility guard
- Concurrent insert race condition convergence
- Non-enumerating cross-user privacy on manual jobs
"""

import threading
import uuid
from datetime import UTC, date, datetime
from http import HTTPStatus

import pytest
import redis
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import (
    CareerProfile,
    CareerProfileRevision,
    FactSourceKind,
    FactStatus,
    FactType,
    ProfileFact,
)
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import User
from tapjob_api.jobs import service as jobs_service
from tapjob_api.jobs.models import Job, JobStatus, JobWorkplaceType
from tapjob_api.matching import service
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
)

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> uuid.UUID:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return uuid.UUID(response.json()["user"]["id"])


def _seed_profile(session: Session, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    profile_id = uuid.uuid4()
    profile = CareerProfile(
        id=profile_id,
        user_id=user_id,
        headline="Senior Python Engineer",
        summary=None,
        target_roles=["Software Engineer"],
        profile_version=1,
    )
    session.add(profile)
    session.flush()

    revision = CareerProfileRevision(
        career_profile_id=profile_id,
        user_id=user_id,
        profile_version=1,
        headline="Senior Python Engineer",
        summary=None,
        target_roles=["Software Engineer"],
    )
    session.add(revision)
    session.flush()

    fact_id = uuid.uuid4()
    fact = ProfileFact(
        id=fact_id,
        user_id=user_id,
        career_profile_id=profile_id,
        fact_type=FactType.SKILL,
        value_json={"kind": "skill", "name": "Python"},
        source_kind=FactSourceKind.USER_DIRECT,
        user_confirmed=True,
        confirmed_at=now,
        status=FactStatus.ACTIVE,
        lineage_id=fact_id,
        active_from_profile_version=1,
    )
    session.add(fact)
    session.flush()


def _seed_job(
    session: Session,
    *,
    status: JobStatus = JobStatus.ACTIVE,
    workplace_type: JobWorkplaceType = JobWorkplaceType.REMOTE,
    created_by_user_id: uuid.UUID | None = None,
) -> Job:
    company = jobs_service.get_or_create_company(session, name="Acme Corp", domain="acme.com")
    session.flush()
    job = Job(
        id=uuid.uuid4(),
        company_id=company.id,
        status=status,
        title="Senior Python Backend Engineer",
        title_normalized="senior python backend engineer",
        location_raw="São Paulo, SP, Brazil",
        location_normalized="sao paulo sp brazil",
        workplace_type=workplace_type,
        description_raw="Procuramos Dev Senior Python com vivência em APIs e testes.",
        description_cleaned=(
            "Procuramos Dev Senior Python com vivência em APIs e testes. Requisitos: python."
        ),
        similarity_fingerprint="sim_fp_12345",
        created_by_user_id=created_by_user_id,
        provenance_summary={},
    )
    session.add(job)
    session.flush()
    return job


def test_matching_integration_e2e_and_redis_cache(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    try:
        with Session(engine) as session:
            _seed_profile(session, user_id)
            job = _seed_job(session)
            session.commit()
            job_id = job.id

        # 1. First HTTP call -> Compute, persist to PostgreSQL, set in Redis
        resp1 = identity_client.post(
            f"/api/v1/jobs/{job_id}/match",
            headers=bearer_header("token-1"),
        )
        assert resp1.status_code == HTTPStatus.OK
        data1 = resp1.json()
        assert data1["jobId"] == str(job_id)
        assert data1["eligibility"] in ("eligible", "review_required")
        assert data1["overallScore"] >= 50
        assert data1["policyVersion"] == MATCHING_POLICY_VERSION
        assert len(data1["deterministicResultDigest"]) == 64

        # Verify persisted in PostgreSQL
        with Session(engine) as session:
            persisted = session.scalar(
                select(MatchResult).where(
                    MatchResult.user_id == user_id,
                    MatchResult.job_id == job_id,
                )
            )
            assert persisted is not None
            assert persisted.deterministic_result_digest == data1["deterministicResultDigest"]
            assert persisted.overall_score == data1["overallScore"]

        # Verify present in Redis
        r = redis.Redis.from_url(str(settings.redis_url))
        cache_key = service.build_match_cache_key(
            user_id,
            job_id,
            1,
            data1["jobMatchFingerprint"],
            MATCHING_POLICY_VERSION,
            date.fromisoformat(data1["evaluationAsOfDate"]),
        )
        cached_raw = r.get(cache_key)
        assert cached_raw is not None

        # 2. Second HTTP call -> Cache hit, identical response
        resp2 = identity_client.post(
            f"/api/v1/jobs/{job_id}/match",
            headers=bearer_header("token-1"),
        )
        assert resp2.status_code == HTTPStatus.OK
        data2 = resp2.json()
        assert data2 == data1

    finally:
        engine.dispose()


def test_matching_integration_concurrency_race(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-conc", subject="uid-conc"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    try:
        with Session(engine) as session:
            _seed_profile(session, user_id)
            job = _seed_job(session)
            session.commit()
            job_id = job.id

        results: list[str] = []
        errors: list[Exception] = []

        def worker() -> None:
            thread_engine = create_engine(str(settings.database_url))
            try:
                with Session(thread_engine, expire_on_commit=False) as session:
                    user = session.get(User, user_id)
                    assert user is not None
                    r_client = redis.Redis.from_url(str(settings.redis_url))
                    res = service.get_or_compute_match(
                        session,
                        r_client,
                        user=user,
                        job_id=job_id,
                        evaluation_as_of_date=date(2026, 1, 1),
                    )
                    digest = res.deterministic_result_digest
                    session.commit()
                    results.append(digest)
            except Exception as e:
                errors.append(e)
            finally:
                thread_engine.dispose()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert len(results) == 2
        assert results[0] == results[1]

        # Exactly 1 row in PostgreSQL
        with Session(engine) as session:
            rows = session.scalars(
                select(MatchResult).where(
                    MatchResult.user_id == user_id,
                    MatchResult.job_id == job_id,
                )
            ).all()
            assert len(rows) == 1

    finally:
        engine.dispose()


def test_matching_integration_cross_user_manual_job_isolation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user1_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-u1", subject="uid-u1"
    )
    _bootstrap(identity_client, fake_identity_verifier, token="token-u2", subject="uid-u2")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    try:
        with Session(engine) as session:
            # Job created manually by user 1
            job = _seed_job(session, created_by_user_id=user1_id)
            session.commit()
            job_id = job.id

        # User 2 attempts to match User 1's manual job -> 404 Not Found (non-enumerating)
        resp = identity_client.post(
            f"/api/v1/jobs/{job_id}/match",
            headers=bearer_header("token-u2"),
        )
        assert resp.status_code == HTTPStatus.NOT_FOUND
        assert resp.json()["code"] == "JOB_NOT_FOUND"

    finally:
        engine.dispose()


def test_matching_integration_real_postgres_redis_commit_order(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Verifies invariant: Redis cache is written ONLY after PostgreSQL commit succeeds."""
    import json

    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-tx1", subject="uid-tx1"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    r_client = redis.Redis.from_url(str(settings.redis_url))

    try:
        with Session(engine) as session:
            _seed_profile(session, user_id)
            job = _seed_job(session)
            session.commit()
            job_id = job.id

        as_of = date(2026, 3, 1)

        with Session(engine) as session:
            user = session.get(User, user_id)
            assert user is not None
            job_row = session.get(Job, job_id)
            assert job_row is not None

            fingerprint = compute_job_match_fingerprint(job_row)
            cache_key = service.build_match_cache_key(
                user.id, job_row.id, 1, fingerprint, MATCHING_POLICY_VERSION, as_of
            )
            # Ensure Redis key is clean before starting
            r_client.delete(cache_key)

            # 1. Compute and insert inside transaction
            res = service.get_or_compute_match(
                session,
                r_client,
                user=user,
                job_id=job_row.id,
                evaluation_as_of_date=as_of,
            )
            assert res is not None

            # 2. Before commit: Redis key MUST be ABSENT
            assert r_client.get(cache_key) is None

            # 3. Commit transaction to PostgreSQL
            session.commit()

            # 4. After commit: Redis key MUST be PRESENT
            cached_raw = r_client.get(cache_key)
            assert cached_raw is not None
            assert isinstance(cached_raw, (str, bytes))
            cached_dict = json.loads(cached_raw)
            assert cached_dict["deterministic_result_digest"] == res.deterministic_result_digest
            assert cached_dict["overall_score"] == res.overall_score

        # 5. Verify PostgreSQL row matches
        with Session(engine) as session:
            db_row = session.scalar(
                select(MatchResult).where(
                    MatchResult.user_id == user_id,
                    MatchResult.job_id == job_id,
                    MatchResult.evaluation_as_of_date == as_of,
                )
            )
            assert db_row is not None
            assert db_row.deterministic_result_digest == res.deterministic_result_digest

    finally:
        engine.dispose()


def test_matching_integration_real_postgres_redis_rollback_order(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Verifies that rollback leaves Redis cache empty and does not persist to PostgreSQL."""
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-tx2", subject="uid-tx2"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    r_client = redis.Redis.from_url(str(settings.redis_url))

    try:
        with Session(engine) as session:
            _seed_profile(session, user_id)
            job = _seed_job(session)
            session.commit()
            job_id = job.id

        as_of = date(2026, 3, 2)

        with Session(engine) as session:
            user = session.get(User, user_id)
            assert user is not None
            job_row = session.get(Job, job_id)
            assert job_row is not None

            fingerprint = compute_job_match_fingerprint(job_row)
            cache_key = service.build_match_cache_key(
                user.id, job_row.id, 1, fingerprint, MATCHING_POLICY_VERSION, as_of
            )
            r_client.delete(cache_key)

            # Compute and insert in session
            service.get_or_compute_match(
                session,
                r_client,
                user=user,
                job_id=job_row.id,
                evaluation_as_of_date=as_of,
            )

            # Before rollback: Redis MUST be absent
            assert r_client.get(cache_key) is None

            # Execute real rollback
            session.rollback()

            # After rollback: Redis MUST STILL be absent
            assert r_client.get(cache_key) is None

        # PostgreSQL MUST NOT contain the row
        with Session(engine) as session:
            db_row = session.scalar(
                select(MatchResult).where(
                    MatchResult.user_id == user_id,
                    MatchResult.job_id == job_id,
                    MatchResult.evaluation_as_of_date == as_of,
                )
            )
            assert db_row is None

    finally:
        engine.dispose()


def test_matching_integration_commit_exception_prevents_cache(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Verifies that an exception during session.commit() prevents publishing to Redis cache."""
    from unittest.mock import patch

    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-tx3", subject="uid-tx3"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    r_client = redis.Redis.from_url(str(settings.redis_url))

    try:
        with Session(engine) as session:
            _seed_profile(session, user_id)
            job = _seed_job(session)
            session.commit()
            job_id = job.id

        as_of = date(2026, 3, 3)

        with Session(engine) as session:
            user = session.get(User, user_id)
            assert user is not None
            job_row = session.get(Job, job_id)
            assert job_row is not None

            fingerprint = compute_job_match_fingerprint(job_row)
            cache_key = service.build_match_cache_key(
                user.id, job_row.id, 1, fingerprint, MATCHING_POLICY_VERSION, as_of
            )
            r_client.delete(cache_key)

            service.get_or_compute_match(
                session,
                r_client,
                user=user,
                job_id=job_row.id,
                evaluation_as_of_date=as_of,
            )

            assert r_client.get(cache_key) is None

            # Mock commit raising an exception
            err_msg = "Simulated DB commit error"
            with (
                patch.object(session, "commit", side_effect=RuntimeError(err_msg)),
                pytest.raises(RuntimeError, match=err_msg),
            ):
                session.commit()

            # Redis cache MUST NOT be populated
            assert r_client.get(cache_key) is None

    finally:
        engine.dispose()
