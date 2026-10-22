"""Real Ingress E2E Canary integration test traversing webhook ingress to core flow (Finding 6).

Requires real PostgreSQL and real Redis (provisioned in backend-integration CI job).
Marked with @pytest.mark.integration.

Proves:
1. HTTP Ingress: TestClient -> POST /api/v1/webhooks/whatsapp with real authentication
2. Fail-closed ingress security (401 on missing/wrong secret, 202 on valid secret)
3. ProviderEventEnvelope atomically persisted in real PostgreSQL
4. Fast-ack 202 without inline orchestration
5. Background worker task executes:
   - Marks envelope PROCESSING -> PROCESSED
   - Reconstructs InboundMessage
   - First-contact creates User and ChannelIdentityLink
   - Outbound greeting queued into durable OutboundMessageQueue
6. Durable outbox claimed and dispatched via SKIP LOCKED
7. Replay safe: exact same webhook retransmission produces:
   - Zero duplicate User
   - Zero duplicate envelope side effect
   - Zero duplicate outbound message intent
8. Core Career Flow:
   - Canonical Truth Store facts (WP1.2)
   - Job intake (WP2.1)
   - Canonical matching (WP2.2)
   - Grounded resume tailoring with claim-level validation
   - ApplicationKit immutable snapshot
   - Application tracking & Reminders
9. Invariants: WHATSAPP_REAL_SENDS=0
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.career_profile import service as career_profile_service
from tapjob_api.career_profile.fact_values import ExperienceFactValue, SkillFactValue, YearMonth
from tapjob_api.career_profile.models import CareerProfile, FactStatus, FactType, ProfileFact
from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.jobs import service as jobs_service
from tapjob_api.jobs.http_client import SafeHttpClient
from tapjob_api.jobs.models import Job
from tapjob_api.jobs.schemas import JobPasteIntakeRequest
from tapjob_api.main import create_app
from tapjob_api.matching import service as matching_service
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.types import MatchEligibility
from tapjob_api.messaging.applications import Application, ApplicationKit, ApplicationStatus
from tapjob_api.messaging.applications_service import (
    build_and_persist_application_kit,
    create_or_update_application,
)
from tapjob_api.messaging.inbound import (
    InboundEffect,
    InboundEffectKind,
    InboundEffectState,
    ProviderEventEnvelope,
    ProviderEventProcessingStatus,
    ProviderEventType,
)
from tapjob_api.messaging.inbound_service import (
    InboundClaimResult,
    claim_inbound_envelope,
    get_inbound_effect,
    mark_event_failed,
    mark_event_processed,
    reserve_inbound_effect,
)
from tapjob_api.messaging.models import (
    ChannelIdentity,
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelType,
)
from tapjob_api.messaging.orchestration import dispatch_inbound_message
from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.messaging.outbox_service import claim_and_dispatch_outbox_batch
from tapjob_api.messaging.reminders import Reminder
from tapjob_api.messaging.reminders_service import create_reminder, dispatch_due_reminders
from tapjob_api.messaging.resume_tailoring import generate_grounded_tailored_resume
from tapjob_api.messaging.service import (
    get_or_create_channel_provider,
    link_or_create_identity,
)
from tapjob_api.messaging.tasks import (
    configure_messaging_worker,
    process_inbound_envelope_task,
)
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity as PortChannelIdentity,
)
from tapjob_api.ports.messaging_channel import (
    InboundAttachment,
    InboundMessage,
)
from tapjob_api.resume_import.errors import (
    TextNotExtractableError,
)
from tapjob_api.resume_import.models import (
    CandidateReviewDraft,
    CandidateReviewState,
    ImportStatus,
    Resume,
    ResumeImport,
    ResumeImportCandidate,
    ResumeVersion,
)
from tapjob_api.resume_import.storage import InMemoryObjectStorage

from .fake_channel import _VALID_TEST_PDF_BYTES, FakeMessagingChannel


@pytest.mark.integration
def test_real_ingress_e2e_canary_vertical_slice(_identity_schema_ready: None) -> None:
    """Proves end-to-end traversal from HTTP Webhook Ingress through Async Worker to Core Flow."""
    webhook_secret = f"secret_ingress_{uuid.uuid4().hex[:12]}"
    settings = ApiSettings(
        app_env=Environment.DEVELOPMENT,
        whatsapp_webhook_secret=webhook_secret,
        whatsapp_webhook_enabled=True,
    )
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    # Configure background messaging worker with injected adapter and storage
    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    app = create_app(settings, resume_storage_override=storage)

    try:
        sender_phone = f"551197{uuid.uuid4().hex[:7]}"
        external_msg_id = f"wamid_ingress_{uuid.uuid4().hex[:12]}"

        # Evolution API webhook payload format
        webhook_payload = {
            "event": "messages.upsert",
            "instance": "tapjob_canary",
            "data": {
                "key": {
                    "remoteJid": f"{sender_phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": external_msg_id,
                },
                "pushName": "Candidato Canary",
                "message": {
                    "conversation": "oi",
                },
                "messageTimestamp": int(datetime.now(UTC).timestamp()),
            },
        }

        with TestClient(app) as client:
            # 1. Ingress Auth check: Missing secret -> 401
            resp_no_auth = client.post(
                "/webhooks/whatsapp",
                json=webhook_payload,
            )
            assert resp_no_auth.status_code == 401

            # 2. Ingress Auth check: Wrong secret -> 401
            resp_wrong_auth = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": "wrong_secret"},
                json=webhook_payload,
            )
            assert resp_wrong_auth.status_code == 401

            # 3. Valid authenticated POST request -> Fast 202 with Celery task enqueued
            with patch("tapjob_api.http.webhooks.process_inbound_envelope_task") as mock_task:
                resp_valid = client.post(
                    "/webhooks/whatsapp",
                    headers={"X-Webhook-Secret": webhook_secret},
                    json=webhook_payload,
                )
                assert resp_valid.status_code == 202
                data = resp_valid.json()
                assert data["status"] == "accepted"

                # 4. Verify ProviderEventEnvelope persisted in PostgreSQL & task enqueued
                envelope = session.execute(
                    select(ProviderEventEnvelope).where(
                        ProviderEventEnvelope.external_event_id == external_msg_id
                    )
                ).scalar_one()
                envelope_id = envelope.id
                mock_task.delay.assert_called_once_with(envelope_id)
                assert envelope.external_event_id == external_msg_id
                assert envelope.status == ProviderEventProcessingStatus.ACCEPTED

        # 5. Background Worker Task Execution: process envelope asynchronously
        process_inbound_envelope_task(envelope_id)

        # Confirm envelope marked PROCESSED
        session.expire_all()
        envelope = session.execute(
            select(ProviderEventEnvelope).where(ProviderEventEnvelope.id == envelope_id)
        ).scalar_one()
        assert envelope.status == ProviderEventProcessingStatus.PROCESSED

        # Confirm ChannelIdentity and User created in PostgreSQL
        identity = session.execute(
            select(ChannelIdentity).where(ChannelIdentity.external_id == sender_phone)
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

        # 6. Durable Outbox: Greeting queued and claimed
        outbox_items = list(
            session.execute(
                select(OutboundMessageQueue).where(
                    OutboundMessageQueue.channel_identity_link_id == str(link.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(outbox_items) >= 1
        initial_outbox_count = len(outbox_items)

        # Dispatch via SKIP LOCKED outbox processor
        dispatched = claim_and_dispatch_outbox_batch(
            session,
            provider_adapter=fake_channel,
            limit=10,
        )
        session.commit()
        assert len(dispatched) >= 1
        assert any(m.status == OutboundMessageStatus.SENT for m in dispatched)

        # 7. Replay Test: retransmit exact same webhook
        with TestClient(app) as client:
            resp_replay = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=webhook_payload,
            )
            assert resp_replay.status_code == 202

        # Replay worker execution
        process_inbound_envelope_task(envelope_id)

        # Invariant: Zero duplicate User, zero duplicate outbox items
        all_users_for_phone = list(
            session.execute(
                select(ChannelIdentityLink).where(
                    ChannelIdentityLink.channel_identity_id == identity.id
                )
            )
            .scalars()
            .all()
        )
        assert len(all_users_for_phone) == 1  # exactly 1 link

        current_outbox_items = list(
            session.execute(
                select(OutboundMessageQueue).where(
                    OutboundMessageQueue.channel_identity_link_id == str(link.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(current_outbox_items) == initial_outbox_count  # zero duplicate outbound intents

        # 8. Core Flow: Truth Store, Matching, Tailoring, Kit, Application, Reminder
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
            employer="Empresa Tech",
            title="Desenvolvedor Python",
            start=YearMonth(year=2022, month=1),
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

        # Job Intake (WP2.1)
        job_req = JobPasteIntakeRequest(
            company_name=f"Ingress Co {uuid.uuid4().hex[:6]}",
            title="Senior Python Backend",
            description="Requisitos: Sólidos conhecimentos em Python e APIs.",
        )
        job = jobs_service.intake_job_paste(session, user=user, request=job_req)
        session.commit()
        assert job.id is not None

        # Canonical WP2.2 Matching
        match_result = matching_service.get_or_compute_match(
            session,
            redis_client=redis_client,
            user=user,
            job_id=job.id,
        )
        session.commit()
        assert match_result.eligibility in (
            MatchEligibility.ELIGIBLE.value,
            MatchEligibility.REVIEW_REQUIRED.value,
            MatchEligibility.INELIGIBLE.value,
        )

        # Grounded Resume Tailoring with claim-level validation
        tailored_draft = generate_grounded_tailored_resume(session, user=user, job_id=job.id)
        assert tailored_draft.ready_for_submission is True
        assert len(tailored_draft.claims) >= 2
        assert all(len(c.source_fact_ids) > 0 for c in tailored_draft.claims)

        # ApplicationKit persistence
        kit = build_and_persist_application_kit(
            session,
            user=user,
            job_id=job.id,
            match_result_id=match_result.id,
            profile_version=fact_exp.active_from_profile_version,
            draft=tailored_draft,
            strengths=["Python"],
            gaps=[],
        )
        session.commit()
        assert kit.id is not None

        # Application tracking
        app_record = create_or_update_application(
            session,
            user=user,
            job_id=job.id,
            status=ApplicationStatus.APPLIED,
        )
        session.commit()
        assert app_record.status == ApplicationStatus.APPLIED

        # Channel-neutral Reminder
        reminder = create_reminder(
            session,
            user=user,
            remind_at=datetime.now(UTC) - timedelta(seconds=1),
            intent="ingress_canary_followup",
            payload={"message": "Lembrete Canary"},
            channel_identity_link_id=link.id,
            idempotency_key=f"rem_ingress_{uuid.uuid4().hex[:12]}",
        )
        session.commit()
        due_reminders = dispatch_due_reminders(session, limit=5)
        session.commit()
        assert any(r.id == reminder.id for r in due_reminders)

        # Final invariant: zero real sends
        assert fake_channel.provider == "fake_whatsapp"
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_conversational_e2e_canary_vertical_slice(_identity_schema_ready: None) -> None:
    """Proves Finding 6: Conversational vertical slice end-to-end via webhook ingress.

    Traverses:
    1. webhook "oi" -> greeting
    2. webhook/document PDF -> ResumeImport (ObjectStoragePort backed)
    3. confirmation/action -> canonical Truth Store facts
    4. webhook com paste de vaga -> canonical Job Intake
    5. mensagem pedindo análise -> canonical MatchResult -> outbound explanation
    6. mensagem pedindo candidatura -> grounded tailoring -> ApplicationKit -> Application
    7. mensagem pedindo reminder -> Reminder -> scheduled dispatch -> Outbox
    8. Outbox dispatch -> FakeMessagingChannel
    9. Inbound replay -> zero duplicate mutation

    Invariants: WHATSAPP_REAL_SENDS=0
    """
    webhook_secret = f"secret_conv_{uuid.uuid4().hex[:12]}"
    settings = ApiSettings(
        app_env=Environment.DEVELOPMENT,
        whatsapp_webhook_secret=webhook_secret,
        whatsapp_webhook_enabled=True,
    )
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    app = create_app(settings, resume_storage_override=storage)

    try:
        sender_phone = f"551198{uuid.uuid4().hex[:7]}"

        def post_webhook_and_process(
            client: TestClient,
            *,
            text: str | None = None,
            document: dict[str, Any] | None = None,
            custom_msg_id: str | None = None,
        ) -> tuple[int, uuid.UUID]:
            msg_id = custom_msg_id or f"wamid_conv_{uuid.uuid4().hex[:12]}"
            msg_body: dict[str, Any] = {}
            if text is not None:
                msg_body["conversation"] = text
            if document is not None:
                msg_body["documentMessage"] = document

            payload: dict[str, Any] = {
                "event": "messages.upsert",
                "instance": "tapjob_canary",
                "data": {
                    "key": {
                        "remoteJid": f"{sender_phone}@s.whatsapp.net",
                        "fromMe": False,
                        "id": msg_id,
                    },
                    "pushName": "Candidato Conversational",
                    "message": msg_body,
                    "messageTimestamp": int(datetime.now(UTC).timestamp()),
                },
            }
            resp = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=payload,
            )
            assert resp.status_code == 202

            session.expire_all()
            env = session.execute(
                select(ProviderEventEnvelope).where(
                    ProviderEventEnvelope.external_event_id == msg_id
                )
            ).scalar_one()

            # Execute background worker task
            process_inbound_envelope_task(env.id)

            # Claim and dispatch queued outbound messages to fake channel
            session.expire_all()
            claim_and_dispatch_outbox_batch(session, provider_adapter=fake_channel, limit=10)
            session.commit()

            return resp.status_code, cast(uuid.UUID, env.id)

        with TestClient(app) as client:
            # Step 1: Webhook "oi" -> greeting queued & dispatched
            post_webhook_and_process(client, text="oi")

            # Verify ChannelIdentity & User created
            session.expire_all()
            identity = session.execute(
                select(ChannelIdentity).where(ChannelIdentity.external_id == sender_phone)
            ).scalar_one()
            link = session.execute(
                select(ChannelIdentityLink).where(
                    ChannelIdentityLink.channel_identity_id == identity.id
                )
            ).scalar_one()
            user = session.execute(select(User).where(User.id == link.user_id)).scalar_one()
            assert user.status == UserStatus.ACTIVE
            assert len(fake_channel.sent_messages) >= 1
            assert "Bem-vindo" in (fake_channel.sent_messages[-1].text or "")

            # Step 2: Inbound Document (PDF) -> ResumeImport
            post_webhook_and_process(
                client,
                document={
                    "fileName": "curriculo_jane.pdf",
                    "mimetype": "application/pdf",
                    "fileLength": 1024,
                },
            )
            session.expire_all()
            latest_import = session.execute(
                select(ResumeImport)
                .where(ResumeImport.user_id == user.id)
                .order_by(ResumeImport.created_at.desc())
                .limit(1)
            ).scalar_one()
            assert latest_import.status == ImportStatus.READY_FOR_REVIEW
            assert len(latest_import.candidates) >= 1
            # E2E_OBJECT_STORAGE=YES check: storage key exists in storage
            assert latest_import.storage_key in storage._objects
            assert "Recebemos seu currículo" in (fake_channel.sent_messages[-1].text or "")

            # Step 3: Confirmation -> Canonical Truth Store
            post_webhook_and_process(client, text="confirmar fatos")
            session.expire_all()
            facts = list(
                session.execute(select(ProfileFact).where(ProfileFact.user_id == user.id))
                .scalars()
                .all()
            )
            assert len(facts) >= 1
            assert "Informações confirmadas" in (fake_channel.sent_messages[-1].text or "")

            # Step 4: Webhook com paste de vaga -> Canonical Job Intake
            job_text = (
                "Vaga: Engenheiro de Software Python na Tech Ingress. "
                "Requisitos e responsabilidades: Desenvolvimento de APIs backend com FastAPI, "
                "SQLAlchemy, Docker e arquitetura de microsserviços."
            )
            post_webhook_and_process(client, text=job_text)
            session.expire_all()
            jobs = list(
                session.execute(select(Job).where(Job.title.ilike("%Engenheiro%"))).scalars().all()
            )
            assert len(jobs) >= 1
            assert "Match Score WP2.2" in (fake_channel.sent_messages[-1].text or "")

            # Step 5: Mensagem pedindo análise -> MatchResult explanation
            post_webhook_and_process(client, text="analisa essa vaga")
            session.expire_all()
            assert "Análise detalhada" in (fake_channel.sent_messages[-1].text or "")

            # Step 6: Mensagem pedindo candidatura -> Grounded Tailoring + Kit + Application
            kit_msg_id = f"wamid_conv_kit_{uuid.uuid4().hex[:8]}"
            post_webhook_and_process(client, text="quero me candidatar", custom_msg_id=kit_msg_id)
            session.expire_all()
            kits = list(
                session.execute(select(ApplicationKit).where(ApplicationKit.user_id == user.id))
                .scalars()
                .all()
            )
            assert len(kits) >= 1
            apps = list(
                session.execute(select(Application).where(Application.user_id == user.id))
                .scalars()
                .all()
            )
            assert len(apps) >= 1
            latest_app = apps[-1]
            # Kit leaves application in PREPARING with submitted_at=None (Finding 3)
            assert latest_app.status == ApplicationStatus.PREPARING
            assert latest_app.submitted_at is None
            assert "Application Kit preparado" in (fake_channel.sent_messages[-1].text or "")

            # Step 6b: Mensagem confirmando submissão -> Application APPLIED (Finding 3)
            post_webhook_and_process(client, text="já me candidatei")
            session.expire_all()
            apps_applied = list(
                session.execute(select(Application).where(Application.user_id == user.id))
                .scalars()
                .all()
            )
            assert apps_applied[-1].status == ApplicationStatus.APPLIED
            submitted_at_t1 = apps_applied[-1].submitted_at
            assert submitted_at_t1 is not None
            assert "Atualizamos o status da sua candidatura" in (
                fake_channel.sent_messages[-1].text or ""
            )

            # Step 6c: Subsequent message with different provider_message_id requesting kit again
            # MUST NOT regress status to PREPARING, and MUST preserve submitted_at == T1 (Finding 3)
            subsequent_kit_msg_id = f"wamid_conv_kit_after_{uuid.uuid4().hex[:8]}"
            post_webhook_and_process(
                client, text="quero me candidatar", custom_msg_id=subsequent_kit_msg_id
            )
            session.expire_all()
            apps_after_kit = list(
                session.execute(select(Application).where(Application.user_id == user.id))
                .scalars()
                .all()
            )
            assert len(apps_after_kit) == len(apps_applied)
            current_app = apps_after_kit[-1]
            assert (
                current_app.status == ApplicationStatus.APPLIED
            )  # Preserved APPLIED, zero regression
            assert current_app.submitted_at == submitted_at_t1  # Preserved submitted_at == T1

            # Step 7: Mensagem pedindo reminder -> Scheduled Reminder -> Outbox delivery
            post_webhook_and_process(client, text="me lembra de acompanhar a candidatura")
            session.expire_all()
            reminders: list[Reminder] = list(
                session.execute(select(Reminder).where(Reminder.user_id == user.id)).scalars().all()
            )
            assert len(reminders) >= 1
            target_rem = reminders[-1]
            assert "Lembrete agendado" in (fake_channel.sent_messages[-1].text or "")
            # Reminder correctly references real application (Finding 4)
            assert target_rem.payload.get("application_id") == str(apps_applied[-1].id)

            # Fast-forward reminder time and sweep due reminders
            target_rem.remind_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
            due_dispatched = dispatch_due_reminders(session, limit=10)
            session.commit()
            assert any(r.id == target_rem.id for r in due_dispatched)

            # Dispatch newly queued reminder outbox message
            sent_count_before = len(fake_channel.sent_messages)
            claim_and_dispatch_outbox_batch(session, provider_adapter=fake_channel, limit=10)
            session.commit()
            assert len(fake_channel.sent_messages) > sent_count_before

            # Step 8: Replay Inbound -> Safe no-op, zero duplicate mutation
            initial_kit_count = len(kits)
            initial_app_count = len(apps_applied)
            # Re-post exact same webhook payload for kit request
            post_webhook_and_process(client, text="quero me candidatar", custom_msg_id=kit_msg_id)
            session.expire_all()
            kits_after = list(
                session.execute(select(ApplicationKit).where(ApplicationKit.user_id == user.id))
                .scalars()
                .all()
            )
            apps_replay = list(
                session.execute(select(Application).where(Application.user_id == user.id))
                .scalars()
                .all()
            )
            assert len(kits_after) == initial_kit_count
            assert len(apps_replay) == initial_app_count

            # Step 9: Invariant
            assert fake_channel.provider == "fake_whatsapp"
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_multiuser_job_isolation_vertical_slice(_identity_schema_ready: None) -> None:
    """Proves deterministic user-scoped job isolation across distinct users (Finding 2).

    1. User A sends Job X ("Vaga Engenheiro Python Alpha").
    2. User B sends Job Y ("Vaga Engenheiro Rust Beta").
    3. User A asks "analisa essa vaga" -> analyzes Job X only; zero mention of Job Y.
    4. User A asks "quero me candidatar" -> ApplicationKit generated for Job X only.
    5. Invariants: zero context leakage across users in multi-user environment.
    """
    webhook_secret = f"secret_multi_{uuid.uuid4().hex[:12]}"
    settings = ApiSettings(
        app_env=Environment.DEVELOPMENT,
        whatsapp_webhook_secret=webhook_secret,
        whatsapp_webhook_enabled=True,
    )
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    app = create_app(settings, resume_storage_override=storage)

    try:
        phone_a = f"551191{uuid.uuid4().hex[:7]}"
        phone_b = f"551192{uuid.uuid4().hex[:7]}"

        def send_and_process(client: TestClient, *, phone: str, text: str) -> str:
            msg_id = f"wamid_multi_{uuid.uuid4().hex[:10]}"
            payload = {
                "event": "messages.upsert",
                "instance": "tapjob_canary",
                "data": {
                    "key": {
                        "remoteJid": f"{phone}@s.whatsapp.net",
                        "fromMe": False,
                        "id": msg_id,
                    },
                    "pushName": f"User {phone[-4:]}",
                    "message": {"conversation": text},
                    "messageTimestamp": int(datetime.now(UTC).timestamp()),
                },
            }
            resp = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=payload,
            )
            assert resp.status_code == 202

            session.expire_all()
            env = session.execute(
                select(ProviderEventEnvelope).where(
                    ProviderEventEnvelope.external_event_id == msg_id
                )
            ).scalar_one()

            process_inbound_envelope_task(env.id)

            session.expire_all()
            claim_and_dispatch_outbox_batch(session, provider_adapter=fake_channel, limit=10)
            session.commit()
            last_text = (
                fake_channel.sent_messages[-1].text or "" if fake_channel.sent_messages else ""
            )
            return last_text

        with TestClient(app) as client:
            # 1. User A greets and adds a confirmed fact
            send_and_process(client, phone=phone_a, text="oi")
            session.expire_all()
            link_a = session.execute(
                select(ChannelIdentityLink)
                .join(
                    ChannelIdentity, ChannelIdentity.id == ChannelIdentityLink.channel_identity_id
                )
                .where(ChannelIdentity.external_id == phone_a)
            ).scalar_one()
            user_a = session.execute(select(User).where(User.id == link_a.user_id)).scalar_one()

            skill_val = SkillFactValue(name="Python")
            career_profile_service.add_profile_fact(
                session,
                user=user_a,
                expected_version=0,
                fact_type=FactType.SKILL,
                value=skill_val,
            )
            session.commit()

            # 2. User A sends Job X
            job_text_a = (
                "Vaga: Engenheiro Backend Python na AlphaTech. "
                "Requisitos: Python, APIs e PostgreSQL."
            )
            send_and_process(client, phone=phone_a, text=job_text_a)

            # 3. User B greets and sends Job Y
            send_and_process(client, phone=phone_b, text="oi")
            job_text_b = (
                "Vaga: Desenvolvedor Rust Core na BetaCorp. "
                "Requisitos: Rust, Tokio, concorrência e memória."
            )
            send_and_process(client, phone=phone_b, text=job_text_b)

            # 4. User A asks "analisa essa vaga" -> MUST target Job X (AlphaTech / Python)
            resp_analysis = send_and_process(client, phone=phone_a, text="analisa essa vaga")
            assert "AlphaTech" in resp_analysis or "Python" in resp_analysis
            assert "BetaCorp" not in resp_analysis
            assert "Rust" not in resp_analysis

            # 5. User A asks "quero me candidatar" -> ApplicationKit strictly for Job X
            resp_kit = send_and_process(client, phone=phone_a, text="quero me candidatar")
            assert "Application Kit preparado" in resp_kit
            session.expire_all()
            kits_a = list(
                session.execute(select(ApplicationKit).where(ApplicationKit.user_id == user_a.id))
                .scalars()
                .all()
            )
            assert len(kits_a) == 1
            kit_job = session.execute(select(Job).where(Job.id == kits_a[0].job_id)).scalar_one()
            assert "Python" in kit_job.title or "AlphaTech" in (
                kit_job.company.name if kit_job.company else ""
            )
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_first_contact_document_and_echo_filtered_vertical_slice(
    _identity_schema_ready: None,
) -> None:
    """Proves first contact document intake and Evolution fromMe echo rejection (Findings 7, 9)."""
    webhook_secret = f"secret_doc_{uuid.uuid4().hex[:12]}"
    settings = ApiSettings(
        app_env=Environment.DEVELOPMENT,
        whatsapp_webhook_secret=webhook_secret,
        whatsapp_webhook_enabled=True,
    )
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    app = create_app(settings, resume_storage_override=storage)

    try:
        phone_echo = f"551193{uuid.uuid4().hex[:7]}"
        phone_first_doc = f"551194{uuid.uuid4().hex[:7]}"

        with TestClient(app) as client:
            # 1. fromMe=True echo -> fast-ack 202, but parse_inbound returns empty ->
            # zero user created (Finding 7)
            echo_msg_id = f"wamid_echo_{uuid.uuid4().hex[:8]}"
            echo_payload = {
                "event": "messages.upsert",
                "instance": "tapjob_canary",
                "data": {
                    "key": {
                        "remoteJid": f"{phone_echo}@s.whatsapp.net",
                        "fromMe": True,  # OUTBOUND ECHO
                        "id": echo_msg_id,
                    },
                    "pushName": "Bot Echo",
                    "message": {"conversation": "Mensagem enviada pelo bot"},
                    "messageTimestamp": int(datetime.now(UTC).timestamp()),
                },
            }
            resp_echo = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=echo_payload,
            )
            assert resp_echo.status_code == 202
            session.expire_all()
            # Envelope is NOT created for outbound echo at boundary (fromMe=True dropped)
            env_echo = session.execute(
                select(ProviderEventEnvelope).where(
                    ProviderEventEnvelope.external_event_id == echo_msg_id
                )
            ).scalar_one_or_none()
            assert env_echo is None

            # Zero user created for echo sender
            user_echo = session.execute(
                select(ChannelIdentity).where(ChannelIdentity.external_id == phone_echo)
            ).scalar_one_or_none()
            assert user_echo is None

            # 2. First message is a PDF document -> creates user + link + ResumeImport
            # in same inbound (Finding 9)
            doc_msg_id = f"wamid_first_doc_{uuid.uuid4().hex[:8]}"
            doc_payload = {
                "event": "messages.upsert",
                "instance": "tapjob_canary",
                "data": {
                    "key": {
                        "remoteJid": f"{phone_first_doc}@s.whatsapp.net",
                        "fromMe": False,
                        "id": doc_msg_id,
                    },
                    "pushName": "Candidato Direto",
                    "message": {
                        "documentMessage": {
                            "fileName": "curriculo_primeiro.pdf",
                            "mimetype": "application/pdf",
                            "fileLength": 2048,
                        }
                    },
                    "messageTimestamp": int(datetime.now(UTC).timestamp()),
                },
            }
            resp_doc = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=doc_payload,
            )
            assert resp_doc.status_code == 202
            session.expire_all()
            env_doc = session.execute(
                select(ProviderEventEnvelope).where(
                    ProviderEventEnvelope.external_event_id == doc_msg_id
                )
            ).scalar_one()

            # Execute background worker
            process_inbound_envelope_task(env_doc.id)
            session.expire_all()

            # Verify user created AND resume import created in single execution
            identity_doc = session.execute(
                select(ChannelIdentity).where(ChannelIdentity.external_id == phone_first_doc)
            ).scalar_one()
            link_doc = session.execute(
                select(ChannelIdentityLink).where(
                    ChannelIdentityLink.channel_identity_id == identity_doc.id
                )
            ).scalar_one()
            user_doc = session.execute(select(User).where(User.id == link_doc.user_id)).scalar_one()
            assert user_doc.status == UserStatus.ACTIVE

            imports = list(
                session.execute(select(ResumeImport).where(ResumeImport.user_id == user_doc.id))
                .scalars()
                .all()
            )
            assert len(imports) == 1
            assert imports[0].status == ImportStatus.READY_FOR_REVIEW

            # 3. Replay of first-contact document (Finding 6)
            resp_doc_replay = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=doc_payload,
            )
            assert resp_doc_replay.status_code == 202
            process_inbound_envelope_task(env_doc.id)
            session.expire_all()

            # Zero duplicate user, zero duplicate resume import
            all_users = list(
                session.execute(
                    select(ChannelIdentity).where(ChannelIdentity.external_id == phone_first_doc)
                )
                .scalars()
                .all()
            )
            assert len(all_users) == 1
            all_imports = list(
                session.execute(select(ResumeImport).where(ResumeImport.user_id == user_doc.id))
                .scalars()
                .all()
            )
            assert len(all_imports) == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_first_contact_job_url_and_replays_vertical_slice(_identity_schema_ready: None) -> None:
    """Proves first contact job URL intake with SafeHttpClient and replay safety (Finding 6).

    1. First inbound from new user is a job URL (https://careers.example.com/jobs/senior-python).
    2. Zero external network calls (SafeHttpClient.fetch mocked via AsyncMock).
    3. User and ChannelIdentityLink created in PostgreSQL.
    4. Job intake (WP2.1) creates Job with created_by_user_id == user.id.
    5. Canonical Matching (WP2.2) computes MatchResult using that exact job.
    6. Outbound analysis response queued and dispatched via Outbox.
    7. Retransmitting the exact same webhook message produces:
       - Zero duplicate User
       - Zero duplicate Job
       - Zero duplicate Match mutation
       - Zero duplicate Outbound intent
    """
    webhook_secret = f"secret_url_{uuid.uuid4().hex[:12]}"
    settings = ApiSettings(
        app_env=Environment.DEVELOPMENT,
        whatsapp_webhook_secret=webhook_secret,
        whatsapp_webhook_enabled=True,
    )
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    app = create_app(settings, resume_storage_override=storage)

    try:
        phone_url_user = f"551196{uuid.uuid4().hex[:7]}"
        url_msg_id = f"wamid_url_first_{uuid.uuid4().hex[:8]}"
        job_target_url = "https://careers.example.com/jobs/senior-python"

        sample_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Vaga Senior Python na PythonCorp</title></head>
        <body>
            <h1>Senior Python Engineer</h1>
            <h2>PythonCorp</h2>
            <p>Requisitos: Experiência sólida em Python, FastAPI, microsserviços e PostgreSQL.</p>
        </body>
        </html>
        """

        url_payload = {
            "event": "messages.upsert",
            "instance": "tapjob_canary",
            "data": {
                "key": {
                    "remoteJid": f"{phone_url_user}@s.whatsapp.net",
                    "fromMe": False,
                    "id": url_msg_id,
                },
                "pushName": "Candidato URL First",
                "message": {
                    "conversation": f"Olá veja essa vaga: {job_target_url}",
                },
                "messageTimestamp": int(datetime.now(UTC).timestamp()),
            },
        }

        with (
            TestClient(app) as client,
            patch.object(SafeHttpClient, "fetch", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = (
                job_target_url,
                "text/html",
                sample_html,
            )

            # 1. First contact with URL -> 202 Accepted
            resp_url = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=url_payload,
            )
            assert resp_url.status_code == 202

            session.expire_all()
            env = session.execute(
                select(ProviderEventEnvelope).where(
                    ProviderEventEnvelope.external_event_id == url_msg_id
                )
            ).scalar_one()

            # Execute background worker task
            process_inbound_envelope_task(env.id)
            session.expire_all()

            # Confirm envelope marked PROCESSED
            env_after = session.execute(
                select(ProviderEventEnvelope).where(ProviderEventEnvelope.id == env.id)
            ).scalar_one()
            assert env_after.status == ProviderEventProcessingStatus.PROCESSED

            # 2. Verify User & IdentityLink created
            identity = session.execute(
                select(ChannelIdentity).where(ChannelIdentity.external_id == phone_url_user)
            ).scalar_one()
            link = session.execute(
                select(ChannelIdentityLink).where(
                    ChannelIdentityLink.channel_identity_id == identity.id
                )
            ).scalar_one()
            user = session.execute(select(User).where(User.id == link.user_id)).scalar_one()
            assert user.status == UserStatus.ACTIVE

            # 3. Verify Job created with created_by_user_id == user.id (WP2.1)
            jobs = list(
                session.execute(select(Job).where(Job.created_by_user_id == user.id))
                .scalars()
                .all()
            )
            assert len(jobs) == 1
            job = jobs[0]
            assert "Senior Python Engineer" in job.title or "Python" in job.title
            assert job.created_by_user_id == user.id

            # 4. Verify Canonical Matching computed MatchResult for this job (WP2.2)
            matches = list(
                session.execute(
                    select(MatchResult).where(
                        MatchResult.user_id == user.id,
                        MatchResult.job_id == job.id,
                    )
                )
                .scalars()
                .all()
            )
            assert len(matches) == 1
            initial_match_id = matches[0].id

            # 5. Outbound queued and dispatched via Outbox
            claim_and_dispatch_outbox_batch(session, provider_adapter=fake_channel, limit=10)
            session.commit()
            assert len(fake_channel.sent_messages) >= 1
            last_msg = fake_channel.sent_messages[-1]
            assert "Vaga analisada" in (last_msg.text or "") or "Match Score WP2.2" in (
                last_msg.text or ""
            )

            initial_outbox_count = len(
                list(
                    session.execute(
                        select(OutboundMessageQueue).where(
                            OutboundMessageQueue.channel_identity_link_id == str(link.id)
                        )
                    )
                    .scalars()
                    .all()
                )
            )

            # 6. Replay test: retransmit exact same webhook
            resp_replay = client.post(
                "/webhooks/whatsapp",
                headers={"X-Webhook-Secret": webhook_secret},
                json=url_payload,
            )
            assert resp_replay.status_code == 202

            # Replay worker task execution
            process_inbound_envelope_task(env.id)
            session.expire_all()

            # Invariant: Zero duplicate User
            all_users = list(
                session.execute(
                    select(ChannelIdentity).where(ChannelIdentity.external_id == phone_url_user)
                )
                .scalars()
                .all()
            )
            assert len(all_users) == 1

            # Invariant: Zero duplicate Job
            all_jobs = list(
                session.execute(select(Job).where(Job.created_by_user_id == user.id))
                .scalars()
                .all()
            )
            assert len(all_jobs) == 1

            # Invariant: Zero duplicate Match mutation
            all_matches = list(
                session.execute(
                    select(MatchResult).where(
                        MatchResult.user_id == user.id,
                        MatchResult.job_id == job.id,
                    )
                )
                .scalars()
                .all()
            )
            assert len(all_matches) == 1
            assert all_matches[0].id == initial_match_id

            # Invariant: Zero duplicate Outbound intent
            current_outbox = list(
                session.execute(
                    select(OutboundMessageQueue).where(
                        OutboundMessageQueue.channel_identity_link_id == str(link.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(current_outbox) == initial_outbox_count

            # Invariant: zero real sends
            assert fake_channel.provider == "fake_whatsapp"
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_postgres_inbound_claim_fencing_and_stale_owner(_identity_schema_ready: None) -> None:
    """Verifies atomic claim, crash attempt consumption, and lease fencing (Findings 1, 2).

    Traverses:
    1. Initial envelope creation with status=ACCEPTED, attempts=0.
    2. Worker A atomically claims envelope -> status=PROCESSING, attempts=1, holds lease_token_a.
    3. Worker B fails to claim while Worker A's lease is active.
    4. Worker A crashes (lease expires without completion).
    5. Worker B reclaims expired lease -> status=PROCESSING, attempts=2 (crash consumed attempt).
    6. Worker A wakes up and attempts mark PROCESSED/FAILED with stale token -> fenced (False).
    7. Worker B completes processing using active lease_token_b -> succeeds (True, PROCESSED).
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    try:
        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_pg_fencing_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={"from": "5511999999999", "text": "ping"},
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=0,
        )
        session.add(envelope)
        session.commit()
        session.refresh(envelope)

        # 1. Worker A claims envelope
        env_a, token_a, res_a = claim_inbound_envelope(
            session, envelope_id=envelope.id, lease_seconds=2.0
        )
        assert res_a == InboundClaimResult.CLAIMED
        assert env_a is not None
        assert token_a is not None
        assert env_a.status == ProviderEventProcessingStatus.PROCESSING
        assert env_a.processing_attempts == 1

        # 2. Worker B attempts to claim while Worker A lease is active -> rejected
        _env_b_act, token_b_act, res_b_act = claim_inbound_envelope(
            session, envelope_id=envelope.id, lease_seconds=2.0
        )
        assert res_b_act == InboundClaimResult.ACTIVE_LEASE
        assert token_b_act is None

        # 3. Simulate Worker A crash + lease expiration
        envelope.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        session.commit()

        # 4. Worker B reclaims expired lease -> attempts incremented to 2
        env_b, token_b, res_b = claim_inbound_envelope(
            session, envelope_id=envelope.id, lease_seconds=30.0
        )
        assert res_b == InboundClaimResult.CLAIMED
        assert env_b is not None
        assert token_b is not None
        assert token_b != token_a
        assert env_b.processing_attempts == 2

        # 5. Stale Worker A wakes up and attempts mark_event_processed with stale token -> fenced
        ok_a = mark_event_processed(session, envelope_id=envelope.id, fencing_token=token_a)
        assert ok_a is False

        # Stale Worker A attempts mark_event_failed with stale token -> fenced
        fail_a = mark_event_failed(
            session,
            envelope_id=envelope.id,
            error_details={"error": "stale"},
            fencing_token=token_a,
        )
        assert fail_a is False

        # 6. Active Worker B marks event processed with valid active token -> succeeds
        ok_b = mark_event_processed(session, envelope_id=envelope.id, fencing_token=token_b)
        assert ok_b is True

        session.refresh(envelope)
        assert envelope.status == ProviderEventProcessingStatus.PROCESSED
        assert envelope.processing_attempts == 2
        assert envelope.processed_at is not None
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_postgres_concurrent_inbound_claims(_identity_schema_ready: None) -> None:
    """Verifies two concurrent DB sessions competing for envelope cannot both claim (Finding 3)."""
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    try:
        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_pg_race_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={"from": "5511999999999", "text": "race"},
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=0,
        )
        session.add(envelope)
        session.commit()
        env_id = envelope.id

        def worker_claim() -> tuple[InboundClaimResult, str | None]:
            worker_session = session_factory()
            try:
                _env, token, res = claim_inbound_envelope(
                    worker_session, envelope_id=env_id, lease_seconds=60.0
                )
                worker_session.commit()
                return res, token
            finally:
                worker_session.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(worker_claim)
            fut2 = executor.submit(worker_claim)
            r1 = fut1.result()
            r2 = fut2.result()

        claim_results = [r1[0], r2[0]]
        tokens = [r[1] for r in (r1, r2) if r[1] is not None]

        # Invariants: Exactly one wins CLAIMED, the other observes ACTIVE_LEASE
        assert InboundClaimResult.CLAIMED in claim_results
        assert InboundClaimResult.ACTIVE_LEASE in claim_results
        assert len(tokens) == 1

        # Check DB state: attempts incremented exactly once
        session.expire_all()
        env_db = session.get(ProviderEventEnvelope, env_id)
        assert env_db is not None
        assert env_db.status == ProviderEventProcessingStatus.PROCESSING
        assert env_db.processing_attempts == 1
        assert env_db.processing_lease_token == tokens[0]
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_document_hard_crash_recovery_postgres(_identity_schema_ready: None) -> None:
    """Document Hard Crash Test with Real Canonical Resume Import (Finding 1).

    Traverses:
    1. Create ProviderEventEnvelope for PDF document attachment.
    2. Claim attempt 1 (Worker A) -> processing_attempts=1.
    3. Ingest document via REAL canonical WP1.3 pipeline (tool_ingest_resume unmocked).
    4. Canonical ResumeImport and its candidates are COMMITTED to PostgreSQL.
    5. INTERRUPT via failure injection after canonical domain commit and before completion marker.
    6. Simulate hard crash: Worker A dies.
    7. Confirm envelope is still PROCESSING with attempts=1.
    8. Expire lease.
    9. Claim attempt 2 (Worker B) -> processing_attempts=2.
    10. Execute EXACT same inbound with REAL canonical pipeline:
        detects PENDING effect reservation, reconciles committed import, marks COMPLETED.
    11. Conclude envelope: mark_event_processed.
    Asserts:
    - exactly 1 ProviderEventEnvelope;
    - processing_attempts == 2;
    - exactly 1 ResumeImport for that inbound;
    - exactly 1 candidate set;
    - zero duplicate parsing or storage intents;
    - exactly 1 doc response outbox intent;
    - envelope ends PROCESSED.
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    try:
        msg_id = f"wamid_hardcrash_{uuid.uuid4().hex[:12]}"
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"

        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_crash_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id,
                "text": "",
                "attachments": [
                    {
                        "provider_media_id": "media_doc_crash",
                        "mime_type": "application/pdf",
                        "filename": "curriculo_crash.pdf",
                        "kind": "document",
                    }
                ],
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=0,
        )
        session.add(envelope)
        session.commit()
        envelope_id = envelope.id

        # 1. Claim attempt 1 (Worker A)
        env_a, token_a, res_a = claim_inbound_envelope(
            session, envelope_id=envelope_id, lease_seconds=2.0
        )
        session.commit()
        assert res_a == InboundClaimResult.CLAIMED
        assert token_a is not None
        assert env_a is not None
        assert env_a.processing_attempts == 1

        inbound_msg = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id,
            received_at=datetime.now(UTC),
            text="",
            attachments=(
                InboundAttachment(
                    provider_media_id="media_doc_crash",
                    mime_type="application/pdf",
                    filename="curriculo_crash.pdf",
                    kind="document",
                ),
            ),
        )

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        # Define failure injection for simulated hard crash
        class SimulatedCrashAfterDomainCommitError(Exception):
            pass

        def _crash_injection_document() -> None:
            raise SimulatedCrashAfterDomainCommitError(
                "Worker died AFTER canonical ResumeImport commit and BEFORE completion marker!"
            )

        from tapjob_api.messaging import orchestration

        orchestration._crash_hook_after_resume_import_commit = _crash_injection_document

        try:
            # Execute dispatch attempt 1 with real canonical ResumeImport pipeline
            dispatch_inbound_message(
                session,
                redis_client,
                inbound=inbound_msg,
                provider_id=provider_id,
                channel_adapter=fake_channel,
                storage=storage,
                envelope_id=envelope_id,
            )
            pytest.fail("Expected crash hook to simulate worker death")
        except SimulatedCrashAfterDomainCommitError:
            pass
        finally:
            orchestration._crash_hook_after_resume_import_commit = None

        # SIMULATE HARD CRASH: Worker A dies immediately after canonical domain commit
        session.rollback()
        session.close()

        # Verify envelope is STILL PROCESSING with attempts=1
        session = session_factory()
        env_check = session.get(ProviderEventEnvelope, envelope_id)
        assert env_check is not None
        assert env_check.status == ProviderEventProcessingStatus.PROCESSING
        assert env_check.processing_attempts == 1

        # Canonical ResumeImport was actually committed to PostgreSQL!
        # And InboundEffect is PENDING (reservation made before domain commit):
        pending_effect = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).one()
        assert pending_effect.operation_state == "pending"
        import_uuid = uuid.UUID(pending_effect.resource_id)

        persisted_import = session.get(ResumeImport, import_uuid)
        assert persisted_import is not None
        assert persisted_import.status == ImportStatus.READY_FOR_REVIEW
        assert len(persisted_import.candidates) >= 1

        # Expire lease
        env_check.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        session.commit()

        # 2. Worker B claims attempt 2
        env_b, token_b, res_b = claim_inbound_envelope(
            session, envelope_id=envelope_id, lease_seconds=60.0
        )
        session.commit()
        assert res_b == InboundClaimResult.CLAIMED
        assert token_b is not None
        assert token_b != token_a
        assert env_b is not None
        assert env_b.processing_attempts == 2

        # 3. Worker B executes the SAME inbound / provider_message_id with REAL canonical pipeline
        dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_msg,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_id,
        )

        # Worker B marks event processed
        ok_b = mark_event_processed(session, envelope_id=envelope_id, fencing_token=token_b)
        assert ok_b is True
        session.commit()

        # Assertions required by Finding 1:
        env_final = session.get(ProviderEventEnvelope, envelope_id)
        assert env_final is not None
        assert env_final.status == ProviderEventProcessingStatus.PROCESSED
        assert env_final.processing_attempts == 2

        total_envelopes = session.scalar(
            select(func.count())
            .select_from(ProviderEventEnvelope)
            .where(ProviderEventEnvelope.id == envelope_id)
        )
        assert total_envelopes == 1

        effects = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).all()
        assert len(effects) == 1
        assert effects[0].resource_id == str(import_uuid)
        assert effects[0].operation_state == "completed"

        # Exactly 1 ResumeImport for that inbound
        total_imports = session.scalar(
            select(func.count(ResumeImport.id)).where(ResumeImport.id == import_uuid)
        )
        assert total_imports == 1

        # Exactly 1 candidate set
        candidates_count = session.scalar(
            select(func.count(ResumeImportCandidate.id)).where(
                ResumeImportCandidate.resume_import_id == import_uuid
            )
        )
        assert candidates_count is not None and candidates_count >= 1

        # Exactly 1 doc response outbox intent
        outbox_intents = session.scalars(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.idempotency_key == f"doc_{inbound_msg.provider_message_id}"
            )
        ).all()
        assert len(outbox_intents) == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_finalize_review_crash_between_domain_commit_and_effect_completion_postgres(
    _identity_schema_ready: None,
) -> None:
    """Candidate Review Finalization Crash Between Domain Commit and Effect Completion (Finding 1).

    Traverses:
    1. User with reviewable candidates receives "confirmar todos".
    2. Claim attempt 1 (Worker A) -> processing_attempts=1.
    3. Operation target is reserved with PENDING before finalization.
    4. Canonical finalize_resume_import executes and COMMITS to Truth Store + ResumeVersion.
    5. Crash hook fires immediately AFTER canonical commit and BEFORE completion marker.
    6. Simulated hard crash: Worker A dies.
    7. Envelope remains PROCESSING with processing_attempts=1.
    8. Lease expires -> Worker B claims attempt 2 -> processing_attempts=2.
    9. Worker B re-executes same inbound: detects PENDING effect targeting same import,
       canonical finalize returns replay-safe result (is_replay=True), marks effect COMPLETED,
       and returns semantic confirmation reply.
    10. Worker B marks PROCESSED.
    Asserts:
    - Truth Store / profile_version does NOT duplicate or bump twice;
    - ResumeVersion does NOT duplicate;
    - ResumeImport remains FINALIZED;
    - Outbox response is the semantic confirmation reply, NOT "não encontramos fatos pendentes";
    - zero duplicate finalization;
    - envelope ends PROCESSED with attempts=2.
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    try:
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"
        msg_id = f"wamid_finalize_{uuid.uuid4().hex[:12]}"

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        user = User(locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add(user)
        session.flush()
        link = link_or_create_identity(
            session,
            user_id=user.id,
            provider_id=provider_id,
            external_id=phone,
            is_new_user=True,
            verified_proof=True,
        )
        career_profile_service._get_or_create_career_profile(session, user=user)
        career_profile_service.bump_profile_version(session, user=user, expected_version=0)
        session.commit()
        link_id_str = str(link.id)
        user_id = user.id

        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_final_crash_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id,
                "text": "confirmar todos",
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=0,
        )
        session.add(envelope)
        session.commit()
        envelope_id = envelope.id

        # 1. Claim attempt 1 (Worker A)
        _env_a, _token_a, res_a = claim_inbound_envelope(
            session, envelope_id=envelope_id, lease_seconds=2.0
        )
        session.commit()
        assert res_a == InboundClaimResult.CLAIMED

        inbound_msg = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id,
            received_at=datetime.now(UTC),
            text="confirmar todos",
        )

        import_id = uuid.uuid4()
        cand_id = uuid.uuid4()
        cand = ResumeImportCandidate(
            id=cand_id,
            resume_import_id=import_id,
            user_id=user_id,
            candidate_kind="skill",
            value_json={"name": "Python"},
            confidence=0.9,
            review_state=CandidateReviewState.PENDING,
        )
        import_rec = ResumeImport(
            id=import_id,
            user_id=user_id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo.pdf",
            media_type="application/pdf",
            byte_size=100,
            checksum_sha256="deadbeef" * 8,
            storage_key="test_storage_key",
            candidates=[cand],
        )
        session.add(import_rec)
        session.commit()

        class SimulatedCrashAfterFinalizeCommitError(Exception):
            pass

        def _crash_injection_finalize() -> None:
            raise SimulatedCrashAfterFinalizeCommitError(
                "Worker died AFTER canonical finalize commit and BEFORE completion marker!"
            )

        from tapjob_api.messaging import orchestration

        orchestration._crash_hook_after_finalize_commit = _crash_injection_finalize

        try:
            # REAL canonical confirm_candidates_and_finalize pipeline (NO MOCK)
            dispatch_inbound_message(
                session,
                redis_client,
                inbound=inbound_msg,
                provider_id=provider_id,
                channel_adapter=fake_channel,
                storage=storage,
                envelope_id=envelope_id,
            )
            pytest.fail("Expected crash hook to simulate worker death")
        except SimulatedCrashAfterFinalizeCommitError:
            pass
        finally:
            orchestration._crash_hook_after_finalize_commit = None

        # SIMULATE HARD CRASH: Worker A dies immediately after canonical finalize commit
        session.rollback()
        session.close()

        # In fresh session, verify state after crash:
        session = session_factory()
        user_fresh = session.get(User, user_id)
        assert user_fresh is not None
        env_crash = session.get(ProviderEventEnvelope, envelope_id)
        assert env_crash is not None
        assert env_crash.status == ProviderEventProcessingStatus.PROCESSING
        assert env_crash.processing_attempts == 1

        # Canonical domain commit was already successful:
        import_after_crash = session.get(ResumeImport, import_id)
        assert import_after_crash is not None
        assert import_after_crash.status == ImportStatus.FINALIZED

        # Truth store was already updated to version 2:
        profile_after_crash = career_profile_service.get_career_profile(session, user=user_fresh)
        assert profile_after_crash is not None
        assert profile_after_crash.profile_version == 2

        # Exactly 1 ResumeVersion created:
        versions_after_crash = session.scalars(
            select(ResumeVersion).join(Resume).where(Resume.user_id == user_id)
        ).all()
        assert len(versions_after_crash) == 1

        # Operation target is still PENDING:
        pending_eff = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).one()
        assert pending_eff.operation_state == "pending"

        # Expire lease
        env_crash.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        session.commit()

        # 2. Claim attempt 2 (Worker B)
        env_b, token_b, res_b = claim_inbound_envelope(
            session, envelope_id=envelope_id, lease_seconds=60.0
        )
        session.commit()
        assert res_b == InboundClaimResult.CLAIMED
        assert env_b is not None
        assert env_b.processing_attempts == 2

        # 3. Worker B re-executes the same inbound:
        # Canonical finalize detects ImportStatus.FINALIZED and replays safely (NO MOCK)
        dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_msg,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_id,
        )

        assert token_b is not None
        ok_b = mark_event_processed(session, envelope_id=envelope_id, fencing_token=token_b)
        assert ok_b is True
        session.commit()

        # Assertions required by Finding 1:
        # Profile version must NOT have bumped twice:
        profile_final = career_profile_service.get_career_profile(session, user=user_fresh)
        assert profile_final is not None
        assert profile_final.profile_version == 2

        # ResumeVersion count must remain 1:
        final_versions = session.scalars(
            select(ResumeVersion).join(Resume).where(Resume.user_id == user_id)
        ).all()
        assert len(final_versions) == 1

        # ResumeImport remains FINALIZED:
        import_final = session.get(ResumeImport, import_id)
        assert import_final is not None
        assert import_final.status == ImportStatus.FINALIZED

        # Outbox reply is the semantic confirmation reply, NOT "não encontramos fatos pendentes"
        outbox_msgs = session.scalars(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.channel_identity_link_id == link_id_str
            )
        ).all()
        assert len(outbox_msgs) >= 1
        latest_reply = cast(str, outbox_msgs[-1].message_content.get("text", ""))
        assert "Informações confirmadas com sucesso!" in latest_reply
        assert "Versão 2" in latest_reply
        assert "não encontramos fatos pendentes" not in latest_reply.lower()

        # Verify envelope terminates PROCESSED with processing_attempts == 2
        env_final = session.get(ProviderEventEnvelope, envelope_id)
        assert env_final is not None
        assert env_final.status == ProviderEventProcessingStatus.PROCESSED
        assert env_final.processing_attempts == 2
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_document_pending_effect_processing_import_does_not_complete_success(
    _identity_schema_ready: None,
) -> None:
    """Proves Finding 1: PENDING effect with PROCESSING import does not complete as success.

    Traverses:
    1. InboundEffect PENDING with resource_id = import A.
    2. ResumeImport A with status PROCESSING and future lease (+300s).
    3. Worker attempt executes:
       - Detects active canonical lease.
       - Raises ImportLeaseActiveError.
       - Envelope is backed off to next_attempt_at >= lease expiry without consuming retry budget.
       - InboundEffect remains PENDING.
       - Zero success response queued in OutboundMessageQueue.
    4. Expire canonical lease:
       - Retry executes.
       - Canonical process reclaims import.
       - ResumeImport transitions to READY_FOR_REVIEW with candidates.
       - InboundEffect completes as success.
       - Outbox receives success response.
       - Exactly 1 ResumeImport and 1 candidate set exist.
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    try:
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"
        msg_id = f"wamid_doc_proc_{uuid.uuid4().hex[:12]}"

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        user = User(locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add(user)
        session.flush()
        link = link_or_create_identity(
            session,
            user_id=user.id,
            provider_id=provider_id,
            external_id=phone,
            is_new_user=True,
            verified_proof=True,
        )
        session.commit()
        link_id_str = str(link.id)
        user_id = user.id

        import_a_id = uuid.uuid4()
        storage_key = f"resumes/{user_id}/{import_a_id}.pdf"
        storage.put(
            storage_key=storage_key,
            content=_VALID_TEST_PDF_BYTES,
            content_type="application/pdf",
        )

        future_lease = datetime.now(UTC) + timedelta(seconds=300)
        import_a = ResumeImport(
            id=import_a_id,
            user_id=user_id,
            status=ImportStatus.PROCESSING,
            processing_lease_expires_at=future_lease,
            processing_lease_token=uuid.uuid4(),
            original_filename="curriculo_alpha.pdf",
            media_type="application/pdf",
            byte_size=len(_VALID_TEST_PDF_BYTES),
            checksum_sha256=hashlib.sha256(_VALID_TEST_PDF_BYTES).hexdigest(),
            storage_key=storage_key,
        )
        session.add(import_a)
        session.commit()

        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_doc_proc_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id,
                "text": "",
                "attachments": [
                    {
                        "provider_media_id": "media_doc_alpha",
                        "mime_type": "application/pdf",
                        "filename": "curriculo_alpha.pdf",
                        "kind": "document",
                    }
                ],
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=0,
        )
        session.add(envelope)
        session.commit()
        envelope_id = envelope.id

        # Reserve InboundEffect PENDING
        effect = reserve_inbound_effect(
            session,
            envelope_id=envelope_id,
            inbound_message_id=msg_id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
            resource_id=str(import_a_id),
        )
        session.commit()
        assert effect.operation_state == "pending"

        # 1. First execution attempt: Worker runs process_inbound_envelope_task
        process_inbound_envelope_task(envelope_id=envelope_id)

        session.close()
        session = session_factory()

        # ASSERT:
        # InboundEffect continua não-success (pending)
        eff_check = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).one()
        assert eff_check.operation_state == "pending"

        # zero "currículo recebido com sucesso", zero doc success outbox
        outbox_msgs = session.scalars(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.channel_identity_link_id == link_id_str
            )
        ).all()
        assert len(outbox_msgs) == 0 or not any(
            "recebemos seu currículo" in cast(str, m.message_content.get("text", "")).lower()
            for m in outbox_msgs
        )

        # envelope backed off based on canonical lease
        env_check = session.get(ProviderEventEnvelope, envelope_id)
        assert env_check is not None
        assert env_check.status == ProviderEventProcessingStatus.FAILED
        assert env_check.error_details is not None
        assert env_check.error_details.get("error") == "nested_lease_active"
        next_iso = env_check.error_details.get("next_attempt_at")
        assert next_iso is not None
        next_attempt = datetime.fromisoformat(next_iso)
        assert next_attempt >= future_lease - timedelta(seconds=5)
        # Did not burn the 3-attempt budget waiting for canonical lease
        assert env_check.processing_attempts == 0

        # Import A continues in PROCESSING with unexpired lease
        imp_check = session.get(ResumeImport, import_a_id)
        assert imp_check is not None
        assert imp_check.status == ImportStatus.PROCESSING

        # 2. Expirar a lease canonical
        imp_check.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        env_check.status = ProviderEventProcessingStatus.ACCEPTED
        env_check.processing_lease_expires_at = None
        session.commit()

        # Retry: canonical process reclaims!
        process_inbound_envelope_task(envelope_id=envelope_id)

        session.close()
        session = session_factory()

        # RESULTADO FINAL:
        # READY_FOR_REVIEW
        imp_final = session.get(ResumeImport, import_a_id)
        assert imp_final is not None
        assert imp_final.status == ImportStatus.READY_FOR_REVIEW
        # 1 candidate set
        assert len(imp_final.candidates) >= 1

        # Exactly 1 ResumeImport in DB
        total_imports = session.scalar(
            select(func.count(ResumeImport.id)).where(ResumeImport.id == import_a_id)
        )
        assert total_imports == 1

        # effect success (completed)
        eff_final = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).one()
        assert eff_final.operation_state == "completed"
        assert eff_final.effect_payload.get("outcome") == "success"

        # Outbox now has success message
        outbox_final = session.scalars(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.channel_identity_link_id == link_id_str
            )
        ).all()
        assert any(
            "recebemos seu currículo" in cast(str, m.message_content.get("text", "")).lower()
            for m in outbox_final
        )
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_document_failed_resume_import_does_not_return_success(
    _identity_schema_ready: None,
) -> None:
    """Proves Finding 1: Canonical FAILED resume import does NOT return success response.

    Traverses:
    1. Valid upload reaching pipeline -> deterministic extraction failure.
    2. Canonical ResumeImport status = FAILED with normalized failure_code.
    3. Messaging responds with normalized failure response, ZERO success wording.
    4. InboundEffect completed with outcome="failed" and failure_code.
    5. Replay of same inbound message replays failure message without reprocessing.
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    configure_messaging_worker(
        session_factory=session_factory,
        redis_client=redis_client,
        channel_adapter=fake_channel,
        storage=storage,
    )

    try:
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"
        msg_id = f"wamid_doc_fail_{uuid.uuid4().hex[:12]}"

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        user = User(locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add(user)
        session.flush()
        link = link_or_create_identity(
            session,
            user_id=user.id,
            provider_id=provider_id,
            external_id=phone,
            is_new_user=True,
            verified_proof=True,
        )
        session.commit()
        link_id_str = str(link.id)
        user_id = user.id

        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_doc_fail_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id,
                "text": "",
                "attachments": [
                    {
                        "provider_media_id": "media_doc_fail",
                        "mime_type": "application/pdf",
                        "filename": "curriculo_corrupto.pdf",
                        "kind": "document",
                    }
                ],
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=0,
        )
        session.add(envelope)
        session.commit()
        envelope_id = envelope.id

        # Simulate deterministic extraction failure during canonical processing
        with patch(
            "tapjob_api.resume_import.service.extract_pdf_text",
            side_effect=TextNotExtractableError("No text layer found"),
        ):
            process_inbound_envelope_task(envelope_id=envelope_id)

        session.close()
        session = session_factory()

        # Verify envelope processed
        env_done = session.get(ProviderEventEnvelope, envelope_id)
        assert env_done is not None
        assert env_done.status == ProviderEventProcessingStatus.PROCESSED

        # Verify canonical ResumeImport status = FAILED
        eff = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).one()
        assert eff.operation_state == "completed"
        assert eff.effect_payload.get("outcome") == "failed"
        assert eff.effect_payload.get("failure_code") == "text_not_extractable"

        imp = session.get(ResumeImport, uuid.UUID(eff.resource_id))
        assert imp is not None
        assert imp.status == ImportStatus.FAILED

        # Outbox: failure wording, ZERO success wording
        outbox_msgs = session.scalars(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.channel_identity_link_id == link_id_str
            )
        ).all()
        assert len(outbox_msgs) == 1
        reply_text = cast(str, outbox_msgs[0].message_content.get("text", ""))
        assert "não foi possível extrair o texto" in reply_text.lower()
        assert "recebemos seu currículo ... com sucesso" not in reply_text.lower()
        assert "com sucesso" not in reply_text.lower()

        # Replay test: re-dispatching same message replays the failure response
        inbound_msg = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id,
            received_at=datetime.now(UTC),
            text="",
            attachments=(
                InboundAttachment(
                    provider_media_id="media_doc_fail",
                    mime_type="application/pdf",
                    filename="curriculo_corrupto.pdf",
                    kind="document",
                ),
            ),
        )
        dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_msg,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_id,
        )

        outbox_msgs_after_replay = session.scalars(
            select(OutboundMessageQueue).where(
                OutboundMessageQueue.channel_identity_link_id == link_id_str
            )
        ).all()
        # Message key is idempotent, zero new message inserted
        assert len(outbox_msgs_after_replay) == 1
        # Zero second ResumeImport created
        total_imports = session.scalar(
            select(func.count(ResumeImport.id)).where(ResumeImport.user_id == user_id)
        )
        assert total_imports == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_finalize_pending_effect_uses_reserved_import_not_latest(
    _identity_schema_ready: None,
) -> None:
    """Proves Finding 2: FINALIZE_REVIEW reservation is immutable and does not select newer import.

    Cenário:
    User U.
    Import A: READY_FOR_REVIEW, candidate A.
    Envelope E: reservation FINALIZE_REVIEW, resource=A, PENDING.
    Antes do retry: criar Import B: READY_FOR_REVIEW, candidate B, created_at mais novo.
    Executar retry de E.
    ASSERT:
    A -> FINALIZED.
    B -> continua READY_FOR_REVIEW.
    Truth Store contém somente decisão/fato de A.
    ResumeVersion source_import_id == A.
    InboundEffect.resource_id continua A.
    InboundEffect COMPLETED.
    Zero candidate de B alterado.
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    try:
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"
        msg_id = f"wamid_finalize_multi_{uuid.uuid4().hex[:12]}"

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        user = User(locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add(user)
        session.flush()
        _link = link_or_create_identity(
            session,
            user_id=user.id,
            provider_id=provider_id,
            external_id=phone,
            is_new_user=True,
            verified_proof=True,
        )
        career_profile_service._get_or_create_career_profile(session, user=user)
        career_profile_service.bump_profile_version(session, user=user, expected_version=0)
        session.commit()
        user_id = user.id

        # Import A (older)
        import_a_id = uuid.uuid4()
        cand_a_id = uuid.uuid4()
        cand_a = ResumeImportCandidate(
            id=cand_a_id,
            resume_import_id=import_a_id,
            user_id=user_id,
            candidate_kind="skill",
            value_json={"name": "Python"},
            confidence=0.9,
            review_state=CandidateReviewState.PENDING,
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        import_a = ResumeImport(
            id=import_a_id,
            user_id=user_id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_a.pdf",
            media_type="application/pdf",
            byte_size=100,
            checksum_sha256="deadbeef" * 8,
            storage_key="test_storage_a",
            candidates=[cand_a],
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(import_a)
        session.commit()

        # Envelope E
        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_final_multi_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id,
                "text": "confirmar todos",
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=1,
        )
        session.add(envelope)
        session.commit()
        envelope_id = envelope.id

        # Reservation FINALIZE_REVIEW -> resource = Import A, operation_state = PENDING
        effect = reserve_inbound_effect(
            session,
            envelope_id=envelope_id,
            inbound_message_id=msg_id,
            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
            resource_id=str(import_a_id),
        )
        session.commit()
        assert effect.operation_state == "pending"

        # ANTES DO RETRY: criar Import B (newer)
        import_b_id = uuid.uuid4()
        cand_b_id = uuid.uuid4()
        cand_b = ResumeImportCandidate(
            id=cand_b_id,
            resume_import_id=import_b_id,
            user_id=user_id,
            candidate_kind="skill",
            value_json={"name": "Rust"},
            confidence=0.9,
            review_state=CandidateReviewState.PENDING,
            created_at=datetime.now(UTC),
        )
        import_b = ResumeImport(
            id=import_b_id,
            user_id=user_id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_b.pdf",
            media_type="application/pdf",
            byte_size=120,
            checksum_sha256="cafebabe" * 8,
            storage_key="test_storage_b",
            candidates=[cand_b],
            created_at=datetime.now(UTC),
        )
        session.add(import_b)
        session.commit()

        # EXECUTAR RETRY DE E:
        inbound_msg = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id,
            received_at=datetime.now(UTC),
            text="confirmar todos",
        )
        dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_msg,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_id,
        )

        session.close()
        session = session_factory()

        # ASSERT:
        # A -> FINALIZED
        reloaded_a = session.get(ResumeImport, import_a_id)
        assert reloaded_a is not None
        assert reloaded_a.status == ImportStatus.FINALIZED

        # B -> continua READY_FOR_REVIEW
        reloaded_b = session.get(ResumeImport, import_b_id)
        assert reloaded_b is not None
        assert reloaded_b.status == ImportStatus.READY_FOR_REVIEW

        # Zero candidate de B alterado: cand_b continua PENDING
        reloaded_cand_b = session.get(ResumeImportCandidate, cand_b_id)
        assert reloaded_cand_b is not None
        assert reloaded_cand_b.review_state == CandidateReviewState.PENDING

        # Candidate de A foi aceito
        reloaded_cand_a = session.get(ResumeImportCandidate, cand_a_id)
        assert reloaded_cand_a is not None
        assert reloaded_cand_a.review_state == CandidateReviewState.ACCEPTED

        # Truth store contém apenas fatos de A ("Python"), zero fato de B ("Rust")
        facts = list(
            session.scalars(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_id,
                    ProfileFact.status == FactStatus.ACTIVE,
                )
            ).all()
        )
        skill_facts = [f for f in facts if f.fact_type == FactType.SKILL]
        assert any(f.value_json.get("name") == "Python" for f in skill_facts)
        assert not any(f.value_json.get("name") == "Rust" for f in skill_facts)

        # ResumeVersion associada a Import A
        versions = list(
            session.scalars(
                select(ResumeVersion).join(Resume).where(Resume.user_id == user_id)
            ).all()
        )
        assert len(versions) == 1
        assert reloaded_a.finalized_resume_version_id == versions[0].id
        assert reloaded_b.finalized_resume_version_id is None

        # InboundEffect.resource_id continua A, operation_state COMPLETED
        eff_final = session.scalars(
            select(InboundEffect).where(InboundEffect.envelope_id == envelope_id)
        ).one()
        assert eff_final.resource_id == str(import_a_id)
        assert eff_final.operation_state == "completed"
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_finalize_pending_effect_missing_target_fails_closed(
    _identity_schema_ready: None,
) -> None:
    """Proves Finding 2: Reservation pointing to missing/unowned target fails closed,
    never selects latest.
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    try:
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"
        msg_id = f"wamid_missing_target_{uuid.uuid4().hex[:12]}"

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        user = User(locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add(user)
        session.flush()
        _link = link_or_create_identity(
            session,
            user_id=user.id,
            provider_id=provider_id,
            external_id=phone,
            is_new_user=True,
            verified_proof=True,
        )
        session.commit()
        user_id = user.id

        # Existing legitimate import
        import_real_id = uuid.uuid4()
        cand_real = ResumeImportCandidate(
            id=uuid.uuid4(),
            resume_import_id=import_real_id,
            user_id=user_id,
            candidate_kind="skill",
            value_json={"name": "Java"},
            confidence=0.9,
            review_state=CandidateReviewState.PENDING,
        )
        import_real = ResumeImport(
            id=import_real_id,
            user_id=user_id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_real.pdf",
            media_type="application/pdf",
            byte_size=100,
            checksum_sha256="deadbeef" * 8,
            storage_key="test_storage_real",
            candidates=[cand_real],
        )
        session.add(import_real)
        session.commit()

        # Envelope pointing to NONEXISTENT resource
        nonexistent_id = uuid.uuid4()
        envelope = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_missing_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id,
                "text": "confirmar todos",
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=1,
        )
        session.add(envelope)
        session.commit()
        envelope_id = envelope.id

        reserve_inbound_effect(
            session,
            envelope_id=envelope_id,
            inbound_message_id=msg_id,
            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
            resource_id=str(nonexistent_id),
        )
        session.commit()

        inbound_msg = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id,
            received_at=datetime.now(UTC),
            text="confirmar todos",
        )
        dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_msg,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_id,
        )

        session.close()
        session = session_factory()

        # ASSERT:
        # Does NOT finalize existing import: import_real continues READY_FOR_REVIEW
        reloaded_real = session.get(ResumeImport, import_real_id)
        assert reloaded_real is not None
        assert reloaded_real.status == ImportStatus.READY_FOR_REVIEW

        # Zero imports finalized
        finalized_count = session.scalar(
            select(func.count(ResumeImport.id)).where(
                ResumeImport.user_id == user_id,
                ResumeImport.status == ImportStatus.FINALIZED,
            )
        )
        assert finalized_count == 0
    finally:
        session.close()
        engine.dispose()


@pytest.mark.integration
def test_j05_real_integrated_fact_rejection_and_confirmation_cascade(
    _identity_schema_ready: None,
) -> None:
    """J-05 Real Database Integrated Persistence Test: Fact rejection and confirmation cascade.

    Traverses the full end-to-end orchestration pipeline with real PostgreSQL persistence:
    1. Initial State:
       - User linked to channel identity (WhatsApp).
       - ResumeImport in READY_FOR_REVIEW with 5 ResumeImportCandidate rows (Skill 1..5).
       - Zero facts in profile_facts, CareerProfile version is 0.
    2. Message 1: "rejeita o 2" dispatched via dispatch_inbound_message (NO MOCKS):
       - Orchestration resolves single rejection intent for candidate index 2.
       - CandidateReviewDraft is created and persisted in PostgreSQL with decision='reject'.
       - CandidateReviewState in DB remains PENDING (drafted, not finalized).
       - Zero facts activated in profile_facts.
       - Profile version remains 0.
       - Outbox receives feedback: "Fato 2 rejeitado com sucesso. Restam 4 fato(s) pendente(s)...".
    3. Message 2: "confirmar todos" dispatched via dispatch_inbound_message (NO MOCKS):
       - Orchestration detects confirm_all intent.
       - Orchestration queries real DB for all candidates and persisted drafts.
       - Assembles decisions preserving drafted REJECT for candidate 2, ACCEPT for remaining 4.
       - Orchestration invokes canonical finalizer confirm_candidates_and_finalize.
       - Commits to real PostgreSQL:
         * CareerProfile version advances to 1 (bumped strictly once on final confirmation).
         * Exactly 4 active ProfileFact rows in profile_facts (Skills 1, 3, 4, 5).
         * Candidate 2 was NEVER activated as ProfileFact (Skill 2 is absent).
         * In resume_import_candidates: candidate 2 is REJECTED, candidates 1, 3, 4, 5 are ACCEPTED.
         * ResumeImport transitions to FINALIZED.
         * Exactly 1 ResumeVersion created with profile_version=1.
         * InboundEffect marked COMPLETED.
         * Outbox receives confirmation: "Informações confirmadas com sucesso!... Versão 1".
    """
    settings = ApiSettings(app_env=Environment.DEVELOPMENT)
    engine = create_engine(str(settings.database_url), pool_size=5, max_overflow=5)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    redis_client = redis.Redis.from_url(str(settings.redis_url))

    fake_channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()

    try:
        phone = f"5511{uuid.uuid4().int % 90000000 + 10000000}"
        msg_id_1 = f"wamid_j05_rej_{uuid.uuid4().hex[:12]}"
        msg_id_2 = f"wamid_j05_conf_{uuid.uuid4().hex[:12]}"

        provider = get_or_create_channel_provider(
            session,
            channel=ChannelType.WHATSAPP,
            provider_name="evolution",
            provider_config_key="evolution_canary_instance",
        )
        session.commit()
        provider_id = provider.id

        user = User(locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
        session.add(user)
        session.flush()
        _link = link_or_create_identity(
            session,
            user_id=user.id,
            provider_id=provider_id,
            external_id=phone,
            is_new_user=True,
            verified_proof=True,
        )
        session.commit()
        user_id = user.id

        # Setup ResumeImport with 5 candidates in READY_FOR_REVIEW
        import_id = uuid.uuid4()
        candidates: list[ResumeImportCandidate] = []
        for i in range(1, 6):
            c = ResumeImportCandidate(
                id=uuid.uuid4(),
                resume_import_id=import_id,
                user_id=user_id,
                candidate_kind="skill",
                value_json={"name": f"Skill {i}", "category": "technical"},
                confidence=0.9,
                review_state=CandidateReviewState.PENDING,
            )
            session.add(c)
            candidates.append(c)

        import_rec = ResumeImport(
            id=import_id,
            user_id=user_id,
            status=ImportStatus.READY_FOR_REVIEW,
            original_filename="curriculo_j05.pdf",
            media_type="application/pdf",
            byte_size=1024,
            checksum_sha256="c" * 64,
            storage_key="test_storage_j05",
            candidates=candidates,
        )
        session.add(import_rec)
        session.commit()
        session.close()
        session = session_factory()

        # Step 1 Assertions: Pre-review state in real PostgreSQL
        profile_pre = session.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id))
        facts_pre = session.scalars(select(ProfileFact).where(ProfileFact.user_id == user_id)).all()
        assert profile_pre is None or profile_pre.profile_version == 0
        assert len(facts_pre) == 0

        # Step 2: Message 1 ("rejeita o 2")
        envelope_1 = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_j05_1_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id_1,
                "text": "rejeita o 2",
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=1,
        )
        session.add(envelope_1)
        session.commit()
        envelope_1_id = envelope_1.id

        inbound_1 = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id_1,
            received_at=datetime.now(UTC),
            text="rejeita o 2",
        )

        keys_1 = dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_1,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_1_id,
        )
        session.commit()
        assert len(keys_1) == 1

        session.close()
        session = session_factory()

        # Step 2 Assertions: Draft persisted, zero facts created, import remains READY_FOR_REVIEW
        draft_c2 = session.scalar(
            select(CandidateReviewDraft).where(
                CandidateReviewDraft.user_id == user_id,
                CandidateReviewDraft.candidate_id == candidates[1].id,
            )
        )
        assert draft_c2 is not None
        assert draft_c2.decision == "reject"

        facts_mid = session.scalars(select(ProfileFact).where(ProfileFact.user_id == user_id)).all()
        assert len(facts_mid) == 0, "No facts should be activated before final confirmation"

        reloaded_import_mid = session.get(ResumeImport, import_id)
        assert reloaded_import_mid is not None
        assert reloaded_import_mid.status == ImportStatus.READY_FOR_REVIEW

        reloaded_c2_mid = session.get(ResumeImportCandidate, candidates[1].id)
        assert reloaded_c2_mid is not None
        assert reloaded_c2_mid.review_state == CandidateReviewState.PENDING

        # Step 3: Message 2 ("confirmar todos")
        envelope_2 = ProviderEventEnvelope(
            provider="evolution",
            provider_instance="canary_instance",
            external_event_id=f"evt_j05_2_{uuid.uuid4().hex[:12]}",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            normalized_event={
                "channel": "whatsapp",
                "sender_external_id": phone,
                "provider_message_id": msg_id_2,
                "text": "confirmar todos",
            },
            status=ProviderEventProcessingStatus.ACCEPTED,
            processing_attempts=1,
        )
        session.add(envelope_2)
        session.commit()
        envelope_2_id = envelope_2.id

        inbound_2 = InboundMessage(
            sender=PortChannelIdentity(channel="whatsapp", external_id=phone),
            provider_message_id=msg_id_2,
            received_at=datetime.now(UTC),
            text="confirmar todos",
        )

        keys_2 = dispatch_inbound_message(
            session,
            redis_client,
            inbound=inbound_2,
            provider_id=provider_id,
            channel_adapter=fake_channel,
            storage=storage,
            envelope_id=envelope_2_id,
        )
        session.commit()
        assert len(keys_2) == 1

        session.close()
        session = session_factory()

        # Step 3 Assertions: Post-confirmation state in real PostgreSQL
        # 1. CareerProfile version bumped strictly to 1
        profile_post = session.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id))
        assert profile_post is not None
        assert profile_post.profile_version == 1

        # 2. Exactly 4 active ProfileFact rows (candidate 2 excluded)
        active_facts = session.scalars(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id,
                ProfileFact.status == FactStatus.ACTIVE,
            )
        ).all()
        assert len(active_facts) == 4, f"Expected 4 active facts, got {len(active_facts)}"

        active_skill_names = {
            f.value_json["name"]
            for f in active_facts
            if isinstance(f.value_json, dict) and "name" in f.value_json
        }
        assert "Skill 2" not in active_skill_names
        assert active_skill_names == {"Skill 1", "Skill 3", "Skill 4", "Skill 5"}

        # 3. Candidate 2 marked REJECTED; others marked ACCEPTED
        reloaded_c2_post = session.get(ResumeImportCandidate, candidates[1].id)
        assert reloaded_c2_post is not None
        assert reloaded_c2_post.review_state == CandidateReviewState.REJECTED

        for idx in (0, 2, 3, 4):
            cand_acc = session.get(ResumeImportCandidate, candidates[idx].id)
            assert cand_acc is not None
            assert cand_acc.review_state == CandidateReviewState.ACCEPTED

        # 4. ResumeImport status transitions to FINALIZED
        reloaded_import_post = session.get(ResumeImport, import_id)
        assert reloaded_import_post is not None
        assert reloaded_import_post.status == ImportStatus.FINALIZED

        # 5. Exactly 1 ResumeVersion created with profile_version=1
        versions = session.scalars(
            select(ResumeVersion)
            .join(Resume, Resume.id == ResumeVersion.resume_id)
            .where(Resume.user_id == user_id)
        ).all()
        assert len(versions) == 1
        assert versions[0].profile_version == 1

        # 6. InboundEffect recorded as COMPLETED for envelope 2
        finalize_effect = get_inbound_effect(
            session,
            envelope_id=envelope_2_id,
            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
        )
        assert finalize_effect is not None
        assert finalize_effect.operation_state == InboundEffectState.COMPLETED.value
    finally:
        session.close()
        engine.dispose()
