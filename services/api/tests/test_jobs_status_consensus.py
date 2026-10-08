"""Unit tests for Job effective market status consensus rules (WP2.1-A)."""

import uuid
from datetime import UTC, datetime, timedelta

from tapjob_api.jobs.models import JobOrigin, JobStatus, ObservationStatus
from tapjob_api.jobs.service import compute_job_effective_status


def _make_origin(provider: str, status: ObservationStatus) -> JobOrigin:
    return JobOrigin(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        provider=provider,
        external_id=None if provider.startswith("manual") else "123",
        observation_status=status,
    )


def test_pure_manual_paste_produces_unknown_status() -> None:
    origins = [_make_origin("manual_paste", ObservationStatus.UNKNOWN)]
    status = compute_job_effective_status(origins, expires_at=None)
    assert status == JobStatus.UNKNOWN


def test_authoritative_active_promotes_job_to_active() -> None:
    origins = [
        _make_origin("manual_paste", ObservationStatus.UNKNOWN),
        _make_origin("greenhouse", ObservationStatus.ACTIVE),
    ]
    status = compute_job_effective_status(origins, expires_at=None)
    assert status == JobStatus.ACTIVE


def test_authoritative_closed_closes_job_regardless_of_manual_paste() -> None:
    # Manual paste snapshot does NOT keep a job ACTIVE when authoritative provider closes
    origins = [
        _make_origin("manual_paste", ObservationStatus.UNKNOWN),
        _make_origin("greenhouse", ObservationStatus.CLOSED),
    ]
    status = compute_job_effective_status(origins, expires_at=None)
    assert status == JobStatus.CLOSED


def test_all_authoritative_unavailable_sets_job_to_unavailable() -> None:
    origins = [
        _make_origin("manual_paste", ObservationStatus.UNKNOWN),
        _make_origin("lever", ObservationStatus.UNAVAILABLE),
    ]
    status = compute_job_effective_status(origins, expires_at=None)
    assert status == JobStatus.UNAVAILABLE


def test_expired_at_overrides_to_expired() -> None:
    origins = [
        _make_origin("greenhouse", ObservationStatus.ACTIVE),
    ]
    past_expiry = datetime.now(UTC) - timedelta(hours=1)
    status = compute_job_effective_status(origins, expires_at=past_expiry)
    assert status == JobStatus.EXPIRED
