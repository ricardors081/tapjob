"""Unit tests for jobs ingestion service logic (WP2.1-B)."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from tapjob_api.jobs.ingestion import (
    MAX_DISCOVERY_PAGES,
    ingest_job_source,
)
from tapjob_api.jobs.models import (
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


def _ctx() -> CallContext:
    return CallContext.with_timeout("test-ingest", 10.0)


class MockJobSource:
    provider: str = "greenhouse"

    def __init__(self, pages: list[DiscoveryPage]) -> None:
        self.pages = pages
        self._current_page = 0
        self.fetched_refs: list[ExternalJobRef] = []

    async def capabilities(self, ctx: CallContext) -> MagicMock:
        return MagicMock()

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        if self._current_page < len(self.pages):
            page = self.pages[self._current_page]
            self._current_page += 1
            return page
        return DiscoveryPage(items=(), next_cursor=None)

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
        self.fetched_refs.append(ref)
        return JobDetails(
            ref=ref,
            description_raw=f"Detailed description for {ref.external_id}",
            fetched_at=datetime.now(UTC),
        )

    async def health(self, ctx: CallContext) -> MagicMock:
        return MagicMock()


class FailingJobSource(MockJobSource):
    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=self.provider,
            message="Non-transient invalid request",
            transient=False,
        )


def test_ingestion_handles_non_transient_discovery_port_error() -> None:
    async def scenario() -> None:
        mock_source = FailingJobSource([])
        config = JobSourceConfig(
            source_id="gh-fail",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme",
        )
        session = MagicMock()

        summary = await ingest_job_source(session, config, ctx=_ctx())
        assert summary.errors == 1
        assert summary.created_jobs == 0
        assert summary.total_discovered == 0
        assert summary.discovery_complete is False

    asyncio.run(scenario())


def test_ingestion_re_raises_transient_discovery_port_error_for_celery_retry() -> None:
    async def scenario() -> None:
        class TransientFailingJobSource(MockJobSource):
            async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
                raise PortError(
                    PortErrorCode.TIMEOUT,
                    provider=self.provider,
                    message="Provider connection timed out",
                    transient=True,
                )

        mock_source = TransientFailingJobSource([])
        config = JobSourceConfig(
            source_id="gh-fail",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme",
        )
        session = MagicMock()

        with pytest.raises(PortError) as exc_info:
            await ingest_job_source(session, config, ctx=_ctx())
        assert exc_info.value.code == PortErrorCode.TIMEOUT
        assert exc_info.value.transient is True

    asyncio.run(scenario())


def test_ingestion_creates_new_jobs_and_origins_with_location() -> None:
    async def scenario() -> None:
        ref1 = ExternalJobRef(
            provider="greenhouse", external_id="101", url="https://boards.greenhouse.io/acme/101"
        )
        ref2 = ExternalJobRef(
            provider="greenhouse", external_id="102", url="https://boards.greenhouse.io/acme/102"
        )

        item1 = DiscoveredJob(
            ref=ref1,
            title="Staff Engineer",
            company_name="Acme",
            posted_at=datetime.now(UTC),
            location_raw="New York, NY",
        )
        item2 = DiscoveredJob(
            ref=ref2,
            title="Frontend Dev",
            company_name="Acme",
            posted_at=datetime.now(UTC),
            location_raw=None,
        )

        page = DiscoveryPage(items=(item1, item2), next_cursor=None)
        mock_source = MockJobSource([page])
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
            corporate_domain="acme.com",
        )

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, ctx=_ctx())
        assert summary.total_discovered == 2
        assert summary.created_jobs == 2
        assert summary.updated_origins == 0
        assert summary.closed_jobs == 0
        assert summary.errors == 0
        assert summary.discovery_complete is True
        assert session.commit.called

        # Verify Job creation received location
        added_jobs = [
            call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Job)
        ]
        assert len(added_jobs) == 2
        assert added_jobs[0].location_raw == "New York, NY"
        assert added_jobs[0].location_normalized is not None

    asyncio.run(scenario())


def test_ingestion_updates_existing_origin() -> None:
    async def scenario() -> None:
        ref = ExternalJobRef(
            provider="greenhouse", external_id="101", url="https://boards.greenhouse.io/acme/101"
        )
        item = DiscoveredJob(
            ref=ref, title="Staff Engineer", company_name="Acme", posted_at=datetime.now(UTC)
        )
        page = DiscoveryPage(items=(item,), next_cursor=None)
        mock_source = MockJobSource([page])
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        job = Job(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            title="Staff Engineer",
            title_normalized="staff engineer",
            description_raw="Desc",
            description_cleaned="Desc",
            similarity_fingerprint="fp",
            status=JobStatus.ACTIVE,
        )
        existing_origin = JobOrigin(
            id=uuid.uuid4(),
            job_id=job.id,
            provider="greenhouse",
            external_id="101",
            observation_status=ObservationStatus.ACTIVE,
            consecutive_not_found_count=2,
            provenance_metadata={"board_id": "gh-acme"},
        )
        existing_origin.job = job
        job.origins = [existing_origin]

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = existing_origin
        mock_scalar.scalars.return_value.all.return_value = [existing_origin]
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, ctx=_ctx())
        assert summary.total_discovered == 1
        assert summary.created_jobs == 0
        assert summary.updated_origins == 1
        assert existing_origin.consecutive_not_found_count == 0
        assert session.commit.called

    asyncio.run(scenario())


def test_ingestion_liveness_reconciliation_increments_and_closes_when_complete() -> None:
    async def scenario() -> None:
        # Discovery returns EMPTY items and complete (next_cursor=None)
        page = DiscoveryPage(items=(), next_cursor=None)
        mock_source = MockJobSource([page])
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        job = Job(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            title="Discontinued Role",
            title_normalized="discontinued role",
            description_raw="Desc",
            description_cleaned="Desc",
            similarity_fingerprint="fp",
            status=JobStatus.ACTIVE,
        )
        origin = JobOrigin(
            id=uuid.uuid4(),
            job_id=job.id,
            provider="greenhouse",
            external_id="missing-999",
            observation_status=ObservationStatus.ACTIVE,
            consecutive_not_found_count=2,  # Already missed 2 times
            provenance_metadata={"board_id": "gh-acme"},
        )
        origin.job = job
        job.origins = [origin]

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = [origin]
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, ctx=_ctx(), not_found_threshold=3)
        assert summary.total_discovered == 0
        assert summary.closed_jobs == 1
        assert summary.discovery_complete is True
        assert origin.consecutive_not_found_count == 3
        assert origin.observation_status == ObservationStatus.CLOSED
        assert job.status == JobStatus.CLOSED
        assert session.commit.called

    asyncio.run(scenario())


def test_ingestion_skips_liveness_when_max_pages_reached_with_remaining_cursor() -> None:
    async def scenario() -> None:
        # Generate 50 pages all with next_cursor="has_more"
        pages = [
            DiscoveryPage(items=(), next_cursor="page-next") for _ in range(MAX_DISCOVERY_PAGES + 2)
        ]
        mock_source = MockJobSource(pages)
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        origin = JobOrigin(
            id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            provider="greenhouse",
            external_id="missing-999",
            observation_status=ObservationStatus.ACTIVE,
            consecutive_not_found_count=1,
            provenance_metadata={"board_id": "gh-acme"},
        )
        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = [origin]
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, ctx=_ctx())
        assert summary.discovery_complete is False
        assert summary.closed_jobs == 0
        # Consecutive not found count MUST NOT increment on incomplete run!
        assert origin.consecutive_not_found_count == 1
        assert origin.observation_status == ObservationStatus.ACTIVE

    asyncio.run(scenario())


def test_ingestion_skips_liveness_when_cursor_cycle_detected() -> None:
    async def scenario() -> None:
        # Page 1 cursor None -> next "cursor-A"; Page 2 cursor "cursor-A" -> next "cursor-A" (loop)
        pages = [
            DiscoveryPage(items=(), next_cursor="cursor-A"),
            DiscoveryPage(items=(), next_cursor="cursor-A"),
        ]
        mock_source = MockJobSource(pages)
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        origin = JobOrigin(
            id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            provider="greenhouse",
            external_id="missing-999",
            observation_status=ObservationStatus.ACTIVE,
            consecutive_not_found_count=2,
            provenance_metadata={"board_id": "gh-acme"},
        )
        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = [origin]
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, ctx=_ctx())
        assert summary.discovery_complete is False
        assert summary.closed_jobs == 0
        assert origin.consecutive_not_found_count == 2
        assert origin.observation_status == ObservationStatus.ACTIVE

    asyncio.run(scenario())


def test_ingestion_skips_job_creation_on_transient_detail_fetch_failure() -> None:
    async def scenario() -> None:
        class TransientDetailFailingSource(MockJobSource):
            async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
                raise PortError(
                    PortErrorCode.RATE_LIMITED,
                    provider=self.provider,
                    message="Rate limit 429",
                    transient=True,
                )

        ref1 = ExternalJobRef(
            provider="greenhouse", external_id="101", url="https://boards.greenhouse.io/acme/101"
        )
        item1 = DiscoveredJob(
            ref=ref1, title="Staff Engineer", company_name="Acme", posted_at=datetime.now(UTC)
        )
        page = DiscoveryPage(items=(item1,), next_cursor=None)
        mock_source = TransientDetailFailingSource([page])
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, ctx=_ctx())
        # Should not create corrupted job with dummy description
        assert summary.created_jobs == 0
        assert summary.errors == 1
        added_jobs_and_origins = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], (Job, JobOrigin))
        ]
        assert len(added_jobs_and_origins) == 0

    asyncio.run(scenario())


def test_ingestion_does_not_create_company_on_empty_discovery() -> None:
    async def scenario() -> None:
        page = DiscoveryPage(items=(), next_cursor=None)
        mock_source = MockJobSource([page])
        config = JobSourceConfig(
            source_id="gh-empty-board",
            provider="greenhouse",
            source=mock_source,
            company_name="Empty Board Co",
            corporate_domain=None,
        )

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = []
        mock_scalar.all.return_value = []
        session.execute.return_value = mock_scalar

        with patch("tapjob_api.jobs.ingestion.get_or_create_company") as mock_create_comp:
            summary = await ingest_job_source(session, config, ctx=_ctx())
            assert summary.total_discovered == 0
            assert summary.created_jobs == 0
            # get_or_create_company must NEVER be called on empty discovery!
            assert not mock_create_comp.called

    asyncio.run(scenario())


def test_ingestion_cycle_idempotency_prevents_duplicate_strikes() -> None:
    async def scenario() -> None:
        # Discovery returns 0 items (complete)
        page = DiscoveryPage(items=(), next_cursor=None)
        mock_source = MockJobSource([page])
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        job = Job(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            title="Absent Job",
            title_normalized="absent job",
            description_raw="Desc",
            description_cleaned="Desc",
            similarity_fingerprint="fp",
            status=JobStatus.ACTIVE,
        )
        origin = JobOrigin(
            id=uuid.uuid4(),
            job_id=job.id,
            provider="greenhouse",
            external_id="absent-101",
            observation_status=ObservationStatus.ACTIVE,
            consecutive_not_found_count=1,
            provenance_metadata={"board_id": "gh-acme", "last_reconciliation_cycle_id": "cycle-X"},
        )
        origin.job = job
        job.origins = [origin]

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_scalar.scalars.return_value.all.return_value = [origin]
        session.execute.return_value = mock_scalar

        # Redelivering with cycle-X: must be a NO-OP, not incrementing consecutive_not_found_count!
        summary = await ingest_job_source(session, config, cycle_id="cycle-X", ctx=_ctx())
        assert summary.closed_jobs == 0
        assert origin.consecutive_not_found_count == 1  # Unchanged!

        # Executing with a NEW cycle-Y: increments to 2!
        summary2 = await ingest_job_source(session, config, cycle_id="cycle-Y", ctx=_ctx())
        assert summary2.closed_jobs == 0
        assert origin.consecutive_not_found_count == 2
        assert origin.provenance_metadata["last_reconciliation_cycle_id"] == "cycle-Y"

    asyncio.run(scenario())


def test_ingestion_updates_mutable_fields_on_existing_posting() -> None:
    async def scenario() -> None:
        ref = ExternalJobRef(
            provider="greenhouse",
            external_id="ext-mut",
            url="https://boards.greenhouse.io/acme/mut",
        )
        item = DiscoveredJob(
            ref=ref,
            title="New Title",
            company_name="Acme",
            posted_at=datetime.now(UTC),
            location_raw="New Location",
            additional_locations_raw=("Second Location",),
            workplace_type_hint="remote",
        )
        page = DiscoveryPage(items=(item,), next_cursor=None)

        class MutatingSource(MockJobSource):
            async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
                return JobDetails(
                    ref=ref,
                    description_raw="<p>Updated brand new description</p>",
                    fetched_at=datetime.now(UTC),
                )

        mock_source = MutatingSource([page])
        config = JobSourceConfig(
            source_id="gh-acme",
            provider="greenhouse",
            source=mock_source,
            company_name="Acme Corp",
        )

        job_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        origin_id = uuid.uuid4()
        first_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        job = Job(
            id=job_id,
            company_id=comp_id,
            title="Old Title",
            title_normalized="old title",
            location_raw="Old Location",
            location_normalized="old location",
            description_raw="Old description",
            description_cleaned="Old description",
            similarity_fingerprint="old-fingerprint",
            status=JobStatus.ACTIVE,
        )
        origin = JobOrigin(
            id=origin_id,
            job_id=job.id,
            provider="greenhouse",
            external_id="ext-mut",
            observation_status=ObservationStatus.ACTIVE,
            consecutive_not_found_count=0,
            first_observed_at=first_time,
            last_observed_at=first_time,
            provenance_metadata={"board_id": "gh-acme"},
        )
        origin.job = job
        job.origins = [origin]

        session = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = origin
        mock_scalar.scalars.return_value.all.return_value = [origin]
        session.execute.return_value = mock_scalar

        summary = await ingest_job_source(session, config, cycle_id="cycle-mut", ctx=_ctx())
        assert summary.updated_origins == 1
        assert summary.created_jobs == 0

        # Mutable fields updated
        assert job.title == "New Title"
        assert job.title_normalized == "new title"
        assert job.location_raw == "New Location"
        assert "second location" in (job.location_normalized or "")
        assert job.workplace_type.value == "remote"
        assert job.description_raw == "<p>Updated brand new description</p>"
        assert job.similarity_fingerprint != "old-fingerprint"

        # Invariants preserved
        assert job.id == job_id
        assert job.company_id == comp_id
        assert origin.id == origin_id
        assert origin.first_observed_at == first_time
        assert origin.last_observed_at >= first_time

    asyncio.run(scenario())


def test_resolve_source_company_invariant_violation_raises() -> None:
    session = MagicMock()
    config = JobSourceConfig(
        source_id="gh-conflict",
        provider="greenhouse",
        source=MockJobSource([]),
        company_name="Conflicted Co",
        corporate_domain=None,
    )

    # Simulate 2 different companies linked to the same board
    session.bind = None
    comp1_id = uuid.uuid4()
    comp2_id = uuid.uuid4()
    mock_rows = [
        (comp1_id, {"board_id": "gh-conflict"}),
        (comp2_id, {"board_id": "gh-conflict"}),
    ]
    session.execute.return_value.all.return_value = mock_rows

    from tapjob_api.jobs.ingestion import resolve_source_company

    with pytest.raises(
        ValueError,
        match="Invariant violation: source board 'gh-conflict' has multiple associated Companies",
    ):
        resolve_source_company(session, config)
