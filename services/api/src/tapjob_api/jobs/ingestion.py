"""External ATS job ingestion pipeline and liveness reconciliation (WP2.1-B).

Implements:
- Bounded discovery and detail fetching across JobSource adapters.
- Complete discovery verification: liveness absence reconciliation runs ONLY on
  proven complete discovery runs (no page cutoffs, no cursor loops, no provider errors).
- Location normalization and persistence (`location_raw`, `location_normalized`).
- Deterministic company resolution and similarity fingerprinting.
- Persistence into canonical Company, Job, and JobOrigin models.
- Fail-safe transient error handling: retries transient errors; skips new job creation
  on transient detail fetch failure to prevent permanent incomplete data.
- Liveness consensus: tracks missing jobs, increments `consecutive_not_found_count`,
  and transitions jobs to CLOSED once the threshold (default 3) is reached across all origins.
"""

import hashlib
import logging
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Engine, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, selectinload

from tapjob_api.jobs.models import Company, Job, JobOrigin, JobStatus, ObservationStatus
from tapjob_api.jobs.normalization import (
    classify_workplace_type,
    clean_description_text,
    compute_similarity_fingerprint,
    normalize_job_title,
    normalize_location,
)
from tapjob_api.jobs.service import compute_job_effective_status, get_or_create_company
from tapjob_api.jobs.sources.registry import JobSourceConfig
from tapjob_api.jobs.url_canonicalization import canonicalize_url
from tapjob_api.ports import CallContext, DiscoveredJob, PortError

logger = logging.getLogger(__name__)

CONSECUTIVE_NOT_FOUND_CLOSE_THRESHOLD = 3
MAX_DISCOVERY_PAGES = 50


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    """Summary of an ingestion run."""

    source_id: str
    provider: str
    total_discovered: int
    created_jobs: int
    updated_origins: int
    closed_jobs: int
    errors: int = 0
    discovery_complete: bool = True


def advisory_lock_key(source_id: str) -> int:
    """Derive deterministic signed 64-bit integer from SHA-256(source_id)."""
    digest = hashlib.sha256(f"tapjob_ats_ingest:{source_id}".encode()).digest()
    return int(struct.unpack(">q", digest[:8])[0])


def try_acquire_source_advisory_lock(conn: Connection, source_id: str) -> bool:
    """Acquire session-level PostgreSQL advisory lock on a dedicated physical Connection.
    Returns True if acquired or non-Postgres.
    """
    key = advisory_lock_key(source_id)
    res = conn.execute(
        sa.text("SELECT pg_try_advisory_lock(:key)"),
        {"key": key},
    ).scalar()
    return bool(res)


def release_source_advisory_lock(conn: Connection, source_id: str) -> bool:
    """Release session-level PostgreSQL advisory lock on the same physical Connection.
    Returns True if successfully unlocked.
    """
    key = advisory_lock_key(source_id)
    res = conn.execute(
        sa.text("SELECT pg_advisory_unlock(:key)"),
        {"key": key},
    ).scalar()
    return bool(res)


def resolve_source_company(
    session: Session,
    config: JobSourceConfig,
) -> Company | None:
    """Resolve an existing Company for this source board without creating a new one (Finding B).

    Rules:
    1. For valid corporate_domain: look up company by domain.
    2. For corporate_domain is None: look up Company already bound to this source
       via existing JobOrigin entries with provider == config.provider AND
       provenance_metadata['board_id'] == config.source_id.
    3. If >1 distinct Companies are found for the same source, raise ValueError (fail closed).
    4. Return None if no Company is yet bound (creating a Company is deferred
       until a new job arrives).
    """
    if config.corporate_domain:
        from tapjob_api.jobs.normalization import clean_corporate_domain

        norm_domain = clean_corporate_domain(config.corporate_domain)
        if norm_domain:
            domain_stmt = select(Company).where(Company.domain == norm_domain)
            existing_by_domain = session.execute(domain_stmt).scalar_one_or_none()
            if existing_by_domain is not None:
                return existing_by_domain

    company_ids: Sequence[UUID]
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        pg_stmt = (
            select(Job.company_id)
            .join(JobOrigin, JobOrigin.job_id == Job.id)
            .where(
                JobOrigin.provider == config.provider,
                JobOrigin.provenance_metadata["board_id"].astext == config.source_id,
            )
            .distinct()
        )
        company_ids = session.execute(pg_stmt).scalars().all()
    else:
        sqlite_stmt = (
            select(Job.company_id, JobOrigin.provenance_metadata)
            .join(JobOrigin, JobOrigin.job_id == Job.id)
            .where(JobOrigin.provider == config.provider)
        )
        found_ids: set[UUID] = set()
        for row in session.execute(sqlite_stmt).all():
            comp_id = row[0]
            meta = row[1]
            if isinstance(meta, dict) and meta.get("board_id") == config.source_id:
                found_ids.add(comp_id)
        company_ids = list(found_ids)

    if len(company_ids) > 1:
        logger.error(
            "Invariant violation: source '%s' (%s) is associated with multiple Companies: %s",
            config.source_id,
            config.provider,
            company_ids,
        )
        raise ValueError(
            f"Invariant violation: source board '{config.source_id}' "
            "has multiple associated Companies"
        )
    if len(company_ids) == 1:
        return session.get(Company, company_ids[0])

    return None


async def ingest_job_source(
    session: Session,
    config: JobSourceConfig,
    *,
    cycle_id: str | None = None,
    ctx: CallContext | None = None,
    not_found_threshold: int = CONSECUTIVE_NOT_FOUND_CLOSE_THRESHOLD,
    lock_connection: Connection | None = None,
) -> IngestionSummary:
    """Run an ingestion cycle for a configured JobSource.

    Performs:
    1. Cross-worker concurrency guard via PostgreSQL advisory lock on a dedicated Connection.
    2. Paginated discovery with completeness checking.
    3. Source-scoped company resolution: reuses source's company, creates Company ONLY on new job.
    4. Updates mutable ATS fields on existing postings (title, location, workplace, description).
    5. Details fetching for new postings with transient failure protection.
    6. Normalization (multi-location, workplace hints) and canonical persistence.
    7. Idempotent absence reconciliation by cycle_id ONLY when discovery was verified complete.
    """
    now = datetime.now(UTC)
    effective_ctx = ctx or CallContext.with_timeout(
        correlation_id=f"ingest-{config.source_id}-{int(now.timestamp())}",
        timeout_seconds=300.0,
        now=now,
    )
    effective_cycle_id = cycle_id or f"cycle-{int(now.timestamp())}"

    bind = None
    try:
        bind = session.get_bind()
    except Exception:
        bind = getattr(session, "bind", None)

    is_postgres = False
    if bind is not None and hasattr(bind, "dialect"):
        is_postgres = getattr(bind.dialect, "name", "") == "postgresql"

    dedicated_conn: Connection | None = None
    close_dedicated_conn = False

    if is_postgres:
        if lock_connection is not None:
            dedicated_conn = lock_connection
            close_dedicated_conn = False
        else:
            engine = bind if isinstance(bind, Engine) else getattr(bind, "engine", None)
            if engine is not None and isinstance(engine, Engine):
                dedicated_conn = engine.connect()
                close_dedicated_conn = True
            elif isinstance(bind, Connection):
                dedicated_conn = bind
                close_dedicated_conn = False

        if dedicated_conn is not None:
            try:
                acquired = try_acquire_source_advisory_lock(dedicated_conn, config.source_id)
            except Exception:
                if close_dedicated_conn:
                    dedicated_conn.close()
                raise

            if not acquired:
                if close_dedicated_conn:
                    dedicated_conn.close()
                logger.warning(
                    "Concurrent ingestion already active for source '%s'. "
                    "Skipping overlapping execution.",
                    config.source_id,
                )
                return IngestionSummary(
                    source_id=config.source_id,
                    provider=config.provider,
                    total_discovered=0,
                    created_jobs=0,
                    updated_origins=0,
                    closed_jobs=0,
                    errors=0,
                    discovery_complete=False,
                )

    try:
        discovered_items: list[DiscoveredJob] = []
        cursor: str | None = None
        page_count = 0
        visited_cursors: set[str] = set()
        discovery_complete = False

        # 1. Discovery phase
        while True:
            if cursor is not None:
                if cursor in visited_cursors:
                    logger.warning(
                        "Cursor cycle detected for source %s (%s) at cursor '%s'. "
                        "Stopping discovery.",
                        config.source_id,
                        config.provider,
                        cursor,
                    )
                    discovery_complete = False
                    break
                visited_cursors.add(cursor)

            try:
                page = await config.source.discover(cursor, effective_ctx)
            except PortError as exc:
                logger.error(
                    "Discovery error for source %s (%s): %s",
                    config.source_id,
                    config.provider,
                    exc.message,
                )
                if exc.transient:
                    raise
                return IngestionSummary(
                    source_id=config.source_id,
                    provider=config.provider,
                    total_discovered=0,
                    created_jobs=0,
                    updated_origins=0,
                    closed_jobs=0,
                    errors=1,
                    discovery_complete=False,
                )

            discovered_items.extend(page.items)
            page_count += 1

            if page.next_cursor is None:
                discovery_complete = True
                break

            if page_count >= MAX_DISCOVERY_PAGES:
                logger.warning(
                    "Discovery page limit (%d) reached for source %s with remaining cursor '%s'. "
                    "Discovery marked incomplete.",
                    MAX_DISCOVERY_PAGES,
                    config.source_id,
                    page.next_cursor,
                )
                discovery_complete = False
                break

            cursor = page.next_cursor

        seen_external_ids: set[str] = set()
        created_jobs = 0
        updated_origins = 0
        closed_jobs = 0
        error_count = 0

        # Resolve existing company bound to this source board without preemptively creating one
        board_company: Company | None = resolve_source_company(session, config)

        # 2. Ingestion / upsert phase
        for item in discovered_items:
            ext_id = item.ref.external_id
            seen_external_ids.add(ext_id)

            # Check existing origin
            origin = session.execute(
                select(JobOrigin)
                .options(selectinload(JobOrigin.job).selectinload(Job.origins))
                .where(
                    JobOrigin.provider == config.provider,
                    JobOrigin.external_id == ext_id,
                )
            ).scalar_one_or_none()

            if origin is not None:
                # Existing posting: update mutable ATS fields (Finding E)
                desc_raw = origin.job.description_raw
                try:
                    details = await config.source.fetch_details(item.ref, effective_ctx)
                    if details.description_raw and details.description_raw.strip():
                        desc_raw = details.description_raw.strip()
                except PortError as exc:
                    if exc.transient:
                        logger.warning(
                            "Transient error fetching details for existing job %s on %s: %s; "
                            "keeping existing description",
                            ext_id,
                            config.provider,
                            exc.message,
                        )
                    else:
                        logger.warning(
                            "Non-transient error fetching details for existing job %s on %s: %s",
                            ext_id,
                            config.provider,
                            exc.message,
                        )
                except Exception as exc:
                    logger.warning(
                        "Error fetching details for existing job %s on %s: %s",
                        ext_id,
                        config.provider,
                        exc,
                    )

                desc_cleaned = clean_description_text(desc_raw)
                title_norm = normalize_job_title(item.title)
                loc_raw = item.location_raw

                norm_loc_parts: list[str] = []
                if loc_raw:
                    norm_primary = normalize_location(loc_raw)
                    if norm_primary:
                        norm_loc_parts.append(norm_primary)
                for add_loc in item.additional_locations_raw:
                    norm_add = normalize_location(add_loc)
                    if norm_add and norm_add not in norm_loc_parts:
                        norm_loc_parts.append(norm_add)
                loc_norm = "; ".join(norm_loc_parts) if norm_loc_parts else None

                workplace = classify_workplace_type(
                    item.title,
                    desc_cleaned,
                    loc_raw,
                    workplace_hint=item.workplace_type_hint,
                )
                fingerprint = compute_similarity_fingerprint(
                    origin.job.company_id, title_norm, loc_norm, desc_cleaned
                )

                job = origin.job
                job.title = item.title.strip() or "Untitled"
                job.title_normalized = title_norm
                job.location_raw = loc_raw
                job.location_normalized = loc_norm
                job.workplace_type = workplace
                job.description_raw = desc_raw
                job.description_cleaned = desc_cleaned
                job.similarity_fingerprint = fingerprint

                canonical_u = canonicalize_url(item.ref.url) if item.ref.url else None
                origin.source_url = item.ref.url
                origin.canonical_url = canonical_u
                origin.last_observed_at = now
                origin.consecutive_not_found_count = 0
                if origin.observation_status != ObservationStatus.ACTIVE:
                    origin.observation_status = ObservationStatus.ACTIVE

                prov_meta = dict(origin.provenance_metadata)
                prov_meta["last_reconciliation_cycle_id"] = effective_cycle_id
                if item.additional_locations_raw:
                    prov_meta["additional_locations"] = list(item.additional_locations_raw)
                if item.workplace_type_hint:
                    prov_meta["workplace_type_hint"] = item.workplace_type_hint
                origin.provenance_metadata = prov_meta

                job.status = compute_job_effective_status(job.origins, job.expires_at)
                updated_origins += 1
            else:
                # New posting: fetch details and persist
                try:
                    details = await config.source.fetch_details(item.ref, effective_ctx)
                    desc_raw = (
                        details.description_raw.strip() if details.description_raw else item.title
                    )
                except PortError as exc:
                    if exc.transient:
                        logger.warning(
                            "Transient error fetching details for new job %s on %s; "
                            "skipping creation in this run: %s",
                            ext_id,
                            config.provider,
                            exc.message,
                        )
                        error_count += 1
                        continue
                    logger.warning(
                        "Non-transient error fetching details for %s job %s: %s; "
                        "using title as description",
                        config.provider,
                        ext_id,
                        exc.message,
                    )
                    desc_raw = item.title
                    error_count += 1
                except Exception as exc:
                    logger.warning(
                        "Unexpected error fetching details for %s job %s: %s; skipping creation",
                        config.provider,
                        ext_id,
                        exc,
                    )
                    error_count += 1
                    continue

                # Resolve or create company only when a new posting arrives (Finding B)
                if not item.company_name or item.company_name == config.company_name:
                    if board_company is None:
                        board_company = get_or_create_company(
                            session,
                            name=config.company_name,
                            domain=config.corporate_domain,
                        )
                    company = board_company
                else:
                    company = get_or_create_company(
                        session,
                        name=item.company_name,
                        domain=config.corporate_domain,
                    )

                desc_cleaned = clean_description_text(desc_raw)
                title_norm = normalize_job_title(item.title)
                loc_raw = item.location_raw

                norm_loc_parts = []
                if loc_raw:
                    norm_primary = normalize_location(loc_raw)
                    if norm_primary:
                        norm_loc_parts.append(norm_primary)
                for add_loc in item.additional_locations_raw:
                    norm_add = normalize_location(add_loc)
                    if norm_add and norm_add not in norm_loc_parts:
                        norm_loc_parts.append(norm_add)
                loc_norm = "; ".join(norm_loc_parts) if norm_loc_parts else None

                workplace = classify_workplace_type(
                    item.title,
                    desc_cleaned,
                    loc_raw,
                    workplace_hint=item.workplace_type_hint,
                )
                fingerprint = compute_similarity_fingerprint(
                    company.id, title_norm, loc_norm, desc_cleaned
                )

                job = Job(
                    company_id=company.id,
                    created_by_user_id=None,  # Discovered by global ATS scheduler
                    title=item.title.strip() or "Untitled",
                    title_normalized=title_norm,
                    location_raw=loc_raw,
                    location_normalized=loc_norm,
                    workplace_type=workplace,
                    description_raw=desc_raw,
                    description_cleaned=desc_cleaned,
                    similarity_fingerprint=fingerprint,
                    status=JobStatus.ACTIVE,
                    provenance_summary={
                        "provider": config.provider,
                        "source": "ats_discovery",
                        "source_id": config.source_id,
                    },
                    expires_at=None,
                )
                session.add(job)
                session.flush()

                canonical_u = canonicalize_url(item.ref.url) if item.ref.url else None
                prov_metadata: dict[str, Any] = {
                    "board_id": config.source_id,
                    "ingested_at": now.isoformat(),
                    "last_reconciliation_cycle_id": effective_cycle_id,
                }
                if item.additional_locations_raw:
                    prov_metadata["additional_locations"] = list(item.additional_locations_raw)
                if item.workplace_type_hint:
                    prov_metadata["workplace_type_hint"] = item.workplace_type_hint

                new_origin = JobOrigin(
                    job_id=job.id,
                    provider=config.provider,
                    external_id=ext_id,
                    source_url=item.ref.url,
                    canonical_url=canonical_u,
                    observation_status=ObservationStatus.ACTIVE,
                    consecutive_not_found_count=0,
                    first_observed_at=now,
                    last_observed_at=now,
                    provenance_metadata=prov_metadata,
                )
                session.add(new_origin)
                session.flush()
                created_jobs += 1

        # 3. Liveness reconciliation for absent jobs ONLY if discovery was verified complete
        if discovery_complete:
            query = (
                select(JobOrigin)
                .options(selectinload(JobOrigin.job).selectinload(Job.origins))
                .where(
                    JobOrigin.provider == config.provider,
                    JobOrigin.observation_status == ObservationStatus.ACTIVE,
                )
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(of=JobOrigin)

            existing_origins = session.execute(query).scalars().all()

            for origin in existing_origins:
                # Check if origin belongs to this board via provenance metadata
                board_id = origin.provenance_metadata.get("board_id")
                if board_id != config.source_id:
                    continue

                if origin.external_id not in seen_external_ids:
                    last_cycle = origin.provenance_metadata.get("last_reconciliation_cycle_id")
                    if last_cycle == effective_cycle_id:
                        # Idempotency under redelivery or re-execution of same cycle (Finding C)
                        logger.info(
                            "Reconciliation cycle %s already applied to origin %s; "
                            "skipping duplicate strike.",
                            effective_cycle_id,
                            origin.id,
                        )
                        continue

                    origin.consecutive_not_found_count += 1
                    origin.last_observed_at = now
                    origin.provenance_metadata = {
                        **origin.provenance_metadata,
                        "last_reconciliation_cycle_id": effective_cycle_id,
                    }
                    if origin.consecutive_not_found_count >= not_found_threshold:
                        origin.observation_status = ObservationStatus.CLOSED
                        job = origin.job
                        job.status = compute_job_effective_status(job.origins, job.expires_at)
                        closed_jobs += 1
        else:
            logger.info(
                "Discovery for source %s was incomplete; skipping missing liveness "
                "reconciliation to prevent false closures.",
                config.source_id,
            )

        session.commit()

        return IngestionSummary(
            source_id=config.source_id,
            provider=config.provider,
            total_discovered=len(discovered_items),
            created_jobs=created_jobs,
            updated_origins=updated_origins,
            closed_jobs=closed_jobs,
            errors=error_count,
            discovery_complete=discovery_complete,
        )
    finally:
        if is_postgres and dedicated_conn is not None:
            try:
                unlocked = release_source_advisory_lock(dedicated_conn, config.source_id)
                if not unlocked:
                    key = advisory_lock_key(config.source_id)
                    logger.error(
                        "Invariant violation: pg_advisory_unlock returned false for source "
                        "'%s' (key=%s)",
                        config.source_id,
                        key,
                    )
                    raise RuntimeError(
                        f"Invariant violation: failed to release advisory lock for source "
                        f"'{config.source_id}' (lock was not held on this connection)"
                    )
            finally:
                if close_dedicated_conn:
                    dedicated_conn.close()
