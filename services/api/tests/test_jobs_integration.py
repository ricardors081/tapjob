"""Integration tests for Jobs domain, manual intake, and normalization foundation (WP2.1-A).
Requires real PostgreSQL and Redis (backend-integration CI job).
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.models import AccountAuditEvent
from tapjob_api.jobs import service
from tapjob_api.jobs.errors import (
    ConcurrentRequestInProgressError,
    IdempotencyKeyPayloadMismatchError,
    IdempotencyOwnershipLostError,
    JobUrlUnreachableError,
)
from tapjob_api.jobs.http_client import SafeHttpClient
from tapjob_api.jobs.models import (
    IdempotencyKey,
    IdempotencyStatus,
    Job,
    JobOrigin,
    JobStatus,
    ObservationStatus,
)
from tapjob_api.jobs.schemas import JobPasteIntakeRequest

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
PASTE_PATH = "/api/v1/jobs/intake/paste"
URL_PATH = "/api/v1/jobs/intake/url"
JOBS_PATH = "/api/v1/jobs"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> str:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return str(response.json()["user"]["id"])


# ---------------------------------------------------------------------------
# Company Identity & Constraints Integration Tests
# ---------------------------------------------------------------------------


def test_same_verified_domain_returns_same_company(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            c1 = service.get_or_create_company(session, name="Acme Inc", domain="acme.com")
            session.commit()
            c2 = service.get_or_create_company(session, name="Acme Corp", domain="acme.com")
            session.commit()
            assert c1.id == c2.id
            assert c1.domain == "acme.com"
    finally:
        engine.dispose()


def test_same_name_different_domain_creates_different_companies(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            c1 = service.get_or_create_company(session, name="Acme", domain="acme.com")
            session.commit()
            c2 = service.get_or_create_company(session, name="Acme", domain="acme.org")
            session.commit()
            assert c1.id != c2.id
    finally:
        engine.dispose()


def test_same_name_no_domain_creates_different_companies(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            c1 = service.get_or_create_company(session, name="Stealth Startup", domain=None)
            session.commit()
            c2 = service.get_or_create_company(session, name="Stealth Startup", domain=None)
            session.commit()
            assert c1.id != c2.id
    finally:
        engine.dispose()


def test_ats_hostname_never_becomes_company_domain(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            c = service.get_or_create_company(session, name="Acme", domain="boards.greenhouse.io")
            session.commit()
            assert c.domain is None
    finally:
        engine.dispose()


def test_concurrent_same_domain_creation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    created_ids: list[uuid.UUID] = []
    lock = threading.Lock()

    def _worker() -> None:
        with Session(engine) as session:
            c = service.get_or_create_company(
                session, name="Race Company", domain="race-domain.com"
            )
            session.commit()
            with lock:
                created_ids.append(c.id)

    try:
        threads = [threading.Thread(target=_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(created_ids) == 5
        # All concurrent workers must have resolved to the EXACT same company ID
        assert len(set(created_ids)) == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Job Identity & JobOrigin Constraints Integration Tests
# ---------------------------------------------------------------------------


def test_job_origin_strong_identity_with_external_id(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            company = service.get_or_create_company(session, name="Corp A", domain="corpa.com")
            session.flush()
            job = Job(
                company_id=company.id,
                title="Dev",
                title_normalized="dev",
                description_raw="A" * 50,
                description_cleaned="A" * 50,
                similarity_fingerprint="fp1",
                status=JobStatus.ACTIVE,
                provenance_summary={},
            )
            session.add(job)
            session.flush()

            o1 = JobOrigin(
                job_id=job.id,
                provider="greenhouse",
                external_id="gh-123",
                observation_status=ObservationStatus.ACTIVE,
            )
            session.add(o1)
            session.commit()

            # Attempt duplicate (provider, external_id)
            o2 = JobOrigin(
                job_id=job.id,
                provider="greenhouse",
                external_id="gh-123",
                observation_status=ObservationStatus.ACTIVE,
            )
            session.add(o2)
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_two_manual_paste_origins_with_null_external_id_coexist(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            company = service.get_or_create_company(session, name="Corp B", domain=None)
            session.flush()
            job1 = Job(
                company_id=company.id,
                title="Dev 1",
                title_normalized="dev 1",
                description_raw="B" * 50,
                description_cleaned="B" * 50,
                similarity_fingerprint="fp2",
                status=JobStatus.UNKNOWN,
                provenance_summary={},
            )
            job2 = Job(
                company_id=company.id,
                title="Dev 2",
                title_normalized="dev 2",
                description_raw="B" * 50,
                description_cleaned="B" * 50,
                similarity_fingerprint="fp2",  # similarity fingerprint is NON-UNIQUE!
                status=JobStatus.UNKNOWN,
                provenance_summary={},
            )
            session.add_all([job1, job2])
            session.flush()

            o1 = JobOrigin(
                job_id=job1.id,
                provider="manual_paste",
                external_id=None,
                observation_status=ObservationStatus.UNKNOWN,
            )
            o2 = JobOrigin(
                job_id=job2.id,
                provider="manual_paste",
                external_id=None,
                observation_status=ObservationStatus.UNKNOWN,
            )
            session.add_all([o1, o2])
            session.commit()  # Must succeed: external_id IS NULL is excluded from unique index
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Idempotency Table & Concurrency Integration Tests
# ---------------------------------------------------------------------------


def test_idempotency_lifecycle_pending_to_completed(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id_str = _bootstrap(
        identity_client, fake_identity_verifier, token="token-1", subject="uid-1"
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    key = "idem-test-1"
    op = "paste_intake"
    fp = "fingerprint-123"

    try:
        with Session(engine) as session:
            # 1. First reservation -> PENDING with reservation_token
            res1 = service.reserve_idempotency_key(
                session, user_id=user_id, key=key, operation=op, request_fingerprint=fp
            )
            session.commit()
            assert res1.is_owner is True
            assert res1.reservation_token is not None
            assert res1.stored_response is None
            assert res1.stored_status_code is None
            token1 = res1.reservation_token

        # 2. Concurrent call while PENDING -> raises ConcurrentRequestInProgressError
        with Session(engine) as session, pytest.raises(ConcurrentRequestInProgressError):
            service.reserve_idempotency_key(
                session, user_id=user_id, key=key, operation=op, request_fingerprint=fp
            )

        with Session(engine) as session:
            # 3. Transition to COMPLETED with matching reservation_token
            resp_payload = {"jobId": str(uuid.uuid4()), "status": "created"}
            service.complete_idempotency_key(
                session,
                user_id=user_id,
                key=key,
                operation=op,
                reservation_token=token1,
                response_status=201,
                response_body=resp_payload,
                resource_id=None,
            )
            session.commit()

        with Session(engine) as session:
            # 4. Same key + same fingerprint -> returns stored response
            res2 = service.reserve_idempotency_key(
                session, user_id=user_id, key=key, operation=op, request_fingerprint=fp
            )
            assert res2.is_owner is False
            assert res2.stored_response == resp_payload
            assert res2.stored_status_code == 201

            # 5. Same key + different fingerprint -> raises IdempotencyKeyPayloadMismatchError
            with pytest.raises(IdempotencyKeyPayloadMismatchError):
                service.reserve_idempotency_key(
                    session,
                    user_id=user_id,
                    key=key,
                    operation=op,
                    request_fingerprint="different-fp",
                )
    finally:
        engine.dispose()


def test_idempotency_db_constraints_enforce_status_coherence(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_id_str = _bootstrap(
        identity_client, fake_identity_verifier, token="token-1", subject="uid-1"
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    now = datetime.now(UTC)

    try:
        with Session(engine) as session:
            # PENDING with response_status NOT NULL violates ck_idempotency_keys_status_coherence
            invalid_pending = IdempotencyKey(
                user_id=user_id,
                key="key-invalid-pending",
                operation="op",
                request_fingerprint="fp",
                status=IdempotencyStatus.PENDING,
                response_status=200,  # Invalid for PENDING!
                response_body=None,
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
            session.add(invalid_pending)
            with pytest.raises(IntegrityError):
                session.commit()

        with Session(engine) as session:
            # COMPLETED with response_body NULL violates ck_idempotency_keys_status_coherence
            invalid_completed = IdempotencyKey(
                user_id=user_id,
                key="key-invalid-completed",
                operation="op",
                request_fingerprint="fp",
                status=IdempotencyStatus.COMPLETED,
                response_status=200,
                response_body=None,  # Invalid for COMPLETED!
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
            session.add(invalid_completed)
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# HTTP Endpoints End-to-End Tests
# ---------------------------------------------------------------------------


def test_paste_intake_e2e_success_and_get_job(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    payload = {
        "companyName": "Tech Inovações Ltda.",
        "title": "Engenheiro de Software Sênior",
        "locationRaw": "São Paulo, SP - Remoto",
        "description": (
            "Estamos contratando pessoa desenvolvedora para atuar "
            "no desenvolvimento de APIs escaláveis com Python e FastAPI."
        ),
    }

    response = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-1"), "Idempotency-Key": "req-paste-1"},
        json=payload,
    )

    assert response.status_code == HTTPStatus.CREATED
    job = response.json()
    job_id = job["id"]

    assert job["title"] == "Engenheiro de Software Sênior"
    assert job["titleNormalized"] == "engenheiro de software sênior"
    assert job["locationRaw"] == "São Paulo, SP - Remoto"
    assert job["locationNormalized"] == "são paulo, sp - remoto"
    assert job["workplaceType"] == "remote"
    assert job["status"] == "unknown"  # Manual paste has status unknown
    assert job["company"]["name"] == "Tech Inovações Ltda."
    assert job["company"]["domain"] is None  # Never copied or guessed
    assert len(job["origins"]) == 1
    assert job["origins"][0]["provider"] == "manual_paste"
    assert job["origins"][0]["externalId"] is None
    assert job["origins"][0]["observationStatus"] == "unknown"

    # GET /api/v1/jobs/{jobId} returns the created job
    get_res = identity_client.get(f"{JOBS_PATH}/{job_id}", headers=bearer_header("token-1"))
    assert get_res.status_code == HTTPStatus.OK
    assert get_res.json()["id"] == job_id

    # Verify AccountAuditEvent recorded without raw description
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            events = list(
                session.scalars(
                    select(AccountAuditEvent).where(
                        AccountAuditEvent.event_type == "job_intake_created"
                    )
                )
            )
            assert len(events) >= 1
            meta = events[-1].metadata_
            assert meta["provider"] == "manual_paste"
            assert "description" not in meta
            assert "FastAPI" not in str(meta)
    finally:
        engine.dispose()


def test_paste_intake_idempotency_replay_and_mismatch(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    payload1 = {
        "companyName": "Acme",
        "title": "Developer",
        "description": "A" * 60,
    }

    # First request
    res1 = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-1"), "Idempotency-Key": "key-fixed"},
        json=payload1,
    )
    assert res1.status_code == HTTPStatus.CREATED
    job1_id = res1.json()["id"]

    # Replay with same key + same payload -> 201 with identical jobId
    res2 = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-1"), "Idempotency-Key": "key-fixed"},
        json=payload1,
    )
    assert res2.status_code == HTTPStatus.CREATED
    assert res2.json()["id"] == job1_id

    # Replay with same key + DIFFERENT payload -> 422 IDEMPOTENCY_KEY_PAYLOAD_MISMATCH
    payload2 = {
        "companyName": "Acme",
        "title": "Developer",
        "description": "B" * 60,
    }
    res3 = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-1"), "Idempotency-Key": "key-fixed"},
        json=payload2,
    )
    assert res3.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert res3.json()["code"] == "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"


def test_paste_intake_rate_limit(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-rl", subject="uid-rl")

    payload = {
        "companyName": "Acme",
        "title": "Developer",
        "description": "A" * 60,
    }

    # Limit is 30 requests per hour
    for i in range(30):
        res = identity_client.post(
            PASTE_PATH,
            headers={**bearer_header("token-rl"), "Idempotency-Key": f"rl-key-{i}"},
            json=payload,
        )
        assert res.status_code == HTTPStatus.CREATED

    # 31st request should be rate limited
    res_exceeded = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-rl"), "Idempotency-Key": "rl-key-31"},
        json=payload,
    )
    assert res_exceeded.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert res_exceeded.json()["code"] == "RATE_LIMITED"


def test_url_intake_e2e_with_mocked_fetch(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-url", subject="uid-url")

    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Software Engineer at GlobalTech</title>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Senior Backend Engineer",
            "hiringOrganization": {
                "@type": "Organization",
                "name": "GlobalTech Solutions"
            },
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": "Curitiba, PR"
                }
            },
            "description": "<p>Responsible for building resilient microservices.</p>"
        }
        </script>
    </head>
    <body>
        <h1>Senior Backend Engineer</h1>
    </body>
    </html>
    """

    with patch.object(SafeHttpClient, "fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (
            "https://globaltech.example/careers/123",
            "text/html",
            sample_html,
        )

        response = identity_client.post(
            URL_PATH,
            headers={**bearer_header("token-url"), "Idempotency-Key": "url-key-1"},
            json={"url": "https://globaltech.example/careers/123"},
        )

        assert response.status_code == HTTPStatus.CREATED
        job = response.json()
        assert job["title"] == "Senior Backend Engineer"
        assert job["company"]["name"] == "GlobalTech Solutions"
        assert job["company"]["domain"] is None  # URL host is NEVER set as company domain
        assert job["status"] == "active"  # Authoritative fetch has status active
        assert job["origins"][0]["provider"] == "manual_url"
        assert job["origins"][0]["observationStatus"] == "active"


def test_url_intake_dynamic_page_requires_manual_paste_error(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-dyn", subject="uid-dyn")

    # JS SPA page with no server rendered content or JSON-LD
    dynamic_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Loading...</title></head>
    <body><div id="root">Loading job posting...</div></body>
    </html>
    """

    with patch.object(SafeHttpClient, "fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (
            "https://spa.example/jobs/react-app",
            "text/html",
            dynamic_html,
        )

        response = identity_client.post(
            URL_PATH,
            headers={**bearer_header("token-dyn"), "Idempotency-Key": "url-key-dyn"},
            json={"url": "https://spa.example/jobs/react-app"},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert response.json()["code"] == "JOB_PAGE_REQUIRES_MANUAL_PASTE"


def test_get_nonexistent_job_returns_404(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    _bootstrap(identity_client, fake_identity_verifier, token="token-1", subject="uid-1")

    random_id = uuid.uuid4()
    response = identity_client.get(f"{JOBS_PATH}/{random_id}", headers=bearer_header("token-1"))
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "JOB_NOT_FOUND"


def test_endpoint_concurrent_url_intake_returns_409_and_replays(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves endpoint concurrency: Request A commits PENDING; Request B with same key gets 409
    immediately while A is in flight; Request A finishes 201; Request C replays 201 with same ID.
    """
    _bootstrap(identity_client, fake_identity_verifier, token="token-concur", subject="uid-concur")

    event_fetch_started = threading.Event()
    event_fetch_pause = threading.Event()

    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Concurrent Engineer at Acme</title>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Concurrent Engineer",
            "hiringOrganization": {"@type": "Organization", "name": "Acme Concurrent"},
            "description": "<p>Building highly concurrent distributed systems.</p>"
        }
        </script>
    </head>
    <body><h1>Concurrent Engineer</h1></body>
    </html>
    """

    async def slow_fetch(url: str) -> tuple[str, str, str]:
        event_fetch_started.set()
        # Pause until released by main thread without blocking the event loop
        await anyio.to_thread.run_sync(event_fetch_pause.wait, 10.0)
        return (url, "text/html", sample_html)

    idem_key = "concurrent-endpoint-key-1"
    url_payload = {"url": "https://acme.example/jobs/concurrent"}

    res_a: list[Any] = []

    def run_request_a() -> None:
        with TestClient(identity_client.app) as client_a:
            r = client_a.post(
                URL_PATH,
                headers={**bearer_header("token-concur"), "Idempotency-Key": idem_key},
                json=url_payload,
            )
            res_a.append(r)

    with patch.object(SafeHttpClient, "fetch", side_effect=slow_fetch):
        thread_a = threading.Thread(target=run_request_a)
        thread_a.start()

        # Wait until Request A has committed PENDING and paused inside fetch
        assert event_fetch_started.wait(timeout=5.0)

        # Request B arrives concurrently while A is still PENDING
        res_b = identity_client.post(
            URL_PATH,
            headers={**bearer_header("token-concur"), "Idempotency-Key": idem_key},
            json=url_payload,
        )
        assert res_b.status_code == HTTPStatus.CONFLICT
        assert res_b.json()["code"] == "CONCURRENT_REQUEST_IN_PROGRESS"

        # Now release Request A
        event_fetch_pause.set()
        thread_a.join(timeout=5.0)

        assert len(res_a) == 1
        assert res_a[0].status_code == HTTPStatus.CREATED
        job_id = res_a[0].json()["id"]

        # Request C (subsequent replay with same key and payload) -> replays 201 with same job ID
        res_c = identity_client.post(
            URL_PATH,
            headers={**bearer_header("token-concur"), "Idempotency-Key": idem_key},
            json=url_payload,
        )
        assert res_c.status_code == HTTPStatus.CREATED
        assert res_c.json()["id"] == job_id


def test_failed_request_releases_idempotency_key(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves that a failed request releases its PENDING idempotency key so retry succeeds."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-fail", subject="uid-fail")

    idem_key = "fail-release-key-1"
    url_payload = {"url": "https://fail.example/jobs/err"}

    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Reliable Engineer at Acme</title>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Reliable Engineer",
            "hiringOrganization": {"@type": "Organization", "name": "Acme Reliable"},
            "description": "<p>Building resilient failure recovery systems.</p>"
        }
        </script>
    </head>
    <body><h1>Reliable Engineer</h1></body>
    </html>
    """

    # 1. First attempt fails due to an upstream fetch error
    with patch.object(SafeHttpClient, "fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = JobUrlUnreachableError("Destination host unreachable")
        res_1 = identity_client.post(
            URL_PATH,
            headers={**bearer_header("token-fail"), "Idempotency-Key": idem_key},
            json=url_payload,
        )
        assert res_1.status_code == HTTPStatus.BAD_GATEWAY
        assert res_1.json()["code"] == "JOB_URL_UNREACHABLE"

    # 2. Key was cleanly released, so immediate retry with same key now succeeds
    with patch.object(SafeHttpClient, "fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = ("https://fail.example/jobs/err", "text/html", sample_html)
        res_2 = identity_client.post(
            URL_PATH,
            headers={**bearer_header("token-fail"), "Idempotency-Key": idem_key},
            json=url_payload,
        )
        assert res_2.status_code == HTTPStatus.CREATED
        assert res_2.json()["title"] == "Reliable Engineer"


def test_stale_pending_idempotency_key_takeover(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves that an objectively stale PENDING reservation (>120s) is safely taken over."""
    user_id_str = _bootstrap(
        identity_client, fake_identity_verifier, token="token-stale", subject="uid-stale"
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    idem_key = "stale-key-1"
    op = "jobs.intake.paste"
    payload = {
        "companyName": "Acme Stale Recovery",
        "title": "Crash Recovery Specialist",
        "description": "Handling crash recovery after abandoned pending reservations.",
    }
    fingerprint = service.compute_request_fingerprint(
        JobPasteIntakeRequest(**payload).model_dump_json(by_alias=True).encode("utf-8")
    )

    # Seed a PENDING key that is older than IDEMPOTENCY_PENDING_STALE_SECONDS (120s)
    stale_created = datetime.now(UTC) - timedelta(seconds=150)
    old_token = uuid.uuid4()
    with Session(engine) as session:
        stale_pending = IdempotencyKey(
            user_id=user_id,
            key=idem_key,
            operation=op,
            request_fingerprint=fingerprint,
            status=IdempotencyStatus.PENDING,
            reservation_token=old_token,
            response_status=None,
            response_body=None,
            resource_id=None,
            created_at=stale_created,
            expires_at=stale_created + timedelta(hours=24),
        )
        session.add(stale_pending)
        session.commit()

    # Call endpoint with same key: should take over stale PENDING and succeed with 201
    res = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-stale"), "Idempotency-Key": idem_key},
        json=payload,
    )
    assert res.status_code == HTTPStatus.CREATED
    assert res.json()["title"] == "Crash Recovery Specialist"

    with Session(engine) as session:
        updated_row = session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.key == idem_key,
                IdempotencyKey.operation == op,
            )
        ).scalar_one()
        assert updated_row.status == IdempotencyStatus.COMPLETED
        assert updated_row.reservation_token != old_token


def test_manual_job_ownership_and_cross_user_isolation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves that manual jobs are user-owned: User A can GET their job, User B gets 404
    (preventing enumeration), and User B's Idempotency-Key is isolated from User A's key.
    """
    # 1. Bootstrap User A and User B
    user_a_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-user-a", subject="uid-user-a"
    )
    user_b_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-user-b", subject="uid-user-b"
    )
    assert user_a_id != user_b_id

    # 2. User A creates a manual paste job
    res_a = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-user-a"), "Idempotency-Key": "shared-idempotency-key"},
        json={
            "companyName": "Confidential Corp",
            "title": "Private Architect",
            "description": "User A proprietary job description text.",
        },
    )
    assert res_a.status_code == HTTPStatus.CREATED
    job_a_id = res_a.json()["id"]

    # Verify created_by_user_id in DB is User A
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    with Session(engine) as session:
        job_db = session.execute(select(Job).where(Job.id == uuid.UUID(job_a_id))).scalar_one()
        assert str(job_db.created_by_user_id) == user_a_id

    # 3. User A can retrieve their own job -> 200 OK
    get_res_a = identity_client.get(
        f"{JOBS_PATH}/{job_a_id}", headers=bearer_header("token-user-a")
    )
    assert get_res_a.status_code == HTTPStatus.OK
    assert get_res_a.json()["id"] == job_a_id

    # 4. User B attempts to GET User A's job -> 404 JOB_NOT_FOUND (not 403, preventing enumeration)
    get_res_b = identity_client.get(
        f"{JOBS_PATH}/{job_a_id}", headers=bearer_header("token-user-b")
    )
    assert get_res_b.status_code == HTTPStatus.NOT_FOUND
    assert get_res_b.json()["code"] == "JOB_NOT_FOUND"

    # 5. User B uses the exact same Idempotency-Key as User A -> does NOT replay User A's job
    res_b = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-user-b"), "Idempotency-Key": "shared-idempotency-key"},
        json={
            "companyName": "Confidential Corp",
            "title": "Private Architect",
            "description": "User A proprietary job description text.",
        },
    )
    assert res_b.status_code == HTTPStatus.CREATED
    job_b_id = res_b.json()["id"]
    assert job_b_id != job_a_id  # Completely distinct job owned by User B


def test_old_owner_cannot_complete_after_stale_takeover(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves fencing token prevents old owner from completing after a stale takeover.
    1. Owner A reserves token A.
    2. Make row stale (>120s).
    3. Owner B performs takeover and receives token B.
    4. Owner A tries to complete with token A -> raises IdempotencyOwnershipLostError.
    5. Owner B completes with token B -> succeeds.
    6. Stored response is Owner B's response.
    """
    user_id_str = _bootstrap(
        identity_client, fake_identity_verifier, token="token-fence-comp", subject="uid-fence-comp"
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    fencing_compensation_key = "fencing-comp-1"
    op = "jobs.intake.paste"
    fp = "fingerprint-fence-comp"

    try:
        # 1. Owner A reserves token A
        with Session(engine) as session:
            res_a = service.reserve_idempotency_key(
                session,
                user_id=user_id,
                key=fencing_compensation_key,
                operation=op,
                request_fingerprint=fp,
            )
            session.commit()
            assert res_a.is_owner is True
            assert res_a.reservation_token is not None
            token_a = res_a.reservation_token

        # 2. Make row stale
        stale_time = datetime.now(UTC) - timedelta(seconds=150)
        with Session(engine) as session:
            session.execute(
                update(IdempotencyKey)
                .where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.key == fencing_compensation_key,
                    IdempotencyKey.operation == op,
                )
                .values(created_at=stale_time)
            )
            session.commit()

        # 3. Owner B performs takeover and receives token B
        with Session(engine) as session:
            res_b = service.reserve_idempotency_key(
                session,
                user_id=user_id,
                key=fencing_compensation_key,
                operation=op,
                request_fingerprint=fp,
            )
            session.commit()
            assert res_b.is_owner is True
            assert res_b.reservation_token is not None
            token_b = res_b.reservation_token
            assert token_b != token_a

        # 4. Owner A tries to complete with token A -> fails with IdempotencyOwnershipLostError
        payload_a = {"owner": "A", "data": "old"}
        with Session(engine) as session, pytest.raises(IdempotencyOwnershipLostError):
            service.complete_idempotency_key(
                session,
                user_id=user_id,
                key=fencing_compensation_key,
                operation=op,
                reservation_token=token_a,
                response_status=201,
                response_body=payload_a,
                resource_id=None,
            )

        # 5. Owner B completes with token B -> succeeds
        payload_b = {"owner": "B", "data": "new"}
        with Session(engine) as session:
            service.complete_idempotency_key(
                session,
                user_id=user_id,
                key=fencing_compensation_key,
                operation=op,
                reservation_token=token_b,
                response_status=201,
                response_body=payload_b,
                resource_id=None,
            )
            session.commit()

        # 6. Replay returns Owner B's stored response
        with Session(engine) as session:
            res_replay = service.reserve_idempotency_key(
                session,
                user_id=user_id,
                key=fencing_compensation_key,
                operation=op,
                request_fingerprint=fp,
            )
            assert res_replay.is_owner is False
            assert res_replay.stored_response == payload_b
    finally:
        engine.dispose()


def test_old_owner_cannot_release_after_stale_takeover(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves that an old owner cannot delete or release a reservation after a stale takeover.
    1. Owner A reserves token A.
    2. Make row stale (>120s).
    3. Owner B performs takeover and receives token B.
    4. Owner A tries release with token A -> returns False (0 rows deleted).
    5. The row remains present, PENDING, and owned by token B.
    """
    user_id_str = _bootstrap(
        identity_client, fake_identity_verifier, token="token-fence-rel", subject="uid-fence-rel"
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    key = "fencing-rel-key-1"
    op = "jobs.intake.paste"
    fp = "fingerprint-fence-rel"

    try:
        # 1. Owner A reserves token A
        with Session(engine) as session:
            res_a = service.reserve_idempotency_key(
                session, user_id=user_id, key=key, operation=op, request_fingerprint=fp
            )
            session.commit()
            token_a = res_a.reservation_token
            assert token_a is not None

        # 2. Make row stale
        stale_time = datetime.now(UTC) - timedelta(seconds=150)
        with Session(engine) as session:
            session.execute(
                update(IdempotencyKey)
                .where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.key == key,
                    IdempotencyKey.operation == op,
                )
                .values(created_at=stale_time)
            )
            session.commit()

        # 3. Owner B performs takeover and receives token B
        with Session(engine) as session:
            res_b = service.reserve_idempotency_key(
                session, user_id=user_id, key=key, operation=op, request_fingerprint=fp
            )
            session.commit()
            token_b = res_b.reservation_token
            assert token_b is not None
            assert token_b != token_a

        # 4. Owner A tries release with token A -> returns False (does not delete row)
        with Session(engine) as session:
            deleted = service.release_pending_idempotency_key(
                session,
                user_id=user_id,
                key=key,
                operation=op,
                reservation_token=token_a,
            )
            session.commit()
            assert deleted is False

        # 5. Row still exists with token B
        with Session(engine) as session:
            row = session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.key == key,
                    IdempotencyKey.operation == op,
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.status == IdempotencyStatus.PENDING
            assert row.reservation_token == token_b
    finally:
        engine.dispose()


def test_simultaneous_stale_takeover_exactly_one_wins(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves atomic conditional update during simultaneous stale takeover:
    Two concurrent callers race to take over a stale PENDING key.
    Exactly one receives ownership (is_owner=True with new token);
    the other receives ConcurrentRequestInProgressError.
    """
    user_id_str = _bootstrap(
        identity_client,
        fake_identity_verifier,
        token="token-simul-stale",
        subject="uid-simul-stale",
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    key = "simul-stale-key-1"
    op = "jobs.intake.paste"
    fp = "fingerprint-simul-stale"

    try:
        # Seed stale PENDING row
        stale_time = datetime.now(UTC) - timedelta(seconds=200)
        with Session(engine) as session:
            seed_row = IdempotencyKey(
                user_id=user_id,
                key=key,
                operation=op,
                request_fingerprint=fp,
                status=IdempotencyStatus.PENDING,
                reservation_token=uuid.uuid4(),
                response_status=None,
                response_body=None,
                resource_id=None,
                created_at=stale_time,
                expires_at=stale_time + timedelta(hours=24),
            )
            session.add(seed_row)
            session.commit()

        results: list[Any] = []
        errors: list[Exception] = []

        def attempt_takeover() -> None:
            with Session(engine) as session:
                try:
                    res = service.reserve_idempotency_key(
                        session, user_id=user_id, key=key, operation=op, request_fingerprint=fp
                    )
                    session.commit()
                    results.append(res)
                except Exception as exc:
                    errors.append(exc)

        t1 = threading.Thread(target=attempt_takeover)
        t2 = threading.Thread(target=attempt_takeover)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one must have won ownership
        assert len(results) == 1
        assert results[0].is_owner is True
        assert results[0].reservation_token is not None

        # The other must have caught ConcurrentRequestInProgressError
        assert len(errors) == 1
        assert isinstance(errors[0], ConcurrentRequestInProgressError)
    finally:
        engine.dispose()


def test_atomic_business_creation_and_idempotency_completion(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves atomic commit of Job resource and idempotency COMPLETED:
    1. Forced failure before commit rolls back both: no Job persisted, reservation not COMPLETED.
    2. Successful commit commits both together atomically.
    """
    user_id_str = _bootstrap(
        identity_client,
        fake_identity_verifier,
        token="token-atomic-commit",
        subject="uid-atomic-commit",
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    # 1. Force failure before commit during paste intake
    with (
        patch(
            "tapjob_api.jobs.service.complete_idempotency_key",
            side_effect=RuntimeError("Simulated DB error before commit"),
        ),
        pytest.raises(RuntimeError, match="Simulated DB error before commit"),
    ):
        identity_client.post(
            PASTE_PATH,
            headers={**bearer_header("token-atomic-commit"), "Idempotency-Key": "atomic-key-fail"},
            json={
                "companyName": "Rollback Corp",
                "title": "Rollback Specialist",
                "description": "This job should never be persisted when complete fails.",
            },
        )

    # Verify: No job persisted and reservation was released (cleaned up)
    with Session(engine) as session:
        jobs = (
            session.execute(
                select(Job).where(
                    Job.created_by_user_id == user_id, Job.title == "Rollback Specialist"
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 0

        idem = session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.key == "atomic-key-fail"
            )
        ).scalar_one_or_none()
        assert idem is None

    # 2. Successful commit commits Job and COMPLETED together
    res_success = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-atomic-commit"), "Idempotency-Key": "atomic-key-success"},
        json={
            "companyName": "Atomic Corp",
            "title": "Atomic Specialist",
            "description": "This job should be persisted atomically with COMPLETED status.",
        },
    )
    assert res_success.status_code == HTTPStatus.CREATED
    job_id = uuid.UUID(res_success.json()["id"])

    with Session(engine) as session:
        job = session.execute(select(Job).where(Job.id == job_id)).scalar_one_or_none()
        assert job is not None
        assert job.created_by_user_id == user_id

        idem = session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.key == "atomic-key-success"
            )
        ).scalar_one_or_none()
        assert idem is not None
        assert idem.status == IdempotencyStatus.COMPLETED
        assert idem.resource_id == job_id
        assert idem.response_status == 201


def test_crash_gap_eliminated_no_job_with_pending_reservation(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Proves crash gap is eliminated: after any completed request, there is never a
    persisted Job alongside a PENDING reservation for that operation.
    """
    user_id_str = _bootstrap(
        identity_client, fake_identity_verifier, token="token-no-gap", subject="uid-no-gap"
    )
    user_id = uuid.UUID(user_id_str)
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))

    idem_key = "no-gap-key-1"
    res = identity_client.post(
        PASTE_PATH,
        headers={**bearer_header("token-no-gap"), "Idempotency-Key": idem_key},
        json={
            "companyName": "Consistent Corp",
            "title": "Consistency Engineer",
            "description": "Verifying that job and idempotency COMPLETED share transaction.",
        },
    )
    assert res.status_code == HTTPStatus.CREATED
    job_id = uuid.UUID(res.json()["id"])

    with Session(engine) as session:
        # Check Job exists
        job = session.execute(select(Job).where(Job.id == job_id)).scalar_one_or_none()
        assert job is not None

        # Check IdempotencyKey is COMPLETED, NEVER PENDING
        idem = session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.key == idem_key
            )
        ).scalar_one_or_none()
        assert idem is not None
        assert idem.status == IdempotencyStatus.COMPLETED
        assert idem.resource_id == job_id
