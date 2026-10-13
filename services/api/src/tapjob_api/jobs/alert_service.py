"""Alert evaluation engine for WP2.3 proactive job alerts.

Implements:
1. Periodic sweep evaluating active JobAlertPreferences against new jobs with durable fairness.
2. Channel-neutral notification intent creation in JobAlertNotification.
3. Durable deduplication via unique constraint on dedupe_key (user:job:policy_version).
4. Direct reuse of canonical WP2.2 MatchingEngine and TruthSnapshot
   via get_or_compute_matches_batch.
5. Integration with messaging OutboundMessageQueue for WhatsApp with verified recipient resolution.
6. Decoupled factual notification delivery state machine
   (PENDING, QUEUED, DELIVERED, FAILED, SKIPPED).
7. Deferred reconciliation with comprehensive revalidation of user consent, policy, job liveness,
   and canonical matching before outbox queuing.
8. Unsent queued alert cancellation on user opt-out / preference disabling.
9. Zero real sends in tests / dry-run safety.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from tapjob_api.career_profile.service import get_truth_snapshot
from tapjob_api.identity.models import User
from tapjob_api.jobs.models import (
    AlertNotificationStatus,
    Job,
    JobAlertDeliveryChannel,
    JobAlertNotification,
    JobAlertPreference,
    JobStatus,
)
from tapjob_api.matching.service import (
    get_or_compute_matches_batch,
    is_profile_matchable,
)
from tapjob_api.matching.types import MATCHING_POLICY_VERSION, MatchEligibility
from tapjob_api.messaging.models import ChannelIdentityLink, ChannelIdentityLinkStatus
from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.messaging.outbox_service import queue_outbound_message

logger = logging.getLogger(__name__)

ALERT_SWEEP_FAIRNESS_STRATEGY = "round_robin_watermark_last_evaluated_at"
ALERT_SWEEP_MAX_USERS = 50
MAX_ALERT_USERS_PER_SWEEP = 50
MAX_ALERT_CANDIDATE_JOBS_PER_USER = 50
PENDING_RECONCILIATION_BATCH_LIMIT = 50
PENDING_RECONCILIATION_MATCH_BATCH_STRATEGY = "per_user_canonical_batch"
SUPPORTED_JOB_ALERT_CHANNELS = ("whatsapp",)


def render_eligibility_label(eligibility: str) -> str:
    """Deterministic Portuguese label for match eligibility."""
    if eligibility == MatchEligibility.ELIGIBLE.value:
        return "Compatível"
    if eligibility == MatchEligibility.REVIEW_REQUIRED.value:
        return "Requer revisão"
    if eligibility == MatchEligibility.INELIGIBLE.value:
        return "Incompatível"
    return eligibility


def render_job_alert_message(
    *,
    job_title: str,
    company_name: str,
    match_score: int,
    eligibility: str,
) -> str:
    """Renders factual WhatsApp alert notification text without hardcoding 'compatível'."""
    status_label = render_eligibility_label(eligibility)
    return (
        f"🎯 Nova vaga recomendada: *{job_title}* na *{company_name}*\n"
        f"Compatibilidade: {match_score}%\n"
        f"Status: {status_label}"
    )


def is_job_alert_currently_eligible(
    *,
    match_overall_score: int,
    match_eligibility: str,
    pref: JobAlertPreference,
) -> bool:
    """Centralized policy check evaluating whether a match satisfies alert preference thresholds."""
    if not pref.enabled:
        return False
    if pref.delivery_channel != JobAlertDeliveryChannel.WHATSAPP:
        return False
    if match_overall_score < pref.min_score:
        return False
    if pref.min_eligibility == "eligible":
        return match_eligibility == MatchEligibility.ELIGIBLE.value
    if pref.min_eligibility == "review_required":
        return match_eligibility in (
            MatchEligibility.ELIGIBLE.value,
            MatchEligibility.REVIEW_REQUIRED.value,
        )
    return True


def is_job_alertable(job: Job, user_id: uuid.UUID, now_utc: datetime) -> bool:
    """Verifies that a job is currently active, unexpired, and accessible to the user."""
    if job.status != JobStatus.ACTIVE:
        return False
    if job.expires_at is not None and job.expires_at <= now_utc:
        return False
    if job.created_by_user_id is not None:
        return job.created_by_user_id == user_id
    return True


def cancel_unsent_queued_alerts_for_user(session: Session, user_id: uuid.UUID) -> int:
    """Cancels queued alert notifications and their unsent outbox messages when alerts are disabled.

    Finds JobAlertNotifications for this user where:
    - status == AlertNotificationStatus.QUEUED
    - outbound_message_id is not None
    Joins OutboundMessageQueue where status in (PENDING, RETRYABLE)
    Locks them with with_for_update()
    Transitions:
    - OutboundMessageQueue.status = OutboundMessageStatus.CANCELLED
    - JobAlertNotification.status = AlertNotificationStatus.SKIPPED
    - JobAlertNotification.delivered_at = None
    """
    stmt = (
        select(JobAlertNotification, OutboundMessageQueue)
        .join(
            OutboundMessageQueue,
            JobAlertNotification.outbound_message_id == OutboundMessageQueue.id,
        )
        .where(
            JobAlertNotification.user_id == user_id,
            JobAlertNotification.status == AlertNotificationStatus.QUEUED,
            OutboundMessageQueue.status.in_(
                [OutboundMessageStatus.PENDING, OutboundMessageStatus.RETRYABLE]
            ),
        )
        .with_for_update()
    )
    rows = list(session.execute(stmt).all())
    cancelled_count = 0
    for notif, outbox in rows:
        outbox.status = OutboundMessageStatus.CANCELLED
        notif.status = AlertNotificationStatus.SKIPPED
        notif.delivered_at = None
        cancelled_count += 1

    if cancelled_count > 0:
        session.flush()
    return cancelled_count


def _resolve_verified_whatsapp_recipient(
    session: Session, user_id: uuid.UUID
) -> tuple[ChannelIdentityLink | None, str]:
    """Deterministically resolves the user's active verified WhatsApp identity link and external_id.

    Fails closed: returns (None, "") if no verified link exists or external_id is empty.
    """
    link_stmt = (
        select(ChannelIdentityLink)
        .options(selectinload(ChannelIdentityLink.channel_identity))
        .where(
            ChannelIdentityLink.user_id == user_id,
            ChannelIdentityLink.status == ChannelIdentityLinkStatus.VERIFIED,
        )
        .order_by(ChannelIdentityLink.created_at.desc())
        .limit(1)
    )
    link = session.execute(link_stmt).scalar_one_or_none()
    recipient_external = ""
    if link is not None and link.channel_identity is not None and link.channel_identity.external_id:
        recipient_external = link.channel_identity.external_id.strip()
    return link, recipient_external


def reconcile_pending_alert_notifications(
    session: Session,
    *,
    limit: int = PENDING_RECONCILIATION_BATCH_LIMIT,
) -> int:
    """Reconciles PENDING job alert notifications, revalidating current consent, policy,
    job liveness, and match.

    Guarantees:
    - Bounded work: selects up to `limit` pending notifications with row locking.
    - Consent revalidation: verifies JobAlertPreference exists, is enabled, and uses whatsapp.
      If disabled/incompatible, transitions notification to SKIPPED without queuing outbox.
    - Job liveness revalidation: verifies job is ACTIVE, unexpired, and accessible.
      If closed/expired, transitions notification to SKIPPED.
    - Canonical matching revalidation: groups jobs by user and calls
      `get_or_compute_matches_batch` with current profile version and evaluation date.
      If thresholds are no longer satisfied, transitions notification to SKIPPED.
    - Factual WhatsApp message rendering: does not hardcode 'Status: compatível';
      accurately renders 'Compatível' or 'Requer revisão' based on real match eligibility.
    - Channel resolution: verifies user has an active VERIFIED ChannelIdentityLink.
      If link is still missing, notification remains PENDING.
    - Concurrency safety: SELECT ... FOR UPDATE SKIP LOCKED prevents duplicate reconciliation.
    """
    now_utc = datetime.now(tz=UTC)
    today_date = now_utc.date()

    stmt = (
        select(JobAlertNotification)
        .options(selectinload(JobAlertNotification.job).selectinload(Job.company))
        .where(
            JobAlertNotification.status == AlertNotificationStatus.PENDING,
            JobAlertNotification.outbound_message_id.is_(None),
        )
        .order_by(JobAlertNotification.created_at.asc(), JobAlertNotification.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    pending_notifications = list(session.execute(stmt).scalars().all())
    if not pending_notifications:
        return 0

    reconciled_count = 0
    # Group by user_id
    user_to_notifs: dict[uuid.UUID, list[JobAlertNotification]] = {}
    for notif in pending_notifications:
        user_to_notifs.setdefault(notif.user_id, []).append(notif)

    for user_id, notifs in user_to_notifs.items():
        # 1. Revalidate current preference / consent
        pref_stmt = select(JobAlertPreference).where(JobAlertPreference.user_id == user_id)
        pref = session.execute(pref_stmt).scalar_one_or_none()

        if (
            pref is None
            or not pref.enabled
            or pref.delivery_channel != JobAlertDeliveryChannel.WHATSAPP
        ):
            for notif in notifs:
                notif.status = AlertNotificationStatus.SKIPPED
                notif.delivered_at = None
            continue

        # 2. Revalidate user and matchable profile
        user = session.get(User, user_id)
        if user is None:
            for notif in notifs:
                notif.status = AlertNotificationStatus.SKIPPED
                notif.delivered_at = None
            continue

        truth_snapshot = get_truth_snapshot(session, user=user)
        if not is_profile_matchable(truth_snapshot):
            for notif in notifs:
                notif.status = AlertNotificationStatus.SKIPPED
                notif.delivered_at = None
            continue

        # 3. Revalidate job liveness
        active_notifs: list[JobAlertNotification] = []
        jobs_to_match: list[Job] = []
        for notif in notifs:
            job = notif.job
            if job is None or not is_job_alertable(job, user_id, now_utc):
                notif.status = AlertNotificationStatus.SKIPPED
                notif.delivered_at = None
            else:
                active_notifs.append(notif)
                jobs_to_match.append(job)

        if not active_notifs:
            continue

        # 4. Canonical batch matching revalidation
        match_results = get_or_compute_matches_batch(
            session,
            redis_client=None,
            user=user,
            jobs=jobs_to_match,
            evaluation_as_of_date=today_date,
        )

        # 5. Check match thresholds for each active notification
        eligible_notifs: list[tuple[JobAlertNotification, Any]] = []
        for notif in active_notifs:
            match_res = match_results.get(notif.job_id)
            if match_res is None or not is_job_alert_currently_eligible(
                match_overall_score=match_res.overall_score,
                match_eligibility=match_res.eligibility,
                pref=pref,
            ):
                notif.status = AlertNotificationStatus.SKIPPED
                notif.delivered_at = None
            else:
                eligible_notifs.append((notif, match_res))

        if not eligible_notifs:
            continue

        # 6. Check verified WhatsApp channel link
        link, recipient_external = _resolve_verified_whatsapp_recipient(session, user_id)
        if link is None or not recipient_external:
            # Channel link still not verified: keep in PENDING state
            continue

        # 7. Transition to QUEUED with factual message and outbox record
        for notif, match_res in eligible_notifs:
            job = notif.job
            company_name = job.company.name if job and job.company else "Empresa"
            job_title = job.title if job else "Vaga"
            msg_text = render_job_alert_message(
                job_title=job_title,
                company_name=company_name,
                match_score=match_res.overall_score,
                eligibility=match_res.eligibility,
            )
            outbox_msg = queue_outbound_message(
                session,
                channel_identity_link_id=str(link.id),
                message_content={
                    "text": msg_text,
                    "recipient_external_id": recipient_external,
                    "notification_id": str(notif.id),
                },
                idempotency_key=f"alert_msg:{notif.id}",
            )
            notif.outbound_message_id = outbox_msg.id
            notif.match_score = match_res.overall_score
            notif.status = AlertNotificationStatus.QUEUED
            notif.delivered_at = None
            reconciled_count += 1

    if reconciled_count > 0:
        session.flush()

    return reconciled_count


def reconcile_alert_notifications_delivery(session: Session, *, limit: int = 100) -> dict[str, int]:
    """Reconciles QUEUED notifications against durable OutboundMessageQueue delivery states.

    Guarantees:
    - JobAlertNotification represents intent; OutboundMessageQueue represents delivery truth.
    - Status DELIVERED and delivered_at are set only after OutboundMessageQueue reaches SENT.
    - Terminal failure (DEAD/FAILED) sets notification status to FAILED.
    - Outbound cancellation (CANCELLED) sets notification status to SKIPPED.
    - Transient states (PENDING/SENDING/RETRYABLE) leave notification as QUEUED.
    """
    stmt = (
        select(JobAlertNotification, OutboundMessageQueue)
        .join(
            OutboundMessageQueue,
            JobAlertNotification.outbound_message_id == OutboundMessageQueue.id,
        )
        .where(JobAlertNotification.status == AlertNotificationStatus.QUEUED)
        .limit(limit)
    )
    rows = list(session.execute(stmt).all())
    delivered_count = 0
    failed_count = 0
    skipped_count = 0

    for notif, outbox in rows:
        if outbox.status == OutboundMessageStatus.SENT:
            notif.status = AlertNotificationStatus.DELIVERED
            notif.delivered_at = outbox.sent_at or datetime.now(tz=UTC)
            delivered_count += 1
        elif outbox.status in (OutboundMessageStatus.DEAD, OutboundMessageStatus.FAILED):
            notif.status = AlertNotificationStatus.FAILED
            notif.delivered_at = None
            failed_count += 1
        elif outbox.status == OutboundMessageStatus.CANCELLED:
            notif.status = AlertNotificationStatus.SKIPPED
            notif.delivered_at = None
            skipped_count += 1

    if delivered_count > 0 or failed_count > 0 or skipped_count > 0:
        session.flush()

    return {"delivered": delivered_count, "failed": failed_count, "skipped": skipped_count}


def evaluate_job_alerts_sweep(
    session: Session,
    *,
    max_users: int = MAX_ALERT_USERS_PER_SWEEP,
    max_jobs_per_user: int = MAX_ALERT_CANDIDATE_JOBS_PER_USER,
) -> dict[str, Any]:
    """Evaluates active job alert preferences, generating notifications and queuing messages.

    Guarantees:
    - Fair bounded sweep using round_robin_watermark_last_evaluated_at.
    - Preserves bounded work and prevents starvation across users.
    - Centralized matchable profile validation including target_roles.
    - Canonical batch matching via get_or_compute_matches_batch with MatchResult.
    - Resolves external recipient strictly from ChannelIdentityLink -> ChannelIdentity.
    - Factual notification lifecycle: PENDING -> QUEUED -> DELIVERED / FAILED / SKIPPED.
    - Deferred reconciliation revalidates consent, policy, job liveness, and current matching.
    - Zero real sends in tests / dry-run safety.
    """
    # 0. Reconcile existing pending and queued notifications
    pending_reconciled = reconcile_pending_alert_notifications(session)
    delivery_reconciled = reconcile_alert_notifications_delivery(session)

    # 1. Select preferences with round-robin watermark ordering
    stmt = (
        select(JobAlertPreference)
        .where(JobAlertPreference.enabled.is_(True))
        .order_by(
            JobAlertPreference.last_evaluated_at.asc().nulls_first(),
            JobAlertPreference.created_at.asc(),
            JobAlertPreference.id.asc(),
        )
        .limit(max_users)
    )
    preferences = list(session.execute(stmt).scalars().all())

    evaluated_users_count = len(preferences)
    notifications_created = 0
    outbound_messages_queued = 0
    now_utc = datetime.now(tz=UTC)
    today_date = now_utc.date()

    for pref in preferences:
        # Update watermark for fairness rotation
        pref.last_evaluated_at = now_utc

        user = session.get(User, pref.user_id)
        if user is None:
            continue

        truth_snapshot = get_truth_snapshot(session, user=user)
        if not is_profile_matchable(truth_snapshot):
            # Without matchable profile facts or target roles, skip matching
            continue

        # Select active, offerable jobs not yet notified for this user
        existing_job_ids_stmt = select(JobAlertNotification.job_id).where(
            JobAlertNotification.user_id == user.id
        )

        jobs_stmt = (
            select(Job)
            .options(selectinload(Job.company))
            .where(
                Job.status == JobStatus.ACTIVE,
                (Job.expires_at.is_(None) | (Job.expires_at > now_utc)),
                (Job.created_by_user_id.is_(None) | (Job.created_by_user_id == user.id)),
                Job.id.notin_(existing_job_ids_stmt),
            )
            .order_by(Job.created_at.desc())
            .limit(max_jobs_per_user)
        )
        candidate_jobs = list(session.execute(jobs_stmt).scalars().all())
        if not candidate_jobs:
            continue

        # Canonical batch matching reusing WP2.2 Matching Engine and MatchResults
        match_results = get_or_compute_matches_batch(
            session,
            redis_client=None,
            user=user,
            jobs=candidate_jobs,
            evaluation_as_of_date=today_date,
        )

        # Resolve verified WhatsApp channel identity for this user
        link, recipient_external = _resolve_verified_whatsapp_recipient(session, user.id)

        for job in candidate_jobs:
            match_res = match_results.get(job.id)
            if match_res is None:
                continue

            # Centralized threshold & policy check
            if not is_job_alert_currently_eligible(
                match_overall_score=match_res.overall_score,
                match_eligibility=match_res.eligibility,
                pref=pref,
            ):
                continue

            # Construct durable deduplication key
            dedupe_key = f"alert:{user.id}:{job.id}:{MATCHING_POLICY_VERSION}"

            # Insert notification atomically in PENDING state
            notif_stmt = (
                insert(JobAlertNotification)
                .values(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    job_id=job.id,
                    dedupe_key=dedupe_key,
                    channel=JobAlertDeliveryChannel.WHATSAPP,
                    match_score=match_res.overall_score,
                    status=AlertNotificationStatus.PENDING,
                    outbound_message_id=None,
                    delivered_at=None,
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(JobAlertNotification)
            )
            notif = session.execute(notif_stmt).scalar_one_or_none()
            if notif is None:
                continue  # Already notified / deduped

            notifications_created += 1

            # Dispatch intent to delivery channel if verified recipient exists
            if link is not None and recipient_external:
                company_name = job.company.name if job.company else "Empresa"
                msg_text = render_job_alert_message(
                    job_title=job.title,
                    company_name=company_name,
                    match_score=match_res.overall_score,
                    eligibility=match_res.eligibility,
                )
                outbox_msg = queue_outbound_message(
                    session,
                    channel_identity_link_id=str(link.id),
                    message_content={
                        "text": msg_text,
                        "recipient_external_id": recipient_external,
                        "notification_id": str(notif.id),
                    },
                    idempotency_key=f"alert_msg:{notif.id}",
                )
                notif.outbound_message_id = outbox_msg.id
                notif.status = AlertNotificationStatus.QUEUED
                notif.delivered_at = None
                outbound_messages_queued += 1
            else:
                logger.info(
                    "job_alert.no_verified_link",
                    extra={"user_id": str(user.id), "notification_id": str(notif.id)},
                )

    session.flush()

    return {
        "status": "completed",
        "evaluated_users_count": evaluated_users_count,
        "notifications_created": notifications_created,
        "outbound_messages_queued": outbound_messages_queued,
        "pending_reconciled": pending_reconciled,
        "delivery_reconciled": delivery_reconciled,
    }
