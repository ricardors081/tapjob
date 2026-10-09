"""Job match fingerprint calculation for WP2.2 (ADR-0014 Decision 2).

Provides an immutable, deterministic digest covering all canonical job fields
affecting matching evaluation (remedying similarity_fingerprint's omission of workplace_type).
"""

from __future__ import annotations

import hashlib
import json

from tapjob_api.jobs.models import Job


def compute_job_match_fingerprint(job: Job) -> str:
    """Computes SHA-256 fingerprint of all job inputs that affect matching evaluation.

    Includes:
    - job_id: UUID string
    - title_normalized: str
    - location_normalized: str | None
    - workplace_type: str ('remote', 'hybrid', 'on_site', 'unknown')
    - description_cleaned_sha256: SHA-256 hex digest of description_cleaned
    """
    payload = {
        "job_id": str(job.id),
        "title_normalized": job.title_normalized,
        "location_normalized": job.location_normalized,
        "workplace_type": job.workplace_type.value,
        "description_cleaned_sha256": hashlib.sha256(
            job.description_cleaned.encode("utf-8")
        ).hexdigest(),
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
