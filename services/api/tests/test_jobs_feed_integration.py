"""Integration tests for WP2.3 Job Feed & Alerts (ADR-0015).

Requires real PostgreSQL and Redis (backend-integration CI job).
Exercises:
- Multi-tenant visibility (global jobs, private manual jobs, cross-user BOLA/IDOR prevention)
- Job liveness & status filtering (closed, unavailable, expired excluded)
- Relevance ordering & deterministic pagination
- Stable cursor pagination & tamper/profile-version invalidation
- SavedJob semantics (save, unsave, dismiss, undismiss, isolation, idempotency)
- Company watchlist semantics (watch, unwatch, list, idempotency)
- Job alert preferences & sweep evaluation with deduplication & outbox integration
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from messaging.fake_channel import FakeMessagingChannel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tapjob_api.career_profile.models import (
    CareerProfile,
    CareerProfileRevision,
    FactSourceKind,
    FactStatus,
    FactType,
    ProfileFact,
)
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import User
from tapjob_api.jobs import feed_service
from tapjob_api.jobs import service as jobs_service
from tapjob_api.jobs.alert_service import (
    evaluate_job_alerts_sweep,
    reconcile_alert_notifications_delivery,
    reconcile_pending_alert_notifications,
)
from tapjob_api.jobs.models import (
    AlertNotificationStatus,
    CompanyWatch,
    Job,
    JobAlertDeliveryChannel,
    JobAlertNotification,
    JobAlertPreference,
    JobStatus,
    JobWorkplaceType,
    SavedJob,
)
from tapjob_api.matching.models import MatchResult
from tapjob_api.messaging.models import (
    ChannelIdentity,
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelProvider,
    ChannelType,
)
from tapjob_api.messaging.outbox import OutboundMessageQueue, OutboundMessageStatus
from tapjob_api.messaging.outbox_service import claim_and_dispatch_outbox_batch

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> uuid.UUID:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return uuid.UUID(response.json()["user"]["id"])


def _seed_profile(session: Session, user_id: uuid.UUID, *, skills: list[str] | None = None) -> None:
    now = datetime.now(UTC)
    profile_id = uuid.uuid4()
    profile = CareerProfile(
        id=profile_id,
        user_id=user_id,
        headline="Senior Python Engineer",
        summary="Backend specialist",
        target_roles=["Backend Engineer", "Software Engineer"],
        profile_version=1,
    )
    session.add(profile)
    session.flush()

    revision = CareerProfileRevision(
        career_profile_id=profile_id,
        user_id=user_id,
        profile_version=1,
        headline="Senior Python Engineer",
        summary="Backend specialist",
        target_roles=["Backend Engineer", "Software Engineer"],
    )
    session.add(revision)
    session.flush()

    for skill in skills or ["Python", "PostgreSQL", "FastAPI"]:
        fact_id = uuid.uuid4()
        fact = ProfileFact(
            id=fact_id,
            user_id=user_id,
            career_profile_id=profile_id,
            fact_type=FactType.SKILL,
            value_json={"kind": "skill", "name": skill},
            source_kind=FactSourceKind.USER_DIRECT,
            user_confirmed=True,
            confirmed_at=now,
            status=FactStatus.ACTIVE,
            lineage_id=fact_id,
            active_from_profile_version=1,
        )
        session.add(fact)
    session.flush()


def _seed_channel_link(session: Session, user_id: uuid.UUID) -> ChannelIdentityLink:
    provider = ChannelProvider(
        id=uuid.uuid4(),
        channel=ChannelType.WHATSAPP,
        provider_name="evolution",
        provider_config_key=f"evolution_cfg_{uuid.uuid4().hex[:8]}",
    )
    session.add(provider)
    session.flush()

    identity = ChannelIdentity(
        id=uuid.uuid4(),
        provider_id=provider.id,
        external_id=f"551199999{uuid.uuid4().hex[:4]}",
    )
    session.add(identity)
    session.flush()

    link = ChannelIdentityLink(
        id=uuid.uuid4(),
        channel_identity_id=identity.id,
        user_id=user_id,
        status=ChannelIdentityLinkStatus.VERIFIED,
    )
    session.add(link)
    session.flush()
    return link


def _seed_job(
    session: Session,
    *,
    title: str = "Senior Python Engineer",
    company_name: str = "Acme Corp",
    status: JobStatus = JobStatus.ACTIVE,
    workplace_type: JobWorkplaceType = JobWorkplaceType.REMOTE,
    location: str = "São Paulo, SP, Brazil",
    created_by_user_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    description: str = "Requisitos: python, postgresql, fastapi.",
) -> Job:
    company = jobs_service.get_or_create_company(
        session, name=company_name, domain=f"{company_name.lower().replace(' ', '')}.com"
    )
    session.flush()
    job = Job(
        id=uuid.uuid4(),
        company_id=company.id,
        status=status,
        title=title,
        title_normalized=title.lower(),
        location_raw=location,
        location_normalized=location.lower(),
        workplace_type=workplace_type,
        description_raw=description,
        description_cleaned=description,
        similarity_fingerprint=f"sim_fp_{uuid.uuid4().hex[:10]}",
        created_by_user_id=created_by_user_id,
        provenance_summary={"source": "test"},
        expires_at=expires_at,
    )
    session.add(job)
    session.flush()
    return job


def test_feed_multi_tenant_visibility_and_idor(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_a = _bootstrap(identity_client, fake_identity_verifier, token="tok-a", subject="sub-a")
    user_b = _bootstrap(identity_client, fake_identity_verifier, token="tok-b", subject="sub-b")

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_a)
        _seed_profile(session, user_b)

        global_job = _seed_job(session, title="Global Job A", created_by_user_id=None)
        job_user_a = _seed_job(session, title="Private Job User A", created_by_user_id=user_a)
        session.commit()
        global_job_id = str(global_job.id)
        private_job_id = str(job_user_a.id)

    # 1. User A sees global job and their own private job
    res_a = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-a"))
    assert res_a.status_code == HTTPStatus.OK
    ids_a = [item["id"] for item in res_a.json()["items"]]
    assert global_job_id in ids_a
    assert private_job_id in ids_a

    # 2. User B sees global job, but User A's private job is NEVER in feed
    res_b = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-b"))
    assert res_b.status_code == HTTPStatus.OK
    ids_b = [item["id"] for item in res_b.json()["items"]]
    assert global_job_id in ids_b
    assert private_job_id not in ids_b

    # 3. User B direct detail access to User A's private job fails with 404 (IDOR guard)
    res_b_detail = identity_client.get(
        f"/api/v1/jobs/{private_job_id}", headers=bearer_header("tok-b")
    )
    assert res_b_detail.status_code == HTTPStatus.NOT_FOUND


def test_feed_job_liveness_and_filters(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-live", subject="sub-live"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    now = datetime.now(UTC)
    with Session(engine) as session:
        _seed_profile(session, user_id)
        active_job = _seed_job(session, title="Active Python Tech Lead", status=JobStatus.ACTIVE)
        closed_job = _seed_job(session, title="Closed Python Role", status=JobStatus.CLOSED)
        expired_job = _seed_job(
            session,
            title="Expired Python Role",
            status=JobStatus.ACTIVE,
            expires_at=now - timedelta(days=1),
        )
        session.commit()
        active_id = str(active_job.id)
        closed_id = str(closed_job.id)
        expired_id = str(expired_job.id)

    res = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-live"))
    assert res.status_code == HTTPStatus.OK
    ids = [item["id"] for item in res.json()["items"]]
    assert active_id in ids
    assert closed_id not in ids
    assert expired_id not in ids

    # Search filter by query
    res_query = identity_client.get("/api/v1/jobs?q=Tech+Lead", headers=bearer_header("tok-live"))
    assert res_query.status_code == HTTPStatus.OK
    query_ids = [item["id"] for item in res_query.json()["items"]]
    assert active_id in query_ids

    res_nomatch = identity_client.get(
        "/api/v1/jobs?q=NonExistentSkill123", headers=bearer_header("tok-live")
    )
    assert res_nomatch.status_code == HTTPStatus.OK
    assert len(res_nomatch.json()["items"]) == 0


def test_feed_pagination_and_tampered_cursor(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-page", subject="sub-page"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id)
        for i in range(5):
            _seed_job(session, title=f"Unique Job Number {i}")
        session.commit()

    # Page 1 with limit=2
    res_p1 = identity_client.get("/api/v1/jobs?limit=2", headers=bearer_header("tok-page"))
    assert res_p1.status_code == HTTPStatus.OK
    data_p1 = res_p1.json()
    assert len(data_p1["items"]) == 2
    assert data_p1["nextCursor"] is not None

    next_cursor = data_p1["nextCursor"]

    # Page 2 with cursor
    res_p2 = identity_client.get(
        f"/api/v1/jobs?limit=2&cursor={next_cursor}", headers=bearer_header("tok-page")
    )
    assert res_p2.status_code == HTTPStatus.OK
    data_p2 = res_p2.json()
    assert len(data_p2["items"]) == 2

    # Disjoint items
    p1_ids = {item["id"] for item in data_p1["items"]}
    p2_ids = {item["id"] for item in data_p2["items"]}
    assert p1_ids.isdisjoint(p2_ids)

    # Tampered cursor should fail closed with 400
    res_tampered = identity_client.get(
        "/api/v1/jobs?cursor=invalid_garbage_base64", headers=bearer_header("tok-page")
    )
    assert res_tampered.status_code == HTTPStatus.BAD_REQUEST

    # Cursor with mismatched filters should fail closed with 400
    res_mismatch = identity_client.get(
        f"/api/v1/jobs?cursor={next_cursor}&q=DifferentQuery", headers=bearer_header("tok-page")
    )
    assert res_mismatch.status_code == HTTPStatus.BAD_REQUEST


def test_saved_job_and_dismiss_semantics(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-save", subject="sub-save"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id)
        job1 = _seed_job(session, title="Job To Save")
        job2 = _seed_job(session, title="Job To Dismiss")
        session.commit()
        job1_id = str(job1.id)
        job2_id = str(job2.id)

    # 1. Save job1
    save_res = identity_client.post(
        f"/api/v1/jobs/{job1_id}/save", headers=bearer_header("tok-save")
    )
    assert save_res.status_code == HTTPStatus.OK
    assert save_res.json()["state"] == "saved"

    # 1b. Idempotent save
    save_res2 = identity_client.post(
        f"/api/v1/jobs/{job1_id}/save", headers=bearer_header("tok-save")
    )
    assert save_res2.status_code == HTTPStatus.OK

    # 2. List saved jobs
    saved_list = identity_client.get("/api/v1/jobs/saved", headers=bearer_header("tok-save"))
    assert saved_list.status_code == HTTPStatus.OK
    saved_ids = [item["jobId"] for item in saved_list.json()["items"]]
    assert job1_id in saved_ids

    # 3. Dismiss job2
    dismiss_res = identity_client.post(
        f"/api/v1/jobs/{job2_id}/dismiss", headers=bearer_header("tok-save")
    )
    assert dismiss_res.status_code == HTTPStatus.OK
    assert dismiss_res.json()["state"] == "dismissed"

    # 4. Job2 should NOT appear in standard feed
    feed_res = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-save"))
    feed_ids = [item["id"] for item in feed_res.json()["items"]]
    assert job2_id not in feed_ids

    # 5. Undismiss job2
    undismiss_res = identity_client.delete(
        f"/api/v1/jobs/{job2_id}/dismiss", headers=bearer_header("tok-save")
    )
    assert undismiss_res.status_code == HTTPStatus.NO_CONTENT

    # Job2 is back in feed
    feed_res2 = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-save"))
    feed_ids2 = [item["id"] for item in feed_res2.json()["items"]]
    assert job2_id in feed_ids2

    # 6. Unsave job1
    unsave_res = identity_client.delete(
        f"/api/v1/jobs/{job1_id}/save", headers=bearer_header("tok-save")
    )
    assert unsave_res.status_code == HTTPStatus.NO_CONTENT
    saved_list2 = identity_client.get("/api/v1/jobs/saved", headers=bearer_header("tok-save"))
    saved_ids2 = [item["jobId"] for item in saved_list2.json()["items"]]
    assert job1_id not in saved_ids2


def test_company_watchlist_semantics(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="tok-comp", subject="sub-comp")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        comp = jobs_service.get_or_create_company(session, name="Stripe", domain="stripe.com")
        session.commit()
        company_id = str(comp.id)

    # 1. Watch company
    watch_res = identity_client.post(
        f"/api/v1/companies/{company_id}/watch", headers=bearer_header("tok-comp")
    )
    assert watch_res.status_code == HTTPStatus.OK
    assert watch_res.json()["companyId"] == company_id

    # 1b. Watch again (idempotent)
    watch_res2 = identity_client.post(
        f"/api/v1/companies/{company_id}/watch", headers=bearer_header("tok-comp")
    )
    assert watch_res2.status_code == HTTPStatus.OK

    # 2. List watched
    list_res = identity_client.get("/api/v1/companies/watched", headers=bearer_header("tok-comp"))
    assert list_res.status_code == HTTPStatus.OK
    watched_ids = [item["id"] for item in list_res.json()]
    assert company_id in watched_ids

    # 3. Unwatch company
    unwatch_res = identity_client.delete(
        f"/api/v1/companies/{company_id}/watch", headers=bearer_header("tok-comp")
    )
    assert unwatch_res.status_code == HTTPStatus.NO_CONTENT

    # 4. Watch non-existent company returns 404
    non_existent = str(uuid.uuid4())
    nf_res = identity_client.post(
        f"/api/v1/companies/{non_existent}/watch", headers=bearer_header("tok-comp")
    )
    assert nf_res.status_code == HTTPStatus.NOT_FOUND


def test_job_alert_preferences_and_sweep_evaluation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-alert", subject="sub-alert"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI", "PostgreSQL"])
        link = _seed_channel_link(session, user_id)
        # Job matching the skills
        alert_job = _seed_job(
            session,
            title="Senior Python Backend Specialist",
            description="Requisitos: python, postgresql, fastapi.",
        )
        session.commit()
        alert_job_id = alert_job.id
        link_id_str = str(link.id)
        expected_recipient = link.channel_identity.external_id

    # 1. Check default preference (disabled by default)
    pref_res = identity_client.get(
        "/api/v1/jobs/alerts/preferences", headers=bearer_header("tok-alert")
    )
    assert pref_res.status_code == HTTPStatus.OK
    assert pref_res.json()["enabled"] is False

    # 2. Opt-in with score threshold 50
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-alert"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK
    assert put_res.json()["enabled"] is True

    # 3. Run alert evaluation sweep in session
    with Session(engine) as session:
        result = evaluate_job_alerts_sweep(session)
        session.commit()
        assert result["notifications_created"] >= 1
        assert result["outbound_messages_queued"] >= 1

        # Check notification persisted with QUEUED status and outbound_message_id linked
        stmt = select(JobAlertNotification).where(
            JobAlertNotification.user_id == user_id,
            JobAlertNotification.job_id == alert_job_id,
        )
        notif = session.execute(stmt).scalar_one_or_none()
        assert notif is not None
        assert notif.dedupe_key is not None
        assert notif.status == AlertNotificationStatus.QUEUED
        assert notif.delivered_at is None
        assert notif.outbound_message_id is not None

        # Check OutboundMessageQueue populated (fake transport, WHATSAPP_REAL_SENDS=0)
        outbox_stmt = select(OutboundMessageQueue).where(
            OutboundMessageQueue.channel_identity_link_id == link_id_str
        )
        outbox_msg = session.execute(outbox_stmt).scalars().first()
        assert outbox_msg is not None
        assert outbox_msg.idempotency_key == f"alert_msg:{notif.id}"
        assert outbox_msg.message_content.get("recipient_external_id") == expected_recipient
        assert outbox_msg.message_content.get("recipient_external_id") != ""

    # 4. Run sweep again -> dedupe should prevent any new notification
    with Session(engine) as session:
        result_second = evaluate_job_alerts_sweep(session)
        session.commit()
        assert result_second["notifications_created"] == 0


def test_job_alert_fake_messaging_dispatch_lifecycle(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-dispatch", subject="sub-dispatch"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        link = _seed_channel_link(session, user_id)
        job = _seed_job(
            session,
            title="Senior Python Backend Architect",
            description="Requisitos: python, fastapi.",
        )
        session.commit()
        job_id = job.id
        link_id_str = str(link.id)
        expected_recipient_id = link.channel_identity.external_id

    # 1. Opt-in alerts
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-dispatch"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates intent with factual state QUEUED (not DELIVERED yet)
    with Session(engine) as session:
        result = evaluate_job_alerts_sweep(session)
        session.commit()
        assert result["notifications_created"] == 1
        assert result["outbound_messages_queued"] == 1

        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        assert notif.status == AlertNotificationStatus.QUEUED
        assert notif.delivered_at is None
        assert notif.outbound_message_id is not None
        notif_id = notif.id

        outbox_msg = session.get(OutboundMessageQueue, notif.outbound_message_id)
        assert outbox_msg is not None
        assert outbox_msg.status == OutboundMessageStatus.PENDING
        assert outbox_msg.channel_identity_link_id == link_id_str
        assert outbox_msg.message_content.get("recipient_external_id") == expected_recipient_id
        assert outbox_msg.message_content.get("recipient_external_id") != ""

    # 3. Dispatch outbox batch through FakeMessagingChannel
    fake_channel = FakeMessagingChannel()
    with Session(engine) as session:
        dispatched = claim_and_dispatch_outbox_batch(
            session, provider_adapter=fake_channel, limit=100
        )
        session.commit()
        my_dispatched = [m for m in dispatched if m.channel_identity_link_id == link_id_str]
        assert len(my_dispatched) == 1
        assert my_dispatched[0].status == OutboundMessageStatus.SENT
        assert my_dispatched[0].sent_at is not None

        # Assert fake channel received the exact external_id and 0 empty recipients
        my_fake_msgs = [
            m
            for m in fake_channel.sent_messages
            if m.recipient.external_id == expected_recipient_id
        ]
        assert len(my_fake_msgs) == 1
        fake_msg = my_fake_msgs[0]
        assert fake_msg.recipient.external_id == expected_recipient_id
        assert fake_msg.recipient.external_id != ""
        assert fake_msg.text is not None

    # 4. Reconcile delivery truth: notification becomes DELIVERED with factual delivered_at
    with Session(engine) as session:
        rec_res = reconcile_alert_notifications_delivery(session)
        session.commit()
        assert rec_res["delivered"] >= 1

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.DELIVERED
        assert notif_after.delivered_at is not None


def test_job_alert_no_link_reconciliation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-nolink", subject="sub-nolink"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        # Notice: NO channel identity link seeded yet!
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in alerts
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-nolink"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Run sweep without verified link -> notification created as PENDING, 0 outbox queued
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        assert res1["outbound_messages_queued"] == 0

        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        assert notif.status == AlertNotificationStatus.PENDING
        assert notif.outbound_message_id is None
        assert notif.delivered_at is None
        notif_id = notif.id

        # Zero outbox records exist for this notification
        outbox_for_notif = list(
            session.execute(
                select(OutboundMessageQueue).where(
                    OutboundMessageQueue.idempotency_key == f"alert_msg:{notif.id}"
                )
            )
            .scalars()
            .all()
        )
        assert len(outbox_for_notif) == 0

    # 2b. Second sweep without link -> dedupe ensures zero duplicate notifications and zero outbox
    with Session(engine) as session:
        res_repeat = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res_repeat["notifications_created"] == 0
        assert res_repeat["outbound_messages_queued"] == 0

    # 3. User now links and verifies WhatsApp identity
    with Session(engine) as session:
        link = _seed_channel_link(session, user_id)
        session.commit()
        expected_recipient = link.channel_identity.external_id

    # 4. Reconcile now updates the same PENDING notification to QUEUED, exactly 1 outbox
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 1

        notif_rec = session.get(JobAlertNotification, notif_id)
        assert notif_rec is not None
        assert notif_rec.status == AlertNotificationStatus.QUEUED
        assert notif_rec.outbound_message_id is not None
        assert notif_rec.delivered_at is None

        outbox_msg = session.get(OutboundMessageQueue, notif_rec.outbound_message_id)
        assert outbox_msg is not None
        assert outbox_msg.message_content.get("recipient_external_id") == expected_recipient

    # 5. Dispatch fake succeeds -> DELIVERED
    fake_channel = FakeMessagingChannel()
    with Session(engine) as session:
        claim_and_dispatch_outbox_batch(session, provider_adapter=fake_channel, limit=10)
        session.commit()
        reconcile_alert_notifications_delivery(session)
        session.commit()

        notif_delivered = session.get(JobAlertNotification, notif_id)
        assert notif_delivered is not None
        assert notif_delivered.status == AlertNotificationStatus.DELIVERED
        assert notif_delivered.delivered_at is not None

    # 6. Failure path: simulate DEAD outbox -> notification status becomes FAILED
    with Session(engine) as session:
        dead_outbox = OutboundMessageQueue(
            channel_identity_link_id=str(link.id),
            idempotency_key=f"dead_test:{uuid.uuid4()}",
            message_content={"text": "Fail test", "recipient_external_id": "123"},
            status=OutboundMessageStatus.DEAD,
        )
        session.add(dead_outbox)
        session.flush()

        failed_notif = JobAlertNotification(
            id=uuid.uuid4(),
            user_id=user_id,
            job_id=job_id,
            dedupe_key=f"dead_notif:{uuid.uuid4()}",
            channel=JobAlertDeliveryChannel.WHATSAPP,
            match_score=90,
            status=AlertNotificationStatus.QUEUED,
            outbound_message_id=dead_outbox.id,
        )
        session.add(failed_notif)
        session.commit()
        failed_notif_id = failed_notif.id

    with Session(engine) as session:
        rec_res = reconcile_alert_notifications_delivery(session)
        session.commit()
        assert rec_res["failed"] >= 1

        notif_failed = session.get(JobAlertNotification, failed_notif_id)
        assert notif_failed is not None
        assert notif_failed.status == AlertNotificationStatus.FAILED
        assert notif_failed.delivered_at is None


def test_pending_alert_disabled_before_link_is_skipped(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.1: When user disables alert preference before link appears,
    deferred reconciliation revalidates consent and transitions notification to SKIPPED,
    queuing ZERO outbox messages and triggering ZERO fake sends.
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-dis-pend", subject="sub-dis-pend"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in alerts initially
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-dis-pend"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep without link -> notification PENDING
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        assert res1["outbound_messages_queued"] == 0

        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        assert notif.status == AlertNotificationStatus.PENDING
        notif_id = notif.id

    # 3. User updates preference to disabled (opt-out)
    put_res_dis = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-dis-pend"),
        json={
            "enabled": False,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res_dis.status_code == HTTPStatus.OK

    # 4. User adds verified WhatsApp link afterwards
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 5. Reconcile runs -> must revalidate consent and skip
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 0

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.SKIPPED
        assert notif_after.outbound_message_id is None
        assert notif_after.delivered_at is None

        # Zero outbox records created
        outbox_rows = list(
            session.execute(
                select(OutboundMessageQueue).where(
                    OutboundMessageQueue.idempotency_key == f"alert_msg:{notif_id}"
                )
            )
            .scalars()
            .all()
        )
        assert len(outbox_rows) == 0


def test_pending_alert_preference_threshold_change_is_revalidated(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.2: If minScore is raised above current match before link appears,
    deferred reconciliation revalidates thresholds and marks notification SKIPPED.
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-thresh-chg", subject="sub-thresh-chg"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in with minScore=50 (job matches at 73)
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-thresh-chg"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates PENDING notification
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        notif_id = notif.id

    # 3. User raises minScore to 95 (higher than match score 73)
    put_res_high = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-thresh-chg"),
        json={
            "enabled": True,
            "minScore": 95,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res_high.status_code == HTTPStatus.OK

    # 4. Verified link appears
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 5. Reconcile revalidates threshold and skips
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 0

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.SKIPPED
        assert notif_after.outbound_message_id is None


def test_pending_alert_eligibility_tightened_is_skipped(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.2b: When minEligibility is tightened from review_required to eligible,
    a pending notification whose match is review_required is revalidated and SKIPPED.
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-elig-tight", subject="sub-elig-tight"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        # Profile with only target_roles -> matches at score=55, eligibility=review_required
        profile_id = uuid.uuid4()
        profile = CareerProfile(
            id=profile_id,
            user_id=user_id,
            headline=None,
            summary=None,
            target_roles=["Rust Developer"],
            profile_version=1,
        )
        session.add(profile)
        session.flush()
        revision = CareerProfileRevision(
            career_profile_id=profile_id,
            user_id=user_id,
            profile_version=1,
            headline=None,
            summary=None,
            target_roles=["Rust Developer"],
        )
        session.add(revision)

        job = _seed_job(
            session,
            title="Rust Developer",
            description="Oportunidade para atuacao remota.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in with minEligibility=review_required
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-elig-tight"),
        json={
            "enabled": True,
            "minScore": 10,
            "minEligibility": "review_required",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep without link -> notification created as PENDING
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        notif_id = notif.id

    # 3. User tightens preference: minEligibility="eligible" (excluding review_required)
    put_res_tight = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-elig-tight"),
        json={
            "enabled": True,
            "minScore": 10,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res_tight.status_code == HTTPStatus.OK

    # 4. Verified link appears
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 5. Reconcile -> SKIPPED, zero outbox
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 0

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.SKIPPED
        assert notif_after.outbound_message_id is None


def test_pending_alert_closed_job_is_skipped(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.3: When a job is closed before link appears,
    deferred reconciliation revalidates liveness and transitions notification to SKIPPED.
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-closed-job", subject="sub-closed-job"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-closed-job"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates PENDING
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        notif_id = notif.id

    # 3. Job is CLOSED
    with Session(engine) as session:
        job_db = session.get(Job, job_id)
        assert job_db is not None
        job_db.status = JobStatus.CLOSED
        session.commit()

    # 4. Verified link appears
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 5. Reconcile -> SKIPPED
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 0

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.SKIPPED
        assert notif_after.outbound_message_id is None


def test_pending_alert_expired_job_is_skipped(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.3b: When a job expires before link appears,
    deferred reconciliation revalidates expiry and transitions notification to SKIPPED.
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-exp-job", subject="sub-exp-job"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-exp-job"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates PENDING
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        notif_id = notif.id

    # 3. Job expires_at set in the past
    with Session(engine) as session:
        job_db = session.get(Job, job_id)
        assert job_db is not None
        job_db.expires_at = datetime.now(UTC) - timedelta(hours=2)
        session.commit()

    # 4. Verified link appears
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 5. Reconcile -> SKIPPED
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 0

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.SKIPPED
        assert notif_after.outbound_message_id is None


def test_pending_review_required_alert_keeps_factual_eligibility_message(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.4: When match is review_required and preference allows it,
    reconciled outbox message accurately renders 'Status: Requer revisão'
    (never false 'Status: compatível').
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-fact-rev", subject="sub-fact-rev"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        # Profile with only target_roles -> score=55, eligibility=review_required
        profile_id = uuid.uuid4()
        profile = CareerProfile(
            id=profile_id,
            user_id=user_id,
            headline=None,
            summary=None,
            target_roles=["Rust Developer"],
            profile_version=1,
        )
        session.add(profile)
        session.flush()
        revision = CareerProfileRevision(
            career_profile_id=profile_id,
            user_id=user_id,
            profile_version=1,
            headline=None,
            summary=None,
            target_roles=["Rust Developer"],
        )
        session.add(revision)

        job = _seed_job(
            session,
            title="Rust Developer",
            description="Oportunidade para atuacao remota.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in with minEligibility=review_required
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-fact-rev"),
        json={
            "enabled": True,
            "minScore": 10,
            "minEligibility": "review_required",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates PENDING notification
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        notif_id = notif.id

    # 3. Add link
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 4. Reconcile creates outbox message
    with Session(engine) as session:
        reconciled = reconcile_pending_alert_notifications(session)
        session.commit()
        assert reconciled == 1

        notif_rec = session.get(JobAlertNotification, notif_id)
        assert notif_rec is not None
        assert notif_rec.status == AlertNotificationStatus.QUEUED

        outbox_msg = session.get(OutboundMessageQueue, notif_rec.outbound_message_id)
        assert outbox_msg is not None
        msg_text = outbox_msg.message_content.get("text", "")
        # Must reflect factual status 'Requer revisão', NOT 'compatível'
        assert "Status: Requer revisão" in msg_text
        assert "Status: compatível" not in msg_text
        assert "Status: Compatível" not in msg_text

        # Drain outbox message to avoid test state pollution
        claim_and_dispatch_outbox_batch(session, provider_adapter=FakeMessagingChannel(), limit=10)
        session.commit()


def test_disabling_alerts_cancels_unsent_queued_alert(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.5: When a user disables job alerts while a notification is QUEUED
    and the outbox message is PENDING, the message is CANCELLED, the notification is SKIPPED,
    and subsequent dispatcher execution sends ZERO messages.
    """
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    # Drain any preexisting outbox messages to ensure strict isolation
    with Session(engine) as session:
        claim_and_dispatch_outbox_batch(session, provider_adapter=FakeMessagingChannel(), limit=100)
        session.commit()

    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-dis-queued", subject="sub-dis-queued"
    )

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        _seed_channel_link(session, user_id)
        session.commit()
        job_id = job.id

    # 1. Opt-in with enabled=True
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-dis-queued"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates QUEUED notification and PENDING outbox message
    with Session(engine) as session:
        res = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res["notifications_created"] == 1
        assert res["outbound_messages_queued"] == 1

        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        assert notif.status == AlertNotificationStatus.QUEUED
        assert notif.outbound_message_id is not None
        notif_id = notif.id
        outbox_id = notif.outbound_message_id

        outbox_msg = session.get(OutboundMessageQueue, outbox_id)
        assert outbox_msg is not None
        assert outbox_msg.status == OutboundMessageStatus.PENDING

    # 3. User disables alerts BEFORE dispatcher runs
    put_res_dis = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-dis-queued"),
        json={
            "enabled": False,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res_dis.status_code == HTTPStatus.OK

    # 4. Verify outbox message is CANCELLED and notification is SKIPPED
    with Session(engine) as session:
        outbox_msg_after = session.get(OutboundMessageQueue, outbox_id)
        assert outbox_msg_after is not None
        assert outbox_msg_after.status == OutboundMessageStatus.CANCELLED

        notif_after = session.get(JobAlertNotification, notif_id)
        assert notif_after is not None
        assert notif_after.status == AlertNotificationStatus.SKIPPED
        assert notif_after.delivered_at is None

    # 5. Dispatcher runs with FakeMessagingChannel -> ZERO messages sent!
    fake_channel = FakeMessagingChannel()
    with Session(engine) as session:
        dispatched = claim_and_dispatch_outbox_batch(
            session, provider_adapter=fake_channel, limit=10
        )
        session.commit()
        # Message was CANCELLED, so claim_and_dispatch_outbox_batch must ignore it
        assert not any(m.id == outbox_id for m in dispatched)
        assert not any(
            m.idempotency_key == f"alert_msg:{notif_id}" for m in fake_channel.sent_messages
        )
        assert len(fake_channel.sent_messages) == 0


def test_concurrent_pending_alert_reconciliation_postgres(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Finding HIGH.7: Concurrent reconciliation workers claiming the same PENDING notification
    safely serialize via row-level locking (SKIP LOCKED), resulting in exactly one outbox message
    and zero duplicate sends.
    """
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-conc-rec", subject="sub-conc-rec"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Vaga para backend engineer com python e fastapi.",
        )
        session.commit()
        job_id = job.id

    # 1. Opt-in
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-conc-rec"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    # 2. Sweep creates PENDING notification
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session)
        session.commit()
        assert res1["notifications_created"] == 1
        notif = session.execute(
            select(JobAlertNotification).where(
                JobAlertNotification.user_id == user_id,
                JobAlertNotification.job_id == job_id,
            )
        ).scalar_one()
        notif_id = notif.id

    # 3. Add link
    with Session(engine) as session:
        _seed_channel_link(session, user_id)
        session.commit()

    # 4. Two concurrent threads attempt reconcile_pending_alert_notifications
    results: list[int] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        try:
            with Session(engine) as s:
                barrier.wait()
                count = reconcile_pending_alert_notifications(s, limit=10)
                s.commit()
                results.append(count)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent reconciliation raised errors: {errors}"
    assert sum(results) == 1, (
        f"Expected exactly 1 reconciled notification across workers, got: {results}"
    )

    # Exactly 1 outbox message exists
    with Session(engine) as session:
        notif_final = session.get(JobAlertNotification, notif_id)
        assert notif_final is not None
        assert notif_final.status == AlertNotificationStatus.QUEUED
        assert notif_final.outbound_message_id is not None

        outbox_count = (
            session.execute(
                select(OutboundMessageQueue).where(
                    OutboundMessageQueue.idempotency_key == f"alert_msg:{notif_id}"
                )
            )
            .scalars()
            .all()
        )
        assert len(outbox_count) == 1

        # Drain outbox message to avoid test state pollution
        claim_and_dispatch_outbox_batch(session, provider_adapter=FakeMessagingChannel(), limit=10)
        session.commit()


def test_job_alert_channel_validation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(
        identity_client, fake_identity_verifier, token="tok-chan-val", subject="sub-chan-val"
    )

    # 1. in_app rejected with 422
    res_in_app = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-chan-val"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "in_app",
        },
    )
    assert res_in_app.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    # 2. random rejected with 422
    res_random = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-chan-val"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "random",
        },
    )
    assert res_random.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    # 3. whatsapp accepted with 200
    res_wa = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-chan-val"),
        json={
            "enabled": True,
            "minScore": 50,
            "minEligibility": "eligible",
            "deliveryChannel": "whatsapp",
        },
    )
    assert res_wa.status_code == HTTPStatus.OK
    assert res_wa.json()["deliveryChannel"] == "whatsapp"


def test_alert_sweep_fairness_and_zero_starvation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    user_ids: list[uuid.UUID] = []
    with Session(engine) as session:
        for i in range(55):
            uid = _bootstrap(
                identity_client,
                fake_identity_verifier,
                token=f"tok-fair-{i}",
                subject=f"sub-fair-{i}",
            )
            user_ids.append(uid)
            pref = JobAlertPreference(
                user_id=uid,
                enabled=True,
                min_score=50,
                min_eligibility="eligible",
                delivery_channel=JobAlertDeliveryChannel.WHATSAPP,
                last_evaluated_at=None,
            )
            session.add(pref)
        session.commit()

    # Sweep 1: Processes exactly 50 users (bounded batch)
    with Session(engine) as session:
        res1 = evaluate_job_alerts_sweep(session, max_users=50)
        session.commit()
        assert res1["evaluated_users_count"] == 50

        # 50 users have last_evaluated_at, 5 still have None
        stmt_evaluated = select(JobAlertPreference).where(
            JobAlertPreference.user_id.in_(user_ids),
            JobAlertPreference.last_evaluated_at.is_not(None),
        )
        evaluated_users_1 = set(session.execute(stmt_evaluated).scalars().all())
        assert len(evaluated_users_1) == 50

        stmt_unevaluated = select(JobAlertPreference).where(
            JobAlertPreference.user_id.in_(user_ids),
            JobAlertPreference.last_evaluated_at.is_(None),
        )
        unevaluated_users_1 = list(session.execute(stmt_unevaluated).scalars().all())
        assert len(unevaluated_users_1) == 5

    # Sweep 2: Evaluates the remaining 5 users FIRST (nulls_first watermark rotation)
    with Session(engine) as session:
        res2 = evaluate_job_alerts_sweep(session, max_users=50)
        session.commit()
        assert res2["evaluated_users_count"] == 50

        # All 55 users now have last_evaluated_at is not None (zero starvation!)
        stmt_all = select(JobAlertPreference).where(
            JobAlertPreference.user_id.in_(user_ids),
            JobAlertPreference.last_evaluated_at.is_(None),
        )
        remaining_unevaluated = list(session.execute(stmt_all).scalars().all())
        assert len(remaining_unevaluated) == 0


def test_canonical_batch_matching_and_persistence(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-batch-match", subject="sub-batch-match"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "PostgreSQL", "FastAPI"])
        j1 = _seed_job(session, title="Python Backend Lead Canonical")
        j2 = _seed_job(session, title="Fullstack Python Engineer Canonical")
        session.commit()
        j1_id = j1.id
        j2_id = j2.id

    # 1. Feed request computes and persists canonical MatchResult records
    feed_res = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-batch-match"))
    assert feed_res.status_code == HTTPStatus.OK
    items = feed_res.json()["items"]
    assert len(items) >= 2

    with Session(engine) as session:
        mr1 = session.execute(
            select(MatchResult).where(
                MatchResult.user_id == user_id,
                MatchResult.job_id == j1_id,
            )
        ).scalar_one_or_none()
        assert mr1 is not None
        assert mr1.overall_score is not None
        assert mr1.deterministic_result_digest is not None
        computed_at_1 = mr1.computed_at

        mr2 = session.execute(
            select(MatchResult).where(
                MatchResult.user_id == user_id,
                MatchResult.job_id == j2_id,
            )
        ).scalar_one_or_none()
        assert mr2 is not None

    # 2. Second feed request reuses existing canonical MatchResult (zero re-evaluation)
    feed_res2 = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-batch-match"))
    assert feed_res2.status_code == HTTPStatus.OK

    with Session(engine) as session:
        mr1_after = session.execute(
            select(MatchResult).where(
                MatchResult.user_id == user_id,
                MatchResult.job_id == j1_id,
            )
        ).scalar_one()
        assert mr1_after.computed_at == computed_at_1


def test_target_roles_only_profile_matching(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-target-only", subject="sub-target-only"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        # Profile with ONLY target_roles (headline=None, summary=None, facts=[])
        profile_id = uuid.uuid4()
        profile = CareerProfile(
            id=profile_id,
            user_id=user_id,
            headline=None,
            summary=None,
            target_roles=["Rust Backend Specialist"],
            profile_version=1,
        )
        session.add(profile)
        session.flush()

        revision = CareerProfileRevision(
            career_profile_id=profile_id,
            user_id=user_id,
            profile_version=1,
            headline=None,
            summary=None,
            target_roles=["Rust Backend Specialist"],
        )
        session.add(revision)

        job = _seed_job(
            session,
            title="Rust Backend Specialist",
            description="Oportunidade para atuacao remota.",
        )
        _seed_channel_link(session, user_id)
        session.commit()
        job_id = job.id

    # 1. Feed must return the job with non-null match (not treated as empty profile!)
    feed_res = identity_client.get("/api/v1/jobs", headers=bearer_header("tok-target-only"))
    assert feed_res.status_code == HTTPStatus.OK
    feed_items = feed_res.json()["items"]
    target_job_item = next((item for item in feed_items if item["id"] == str(job_id)), None)
    assert target_job_item is not None
    assert target_job_item["matchScore"] is not None
    assert target_job_item["matchScore"] > 0

    # 2. Alert opt-in and evaluation evaluates matching properly
    put_res = identity_client.put(
        "/api/v1/jobs/alerts/preferences",
        headers=bearer_header("tok-target-only"),
        json={
            "enabled": True,
            "minScore": 10,
            "minEligibility": "review_required",
            "deliveryChannel": "whatsapp",
        },
    )
    assert put_res.status_code == HTTPStatus.OK

    with Session(engine) as session:
        sweep_res = evaluate_job_alerts_sweep(session)
        session.commit()
        assert sweep_res["notifications_created"] == 1
        assert sweep_res["outbound_messages_queued"] == 1


def test_feed_candidate_pool_snapshot_bound(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-snap", subject="sub-snap"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id)
        # Create 5 initial jobs
        initial_jobs = [_seed_job(session, title=f"Initial Job Snapshot {i}") for i in range(5)]
        session.commit()
        initial_job_ids = [str(j.id) for j in initial_jobs]

    # Page 1 with limit=2
    res_p1 = identity_client.get("/api/v1/jobs?limit=2", headers=bearer_header("tok-snap"))
    assert res_p1.status_code == HTTPStatus.OK
    data_p1 = res_p1.json()
    assert len(data_p1["items"]) == 2
    cursor_p1 = data_p1["nextCursor"]
    assert cursor_p1 is not None
    p1_ids = {item["id"] for item in data_p1["items"]}

    # Insert 3 NEWER jobs before requesting page 2
    with Session(engine) as session:
        new_jobs = [
            _seed_job(session, title=f"Newer Interleaved Job Snapshot {i}") for i in range(3)
        ]
        session.commit()
        new_job_ids = {str(j.id) for j in new_jobs}

    # Page 2 using original cursor
    res_p2 = identity_client.get(
        f"/api/v1/jobs?limit=2&cursor={cursor_p1}", headers=bearer_header("tok-snap")
    )
    assert res_p2.status_code == HTTPStatus.OK
    data_p2 = res_p2.json()
    p2_ids = {item["id"] for item in data_p2["items"]}

    # Assert candidate pool was frozen by candidate_cutoff:
    # 1. No items from page 1 duplicated
    assert p1_ids.isdisjoint(p2_ids)
    # 2. NONE of the newer jobs appear in page 2 of this snapshot session
    assert p2_ids.isdisjoint(new_job_ids)
    # 3. Items in page 2 are from the initial snapshot
    for pid in p2_ids:
        assert pid in initial_job_ids


def test_concurrent_saved_jobs_postgres(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-conc-save", subject="sub-conc-save"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        job = _seed_job(session, title="Concurrent Save Job")
        session.commit()
        job_id = job.id

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            with Session(engine) as sess:
                user = sess.get(User, user_id)
                assert user is not None
                feed_service.save_job(sess, user=user, job_id=job_id)
                sess.commit()
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(errors) == 0, f"Concurrent save errors: {errors}"

    with Session(engine) as session:
        saved_rows = list(
            session.execute(
                select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.job_id == job_id)
            )
            .scalars()
            .all()
        )
        assert len(saved_rows) == 1


def test_concurrent_company_watch_postgres(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-conc-watch", subject="sub-conc-watch"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        comp = jobs_service.get_or_create_company(session, name="Concurrent Watch Corp")
        session.commit()
        company_id = comp.id

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            with Session(engine) as sess:
                user = sess.get(User, user_id)
                assert user is not None
                feed_service.watch_company(sess, user=user, company_id=company_id)
                sess.commit()
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(errors) == 0, f"Concurrent watch errors: {errors}"

    with Session(engine) as session:
        watch_rows = list(
            session.execute(
                select(CompanyWatch).where(
                    CompanyWatch.user_id == user_id, CompanyWatch.company_id == company_id
                )
            )
            .scalars()
            .all()
        )
        assert len(watch_rows) == 1


def test_concurrent_alert_worker_postgres(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id = _bootstrap(
        identity_client, fake_identity_verifier, token="tok-conc-alert", subject="sub-conc-alert"
    )
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    with Session(engine) as session:
        _seed_profile(session, user_id, skills=["Python", "FastAPI"])
        link = _seed_channel_link(session, user_id)
        job = _seed_job(
            session,
            title="Backend Engineer",
            description="Requisitos: python, fastapi.",
        )
        pref = JobAlertPreference(
            user_id=user_id,
            enabled=True,
            min_score=10,
            min_eligibility="review_required",
            delivery_channel=JobAlertDeliveryChannel.WHATSAPP,
        )
        session.add(pref)
        session.commit()
        job_id = job.id
        link_id_str = str(link.id)

    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            with Session(engine) as sess:
                res = evaluate_job_alerts_sweep(sess, max_users=100)
                sess.commit()
                results.append(res)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(errors) == 0, f"Concurrent alert worker errors: {errors}"

    with Session(engine) as session:
        # Exactly ONE JobAlertNotification created for this user and job
        notifs = list(
            session.execute(
                select(JobAlertNotification).where(
                    JobAlertNotification.user_id == user_id,
                    JobAlertNotification.job_id == job_id,
                )
            )
            .scalars()
            .all()
        )
        assert len(notifs) == 1

        # Exactly ONE OutboundMessageQueue created for this notification
        outbox_msgs = list(
            session.execute(
                select(OutboundMessageQueue).where(
                    OutboundMessageQueue.channel_identity_link_id == link_id_str
                )
            )
            .scalars()
            .all()
        )
        assert len(outbox_msgs) == 1
        claim_and_dispatch_outbox_batch(session, provider_adapter=FakeMessagingChannel(), limit=10)
        session.commit()
