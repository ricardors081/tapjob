"""Service layer for WP2.3 Job Feed, Alerts, Saved Jobs and Company Watchlist.

Implements:
1. Multi-tenant visibility: global jobs + user's own private jobs (never cross-user).
2. Total deterministic relevance ordering based on WP2.2 matching intelligence.
3. Stable cursor pagination via tapjob_api.http.pagination bound to filters and profile version.
4. Bounded work policy: candidate pool limited to FEED_MAX_CANDIDATES (100).
5. Idempotent SavedJob and CompanyWatch management.
6. Channel-neutral JobAlertPreference and deduplicated alert evaluation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from tapjob_api.career_profile.service import get_truth_snapshot
from tapjob_api.http.pagination import (
    CursorPage,
    CursorPayload,
    decode_cursor,
    encode_cursor,
    fingerprint_filters,
)
from tapjob_api.identity.models import User
from tapjob_api.jobs.errors import CompanyNotFoundError, JobNotFoundError
from tapjob_api.jobs.models import (
    Company,
    CompanyWatch,
    Job,
    JobAlertDeliveryChannel,
    JobAlertPreference,
    JobStatus,
    JobWorkplaceType,
    SavedJob,
    SavedJobState,
)
from tapjob_api.jobs.schemas import (
    CompanyDto,
    FeedItemResponse,
    JobDetailResponse,
    JobMatchSummaryDto,
    JobOriginDto,
    JobProvenanceSummary,
    SavedJobResponse,
    UpdateJobAlertPreferenceRequest,
)
from tapjob_api.matching.service import (
    get_or_compute_match,
    get_or_compute_matches_batch,
    is_profile_matchable,
)
from tapjob_api.matching.types import MATCHING_POLICY_VERSION, MatchEligibility

logger = logging.getLogger(__name__)

FEED_MAX_CANDIDATES = 100
FEED_MATCH_BATCH_STRATEGY = "canonical_batch_matching_with_db_persistence"
DEFAULT_ALERT_MIN_SCORE = 50
DEFAULT_ALERT_MIN_ELIGIBILITY = "eligible"
DEFAULT_ALERT_CHANNEL = JobAlertDeliveryChannel.WHATSAPP


def _eligibility_rank(eligibility: str | None) -> int:
    """Deterministic numeric rank for eligibility sorting: lower number = higher relevance."""
    if eligibility == MatchEligibility.ELIGIBLE.value:
        return 0
    if eligibility == MatchEligibility.REVIEW_REQUIRED.value:
        return 1
    if eligibility == MatchEligibility.INELIGIBLE.value:
        return 2
    return 3


def _to_company_dto(company: Company) -> CompanyDto:
    return CompanyDto(
        id=company.id,
        name=company.name,
        name_normalized=company.name_normalized,
        domain=company.domain,
        created_at=company.created_at,
        updated_at=company.updated_at,
    )


def _to_feed_item_response(
    job: Job,
    *,
    match_score: int | None,
    eligibility: str | None,
    is_saved: bool,
) -> FeedItemResponse:
    return FeedItemResponse(
        id=job.id,
        company=_to_company_dto(job.company),
        title=job.title,
        title_normalized=job.title_normalized,
        location_raw=job.location_raw,
        location_normalized=job.location_normalized,
        workplace_type=job.workplace_type,
        status=job.status,
        match_score=match_score,
        eligibility=eligibility,
        is_saved=is_saved,
        created_at=job.created_at,
        expires_at=job.expires_at,
    )


def get_job_feed(
    session: Session,
    *,
    user: User,
    q: str | None = None,
    company: str | None = None,
    location: str | None = None,
    workplace_type: JobWorkplaceType | None = None,
    min_score: int | None = None,
    eligibility: list[str] | None = None,
    saved_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> CursorPage[FeedItemResponse]:
    """Retrieve personalized, relevance-ordered job feed with cursor pagination."""
    now_utc = datetime.now(UTC)
    today = now_utc.date()

    # 1. TruthSnapshot boundary
    snapshot = get_truth_snapshot(session, user=user)
    has_profile = is_profile_matchable(snapshot)

    # 2. Bind filters to detect mid-pagination changes
    filters_dict: dict[str, object] = {
        "q": q,
        "company": company,
        "location": location,
        "workplace_type": workplace_type.value if workplace_type else None,
        "min_score": min_score,
        "eligibility": sorted(eligibility) if eligibility else None,
        "saved_only": saved_only,
        "profile_version": snapshot.profile_version,
        "policy_version": MATCHING_POLICY_VERSION,
    }
    expected_fingerprint = fingerprint_filters(filters_dict)

    # 3. Freeze candidate pool universe across pages (Finding 7)
    feed_as_of = now_utc
    decoded = None
    if cursor:
        decoded = decode_cursor(
            cursor,
            expected_sort="relevance",
            expected_filters_fingerprint=expected_fingerprint,
        )
        if "feed_as_of" in decoded.last_key:
            feed_as_of = datetime.fromtimestamp(float(decoded.last_key["feed_as_of"]), tz=UTC)

    # 4. Build coarse DB candidate selection query
    # Visibility rule: global jobs (created_by_user_id IS NULL) + user's own private jobs
    visibility_clause = or_(
        Job.created_by_user_id.is_(None),
        Job.created_by_user_id == user.id,
    )

    # Liveness rule: offerable opportunities
    liveness_clause = and_(
        Job.status.in_([JobStatus.ACTIVE, JobStatus.UNKNOWN]),
        or_(Job.expires_at.is_(None), Job.expires_at > now_utc),
    )

    # Dismissal rule: exclude jobs dismissed by this user
    dismissed_subquery = (
        select(SavedJob.job_id)
        .where(
            SavedJob.user_id == user.id,
            SavedJob.state == SavedJobState.DISMISSED,
        )
        .scalar_subquery()
    )
    not_dismissed_clause = Job.id.not_in(dismissed_subquery)

    query = (
        select(Job)
        .join(Job.company)
        .options(selectinload(Job.company), selectinload(Job.origins))
        .where(
            visibility_clause,
            liveness_clause,
            not_dismissed_clause,
            Job.created_at <= feed_as_of,
        )
    )

    if saved_only:
        saved_subquery = (
            select(SavedJob.job_id)
            .where(
                SavedJob.user_id == user.id,
                SavedJob.state == SavedJobState.SAVED,
            )
            .scalar_subquery()
        )
        query = query.where(Job.id.in_(saved_subquery))

    if q:
        clean_q = q.strip().lower()
        query = query.where(
            or_(
                func.lower(Job.title).contains(clean_q),
                func.lower(Job.description_cleaned).contains(clean_q),
                func.lower(Company.name).contains(clean_q),
            )
        )

    if company:
        query = query.where(func.lower(Company.name).contains(company.strip().lower()))

    if location:
        query = query.where(
            or_(
                func.lower(Job.location_raw).contains(location.strip().lower()),
                func.lower(Job.location_normalized).contains(location.strip().lower()),
            )
        )

    if workplace_type:
        query = query.where(Job.workplace_type == workplace_type)

    # Order by newest first for candidate selection, bounded by FEED_MAX_CANDIDATES
    query = query.order_by(desc(Job.created_at)).limit(FEED_MAX_CANDIDATES)
    candidate_jobs: list[Job] = list(session.execute(query).scalars().all())

    # 5. Load user's saved jobs set for fast is_saved lookup
    saved_job_ids: set[uuid.UUID] = set(
        session.execute(
            select(SavedJob.job_id).where(
                SavedJob.user_id == user.id,
                SavedJob.state == SavedJobState.SAVED,
            )
        )
        .scalars()
        .all()
    )

    # 6. Canonical batch matching using WP2.2 Matching Engine (Finding 5 & 6)
    evaluated_items: list[tuple[Job, int | None, str | None]] = []

    if has_profile and candidate_jobs:
        match_results = get_or_compute_matches_batch(
            session,
            redis_client=None,
            user=user,
            jobs=candidate_jobs,
            evaluation_as_of_date=today,
        )
        for job in candidate_jobs:
            match_res = match_results.get(job.id)
            score = match_res.overall_score if match_res else None
            elig = match_res.eligibility if match_res else None
            evaluated_items.append((job, score, elig))
    else:
        for job in candidate_jobs:
            evaluated_items.append((job, None, None))

    # 7. Apply score and eligibility post-filters if specified
    filtered_items: list[tuple[Job, int | None, str | None]] = []
    for job, item_score, item_elig in evaluated_items:
        if min_score is not None and (item_score is None or item_score < min_score):
            continue
        if eligibility is not None and (item_elig is None or item_elig not in eligibility):
            continue
        filtered_items.append((job, item_score, item_elig))

    # 8. Total deterministic relevance ordering
    # Sort tuple: (eligibility_rank ASC, score DESC, created_at DESC, job_id ASC)
    def sort_key(item: tuple[Job, int | None, str | None]) -> tuple[int, int, float, str]:
        job, score, elig = item
        erank = _eligibility_rank(elig)
        score_val = -(score if score is not None else -1)
        created_val = -job.created_at.timestamp()
        id_val = str(job.id)
        return (erank, score_val, created_val, id_val)

    sorted_items = sorted(filtered_items, key=sort_key)

    # 9. Cursor pagination
    start_index = 0
    if decoded is not None:
        last_key = decoded.last_key
        last_erank = int(last_key["eligibility_rank"])
        last_score = -(int(last_key["score"]))
        last_created = -(float(last_key["created_at"]))
        last_id = str(last_key["job_id"])
        last_tuple = (last_erank, last_score, last_created, last_id)

        # Seek past the last_key
        for idx, item in enumerate(sorted_items):
            if sort_key(item) > last_tuple:
                start_index = idx
                break
        else:
            start_index = len(sorted_items)

    page_items = sorted_items[start_index : start_index + limit]
    has_next = (start_index + limit) < len(sorted_items)

    next_cursor: str | None = None
    if has_next and page_items:
        last_job, last_item_score, last_item_elig = page_items[-1]
        next_payload = CursorPayload(
            sort="relevance",
            filters_fingerprint=expected_fingerprint,
            last_key={
                "eligibility_rank": _eligibility_rank(last_item_elig),
                "score": last_item_score if last_item_score is not None else -1,
                "created_at": last_job.created_at.timestamp(),
                "job_id": str(last_job.id),
                "feed_as_of": feed_as_of.timestamp(),
            },
        )
        next_cursor = encode_cursor(next_payload)

    result_items = [
        _to_feed_item_response(
            job,
            match_score=score,
            eligibility=elig,
            is_saved=job.id in saved_job_ids,
        )
        for job, score, elig in page_items
    ]

    return CursorPage[FeedItemResponse](items=result_items, next_cursor=next_cursor)


def get_job_detail(
    session: Session,
    *,
    user: User,
    job_id: uuid.UUID,
) -> JobDetailResponse:
    """Retrieve detailed job view with WP2.2 match intelligence and user intent."""
    now_utc = datetime.now(UTC)
    today = now_utc.date()

    stmt = (
        select(Job)
        .options(selectinload(Job.company), selectinload(Job.origins))
        .where(
            Job.id == job_id,
            or_(
                Job.created_by_user_id.is_(None),
                Job.created_by_user_id == user.id,
            ),
        )
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        raise JobNotFoundError(f"Job not found: {job_id}")

    # Check saved / dismissed state
    saved_entry = session.execute(
        select(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_id == job.id)
    ).scalar_one_or_none()

    is_saved = saved_entry is not None and saved_entry.state == SavedJobState.SAVED
    is_dismissed = saved_entry is not None and saved_entry.state == SavedJobState.DISMISSED

    # Check company watch
    watch_entry = session.execute(
        select(CompanyWatch).where(
            CompanyWatch.user_id == user.id,
            CompanyWatch.company_id == job.company_id,
        )
    ).scalar_one_or_none()
    is_company_watched = watch_entry is not None

    # Compute or retrieve match intelligence under WP2.2 (Finding 5 & 6)
    snapshot = get_truth_snapshot(session, user=user)
    has_profile = is_profile_matchable(snapshot)

    match_summary: JobMatchSummaryDto | None = None
    if has_profile:
        stored = get_or_compute_match(
            session,
            redis_client=None,
            user=user,
            job_id=job.id,
            evaluation_as_of_date=today,
        )
        match_summary = JobMatchSummaryDto(
            overall_score=stored.overall_score,
            eligibility=stored.eligibility,
            blockers=stored.blockers,
            strengths=stored.strengths,
            missing_requirements=stored.missing_requirements,
            critical_unknowns=stored.critical_unknowns,
            explanation=stored.explanation,
        )

    provenance_summary = JobProvenanceSummary.model_validate(job.provenance_summary)
    origins_dto = [
        JobOriginDto(
            id=origin.id,
            job_id=origin.job_id,
            provider=origin.provider,
            external_id=origin.external_id,
            source_url=origin.source_url,
            canonical_url=origin.canonical_url,
            observation_status=origin.observation_status,
            consecutive_not_found_count=origin.consecutive_not_found_count,
            first_observed_at=origin.first_observed_at,
            last_observed_at=origin.last_observed_at,
            provenance_metadata=origin.provenance_metadata,
            created_at=origin.created_at,
            updated_at=origin.updated_at,
        )
        for origin in job.origins
    ]

    return JobDetailResponse(
        id=job.id,
        company_id=job.company_id,
        company=_to_company_dto(job.company),
        title=job.title,
        title_normalized=job.title_normalized,
        location_raw=job.location_raw,
        location_normalized=job.location_normalized,
        workplace_type=job.workplace_type,
        description_raw=job.description_raw,
        description_cleaned=job.description_cleaned,
        similarity_fingerprint=job.similarity_fingerprint,
        status=job.status,
        provenance_summary=provenance_summary,
        origins=origins_dto,
        expires_at=job.expires_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        match=match_summary,
        is_saved=is_saved,
        is_dismissed=is_dismissed,
        is_company_watched=is_company_watched,
    )


# --- SavedJob & Dismiss Operations ---


def save_job(session: Session, *, user: User, job_id: uuid.UUID) -> SavedJob:
    """Idempotently save a job for the user."""
    # Validate job exists and is visible
    job = session.execute(
        select(Job).where(
            Job.id == job_id,
            or_(
                Job.created_by_user_id.is_(None),
                Job.created_by_user_id == user.id,
            ),
        )
    ).scalar_one_or_none()
    if job is None:
        raise JobNotFoundError(f"Job not found: {job_id}")

    stmt = (
        insert(SavedJob)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job_id,
            state=SavedJobState.SAVED,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="uq_saved_jobs_user_job",
            set_={"state": SavedJobState.SAVED, "updated_at": func.now()},
        )
        .returning(SavedJob)
    )
    return session.execute(stmt).scalar_one()


def unsave_job(session: Session, *, user: User, job_id: uuid.UUID) -> None:
    """Idempotently remove saved status for a job."""
    session.execute(
        delete(SavedJob).where(
            SavedJob.user_id == user.id,
            SavedJob.job_id == job_id,
            SavedJob.state == SavedJobState.SAVED,
        )
    )


def dismiss_job(session: Session, *, user: User, job_id: uuid.UUID) -> SavedJob:
    """Idempotently dismiss a job from the user's feed."""
    job = session.execute(
        select(Job).where(
            Job.id == job_id,
            or_(
                Job.created_by_user_id.is_(None),
                Job.created_by_user_id == user.id,
            ),
        )
    ).scalar_one_or_none()
    if job is None:
        raise JobNotFoundError(f"Job not found: {job_id}")

    stmt = (
        insert(SavedJob)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job_id,
            state=SavedJobState.DISMISSED,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="uq_saved_jobs_user_job",
            set_={"state": SavedJobState.DISMISSED, "updated_at": func.now()},
        )
        .returning(SavedJob)
    )
    return session.execute(stmt).scalar_one()


def undismiss_job(session: Session, *, user: User, job_id: uuid.UUID) -> None:
    """Idempotently undo dismissal of a job."""
    session.execute(
        delete(SavedJob).where(
            SavedJob.user_id == user.id,
            SavedJob.job_id == job_id,
            SavedJob.state == SavedJobState.DISMISSED,
        )
    )


def list_saved_jobs(
    session: Session,
    *,
    user: User,
    cursor: str | None = None,
    limit: int = 20,
) -> CursorPage[SavedJobResponse]:
    """Retrieve user's saved jobs list with cursor pagination."""
    query = (
        select(SavedJob)
        .join(SavedJob.job)
        .options(
            selectinload(SavedJob.job).selectinload(Job.company),
            selectinload(SavedJob.job).selectinload(Job.origins),
        )
        .where(
            SavedJob.user_id == user.id,
            SavedJob.state == SavedJobState.SAVED,
        )
        .order_by(desc(SavedJob.created_at), SavedJob.id.asc())
    )

    saved_items: list[SavedJob] = list(session.execute(query).scalars().all())

    expected_fingerprint = fingerprint_filters({"user_id": str(user.id), "scope": "saved"})

    start_index = 0
    if cursor:
        decoded = decode_cursor(
            cursor,
            expected_sort="saved_created_at",
            expected_filters_fingerprint=expected_fingerprint,
        )
        last_created = float(decoded.last_key["created_at"])
        last_id = str(decoded.last_key["id"])

        for idx, item in enumerate(saved_items):
            item_created = item.created_at.timestamp()
            item_id = str(item.id)
            if (-item_created, item_id) > (-last_created, last_id):
                start_index = idx
                break
        else:
            start_index = len(saved_items)

    page_items = saved_items[start_index : start_index + limit]
    has_next = (start_index + limit) < len(saved_items)

    next_cursor: str | None = None
    if has_next and page_items:
        last_item = page_items[-1]
        next_cursor = encode_cursor(
            CursorPayload(
                sort="saved_created_at",
                filters_fingerprint=expected_fingerprint,
                last_key={
                    "created_at": last_item.created_at.timestamp(),
                    "id": str(last_item.id),
                },
            )
        )

    responses = [
        SavedJobResponse(
            id=item.id,
            job_id=item.job_id,
            state=item.state.value,
            created_at=item.created_at,
            job=_to_feed_item_response(
                item.job,
                match_score=None,
                eligibility=None,
                is_saved=True,
            ),
        )
        for item in page_items
    ]

    return CursorPage[SavedJobResponse](items=responses, next_cursor=next_cursor)


# --- Company Watchlist Operations ---


def watch_company(session: Session, *, user: User, company_id: uuid.UUID) -> CompanyWatch:
    """Idempotently watch a canonical Company."""
    company = session.get(Company, company_id)
    if company is None:
        raise CompanyNotFoundError(f"Company not found: {company_id}")

    stmt = (
        insert(CompanyWatch)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            company_id=company_id,
            created_at=func.now(),
        )
        .on_conflict_do_nothing(constraint="uq_company_watches_user_company")
        .returning(CompanyWatch)
    )
    result = session.execute(stmt).scalar_one_or_none()
    if result is not None:
        return result

    # Already watching, fetch existing
    return session.execute(
        select(CompanyWatch).where(
            CompanyWatch.user_id == user.id,
            CompanyWatch.company_id == company_id,
        )
    ).scalar_one()


def unwatch_company(session: Session, *, user: User, company_id: uuid.UUID) -> None:
    """Idempotently unwatch a company."""
    session.execute(
        delete(CompanyWatch).where(
            CompanyWatch.user_id == user.id,
            CompanyWatch.company_id == company_id,
        )
    )


def list_watched_companies(session: Session, *, user: User) -> list[Company]:
    """Retrieve list of companies watched by the user."""
    stmt = (
        select(Company)
        .join(CompanyWatch, CompanyWatch.company_id == Company.id)
        .where(CompanyWatch.user_id == user.id)
        .order_by(Company.name)
    )
    return list(session.execute(stmt).scalars().all())


# --- Job Alert Preferences Operations ---


def get_job_alert_preference(session: Session, *, user: User) -> JobAlertPreference:
    """Retrieve user alert preferences, returning safe disabled defaults if unconfigured."""
    pref = session.execute(
        select(JobAlertPreference).where(JobAlertPreference.user_id == user.id)
    ).scalar_one_or_none()

    if pref is not None:
        return pref

    # Safe opt-in default: disabled until user explicitly opts in
    return JobAlertPreference(
        id=uuid.uuid4(),
        user_id=user.id,
        enabled=False,
        min_score=DEFAULT_ALERT_MIN_SCORE,
        min_eligibility=DEFAULT_ALERT_MIN_ELIGIBILITY,
        delivery_channel=DEFAULT_ALERT_CHANNEL,
    )


def update_job_alert_preference(
    session: Session,
    *,
    user: User,
    request: UpdateJobAlertPreferenceRequest,
) -> JobAlertPreference:
    """Create or update user job alert preferences."""
    stmt = (
        insert(JobAlertPreference)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            enabled=request.enabled,
            min_score=request.min_score,
            min_eligibility=request.min_eligibility,
            delivery_channel=request.delivery_channel,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "enabled": request.enabled,
                "min_score": request.min_score,
                "min_eligibility": request.min_eligibility,
                "delivery_channel": request.delivery_channel,
                "updated_at": func.now(),
            },
        )
        .returning(JobAlertPreference)
    )
    pref = session.execute(stmt).scalar_one()
    if not request.enabled:
        from tapjob_api.jobs.alert_service import cancel_unsent_queued_alerts_for_user

        cancel_unsent_queued_alerts_for_user(session, user.id)
    return pref
