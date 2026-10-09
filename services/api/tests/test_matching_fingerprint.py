"""Tests for job match fingerprint calculation (ADR-0014 Decision 2).

Verifies:
- SHA-256 deterministic stability for identical inputs.
- Sensitivity to workplace_type changes (remedying similarity_fingerprint omission).
- Sensitivity to title, location, description, and job ID changes.
"""

import uuid

from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint


def _make_dummy_job(
    *,
    job_id: uuid.UUID | None = None,
    title_normalized: str = "software engineer",
    location_normalized: str | None = "sao paulo, sp",
    workplace_type: JobWorkplaceType = JobWorkplaceType.REMOTE,
    description_cleaned: str = "Python and FastAPI backend role.",
) -> Job:
    job = Job()
    job.id = job_id or uuid.UUID("11111111-1111-1111-1111-111111111111")
    job.title_normalized = title_normalized
    job.location_normalized = location_normalized
    job.workplace_type = workplace_type
    job.description_cleaned = description_cleaned
    return job


def test_fingerprint_deterministic_stability() -> None:
    job1 = _make_dummy_job()
    job2 = _make_dummy_job()

    fp1 = compute_job_match_fingerprint(job1)
    fp2 = compute_job_match_fingerprint(job2)

    assert fp1 == fp2
    assert len(fp1) == 64
    assert isinstance(fp1, str)


def test_fingerprint_sensitivity_to_workplace_type() -> None:
    job_remote = _make_dummy_job(workplace_type=JobWorkplaceType.REMOTE)
    job_hybrid = _make_dummy_job(workplace_type=JobWorkplaceType.HYBRID)
    job_onsite = _make_dummy_job(workplace_type=JobWorkplaceType.ON_SITE)

    fp_remote = compute_job_match_fingerprint(job_remote)
    fp_hybrid = compute_job_match_fingerprint(job_hybrid)
    fp_onsite = compute_job_match_fingerprint(job_onsite)

    assert fp_remote != fp_hybrid
    assert fp_remote != fp_onsite
    assert fp_hybrid != fp_onsite


def test_fingerprint_sensitivity_to_title() -> None:
    job1 = _make_dummy_job(title_normalized="software engineer")
    job2 = _make_dummy_job(title_normalized="senior software engineer")

    assert compute_job_match_fingerprint(job1) != compute_job_match_fingerprint(job2)


def test_fingerprint_sensitivity_to_location() -> None:
    job1 = _make_dummy_job(location_normalized="sao paulo, sp")
    job2 = _make_dummy_job(location_normalized="rio de janeiro, rj")
    job3 = _make_dummy_job(location_normalized=None)

    fp1 = compute_job_match_fingerprint(job1)
    fp2 = compute_job_match_fingerprint(job2)
    fp3 = compute_job_match_fingerprint(job3)

    assert fp1 != fp2
    assert fp1 != fp3
    assert fp2 != fp3


def test_fingerprint_sensitivity_to_description() -> None:
    job1 = _make_dummy_job(description_cleaned="Python backend")
    job2 = _make_dummy_job(description_cleaned="Python backend with AWS")

    assert compute_job_match_fingerprint(job1) != compute_job_match_fingerprint(job2)


def test_fingerprint_sensitivity_to_job_id() -> None:
    job1 = _make_dummy_job(job_id=uuid.UUID("11111111-1111-1111-1111-111111111111"))
    job2 = _make_dummy_job(job_id=uuid.UUID("22222222-2222-2222-2222-222222222222"))

    assert compute_job_match_fingerprint(job1) != compute_job_match_fingerprint(job2)
