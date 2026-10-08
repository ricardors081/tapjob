"""Integration tests for ATS job ingestion pipeline with real PostgreSQL (WP2.1-B)."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, select

from tapjob_api.core.config import ApiSettings
from tapjob_api.db.session import create_session_factory
from tapjob_api.jobs.ingestion import (
    IngestionSummary,
    advisory_lock_key,
    ingest_job_source,
    release_source_advisory_lock,
    try_acquire_source_advisory_lock,
)
from tapjob_api.jobs.models import (
    Company,
    Job,
    JobOrigin,
    JobStatus,
    ObservationStatus,
)
from tapjob_api.jobs.sources.registry import JobSourceConfig
from tapjob_api.ports import (
    CallContext,
    DiscoveredJob,
    DiscoveryPage,
    ExternalJobRef,
    JobDetails,
    PortError,
    PortErrorCode,
)

pytestmark = pytest.mark.integration


def _ctx() -> CallContext:
    return CallContext.with_timeout("integration-test-ingest", 15.0)


class InMemoryJobSource:
    provider: str

    def __init__(
        self,
        provider: str,
        items: list[DiscoveredJob],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.items = list(items)
        self.descriptions = dict(descriptions or {})

    async def capabilities(self, ctx: CallContext) -> MagicMock:
        return MagicMock()

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        return DiscoveryPage(items=tuple(self.items), next_cursor=None)

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
        desc = self.descriptions.get(
            ref.external_id,
            f"Detailed description for {ref.external_id} at {ref.provider}",
        )
        return JobDetails(
            ref=ref,
            description_raw=desc,
            fetched_at=datetime.now(UTC),
        )

    async def health(self, ctx: CallContext) -> MagicMock:
        return MagicMock()


def test_ingestion_persists_canonical_entities_in_postgres(
    identity_client: object,
) -> None:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    board_id = f"test-gh-{uuid.uuid4().hex[:8]}"
    ref1 = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-gh-1-{uuid.uuid4().hex[:8]}",
        url="https://boards.greenhouse.io/acme/1",
    )
    ref2 = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-gh-2-{uuid.uuid4().hex[:8]}",
        url="https://boards.greenhouse.io/acme/2",
    )
    items = [
        DiscoveredJob(
            ref=ref1,
            title="Staff Backend Engineer",
            company_name="Acme Corp",
            posted_at=datetime.now(UTC),
            location_raw="Austin, TX",
        ),
        DiscoveredJob(
            ref=ref2,
            title="Lead Mobile Engineer",
            company_name="Acme Corp",
            posted_at=datetime.now(UTC),
        ),
    ]
    source = InMemoryJobSource("greenhouse", items)
    # Note: provide ATS hostname as corporate_domain to verify sanitization
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Acme Corp",
        corporate_domain="boards.greenhouse.io",
    )

    with session_factory() as session:
        summary = asyncio.run(ingest_job_source(session, config, cycle_id="cycle-init", ctx=_ctx()))
        assert summary.created_jobs == 2
        assert summary.updated_origins == 0
        assert summary.closed_jobs == 0

        # Verify company domain sanitization: ATS hostnames must never become Company.domain
        company = (
            session.execute(select(Company).where(Company.name_normalized == "acme corp"))
            .scalars()
            .first()
        )
        if company is None:
            company = (
                session.execute(select(Company).where(Company.name_normalized == "acme"))
                .scalars()
                .first()
            )
        assert company is not None
        assert company.name == "Acme Corp"
        assert company.domain is None  # sanitized from boards.greenhouse.io!

        # Verify jobs created with created_by_user_id IS NULL and location persisted
        jobs = (
            session.execute(select(Job).where(Job.company_id == company.id).order_by(Job.title))
            .scalars()
            .all()
        )
        assert len(jobs) >= 2
        for j in jobs:
            assert j.created_by_user_id is None
            assert j.status == JobStatus.ACTIVE
            assert j.provenance_summary.get("provider") == "greenhouse"

        staff_job = next(j for j in jobs if j.title == "Staff Backend Engineer")
        assert staff_job.location_raw == "Austin, TX"
        assert staff_job.location_normalized is not None

        # Verify origins created
        origins = (
            session.execute(select(JobOrigin).where(JobOrigin.provider == "greenhouse"))
            .scalars()
            .all()
        )
        matched_origins = [o for o in origins if o.provenance_metadata.get("board_id") == board_id]
        assert len(matched_origins) == 2
        for o in matched_origins:
            assert o.observation_status == ObservationStatus.ACTIVE
            assert o.consecutive_not_found_count == 0

    # Second run: ref2 disappears from discovery
    source.items = [items[0]]  # only ref1 remains
    with session_factory() as session:
        # Run 1 with missing job
        summary1 = asyncio.run(
            ingest_job_source(session, config, cycle_id="cycle-miss-1", ctx=_ctx())
        )
        assert summary1.updated_origins == 1
        assert summary1.closed_jobs == 0

        origin2 = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref2.external_id)
        ).scalar_one()
        assert origin2.consecutive_not_found_count == 1
        assert origin2.observation_status == ObservationStatus.ACTIVE

        # Run 2 with missing job
        summary2 = asyncio.run(
            ingest_job_source(session, config, cycle_id="cycle-miss-2", ctx=_ctx())
        )
        assert summary2.closed_jobs == 0
        session.refresh(origin2)
        assert origin2.consecutive_not_found_count == 2
        assert origin2.observation_status == ObservationStatus.ACTIVE

        # Run 3 with missing job: reaches threshold of 3 -> closes!
        summary3 = asyncio.run(
            ingest_job_source(session, config, cycle_id="cycle-miss-3", ctx=_ctx())
        )
        assert summary3.closed_jobs == 1
        origin2_closed = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref2.external_id)
        ).scalar_one()
        assert origin2_closed.consecutive_not_found_count == 3
        assert origin2_closed.observation_status == ObservationStatus.CLOSED

        job2 = session.execute(select(Job).where(Job.id == origin2_closed.job_id)).scalar_one()
        assert job2.status == JobStatus.CLOSED


def test_company_identity_stability_and_source_scoped_reuse_in_postgres(
    identity_client: object,
) -> None:
    """Verifies Finding B requirements in real PostgreSQL:
    - Empty discovery creates NO Company.
    - Sweep 1 creates Company C1.
    - Sweep 2 (identical) keeps exactly C1 (no new Company).
    - Sweep 3 (identical) keeps exactly C1.
    - Adding a late new job reuses C1 (even with corporate_domain sanitized to None).
    - Two different source boards with same company name do NOT merge.
    """
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    board_id_a = f"board-a-{uuid.uuid4().hex[:8]}"
    company_name = f"Stable Co {uuid.uuid4().hex[:6]}"

    # 1. Empty discovery does not create a Company
    empty_source = InMemoryJobSource("greenhouse", [])
    config_a = JobSourceConfig(
        source_id=board_id_a,
        provider="greenhouse",
        source=empty_source,
        company_name=company_name,
        corporate_domain="boards.greenhouse.io",  # sanitizes to None
    )

    with session_factory() as session:
        summary_empty = asyncio.run(
            ingest_job_source(session, config_a, cycle_id="c-empty", ctx=_ctx())
        )
        assert summary_empty.total_discovered == 0
        assert summary_empty.created_jobs == 0

        # Prove 0 companies created on empty discovery
        comps = session.execute(select(Company).where(Company.name == company_name)).scalars().all()
        assert len(comps) == 0

    # 2. First real sweep creates Company C1
    ref_a1 = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-a1-{uuid.uuid4().hex[:8]}",
        url="https://boards.greenhouse.io/stable/1",
    )
    job_a1 = DiscoveredJob(
        ref=ref_a1,
        title="Engineer 1",
        company_name=company_name,
        posted_at=datetime.now(UTC),
    )
    source_a = InMemoryJobSource("greenhouse", [job_a1])
    config_a = JobSourceConfig(
        source_id=board_id_a,
        provider="greenhouse",
        source=source_a,
        company_name=company_name,
        corporate_domain="boards.greenhouse.io",
    )

    with session_factory() as session:
        summary_1 = asyncio.run(ingest_job_source(session, config_a, cycle_id="c-run1", ctx=_ctx()))
        assert summary_1.created_jobs == 1
        comps_1 = (
            session.execute(select(Company).where(Company.name == company_name)).scalars().all()
        )
        assert len(comps_1) == 1
        company_c1_id = comps_1[0].id

    # 3. Second and Third identical sweeps keep exactly C1
    with session_factory() as session:
        summary_2 = asyncio.run(ingest_job_source(session, config_a, cycle_id="c-run2", ctx=_ctx()))
        assert summary_2.created_jobs == 0
        assert summary_2.updated_origins == 1

        summary_3 = asyncio.run(ingest_job_source(session, config_a, cycle_id="c-run3", ctx=_ctx()))
        assert summary_3.created_jobs == 0
        assert summary_3.updated_origins == 1

        comps_after = (
            session.execute(select(Company).where(Company.name == company_name)).scalars().all()
        )
        assert len(comps_after) == 1
        assert comps_after[0].id == company_c1_id

    # 4. Late arrival of a new job on the same board reuses C1
    ref_a2 = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-a2-{uuid.uuid4().hex[:8]}",
        url="https://boards.greenhouse.io/stable/2",
    )
    job_a2 = DiscoveredJob(
        ref=ref_a2,
        title="Engineer 2",
        company_name=company_name,
        posted_at=datetime.now(UTC),
    )
    source_a.items = [job_a1, job_a2]

    with session_factory() as session:
        summary_4 = asyncio.run(ingest_job_source(session, config_a, cycle_id="c-run4", ctx=_ctx()))
        assert summary_4.created_jobs == 1
        assert summary_4.updated_origins == 1

        # Still exactly 1 company, and both jobs point to C1
        comps_after_new_job = (
            session.execute(select(Company).where(Company.name == company_name)).scalars().all()
        )
        assert len(comps_after_new_job) == 1
        assert comps_after_new_job[0].id == company_c1_id

        jobs_c1 = (
            session.execute(select(Job).where(Job.company_id == company_c1_id)).scalars().all()
        )
        assert len(jobs_c1) == 2

    # 5. Different board B with the same company name does NOT merge with C1
    board_id_b = f"board-b-{uuid.uuid4().hex[:8]}"
    ref_b1 = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-b1-{uuid.uuid4().hex[:8]}",
        url="https://boards.greenhouse.io/other/1",
    )
    job_b1 = DiscoveredJob(
        ref=ref_b1,
        title="Engineer from other board",
        company_name=company_name,
        posted_at=datetime.now(UTC),
    )
    source_b = InMemoryJobSource("greenhouse", [job_b1])
    config_b = JobSourceConfig(
        source_id=board_id_b,
        provider="greenhouse",
        source=source_b,
        company_name=company_name,
        corporate_domain="boards.greenhouse.io",
    )

    with session_factory() as session:
        summary_b = asyncio.run(
            ingest_job_source(session, config_b, cycle_id="c-board-b", ctx=_ctx())
        )
        assert summary_b.created_jobs == 1

        # Exactly 2 distinct Companies now exist for this name
        comps_total = (
            session.execute(select(Company).where(Company.name == company_name)).scalars().all()
        )
        assert len(comps_total) == 2
        comp_ids = {c.id for c in comps_total}
        assert company_c1_id in comp_ids


def test_liveness_idempotency_under_redelivery_and_cycles_in_postgres(
    identity_client: object,
) -> None:
    """Verifies Finding C requirements in real PostgreSQL:
    - Same cycle_id executed twice => 1 strike (idempotent redelivery).
    - Same cycle_id executed three times => still 1 strike.
    - Three different cycle_ids => 3 strikes (closes job).
    - Reappearance in a subsequent cycle resets consecutive_not_found_count to 0.
    - Different source does not interfere.
    """
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    board_id = f"test-cycle-liveness-{uuid.uuid4().hex[:8]}"
    ref = ExternalJobRef(
        provider="greenhouse",
        external_id=f"ext-cycle-{uuid.uuid4().hex[:8]}",
        url="https://boards.greenhouse.io/acme/liveness-test",
    )
    item = DiscoveredJob(
        ref=ref,
        title="Software Engineer",
        company_name="Cycle Co",
        posted_at=datetime.now(UTC),
    )
    source = InMemoryJobSource("greenhouse", [item])
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Cycle Co",
    )

    # Initial ingestion creates active job
    with session_factory() as session:
        summary_init = asyncio.run(
            ingest_job_source(session, config, cycle_id="cycle-0", ctx=_ctx())
        )
        assert summary_init.created_jobs == 1

    # Remove job from discovery to trigger missing reconciliations
    source.items = []

    # Run 1 with cycle-A: strike 1
    with session_factory() as session:
        asyncio.run(ingest_job_source(session, config, cycle_id="cycle-A", ctx=_ctx()))
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 1
        assert origin.observation_status == ObservationStatus.ACTIVE

    # REDELIVERY 1: same cycle-A reprocessed => MUST REMAIN 1 strike!
    with session_factory() as session:
        asyncio.run(ingest_job_source(session, config, cycle_id="cycle-A", ctx=_ctx()))
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 1  # No duplicate strike!
        assert origin.observation_status == ObservationStatus.ACTIVE

    # REDELIVERY 2: third delivery of cycle-A => STILL 1 strike!
    with session_factory() as session:
        asyncio.run(ingest_job_source(session, config, cycle_id="cycle-A", ctx=_ctx()))
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 1
        assert origin.observation_status == ObservationStatus.ACTIVE

    # Run with new cycle-B: strike 2
    with session_factory() as session:
        asyncio.run(ingest_job_source(session, config, cycle_id="cycle-B", ctx=_ctx()))
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 2
        assert origin.observation_status == ObservationStatus.ACTIVE

    # Run with another unrelated source board during cycle-C: does not touch this origin
    unrelated_config = JobSourceConfig(
        source_id="unrelated-source-board",
        provider="greenhouse",
        source=InMemoryJobSource("greenhouse", []),
        company_name="Other Co",
    )
    with session_factory() as session:
        asyncio.run(ingest_job_source(session, unrelated_config, cycle_id="cycle-C", ctx=_ctx()))
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 2  # Untouched by other source!

    # Run with new cycle-C for this source: strike 3 => CLOSED
    with session_factory() as session:
        asyncio.run(ingest_job_source(session, config, cycle_id="cycle-C", ctx=_ctx()))
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 3
        assert origin.observation_status == ObservationStatus.CLOSED
        job = session.execute(select(Job).where(Job.id == origin.job_id)).scalar_one()
        assert job.status == JobStatus.CLOSED

    # REAPPEARANCE in cycle-D: job returns to discovery => resets to 0 and ACTIVE
    source.items = [item]
    with session_factory() as session:
        summary_revive = asyncio.run(
            ingest_job_source(session, config, cycle_id="cycle-D", ctx=_ctx())
        )
        assert summary_revive.updated_origins == 1
        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ref.external_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 0
        assert origin.observation_status == ObservationStatus.ACTIVE
        job = session.execute(select(Job).where(Job.id == origin.job_id)).scalar_one()
        assert job.status == JobStatus.ACTIVE


def test_existing_posting_mutation_updates_canonical_fields_in_postgres(
    identity_client: object,
) -> None:
    """Verifies Finding E requirements in real PostgreSQL:
    - First ingestion persists title A, location A, description A.
    - Second ingestion with same external_id provides title B, location B, description B.
    - Exactly 1 Job and 1 JobOrigin remain.
    - Canonical fields reflect B.
    - Fingerprint is recomputed.
    - last_observed_at advances while first_observed_at is preserved.
    - job.id, company_id, and created_by_user_id are preserved.
    """
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    board_id = f"test-mutation-{uuid.uuid4().hex[:8]}"
    ext_id = f"mut-{uuid.uuid4().hex[:8]}"
    ref = ExternalJobRef(
        provider="greenhouse",
        external_id=ext_id,
        url="https://boards.greenhouse.io/acme/job-mut",
    )

    # Initial state A
    item_a = DiscoveredJob(
        ref=ref,
        title="Junior Software Developer",
        company_name="Mutating Co",
        posted_at=datetime.now(UTC),
        location_raw="Austin, TX",
        workplace_type_hint="on-site",
    )
    source = InMemoryJobSource(
        "greenhouse",
        [item_a],
        descriptions={ext_id: "<p>Original description A with junior requirements.</p>"},
    )
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Mutating Co",
    )

    with session_factory() as session:
        summary_a = asyncio.run(ingest_job_source(session, config, cycle_id="c-mut-1", ctx=_ctx()))
        assert summary_a.created_jobs == 1

        origin_a = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ext_id)
        ).scalar_one()
        job_a = session.execute(select(Job).where(Job.id == origin_a.job_id)).scalar_one()

        saved_job_id = job_a.id
        saved_company_id = job_a.company_id
        saved_origin_id = origin_a.id
        saved_first_observed = origin_a.first_observed_at
        fp_a = job_a.similarity_fingerprint

        assert job_a.title == "Junior Software Developer"
        assert job_a.location_raw == "Austin, TX"
        assert "Original description A" in job_a.description_raw

    # Updated state B
    item_b = DiscoveredJob(
        ref=ref,
        title="Senior Software Architect",
        company_name="Mutating Co",
        posted_at=datetime.now(UTC),
        location_raw="San Francisco, CA",
        additional_locations_raw=("London, UK",),
        workplace_type_hint="hybrid",
    )
    source.items = [item_b]
    source.descriptions[ext_id] = "<p>Updated description B with architect requirements.</p>"

    with session_factory() as session:
        summary_b = asyncio.run(ingest_job_source(session, config, cycle_id="c-mut-2", ctx=_ctx()))
        assert summary_b.created_jobs == 0
        assert summary_b.updated_origins == 1

        origin_b = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ext_id)
        ).scalar_one()
        job_b = session.execute(select(Job).where(Job.id == origin_b.job_id)).scalar_one()

        # Invariants preserved
        assert job_b.id == saved_job_id
        assert job_b.company_id == saved_company_id
        assert job_b.created_by_user_id is None
        assert origin_b.id == saved_origin_id
        assert origin_b.first_observed_at == saved_first_observed
        assert origin_b.last_observed_at >= saved_first_observed

        # Mutable fields updated to state B
        assert job_b.title == "Senior Software Architect"
        assert job_b.title_normalized == "senior software architect"
        assert job_b.location_raw == "San Francisco, CA"
        assert "london, uk" in (job_b.location_normalized or "")
        assert job_b.workplace_type.value == "hybrid"
        assert "Updated description B" in job_b.description_raw
        assert job_b.similarity_fingerprint != fp_a


def test_celery_sweep_redelivery_idempotency_in_postgres(
    identity_client: object,
) -> None:
    """Finding 1 Test 6: Celery sweep redelivery with same cycle_id is idempotent in Postgres."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    board_id = f"test-redeliver-{uuid.uuid4().hex[:8]}"
    ext_id = f"job-redeliver-{uuid.uuid4().hex[:8]}"
    ref = ExternalJobRef(
        provider="greenhouse",
        external_id=ext_id,
        url=f"https://boards.greenhouse.io/{board_id}/jobs/{ext_id}",
    )
    item = DiscoveredJob(
        ref=ref,
        title="Initial Engineer",
        company_name="Redeliver Corp",
        posted_at=datetime.now(UTC),
    )
    source = InMemoryJobSource("greenhouse", [item])
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Redeliver Corp",
    )

    # Initial sweep / run creates the job
    with session_factory() as session:
        summary_0 = asyncio.run(
            ingest_job_source(session, config, cycle_id="sweep:init-task", ctx=_ctx())
        )
        assert summary_0.created_jobs == 1

        origin = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ext_id)
        ).scalar_one()
        assert origin.consecutive_not_found_count == 0
        assert origin.observation_status == ObservationStatus.ACTIVE

    # Job disappears upstream (upstream board empty)
    source.items = []

    # First execution of sweep task X: child runs with cycle_id = "sweep:task-cycle-X"
    with session_factory() as session:
        summary_1 = asyncio.run(
            ingest_job_source(session, config, cycle_id="sweep:task-cycle-X", ctx=_ctx())
        )
        assert summary_1.total_discovered == 0
        origin_1 = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ext_id)
        ).scalar_one()
        assert origin_1.consecutive_not_found_count == 1
        assert (
            origin_1.provenance_metadata.get("last_reconciliation_cycle_id") == "sweep:task-cycle-X"
        )

    # Celery worker crashed before ACK; broker redelivers sweep task X with the exact same cycle_id
    with session_factory() as session:
        summary_2 = asyncio.run(
            ingest_job_source(session, config, cycle_id="sweep:task-cycle-X", ctx=_ctx())
        )
        assert summary_2.total_discovered == 0
        origin_2 = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ext_id)
        ).scalar_one()
        # IDEMPOTENT: consecutive_not_found_count must NOT increment a second time!
        assert origin_2.consecutive_not_found_count == 1
        assert (
            origin_2.provenance_metadata.get("last_reconciliation_cycle_id") == "sweep:task-cycle-X"
        )

    # Subsequent independent sweep Y runs: now consecutive_not_found_count advances to 2
    with session_factory() as session:
        summary_3 = asyncio.run(
            ingest_job_source(session, config, cycle_id="sweep:task-cycle-Y", ctx=_ctx())
        )
        assert summary_3.total_discovered == 0
        origin_3 = session.execute(
            select(JobOrigin).where(JobOrigin.external_id == ext_id)
        ).scalar_one()
        assert origin_3.consecutive_not_found_count == 2
        assert (
            origin_3.provenance_metadata.get("last_reconciliation_cycle_id") == "sweep:task-cycle-Y"
        )


def test_advisory_lock_dedicated_connection_semantics_in_postgres(
    identity_client: object,
) -> None:
    """Finding 2 Tests 1-4, 7: Conn A locks, Conn B blocked, unlock returns True."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url), pool_size=5)

    source_id = f"lock-test-{uuid.uuid4().hex[:8]}"

    with engine.connect() as conn_a, engine.connect() as conn_b:
        # 1. Connection A acquires lock for source
        acquired_a = try_acquire_source_advisory_lock(conn_a, source_id)
        assert acquired_a is True

        # 2. Connection B CANNOT acquire while A holds lock
        acquired_b1 = try_acquire_source_advisory_lock(conn_b, source_id)
        assert acquired_b1 is False

        # 3. Release on Connection A returns True
        unlocked_a = release_source_advisory_lock(conn_a, source_id)
        assert unlocked_a is True

        # 4. Now Connection B can acquire
        acquired_b2 = try_acquire_source_advisory_lock(conn_b, source_id)
        assert acquired_b2 is True

        # Clean up
        unlocked_b = release_source_advisory_lock(conn_b, source_id)
        assert unlocked_b is True


def test_advisory_lock_released_on_ingestion_exception_in_postgres(
    identity_client: object,
) -> None:
    """Finding 2 Test 5: Ingestion exception releases dedicated advisory lock in finally."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url), pool_size=5)
    session_factory = create_session_factory(engine)

    board_id = f"exc-lock-{uuid.uuid4().hex[:8]}"

    class FailingSource(InMemoryJobSource):
        async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=self.provider,
                message="Simulated discovery timeout for lock release test",
                transient=True,
            )

    source = FailingSource("greenhouse", [])
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Fail Corp",
    )

    with session_factory() as session, pytest.raises(PortError):
        asyncio.run(ingest_job_source(session, config, ctx=_ctx()))

    # Lock must be released immediately in finally; a new connection can acquire it
    with engine.connect() as test_conn:
        acquired = try_acquire_source_advisory_lock(test_conn, board_id)
        assert acquired is True
        release_source_advisory_lock(test_conn, board_id)


def test_advisory_lock_session_commit_does_not_switch_lock_connection(
    identity_client: object,
) -> None:
    """Finding 2 Tests 6-8: session.commit does not switch lock connection; unlock is True."""
    settings = ApiSettings()
    # Pool with at least 2 connections to ensure session.commit returns connection to pool
    engine = create_engine(str(settings.database_url), pool_size=5)
    session_factory = create_session_factory(engine)

    board_id = f"commit-lock-{uuid.uuid4().hex[:8]}"
    ref = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-{uuid.uuid4().hex[:8]}",
        url=f"https://boards.greenhouse.io/{board_id}/1",
    )
    item = DiscoveredJob(
        ref=ref,
        title="Software Engineer",
        company_name="Commit Corp",
        posted_at=datetime.now(UTC),
    )
    source = InMemoryJobSource("greenhouse", [item])
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Commit Corp",
    )

    with session_factory() as session:
        # Runs ingest_job_source which commits the session
        summary = asyncio.run(
            ingest_job_source(session, config, cycle_id="c-commit-test", ctx=_ctx())
        )
        assert summary.created_jobs == 1

    # 8. Prove no advisory lock remains held after function completes
    with engine.connect() as verify_conn:
        # If the lock had been leaked or held on another connection, this would fail (return False)
        acquired = try_acquire_source_advisory_lock(verify_conn, board_id)
        assert acquired is True
        release_source_advisory_lock(verify_conn, board_id)


def test_advisory_lock_concurrent_execution_rejection(
    identity_client: object,
) -> None:
    """Finding 2 Test 9: Concurrent executions on same source: exactly one runs, one rejected."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url), pool_size=5)
    session_factory = create_session_factory(engine)

    board_id = f"concur-lock-{uuid.uuid4().hex[:8]}"
    ref = ExternalJobRef(
        provider="greenhouse",
        external_id=f"job-{uuid.uuid4().hex[:8]}",
        url=f"https://boards.greenhouse.io/{board_id}/1",
    )
    item = DiscoveredJob(
        ref=ref,
        title="Concurrent Engineer",
        company_name="Concur Corp",
        posted_at=datetime.now(UTC),
    )

    class SlowSource(InMemoryJobSource):
        async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
            # Hold lock during slow discovery
            await asyncio.sleep(0.3)
            return await super().discover(cursor, ctx)

    source = SlowSource("greenhouse", [item])
    config = JobSourceConfig(
        source_id=board_id,
        provider="greenhouse",
        source=source,
        company_name="Concur Corp",
    )

    async def run_pair() -> tuple[IngestionSummary, IngestionSummary]:
        session1 = session_factory()
        session2 = session_factory()
        try:
            task1: asyncio.Task[IngestionSummary] = asyncio.create_task(
                ingest_job_source(session1, config, cycle_id="c-pair-1", ctx=_ctx())
            )
            # Small delay to ensure task1 acquires the lock first
            await asyncio.sleep(0.05)
            task2: asyncio.Task[IngestionSummary] = asyncio.create_task(
                ingest_job_source(session2, config, cycle_id="c-pair-2", ctx=_ctx())
            )
            r1 = await task1
            r2 = await task2
            return r1, r2
        finally:
            session1.close()
            session2.close()

    res1, res2 = asyncio.run(run_pair())

    # Exactly one executed; the other was skipped due to advisory lock
    summaries: list[IngestionSummary] = [res1, res2]
    completed = [s for s in summaries if s.discovery_complete is True]
    skipped = [s for s in summaries if s.discovery_complete is False]

    assert len(completed) == 1
    assert len(skipped) == 1
    assert skipped[0].created_jobs == 0
    assert skipped[0].total_discovered == 0

    # Subsequent execution after pair finishes can acquire normally
    with session_factory() as session3:
        subsequent = asyncio.run(
            ingest_job_source(session3, config, cycle_id="c-pair-3", ctx=_ctx())
        )
        assert subsequent.discovery_complete is True


def test_regression_old_session_advisory_lock_defect(
    identity_client: object,
) -> None:
    """Finding 2 Test 10: Demonstrate session unlock across commit fails, dedicated passes."""
    settings = ApiSettings()
    # Explicit pool of 2 connections
    engine = create_engine(str(settings.database_url), pool_size=2, max_overflow=0)
    session_factory = create_session_factory(engine)

    board_id = f"defect-repro-{uuid.uuid4().hex[:8]}"
    key = advisory_lock_key(board_id)

    # 1. Reproduce old bug: acquiring via Session, committing, and trying to unlock
    with session_factory() as buggy_session:
        # Acquire lock on whichever connection the session gets (Conn 1)
        res_acq = buggy_session.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar()
        assert res_acq is True

        # Commit returns Conn 1 to the pool
        buggy_session.commit()

        # Another client checks out Conn 1 from the pool
        with engine.connect() as stolen_conn:
            # Now buggy_session.execute(...) gets Conn 2 from the pool!
            unlock_attempt = buggy_session.execute(
                sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key}
            ).scalar()
            # Conn 2 does NOT hold the lock, so pg_advisory_unlock returns False!
            assert unlock_attempt is False

            # Clean up the lock still held by Conn 1 (stolen_conn)
            cleanup_unlocked = stolen_conn.execute(
                sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key}
            ).scalar()
            assert cleanup_unlocked is True

    # 2. Demonstrate new dedicated connection approach always unlocks successfully (returns True)
    with engine.connect() as dedicated:
        acquired = try_acquire_source_advisory_lock(dedicated, board_id)
        assert acquired is True

        # Business session commits
        with session_factory() as business_session:
            business_session.execute(sa.text("SELECT 1"))
            business_session.commit()

        # Unlock on dedicated connection is guaranteed to be on the same physical connection
        unlocked = release_source_advisory_lock(dedicated, board_id)
        assert unlocked is True
