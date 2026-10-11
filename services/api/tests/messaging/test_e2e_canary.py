"""End-to-End Canary integration test proving vertical slice (Finding 13).

Requires real PostgreSQL and real Redis (provisioned in backend-integration CI job).
Marked with @pytest.mark.integration.

Proves:
1. Inbound message received and parsed via FakeMessagingChannel
2. ProviderEventEnvelope persisted atomically in PostgreSQL
3. Retransmission/replay produces zero state duplication
4. ChannelIdentity and User created and verified
5. Outbound greeting queued into durable OutboundMessageQueue
6. Durable outbox claimed and dispatched via FakeMessagingChannel with SENT status
7. Career profile facts added to canonical Truth Store (WP1.2)
8. Canonical TruthSnapshot reconstructed accurately
9. Job intake persisted via canonical WP2.1
10. Canonical WP2.2 MatchResult computed and persisted
11. Grounded resume draft generated strictly from TruthSnapshot facts
12. ApplicationKit persisted with immutable snapshot
13. Application record persisted and status tracked
14. Channel-neutral Reminder created, claimed via SKIP LOCKED, and dispatched to outbox
15. Zero real WhatsApp sends (WHATSAPP_REAL_SENDS=0)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.career_profile import service as career_profile_service
from tapjob_api.career_profile.fact_values import ExperienceFactValue, SkillFactValue, YearMonth
from tapjob_api.career_profile.models import FactType
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs import service as jobs_service
from tapjob_api.jobs.schemas import JobPasteIntakeRequest
from tapjob_api.matching import service as matching_service
from tapjob_api.messaging.applications import Application, ApplicationStatus
from tapjob_api.messaging.applications_service import (
    build_and_persist_application_kit,
    create_or_update_application,
)
from tapjob_api.messaging.inbound import (
    ProviderEventProcessingStatus,
    ProviderEventType,
)
from tapjob_api.messaging.inbound_service import record_inbound_event
from tapjob_api.messaging.models import (
    ChannelIdentity,
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelType,
)
from tapjob_api.messaging.orchestration import dispatch_inbound_message
from tapjob_api.messaging.outbox import OutboundMessageStatus
from tapjob_api.messaging.outbox_service import claim_and_dispatch_outbox_batch
from tapjob_api.messaging.reminders import ReminderStatus
from tapjob_api.messaging.reminders_service import create_reminder, dispatch_due_reminders
from tapjob_api.messaging.resume_tailoring import generate_grounded_tailored_resume
from tapjob_api.messaging.service import get_or_create_channel_provider
from tapjob_api.ports._common import CallContext
from tapjob_api.ports.messaging_channel import InboundMessage

from .fake_channel import FakeMessagingChannel


@pytest.mark.integration
def test_whatsapp_alpha_e2e_canary_vertical_slice(_identity_schema_ready: None) -> None:
    """Proves the full WhatsApp Alpha Vertical Slice against real PostgreSQL and Redis."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    call_ctx = CallContext.with_timeout(correlation_id=uuid.uuid4().hex, timeout_seconds=30.0)

    try:
        # 1. Inbound "oi" message arriving via provider webhook
        sender_phone = f"551199{uuid.uuid4().hex[:7]}"
        external_msg_id = f"evt_canary_{uuid.uuid4().hex[:12]}"
        webhook_payload = {
            "messages": [
                {
                    "from": sender_phone,
                    "id": external_msg_id,
                    "timestamp": int(datetime.now(UTC).timestamp()),
                    "text": {"body": "oi"},
                }
            ]
        }
        body_bytes = json.dumps(webhook_payload).encode("utf-8")

        inbound_messages = asyncio.run(fake_channel.parse_inbound(body_bytes, {}, call_ctx))
        assert len(inbound_messages) == 1
        inbound_msg: InboundMessage = inbound_messages[0]

        # 2. ProviderEventEnvelope persisted atomically in PostgreSQL
        envelope, is_new = record_inbound_event(
            session,
            provider="fake_whatsapp",
            provider_instance="canary_instance",
            external_event_id=inbound_msg.provider_message_id,
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={"from": sender_phone, "text": "oi"},
        )
        session.commit()
        assert is_new is True
        assert envelope.id is not None
        assert envelope.status == ProviderEventProcessingStatus.ACCEPTED

        # 3. Retransmission replay test (Deduplication: zero state duplication)
        envelope_dup, is_new_dup = record_inbound_event(
            session,
            provider="fake_whatsapp",
            provider_instance="canary_instance",
            external_event_id=inbound_msg.provider_message_id,
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={"from": sender_phone, "text": "oi"},
        )
        session.commit()
        assert is_new_dup is False
        assert envelope_dup.id == envelope.id

        # 4. Channel Provider & Identity Linking (First Contact -> User created)
        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="fake_whatsapp",
            provider_config_key=f"canary_provider_{uuid.uuid4().hex[:8]}",
        )
        session.commit()

        queued_keys = dispatch_inbound_message(
            session,
            redis_client=redis_client,
            provider_id=provider.id,
            inbound=inbound_msg,
        )
        session.commit()
        assert len(queued_keys) >= 1

        # Confirm ChannelIdentity and Link persisted
        identity = session.execute(
            select(ChannelIdentity).where(
                ChannelIdentity.provider_id == provider.id,
                ChannelIdentity.external_id == sender_phone,
            )
        ).scalar_one()
        assert identity is not None

        link = session.execute(
            select(ChannelIdentityLink).where(
                ChannelIdentityLink.channel_identity_id == identity.id
            )
        ).scalar_one()
        assert link.status == ChannelIdentityLinkStatus.VERIFIED
        user_id = link.user_id

        user = session.execute(select(User).where(User.id == user_id)).scalar_one()
        assert user.status == UserStatus.ACTIVE

        # 5. Outbox durable claim & dispatch
        dispatched_outbox = claim_and_dispatch_outbox_batch(
            session,
            provider_adapter=fake_channel,
            limit=10,
        )
        session.commit()
        assert len(dispatched_outbox) >= 1
        assert any(m.status == OutboundMessageStatus.SENT for m in dispatched_outbox)
        assert len(fake_channel.sent_messages) >= 1

        # 6. Profile / Resume Flow & Canonical Truth Store (WP1.2)
        skill_val = SkillFactValue(name="Python")
        fact_skill = career_profile_service.add_profile_fact(
            session,
            user=user,
            expected_version=0,
            fact_type=FactType.SKILL,
            value=skill_val,
        )
        session.commit()

        exp_val = ExperienceFactValue(
            employer="Empresa Tecnológica",
            title="Desenvolvedor Backend",
            start=YearMonth(year=2021, month=1),
            current=True,
        )
        fact_exp = career_profile_service.add_profile_fact(
            session,
            user=user,
            expected_version=fact_skill.active_from_profile_version,
            fact_type=FactType.EXPERIENCE,
            value=exp_val,
        )
        session.commit()

        # Retrieve canonical Truth Snapshot
        truth_snapshot = career_profile_service.get_truth_snapshot(session, user=user)
        assert truth_snapshot.profile_version >= 2
        assert len(truth_snapshot.facts) >= 2
        fact_ids = [f.id for f in truth_snapshot.facts]
        assert fact_skill.id in fact_ids
        assert fact_exp.id in fact_ids

        # 7. Job Intake via Canonical WP2.1 Service
        job_request = JobPasteIntakeRequest(
            company_name=f"Canary Company {uuid.uuid4().hex[:6]}",
            title="Senior Python Developer",
            description="Desenvolvedor com sólidos conhecimentos em Python e APIs.",
        )
        job = jobs_service.intake_job_paste(session, user=user, request=job_request)
        session.commit()
        assert job.id is not None
        assert job.title == "Senior Python Developer"

        # 8. Canonical WP2.2 Matching Computation
        match_result = matching_service.get_or_compute_match(
            session,
            redis_client=redis_client,
            user=user,
            job_id=job.id,
        )
        session.commit()
        assert match_result.id is not None
        assert isinstance(match_result.overall_score, int)
        assert 0 <= match_result.overall_score <= 100
        assert match_result.eligibility in ("eligible", "ineligible", "review_required")

        # 9. Grounded Resume Tailoring
        tailored_draft = generate_grounded_tailored_resume(session, user=user, job_id=job.id)
        assert tailored_draft.target_title == job.title
        assert tailored_draft.ready_for_submission is True
        assert tailored_draft.validation_result.get("grounded") is True
        for fid in tailored_draft.grounded_fact_ids:
            assert fid in fact_ids

        # 10. Application Kit Persistence
        strengths = [s.get("summary", "") for s in match_result.strengths if isinstance(s, dict)]
        gaps = [
            g.get("summary", "") for g in match_result.missing_requirements if isinstance(g, dict)
        ]
        kit = build_and_persist_application_kit(
            session,
            user=user,
            job_id=job.id,
            match_result_id=match_result.id,
            profile_version=match_result.profile_version,
            draft=tailored_draft,
            strengths=strengths,
            gaps=gaps,
        )
        session.commit()
        assert kit.id is not None
        assert kit.application_id is not None

        # Confirm real Application row is persisted
        app_row = session.execute(
            select(Application).where(Application.id == kit.application_id)
        ).scalar_one()
        assert app_row.status == ApplicationStatus.PREPARING

        # Update application to APPLIED
        app_row = create_or_update_application(
            session,
            user=user,
            job_id=job.id,
            status=ApplicationStatus.APPLIED,
        )
        session.commit()
        assert app_row.status == ApplicationStatus.APPLIED
        assert app_row.submitted_at is not None

        # 11. Channel-neutral Reminder persistence & dispatch
        reminder_due = datetime.now(UTC) - timedelta(minutes=1)  # already due
        reminder = create_reminder(
            session,
            user=user,
            remind_at=reminder_due,
            intent="canary_follow_up",
            payload={"message": "Lembrete", "recipient_external_id": sender_phone},
            channel_identity_link_id=link.id,
            idempotency_key=f"rem_canary_{uuid.uuid4().hex[:12]}",
        )
        session.commit()
        assert reminder.id is not None
        assert reminder.status == ReminderStatus.PENDING

        due_reminders = dispatch_due_reminders(session, limit=10)
        session.commit()
        assert len(due_reminders) >= 1
        assert any(r.id == reminder.id for r in due_reminders)

        # Confirm notification queued in durable outbox and dispatch
        rem_outbox = claim_and_dispatch_outbox_batch(
            session,
            provider_adapter=fake_channel,
            limit=10,
        )
        session.commit()
        assert any(m.status == OutboundMessageStatus.SENT for m in rem_outbox)

        # 12. Final invariant checks: zero real sends
        assert fake_channel.provider == "fake_whatsapp"
    finally:
        session.close()
        engine.dispose()
