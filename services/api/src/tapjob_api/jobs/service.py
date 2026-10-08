"""Service layer implementing business rules and invariants for Jobs (WP2.1-A)."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tapjob_api.identity.models import AuditOutcome, User
from tapjob_api.identity.service import record_audit_event
from tapjob_api.jobs.errors import (
    ConcurrentRequestInProgressError,
    IdempotencyKeyPayloadMismatchError,
    IdempotencyOwnershipLostError,
    JobNotFoundError,
    JobTextTooLongError,
    JobTextTooShortError,
)
from tapjob_api.jobs.extraction import ExtractedJobDetails, extract_job_from_html
from tapjob_api.jobs.http_client import SafeHttpClient
from tapjob_api.jobs.models import (
    Company,
    IdempotencyKey,
    IdempotencyStatus,
    Job,
    JobOrigin,
    JobStatus,
    ObservationStatus,
)
from tapjob_api.jobs.normalization import (
    classify_workplace_type,
    clean_corporate_domain,
    clean_description_text,
    compute_similarity_fingerprint,
    normalize_company_name,
    normalize_job_title,
    normalize_location,
)
from tapjob_api.jobs.schemas import (
    JobPasteIntakeRequest,
    JobProvenanceSummary,
    JobUrlIntakeRequest,
    ProvenanceFieldSource,
)
from tapjob_api.jobs.url_canonicalization import canonicalize_url

MIN_DESCRIPTION_LENGTH = 10
MAX_DESCRIPTION_LENGTH = 100000

IDEMPOTENCY_TTL_HOURS = 24
IDEMPOTENCY_PENDING_STALE_SECONDS = 120


@dataclass(frozen=True, slots=True)
class ReservationResult:
    """Outcome of attempting an idempotency reservation."""

    is_owner: bool
    reservation_token: uuid.UUID | None
    stored_response: dict[str, Any] | None = None
    stored_status_code: int | None = None
    resource_id: uuid.UUID | None = None


def compute_request_fingerprint(data_bytes: bytes) -> str:
    """Compute deterministic SHA-256 fingerprint of request payload."""
    return hashlib.sha256(data_bytes).hexdigest()


def reserve_idempotency_key(
    session: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    operation: str,
    request_fingerprint: str,
) -> ReservationResult:
    """Attempt to reserve an idempotency key atomically with fencing token.

    Returns:
        ReservationResult indicating whether caller is the active owner (with reservation_token)
        or if request was completed previously and can replay stored response.

    Raises:
        IdempotencyKeyPayloadMismatchError: if key was previously used with different payload.
        ConcurrentRequestInProgressError: if a request with this key is currently PENDING.
    """
    now = datetime.now(UTC)
    existing = session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.key == key,
            IdempotencyKey.operation == operation,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.expires_at < now:
            # Expired key: remove it so new reservation can proceed
            session.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.key == key,
                    IdempotencyKey.operation == operation,
                    IdempotencyKey.expires_at < now,
                )
            )
            session.flush()
        else:
            if existing.request_fingerprint != request_fingerprint:
                raise IdempotencyKeyPayloadMismatchError(
                    "Idempotency key payload mismatch: key was previously used with different input"
                )
            if existing.status == IdempotencyStatus.PENDING:
                stale_threshold = now - timedelta(seconds=IDEMPOTENCY_PENDING_STALE_SECONDS)
                if existing.created_at < stale_threshold:
                    # Stale PENDING: atomic conditional takeover with new fencing token
                    takeover_token = uuid.uuid4()
                    stmt = (
                        update(IdempotencyKey)
                        .where(
                            IdempotencyKey.user_id == user_id,
                            IdempotencyKey.key == key,
                            IdempotencyKey.operation == operation,
                            IdempotencyKey.status == IdempotencyStatus.PENDING,
                            IdempotencyKey.created_at < stale_threshold,
                        )
                        .values(
                            created_at=now,
                            expires_at=now + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
                            reservation_token=takeover_token,
                        )
                        .returning(IdempotencyKey.reservation_token)
                    )
                    updated_token = session.execute(stmt).scalar_one_or_none()
                    if updated_token is not None:
                        session.flush()
                        return ReservationResult(
                            is_owner=True,
                            reservation_token=updated_token,
                        )
                    # If conditional update updated 0 rows:
                    # another caller won takeover race or completed.
                    race_row = session.execute(
                        select(IdempotencyKey).where(
                            IdempotencyKey.user_id == user_id,
                            IdempotencyKey.key == key,
                            IdempotencyKey.operation == operation,
                        )
                    ).scalar_one_or_none()
                    if race_row is not None:
                        if race_row.request_fingerprint != request_fingerprint:
                            raise IdempotencyKeyPayloadMismatchError(
                                "Idempotency key payload mismatch"
                            )
                        if race_row.status == IdempotencyStatus.COMPLETED:
                            return ReservationResult(
                                is_owner=False,
                                reservation_token=None,
                                stored_response=race_row.response_body,
                                stored_status_code=race_row.response_status,
                                resource_id=race_row.resource_id,
                            )
                    raise ConcurrentRequestInProgressError(
                        "A request with this idempotency key is currently in progress"
                    )
                raise ConcurrentRequestInProgressError(
                    "A request with this idempotency key is currently in progress"
                )
            if existing.status == IdempotencyStatus.COMPLETED:
                return ReservationResult(
                    is_owner=False,
                    reservation_token=None,
                    stored_response=existing.response_body,
                    stored_status_code=existing.response_status,
                    resource_id=existing.resource_id,
                )

    # Insert new PENDING row with freshly generated fencing token
    new_token = uuid.uuid4()
    reservation = IdempotencyKey(
        user_id=user_id,
        key=key,
        operation=operation,
        request_fingerprint=request_fingerprint,
        status=IdempotencyStatus.PENDING,
        reservation_token=new_token,
        response_status=None,
        response_body=None,
        resource_id=None,
        created_at=now,
        expires_at=now + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
    )
    try:
        session.add(reservation)
        session.flush()
        return ReservationResult(is_owner=True, reservation_token=new_token)
    except IntegrityError:
        session.rollback()
        # Concurrency race on initial insert: re-query existing row
        existing_race = session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.key == key,
                IdempotencyKey.operation == operation,
            )
        ).scalar_one_or_none()
        if existing_race is not None:
            if existing_race.request_fingerprint != request_fingerprint:
                raise IdempotencyKeyPayloadMismatchError(
                    "Idempotency key payload mismatch"
                ) from None
            if existing_race.status == IdempotencyStatus.COMPLETED:
                return ReservationResult(
                    is_owner=False,
                    reservation_token=None,
                    stored_response=existing_race.response_body,
                    stored_status_code=existing_race.response_status,
                    resource_id=existing_race.resource_id,
                )
            if existing_race.status == IdempotencyStatus.PENDING:
                stale_threshold = now - timedelta(seconds=IDEMPOTENCY_PENDING_STALE_SECONDS)
                if existing_race.created_at < stale_threshold:
                    takeover_token = uuid.uuid4()
                    stmt = (
                        update(IdempotencyKey)
                        .where(
                            IdempotencyKey.user_id == user_id,
                            IdempotencyKey.key == key,
                            IdempotencyKey.operation == operation,
                            IdempotencyKey.status == IdempotencyStatus.PENDING,
                            IdempotencyKey.created_at < stale_threshold,
                        )
                        .values(
                            created_at=now,
                            expires_at=now + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
                            reservation_token=takeover_token,
                        )
                        .returning(IdempotencyKey.reservation_token)
                    )
                    updated_token = session.execute(stmt).scalar_one_or_none()
                    if updated_token is not None:
                        session.flush()
                        return ReservationResult(is_owner=True, reservation_token=updated_token)
                raise ConcurrentRequestInProgressError(
                    "A request with this idempotency key is currently in progress"
                ) from None
        raise


def complete_idempotency_key(
    session: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    operation: str,
    reservation_token: uuid.UUID,
    response_status: int,
    response_body: dict[str, Any],
    resource_id: uuid.UUID | None,
) -> None:
    """Transition a PENDING idempotency key to COMPLETED with stored response.

    Fencing token verification: updates only if status is PENDING and reservation_token matches.
    If 0 rows are updated, raises IdempotencyOwnershipLostError so the transaction rolls back
    and prevents an overtaken execution from corrupting state.
    """
    stmt = (
        update(IdempotencyKey)
        .where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.key == key,
            IdempotencyKey.operation == operation,
            IdempotencyKey.status == IdempotencyStatus.PENDING,
            IdempotencyKey.reservation_token == reservation_token,
        )
        .values(
            status=IdempotencyStatus.COMPLETED,
            response_status=response_status,
            response_body=response_body,
            resource_id=resource_id,
        )
    )
    result = session.execute(stmt)
    cursor_result = cast(CursorResult[Any], result)
    if cursor_result.rowcount == 0:
        raise IdempotencyOwnershipLostError(
            "Idempotency reservation ownership lost: cannot complete an overtaken reservation"
        )
    session.flush()


def release_pending_idempotency_key(
    session: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    operation: str,
    reservation_token: uuid.UUID,
) -> bool:
    """Release a PENDING idempotency key on failure so retries are not poisoned.

    Only deletes if status is PENDING and reservation_token matches.
    Returns True if deleted, False if ownership was lost or already completed.
    """
    stmt = delete(IdempotencyKey).where(
        IdempotencyKey.user_id == user_id,
        IdempotencyKey.key == key,
        IdempotencyKey.operation == operation,
        IdempotencyKey.status == IdempotencyStatus.PENDING,
        IdempotencyKey.reservation_token == reservation_token,
    )
    result = session.execute(stmt)
    cursor_result = cast(CursorResult[Any], result)
    session.flush()
    return bool(cursor_result.rowcount > 0)


def get_or_create_company(
    session: Session,
    *,
    name: str,
    domain: str | None = None,
) -> Company:
    """Find or create a Company following conservative identity rules.

    Invariants:
    - If domain is provided: match by domain (strong identity).
    - If domain is None: do NOT merge with other companies by name alone; create a distinct Company.
    """
    clean_name = name.strip()
    normalized_name = normalize_company_name(clean_name)
    verified_domain = clean_corporate_domain(domain)

    if verified_domain:
        existing = session.execute(
            select(Company).where(Company.domain == verified_domain)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        company = Company(
            name=clean_name,
            name_normalized=normalized_name,
            domain=verified_domain,
        )
        try:
            session.add(company)
            session.flush()
            return company
        except IntegrityError:
            session.rollback()
            existing_after_race = session.execute(
                select(Company).where(Company.domain == verified_domain)
            ).scalar_one_or_none()
            if existing_after_race is not None:
                return existing_after_race
            raise

    # Domain is None: conservative creation (no false merge by name)
    company = Company(
        name=clean_name,
        name_normalized=normalized_name,
        domain=None,
    )
    session.add(company)
    session.flush()
    return company


def compute_job_effective_status(
    origins: list[JobOrigin], expires_at: datetime | None
) -> JobStatus:
    """Compute effective market liveness status of a Job based on linked origins.

    Rules:
    - Non-authoritative origins (manual_paste with status UNKNOWN) have zero weight.
    - If expires_at is set and passed -> EXPIRED.
    - If at least one authoritative origin is ACTIVE -> ACTIVE.
    - If all authoritative origins are CLOSED -> CLOSED (manual_paste does NOT prevent closing).
    - If all authoritative origins are UNAVAILABLE -> UNAVAILABLE.
    - If all linked origins are UNKNOWN (or only manual_paste exists) -> UNKNOWN.
    """
    now = datetime.now(UTC)
    if expires_at is not None and expires_at <= now:
        return JobStatus.EXPIRED

    authoritative_origins = [o for o in origins if o.provider != "manual_paste"]

    if not authoritative_origins:
        return JobStatus.UNKNOWN

    has_active = any(
        o.observation_status == ObservationStatus.ACTIVE for o in authoritative_origins
    )
    if has_active:
        return JobStatus.ACTIVE

    all_closed = all(
        o.observation_status == ObservationStatus.CLOSED for o in authoritative_origins
    )
    if all_closed:
        return JobStatus.CLOSED

    all_unavailable = all(
        o.observation_status == ObservationStatus.UNAVAILABLE for o in authoritative_origins
    )
    if all_unavailable:
        return JobStatus.UNAVAILABLE

    return JobStatus.UNKNOWN


def intake_job_paste(
    session: Session,
    *,
    user: User,
    request: JobPasteIntakeRequest,
) -> Job:
    """Ingest a job description supplied via manual paste."""
    raw_desc = request.description.strip()
    if len(raw_desc) < MIN_DESCRIPTION_LENGTH:
        raise JobTextTooShortError(
            f"Job description too short: minimum {MIN_DESCRIPTION_LENGTH} characters required"
        )
    if len(raw_desc) > MAX_DESCRIPTION_LENGTH:
        raise JobTextTooLongError(
            f"Job description too long: maximum {MAX_DESCRIPTION_LENGTH} characters permitted"
        )

    # 1. Company identity: domain is NULL for manual paste
    company = get_or_create_company(session, name=request.company_name, domain=None)

    # 2. Normalization
    title_clean = request.title.strip()
    title_norm = normalize_job_title(title_clean)
    loc_norm = normalize_location(request.location_raw)
    desc_cleaned = clean_description_text(raw_desc)
    workplace = classify_workplace_type(title_clean, desc_cleaned, request.location_raw)
    fingerprint = compute_similarity_fingerprint(company.id, title_norm, loc_norm, desc_cleaned)

    provenance_summary = JobProvenanceSummary(
        title=ProvenanceFieldSource.USER_DIRECT,
        company=ProvenanceFieldSource.USER_DIRECT,
        location=ProvenanceFieldSource.USER_DIRECT if request.location_raw else None,
        workplace_type=ProvenanceFieldSource.HEURISTIC_EXTRACTED,
        description=ProvenanceFieldSource.USER_DIRECT,
    )

    # 3. Create Job (initial status is UNKNOWN for manual paste)
    job = Job(
        company_id=company.id,
        created_by_user_id=user.id,
        title=title_clean,
        title_normalized=title_norm,
        location_raw=request.location_raw.strip() if request.location_raw else None,
        location_normalized=loc_norm,
        workplace_type=workplace,
        description_raw=raw_desc,
        description_cleaned=desc_cleaned,
        similarity_fingerprint=fingerprint,
        status=JobStatus.UNKNOWN,
        provenance_summary=provenance_summary.model_dump(by_alias=True),
        expires_at=None,
    )
    session.add(job)
    session.flush()

    # 4. Create JobOrigin with external_id = NULL
    canonical_source_url = canonicalize_url(request.source_url) if request.source_url else None
    origin = JobOrigin(
        job_id=job.id,
        provider="manual_paste",
        external_id=None,
        source_url=request.source_url,
        canonical_url=canonical_source_url,
        observation_status=ObservationStatus.UNKNOWN,
        consecutive_not_found_count=0,
        provenance_metadata={"channel": "user_direct", "intake_mode": "manual_paste"},
    )
    session.add(origin)
    session.flush()

    # 5. Audit event (never logging job text or sensitive data)
    record_audit_event(
        session,
        user=user,
        event_type="job_intake_created",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "job_id": str(job.id),
            "company_id": str(company.id),
            "provider": "manual_paste",
        },
    )

    # Reload relationships for response serialization
    return get_job_by_id(session, job_id=job.id, user_id=user.id)


async def fetch_and_extract_job_url(
    *,
    request: JobUrlIntakeRequest,
    http_client: SafeHttpClient,
) -> tuple[str, str, ExtractedJobDetails]:
    """Fetch URL and extract details with SSRF protection outside any DB transaction."""
    canonical_input_url = canonicalize_url(request.url)

    # 1. Fetch URL with SSRF protection, manual redirects, and bounded streaming
    final_url, media_type, fetched_html = await http_client.fetch(canonical_input_url)

    # 2. Extract job details from HTML/JSON-LD
    extracted = extract_job_from_html(fetched_html)

    # Raw HTML body is discarded immediately: RAW_URL_RESPONSE_RETENTION = DISCARD_AFTER_EXTRACTION
    del fetched_html

    return final_url, media_type, extracted


def persist_job_url(
    session: Session,
    *,
    user: User,
    source_url: str,
    final_url: str,
    media_type: str,
    extracted: ExtractedJobDetails,
) -> Job:
    """Persist a parsed job from URL extraction into the database."""
    # 1. Company identity: NEVER copy job URL host to Company.domain (domain remains None)
    company = get_or_create_company(session, name=extracted.company_name, domain=None)

    # 2. Normalization
    title_clean = extracted.title.strip()
    title_norm = normalize_job_title(title_clean)
    loc_norm = normalize_location(extracted.location)
    desc_cleaned = clean_description_text(extracted.description)
    workplace = classify_workplace_type(title_clean, desc_cleaned, extracted.location)
    fingerprint = compute_similarity_fingerprint(company.id, title_norm, loc_norm, desc_cleaned)

    field_source = (
        ProvenanceFieldSource.SOURCE_DECLARED
        if extracted.is_source_declared
        else ProvenanceFieldSource.HEURISTIC_EXTRACTED
    )
    provenance_summary = JobProvenanceSummary(
        title=field_source,
        company=field_source,
        location=field_source if extracted.location else None,
        workplace_type=ProvenanceFieldSource.HEURISTIC_EXTRACTED,
        description=field_source,
    )

    # 3. Create Job (initial status ACTIVE since successfully fetched)
    job = Job(
        company_id=company.id,
        created_by_user_id=user.id,
        title=title_clean,
        title_normalized=title_norm,
        location_raw=extracted.location,
        location_normalized=loc_norm,
        workplace_type=workplace,
        description_raw=extracted.description,
        description_cleaned=desc_cleaned,
        similarity_fingerprint=fingerprint,
        status=JobStatus.ACTIVE,
        provenance_summary=provenance_summary.model_dump(by_alias=True),
        expires_at=None,
    )
    session.add(job)
    session.flush()

    # 4. Create JobOrigin with external_id = NULL
    origin = JobOrigin(
        job_id=job.id,
        provider="manual_url",
        external_id=None,
        source_url=source_url,
        canonical_url=canonicalize_url(final_url),
        observation_status=ObservationStatus.ACTIVE,
        consecutive_not_found_count=0,
        provenance_metadata={
            "channel": "user_direct",
            "intake_mode": "manual_url",
            "media_type": media_type,
        },
    )
    session.add(origin)
    session.flush()

    # 5. Audit event
    record_audit_event(
        session,
        user=user,
        event_type="job_intake_created",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "job_id": str(job.id),
            "company_id": str(company.id),
            "provider": "manual_url",
        },
    )

    return get_job_by_id(session, job_id=job.id, user_id=user.id)


async def intake_job_url(
    session: Session,
    *,
    user: User,
    request: JobUrlIntakeRequest,
    http_client: SafeHttpClient,
) -> Job:
    """Ingest a job by fetching and parsing a public URL with SSRF protection."""
    final_url, media_type, extracted = await fetch_and_extract_job_url(
        request=request, http_client=http_client
    )
    return persist_job_url(
        session,
        user=user,
        source_url=request.url,
        final_url=final_url,
        media_type=media_type,
        extracted=extracted,
    )


def get_job_by_id(session: Session, *, job_id: uuid.UUID, user_id: uuid.UUID) -> Job:
    """Retrieve canonical Job by ID with company and origins loaded, scoped to owner."""
    stmt = (
        select(Job)
        .options(selectinload(Job.company), selectinload(Job.origins))
        .where(Job.id == job_id, Job.created_by_user_id == user_id)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        raise JobNotFoundError(f"Job not found: {job_id}")
    return job
