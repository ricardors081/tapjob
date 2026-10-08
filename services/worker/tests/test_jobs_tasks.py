"""Unit tests for Celery jobs tasks (WP2.1-B)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from tapjob_api.jobs.sources.registry import (
    JobSourceConfig,
    JobSourceRegistry,
)
from tapjob_api.jobs.tasks import (
    INGEST_JOB_SOURCE_TASK_NAME,
    configure_jobs_worker,
    ingest_job_source_task,
    sweep_scheduled_ingestion_task,
)
from tapjob_api.ports import (
    CallContext,
    DiscoveryPage,
    ExternalJobRef,
    JobDetails,
    PortError,
    PortErrorCode,
)


class DummySource:
    provider = "greenhouse"

    async def capabilities(self, ctx: CallContext) -> MagicMock:
        return MagicMock()

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        return DiscoveryPage(items=(), next_cursor=None)

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
        return JobDetails(ref=ref, description_raw="", fetched_at=datetime.now(UTC))

    async def health(self, ctx: CallContext) -> MagicMock:
        return MagicMock()


def test_ingest_job_source_task_unregistered_skips() -> None:
    registry = JobSourceRegistry()
    mock_session_factory = MagicMock()
    configure_jobs_worker(session_factory=mock_session_factory, registry=registry)

    result = ingest_job_source_task.run("non-existent")
    assert result["status"] == "skipped"
    assert result["reason"] == "unregistered_source"


def test_ingest_job_source_task_executes_registered_source() -> None:
    registry = JobSourceRegistry()
    config = JobSourceConfig(
        source_id="gh-test",
        provider="greenhouse",
        source=DummySource(),
        company_name="Test Company",
    )
    registry.register(config)

    mock_session = MagicMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session

    configure_jobs_worker(session_factory=mock_session_factory, registry=registry)

    with patch("tapjob_api.jobs.tasks.ingest_job_source") as mock_ingest:
        mock_summary = MagicMock(
            source_id="gh-test",
            provider="greenhouse",
            total_discovered=5,
            created_jobs=5,
            updated_origins=0,
            closed_jobs=0,
            errors=0,
            discovery_complete=True,
        )
        mock_ingest.return_value = mock_summary

        result = ingest_job_source_task.run("gh-test")

        assert result["status"] == "completed"
        assert result["total_discovered"] == 5
        assert result["created_jobs"] == 5
        assert result["discovery_complete"] is True
        assert "cycle_id" in result
        # Verify cycle_id was passed to ingest_job_source
        assert mock_ingest.call_args.kwargs["cycle_id"] == result["cycle_id"]


def test_ingest_job_source_task_retries_on_transient_failure() -> None:
    registry = JobSourceRegistry()
    config = JobSourceConfig(
        source_id="gh-test",
        provider="greenhouse",
        source=DummySource(),
        company_name="Test Company",
    )
    registry.register(config)

    mock_session = MagicMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session

    configure_jobs_worker(session_factory=mock_session_factory, registry=registry)

    with (
        patch("tapjob_api.jobs.tasks.ingest_job_source") as mock_ingest,
        patch.object(ingest_job_source_task, "retry") as mock_retry,
    ):
        mock_ingest.side_effect = PortError(
            PortErrorCode.TIMEOUT,
            provider="greenhouse",
            message="Connection timed out",
            transient=True,
        )
        mock_retry.side_effect = RuntimeError("Retry triggered")

        with pytest.raises(RuntimeError, match="Retry triggered"):
            ingest_job_source_task.run("gh-test")

        assert mock_retry.called
        assert mock_retry.call_args.kwargs["countdown"] == 60


def test_sweep_scheduled_ingestion_task_dispatches_active_sources_without_self_reschedule() -> None:
    registry = JobSourceRegistry()
    config1 = JobSourceConfig(
        source_id="gh-1",
        provider="greenhouse",
        source=DummySource(),
        company_name="Company 1",
        enabled=True,
    )
    config2 = JobSourceConfig(
        source_id="lv-2",
        provider="lever",
        source=DummySource(),
        company_name="Company 2",
        enabled=False,  # disabled should not be swept
    )
    registry.register(config1)
    registry.register(config2)

    mock_session_factory = MagicMock()
    configure_jobs_worker(session_factory=mock_session_factory, registry=registry)

    mock_app = MagicMock()
    with patch.object(sweep_scheduled_ingestion_task, "app", mock_app):
        result = sweep_scheduled_ingestion_task.run(3600)

        assert result["status"] == "dispatched"
        assert result["enqueued_count"] == 1
        assert result["enqueued_sources"] == ["gh-1"]
        assert "cycle_id" in result
        assert result["cycle_id"].startswith("sweep:")

        mock_app.send_task.assert_called_once_with(
            INGEST_JOB_SOURCE_TASK_NAME,
            args=["gh-1"],
            kwargs={"cycle_id": result["cycle_id"]},
        )
        # Verified: no self-rescheduling chain; Celery Beat handles periodic schedule centrally


def test_sweep_cycle_id_stable_under_celery_redelivery() -> None:
    """1 & 3: Prove same self.request.id in sweep produces identical cycle_id."""
    registry = JobSourceRegistry()
    registry.register(
        JobSourceConfig(
            source_id="gh-sweep",
            provider="greenhouse",
            source=DummySource(),
            company_name="Acme",
            enabled=True,
        )
    )
    configure_jobs_worker(session_factory=MagicMock(), registry=registry)

    mock_app = MagicMock()
    with patch.object(sweep_scheduled_ingestion_task, "app", mock_app):
        # Simulate worker executing sweep task with Celery task ID "sweep-uuid-123"
        sweep_scheduled_ingestion_task.request.id = "sweep-uuid-123"

        # First delivery
        result_1 = sweep_scheduled_ingestion_task.run(3600)
        assert result_1["cycle_id"] == "sweep:sweep-uuid-123"
        mock_app.send_task.assert_called_with(
            INGEST_JOB_SOURCE_TASK_NAME,
            args=["gh-sweep"],
            kwargs={"cycle_id": "sweep:sweep-uuid-123"},
        )

        # Worker dies before ACK; Celery redelivers task with the EXACT same request.id
        mock_app.send_task.reset_mock()
        result_2 = sweep_scheduled_ingestion_task.run(3600)
        assert result_2["cycle_id"] == "sweep:sweep-uuid-123"
        assert result_2["cycle_id"] == result_1["cycle_id"]
        mock_app.send_task.assert_called_with(
            INGEST_JOB_SOURCE_TASK_NAME,
            args=["gh-sweep"],
            kwargs={"cycle_id": "sweep:sweep-uuid-123"},
        )


def test_sweep_cycle_id_different_for_different_tasks() -> None:
    """2: Prove distinct sweep tasks produce distinct cycle_ids."""
    registry = JobSourceRegistry()
    registry.register(
        JobSourceConfig(
            source_id="gh-sweep",
            provider="greenhouse",
            source=DummySource(),
            company_name="Acme",
            enabled=True,
        )
    )
    configure_jobs_worker(session_factory=MagicMock(), registry=registry)

    with patch.object(sweep_scheduled_ingestion_task, "app", MagicMock()):
        sweep_scheduled_ingestion_task.request.id = "sweep-task-A"
        res_a = sweep_scheduled_ingestion_task.run(3600)

        sweep_scheduled_ingestion_task.request.id = "sweep-task-B"
        res_b = sweep_scheduled_ingestion_task.run(3600)

        assert res_a["cycle_id"] == "sweep:sweep-task-A"
        assert res_b["cycle_id"] == "sweep:sweep-task-B"
        assert res_a["cycle_id"] != res_b["cycle_id"]


def test_ingest_standalone_cycle_id_stable_under_retry_and_redelivery() -> None:
    """4: Ingest task without explicit cycle_id derives stable cycle_id from request.id."""
    registry = JobSourceRegistry()
    registry.register(
        JobSourceConfig(
            source_id="gh-ingest",
            provider="greenhouse",
            source=DummySource(),
            company_name="Acme",
        )
    )
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = MagicMock()
    configure_jobs_worker(session_factory=mock_session_factory, registry=registry)

    with patch("tapjob_api.jobs.tasks.ingest_job_source") as mock_ingest:
        mock_ingest.return_value = MagicMock(
            source_id="gh-ingest",
            provider="greenhouse",
            total_discovered=0,
            created_jobs=0,
            updated_origins=0,
            closed_jobs=0,
            errors=0,
            discovery_complete=True,
        )

        ingest_job_source_task.request.id = "ingest-task-456"

        # First run / attempt
        res_1 = ingest_job_source_task.run("gh-ingest")
        assert res_1["cycle_id"] == "task:ingest-task-456"
        assert mock_ingest.call_args.kwargs["cycle_id"] == "task:ingest-task-456"

        # Redelivery or retry retains the same request.id
        res_2 = ingest_job_source_task.run("gh-ingest")
        assert res_2["cycle_id"] == "task:ingest-task-456"
        assert res_2["cycle_id"] == res_1["cycle_id"]


def test_ingest_job_source_task_preserves_explicit_cycle_id() -> None:
    """5: Ingest task with explicit cycle_id (e.g. from sweep) preserves it exactly."""
    registry = JobSourceRegistry()
    registry.register(
        JobSourceConfig(
            source_id="gh-ingest",
            provider="greenhouse",
            source=DummySource(),
            company_name="Acme",
        )
    )
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = MagicMock()
    configure_jobs_worker(session_factory=mock_session_factory, registry=registry)

    with patch("tapjob_api.jobs.tasks.ingest_job_source") as mock_ingest:
        mock_ingest.return_value = MagicMock(
            source_id="gh-ingest",
            provider="greenhouse",
            total_discovered=0,
            created_jobs=0,
            updated_origins=0,
            closed_jobs=0,
            errors=0,
            discovery_complete=True,
        )

        # Even if task has its own request.id, explicit cycle_id from parent sweep is preserved
        ingest_job_source_task.request.id = "child-task-789"
        res = ingest_job_source_task.run("gh-ingest", cycle_id="sweep:parent-sweep-001")
        assert res["cycle_id"] == "sweep:parent-sweep-001"
        assert mock_ingest.call_args.kwargs["cycle_id"] == "sweep:parent-sweep-001"
