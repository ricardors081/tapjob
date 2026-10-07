"""Integration tests: real PostgreSQL, worker processing lease/retry/ownership semantics
(docs/adr/0011-resume-import.md "worker idempotency", round-1 independent review "Finding 2",
round-2 independent review "Finding 2"). Directly exercises
`service.claim_import_for_processing`/`process_resume_import` -- never through a real Celery
broker, same convention as `test_resume_import_integration.py`.
"""

import io
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import cast

import docx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, select, update

from tapjob_api.core.config import ApiSettings
from tapjob_api.db.session import create_session_factory
from tapjob_api.resume_import import service
from tapjob_api.resume_import.errors import ImportLeaseActiveError, TransientProcessingError
from tapjob_api.resume_import.limits import MAX_PROCESSING_ATTEMPTS
from tapjob_api.resume_import.models import (
    ImportFailureCode,
    ImportStatus,
    ResumeImport,
    ResumeImportCandidate,
)
from tapjob_api.resume_import.storage import ObjectStorageError, ObjectStoragePort, StoredObject

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
IMPORTS_PATH = "/api/v1/me/resume-imports"


class _AlwaysFailingStorage:
    """A `get()` that always raises `ObjectStorageError` -- simulates a transient storage-layer
    failure (e.g. R2 temporarily unreachable), never a document-content problem."""

    def put(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        raise AssertionError("not used by these tests")

    def get(self, *, storage_key: str) -> bytes:
        raise ObjectStorageError("simulated transient failure")

    def delete(self, *, storage_key: str) -> None:
        raise AssertionError("not used by these tests")


def _bootstrap_and_upload(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> uuid.UUID:
    verifier.issue_token(token, make_identity(subject=subject))
    bootstrap = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert bootstrap.status_code == HTTPStatus.OK
    response = client.post(
        IMPORTS_PATH,
        headers=bearer_header(token),
        files={
            "file": (
                "resume.pdf",
                b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF",
                "application/pdf",
            )
        },
    )
    assert response.status_code == HTTPStatus.ACCEPTED
    return uuid.UUID(response.json()["id"])


def test_claim_is_not_reclaimable_while_the_lease_is_still_active(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """A fresh claim's own lease has not expired -- a second claim attempt for the SAME import
    must find nothing to claim (round-1 independent review "Finding 2" scenario D: "active lease
    não gera processamento concorrente")."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-1", subject="uid-lease-1"
    )
    with session_factory() as session:
        claim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert claim_result is not None
        claimed, token = claim_result
        assert claimed.status == ImportStatus.PROCESSING
        assert claimed.processing_attempts == 1
        assert claimed.processing_lease_token == token

        second_attempt = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert second_attempt is None
    engine.dispose()


def test_redelivery_while_lease_is_active_raises_for_a_bounded_retry_never_a_silent_success(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-1 independent review "Finding 2" scenarios A/B/D: a redelivered task that finds the
    row already claimed under a STILL-ACTIVE lease must never be treated as a safe no-op -- it
    raises `ImportLeaseActiveError`, carrying the lease's own real expiry, which `tasks.py`'s
    Celery wrapper turns into an explicit, bounded retry (round-2 review "Finding 1")."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-2", subject="uid-lease-2"
    )
    storage = _resume_storage(identity_client)
    with session_factory() as session:
        # First claim leaves the row genuinely `PROCESSING` under an active lease (never completed
        # in THIS session, simulating a worker still genuinely running).
        claim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert claim_result is not None
        expected_lease_expiry = claim_result[0].processing_lease_expires_at

    with session_factory() as session, pytest.raises(ImportLeaseActiveError) as exc_info:
        service.process_resume_import(session, import_id=import_id, storage=storage)
    assert exc_info.value.retry_at == expected_lease_expiry

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.PROCESSING  # untouched, still leased
        assert resume_import.processing_attempts == 1  # the retry did NOT consume a new attempt
    engine.dispose()


def test_a_duplicate_delivery_with_exhausted_attempts_and_an_active_owner_never_fails_the_row(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-2 independent review "Finding 2" scenario A: a duplicate delivery observing
    `processing_attempts == MAX_PROCESSING_ATTEMPTS` must NEVER mark the row `FAILED` just because
    the attempt BUDGET is exhausted, when a genuinely different, currently active owner still
    holds a valid lease on it right now."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-8", subject="uid-lease-8"
    )
    storage = _resume_storage(identity_client)

    with session_factory() as session:
        # Drive processing_attempts up to MAX by expiring the lease twice and reclaiming, leaving
        # the FINAL reclaim's lease genuinely active (never expired in this test).
        for _ in range(MAX_PROCESSING_ATTEMPTS - 1):
            claim_result = service.claim_import_for_processing(session, import_id=import_id)
            session.commit()
            assert claim_result is not None
            session.execute(
                update(ResumeImport)
                .where(ResumeImport.id == import_id)
                .values(processing_lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            session.commit()
        final_claim = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert final_claim is not None
        assert final_claim[0].processing_attempts == MAX_PROCESSING_ATTEMPTS

    # A duplicate delivery of the SAME (now attempt-exhausted) import, while the final attempt's
    # own lease is still genuinely active -- must raise, never silently fail the row.
    with session_factory() as session, pytest.raises(ImportLeaseActiveError):
        service.process_resume_import(session, import_id=import_id, storage=storage)

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.PROCESSING  # never FAILED
        assert resume_import.processing_attempts == MAX_PROCESSING_ATTEMPTS
    engine.dispose()


def test_an_expired_lease_can_be_reclaimed_and_increments_attempts(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-1 independent review "Finding 2" scenario C: a worker that crashed after claiming a
    row (never reaching a terminal state) leaves it reclaimable once the lease window passes --
    simulated here by moving `processing_lease_expires_at` into the past, exactly as if real wall-
    clock time had elapsed. The reclaim also generates a FRESH lease token (round-2 independent
    review "Finding 2"), distinct from the first claim's own."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-3", subject="uid-lease-3"
    )
    with session_factory() as session:
        claim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert claim_result is not None
        first_claim, first_token = claim_result
        assert first_claim.processing_attempts == 1

        # Simulate the lease having expired (a crashed worker, real wall-clock time elapsed) --
        # never something `claim_import_for_processing` itself would do.
        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == import_id)
            .values(processing_lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

        reclaim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert reclaim_result is not None
        reclaimed, second_token = reclaim_result
        assert reclaimed.status == ImportStatus.PROCESSING
        assert reclaimed.processing_attempts == 2
        assert second_token != first_token
    engine.dispose()


def test_transient_storage_failure_releases_the_row_for_a_bounded_retry(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-1 independent review "Finding 2" scenario E: a storage-layer failure is presumed
    RECOVERABLE, never a document-content problem -- the row is released back to `QUEUED`
    immediately (never waiting out the full lease) so a bounded retry can reclaim it right away."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-4", subject="uid-lease-4"
    )
    flaky_storage: ObjectStoragePort = _AlwaysFailingStorage()

    with session_factory() as session, pytest.raises(TransientProcessingError):
        service.process_resume_import(session, import_id=import_id, storage=flaky_storage)

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.QUEUED  # released, NOT terminally failed
        assert resume_import.processing_attempts == 1
        assert resume_import.processing_lease_token is None
        assert resume_import.failure_code is None
    engine.dispose()


def test_a_persistently_failing_storage_eventually_fails_terminally_without_an_infinite_loop(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-1 independent review "Finding 2" scenario F (bounded, not infinite): once
    `processing_attempts` reaches `MAX_PROCESSING_ATTEMPTS`, a transient failure stops being
    retried and becomes a terminal `FAILED` instead -- a pathological, permanently-unreachable
    storage backend can never retry forever."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-5", subject="uid-lease-5"
    )
    flaky_storage: ObjectStoragePort = _AlwaysFailingStorage()

    for attempt in range(1, MAX_PROCESSING_ATTEMPTS + 1):
        with session_factory() as session:
            if attempt < MAX_PROCESSING_ATTEMPTS:
                with pytest.raises(TransientProcessingError):
                    service.process_resume_import(
                        session, import_id=import_id, storage=flaky_storage
                    )
            else:
                # The FINAL attempt: claim succeeds (attempts still < MAX at claim time), the
                # storage failure is transient, but releasing-for-retry would exceed the bound --
                # this call marks the row terminally FAILED instead of raising.
                service.process_resume_import(session, import_id=import_id, storage=flaky_storage)

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.FAILED
        assert resume_import.failure_code == ImportFailureCode.INTERNAL_ERROR
        assert resume_import.processing_attempts == MAX_PROCESSING_ATTEMPTS
        assert resume_import.processing_lease_token is None
    engine.dispose()


def test_recovery_after_a_reclaimed_lease_never_duplicates_candidates(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-1 independent review "Finding 2" scenario G: after a lease is reclaimed (simulating
    crash recovery), the import is processed exactly once to completion -- never a duplicated
    candidate set from the abandoned first attempt. Uses a REAL document with genuine extractable
    text/candidates (unlike this file's other, claim-only tests' minimal stub PDF) -- this is the
    one test here that exercises a full, successful completion."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    fake_identity_verifier.issue_token("lease-6", make_identity(subject="uid-lease-6"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("lease-6"))
    assert bootstrap.status_code == HTTPStatus.OK
    document = docx.Document()
    document.add_paragraph("Summary")
    document.add_paragraph("Experienced backend engineer.")
    document.add_paragraph("Skills")
    document.add_paragraph("Python, PostgreSQL")
    buffer = io.BytesIO()
    document.save(buffer)
    upload = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("lease-6"),
        files={
            "file": (
                "resume.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == HTTPStatus.ACCEPTED
    import_id = uuid.UUID(upload.json()["id"])
    storage = _resume_storage(identity_client)

    with session_factory() as session:
        # First claim -- simulates a worker that claimed the row and then crashed before doing
        # any real work at all (never even reaching `storage.get`).
        claim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert claim_result is not None

        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == import_id)
            .values(processing_lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

    with session_factory() as session:
        # Recovery: `process_resume_import` reclaims the expired lease and completes normally.
        service.process_resume_import(session, import_id=import_id, storage=storage)

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.READY_FOR_REVIEW
        assert resume_import.processing_lease_token is None
        candidates = list(
            session.scalars(
                select(ResumeImportCandidate).where(
                    ResumeImportCandidate.resume_import_id == import_id
                )
            )
        )
        candidate_ids = [c.id for c in candidates]
        assert len(candidate_ids) == len(set(candidate_ids))
    engine.dispose()


def test_lease_fencing_rejects_a_stale_workers_completion_after_reclaim(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Background security review follow-up to round-1 independent review "Finding 2": "missing
    lease fencing" -- generalized and hardened by round-2 independent review "Finding 2" into a
    real per-claim lease TOKEN, never only an attempt-count coincidence. The lease prevents a
    SECOND worker from concurrently claiming the same row, but without token-based fencing, nothing
    stopped a FIRST worker -- whose lease already expired and was reclaimed by someone else while
    it was still slowly (not dead) running -- from later overwriting whatever the reclaiming
    attempt had already done. Proves `_owner_transition` rejects a stale token for EVERY one of the
    three transitions out of `PROCESSING` (FAILED, QUEUED, READY_FOR_REVIEW) -- scenarios B, C, D
    of the review's own explicit test list -- while the CURRENT, genuinely-owning token is always
    accepted (scenario E)."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-7", subject="uid-lease-7"
    )

    with session_factory() as session:
        # "Worker A" claims the row -- its own fencing token.
        stale_claim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert stale_claim_result is not None
        _stale_claim, stale_token = stale_claim_result

        # Simulate the lease expiring while worker A is still genuinely (just slowly) running.
        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == import_id)
            .values(processing_lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

        # "Worker B" reclaims the now-expired lease -- a genuinely different delivery/attempt,
        # with its own fresh token.
        reclaim_result = service.claim_import_for_processing(session, import_id=import_id)
        session.commit()
        assert reclaim_result is not None
        _reclaimed, current_token = reclaim_result
        assert current_token != stale_token

        # Scenario B/C/D: worker A, unaware it lost the lease, belatedly tries each of the three
        # possible transitions using its OWN (now stale) token -- every one must be rejected.
        for stale_attempt_values in (
            {
                "status": ImportStatus.FAILED,
                "failure_code": ImportFailureCode.INTERNAL_ERROR,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
            {
                "status": ImportStatus.QUEUED,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
            {
                "status": ImportStatus.READY_FOR_REVIEW,
                "parser_identity": "x",
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
        ):
            accepted = service._owner_transition(
                session, import_id=import_id, fencing_token=stale_token, values=stale_attempt_values
            )
            assert accepted is False
            session.rollback()

        with session_factory() as verify_session:
            resume_import = verify_session.get(ResumeImport, import_id)
            assert resume_import is not None
            # Still PROCESSING under worker B's reclaim -- never overwritten by any of worker A's
            # stale writes above.
            assert resume_import.status == ImportStatus.PROCESSING
            assert resume_import.processing_attempts == 2
            assert resume_import.processing_lease_token == current_token

        # Scenario E: the CURRENT, genuinely-owning token IS accepted.
        accepted = service._owner_transition(
            session,
            import_id=import_id,
            fencing_token=current_token,
            values={
                "status": ImportStatus.QUEUED,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
        )
        session.commit()
        assert accepted is True

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.QUEUED
        assert resume_import.processing_lease_token is None
    engine.dispose()


def test_exhausted_attempts_with_no_active_owner_fails_terminally_once(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-2 independent review "Finding 2" scenario F: once attempts are exhausted AND there is
    no active owner (lease expired, nobody currently holds it), a duplicate delivery fails the row
    terminally -- fenced on `(processing_attempts, status)` since there is no lease token to fence
    on at all in this path -- and a SECOND duplicate observing the same exhausted state is a safe,
    idempotent no-op, never a second competing write."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-9", subject="uid-lease-9"
    )
    storage = _resume_storage(identity_client)

    with session_factory() as session:
        for _ in range(MAX_PROCESSING_ATTEMPTS):
            claim_result = service.claim_import_for_processing(session, import_id=import_id)
            session.commit()
            assert claim_result is not None
            session.execute(
                update(ResumeImport)
                .where(ResumeImport.id == import_id)
                .values(processing_lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            session.commit()

    # Attempts are now exhausted and the lease is expired -- no active owner. Two duplicate
    # deliveries in a row must both resolve safely to the SAME terminal outcome.
    with session_factory() as session:
        service.process_resume_import(session, import_id=import_id, storage=storage)
    with session_factory() as session:
        service.process_resume_import(session, import_id=import_id, storage=storage)

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.FAILED
        assert resume_import.failure_code == ImportFailureCode.INTERNAL_ERROR
        assert resume_import.processing_attempts == MAX_PROCESSING_ATTEMPTS
    engine.dispose()


def test_an_object_that_does_not_match_its_recorded_checksum_is_treated_as_transient(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-2 independent review "Hardening 9": a retrieved object that does not match its own
    recorded `byte_size`/`checksum_sha256` is at least as plausibly a transient truncated/corrupted
    read as genuine tampering -- treated as `TransientProcessingError`, reusing the exact same
    bounded-retry machinery a flaky storage backend already has."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    import_id = _bootstrap_and_upload(
        identity_client, fake_identity_verifier, token="lease-10", subject="uid-lease-10"
    )

    class _CorruptingStorage:
        def put(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
            raise AssertionError("not used by this test")

        def get(self, *, storage_key: str) -> bytes:
            return b"completely different bytes than what was actually uploaded"

        def delete(self, *, storage_key: str) -> None:
            raise AssertionError("not used by this test")

    corrupting_storage: ObjectStoragePort = _CorruptingStorage()
    with session_factory() as session, pytest.raises(TransientProcessingError):
        service.process_resume_import(session, import_id=import_id, storage=corrupting_storage)

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.status == ImportStatus.QUEUED  # released for retry, not terminal yet
        assert resume_import.failure_code is None
    engine.dispose()


def _resume_storage(client: TestClient) -> ObjectStoragePort:
    app = client.app
    assert isinstance(app, FastAPI)
    return cast(ObjectStoragePort, app.state.resume_storage)
