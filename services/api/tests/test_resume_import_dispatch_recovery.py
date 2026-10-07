"""Integration tests: real PostgreSQL, durable dispatch recovery after a broker/producer failure
(docs/adr/0011-resume-import.md "async processing", round-1 independent review "Finding 3",
round-2 independent review "Finding 4"). The `ResumeImport` row itself -- durable at `QUEUED` in
the SAME transaction as its own creation -- is the recorded intent to process; no separate outbox
table is needed. Recovery is now SERVER-OWNED (`service.sweep_stale_queued_imports`,
`tasks.sweep_stale_resume_import_dispatches_task`) -- never a side effect of a client's own `GET`
poll any more. These tests prove a lost dispatch is recoverable, NEVER depends on the client ever
polling again, and that recovery can never hot-loop or duplicate processing.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, update

from tapjob_api.core.config import ApiSettings
from tapjob_api.db.session import create_session_factory
from tapjob_api.resume_import import dependencies as deps
from tapjob_api.resume_import import service, tasks
from tapjob_api.resume_import.limits import DISPATCH_RECOVERY_THRESHOLD_SECONDS
from tapjob_api.resume_import.models import ImportStatus, ResumeImport
from tapjob_api.resume_import.storage import ObjectStoragePort
from tapjob_api.resume_import.tasks import RESUME_IMPORT_PROCESS_TASK_NAME

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
IMPORTS_PATH = "/api/v1/me/resume-imports"


class _FailingTaskProducer:
    """Simulates the Celery broker being unreachable at dispatch time -- `send_task` always
    raises, exactly the failure mode round-1 independent review "Finding 3" describes."""

    def send_task(self, name: str, args: list[str]) -> None:
        raise ConnectionError("simulated broker failure")


@contextmanager
def _overridden_task_producer(client: TestClient, producer: object) -> Iterator[None]:
    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[deps.get_task_producer] = lambda: producer
    try:
        yield
    finally:
        app.dependency_overrides.pop(deps.get_task_producer, None)


def _resume_storage(client: TestClient) -> ObjectStoragePort:
    app = client.app
    assert isinstance(app, FastAPI)
    return cast(ObjectStoragePort, app.state.resume_storage)


def _minimal_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF"


def test_a_broker_failure_at_upload_time_never_loses_the_import_and_never_surfaces_as_a_500(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """The row's own durable `QUEUED` status IS the recorded intent to process -- a dispatch
    failure immediately after is logged and swallowed, never an unhandled 500 for what is
    fundamentally a "the row still exists and will be recovered later" situation."""
    with _overridden_task_producer(identity_client, _FailingTaskProducer()):
        fake_identity_verifier.issue_token("dispatch-1", make_identity(subject="uid-dispatch-1"))
        bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("dispatch-1"))
        assert bootstrap.status_code == HTTPStatus.OK

        response = identity_client.post(
            IMPORTS_PATH,
            headers=bearer_header("dispatch-1"),
            files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        # The broker failure is swallowed -- the client still gets a clean 202, never a 500 for a
        # durably-created row whose dispatch merely failed.
        assert response.status_code == HTTPStatus.ACCEPTED
        import_id = uuid.UUID(response.json()["id"])

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        # The row is NOT lost: it durably exists at QUEUED, the real recorded intent to process.
        assert resume_import.status == ImportStatus.QUEUED
        assert service.should_redispatch_processing(resume_import) is False  # not stale yet
    engine.dispose()


def test_getting_an_import_never_dispatches_anything_as_a_side_effect(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-2 independent review "Finding 4": a `GET` is a pure read now -- even for a stale
    `QUEUED` import, it must never itself trigger a dispatch. Recovery is entirely server-owned."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    fake_identity_verifier.issue_token("dispatch-get", make_identity(subject="uid-dispatch-get"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("dispatch-get"))
    assert bootstrap.status_code == HTTPStatus.OK
    upload = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("dispatch-get"),
        files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert upload.status_code == HTTPStatus.ACCEPTED
    import_id = uuid.UUID(upload.json()["id"])

    with session_factory() as session:
        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == import_id)
            .values(
                created_at=datetime.now(UTC)
                - timedelta(seconds=DISPATCH_RECOVERY_THRESHOLD_SECONDS + 5)
            )
        )
        session.commit()

    get_response = identity_client.get(
        f"{IMPORTS_PATH}/{import_id}", headers=bearer_header("dispatch-get")
    )
    assert get_response.status_code == HTTPStatus.OK

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        # Never touched by the GET -- no `last_dispatch_attempt_at` bookkeeping update either.
        assert resume_import.status == ImportStatus.QUEUED
        assert resume_import.last_dispatch_attempt_at is None
    engine.dispose()


# -- the server-owned sweep itself (service.sweep_stale_queued_imports) --------------------------


def test_a_stale_queued_import_is_a_sweep_candidate_and_the_sweep_stamps_its_own_backoff(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-2 independent review "Finding 4": once a `QUEUED` import has aged past the recovery
    threshold (simulating real elapsed time since a lost dispatch), the SERVER-OWNED sweep -- never
    a client poll -- picks it up, with ZERO subsequent client `GET` at all."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    fake_identity_verifier.issue_token("dispatch-2", make_identity(subject="uid-dispatch-2"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("dispatch-2"))
    assert bootstrap.status_code == HTTPStatus.OK
    upload = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("dispatch-2"),
        files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert upload.status_code == HTTPStatus.ACCEPTED
    import_id = uuid.UUID(upload.json()["id"])

    with session_factory() as session:
        # Simulate real elapsed time: the row has been QUEUED, unclaimed, for longer than the
        # recovery threshold -- with ZERO client interaction since upload.
        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == import_id)
            .values(
                created_at=datetime.now(UTC)
                - timedelta(seconds=DISPATCH_RECOVERY_THRESHOLD_SECONDS + 5)
            )
        )
        session.commit()

    with session_factory() as session:
        stale_ids = service.sweep_stale_queued_imports(session)
    assert import_id in stale_ids

    with session_factory() as session:
        resume_import = session.get(ResumeImport, import_id)
        assert resume_import is not None
        assert resume_import.last_dispatch_attempt_at is not None
        # The row itself is otherwise untouched -- the sweep does not claim/process it, it only
        # marks "a redispatch was attempted"; the worker's own claim is what actually processes it.
        assert resume_import.status == ImportStatus.QUEUED

    # A SECOND sweep cycle immediately after must NOT re-select the same row -- the backoff
    # bookkeeping just written prevents a hot loop.
    with session_factory() as session:
        second_stale_ids = service.sweep_stale_queued_imports(session)
    assert import_id not in second_stale_ids
    engine.dispose()


def test_a_fresh_queued_import_is_never_a_sweep_candidate_before_the_threshold(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """A normal, recently-created import must never be swept just because a sweep cycle happened
    to run quickly -- staleness is a recovery signal, not a default."""
    fake_identity_verifier.issue_token("dispatch-3", make_identity(subject="uid-dispatch-3"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("dispatch-3"))
    assert bootstrap.status_code == HTTPStatus.OK
    upload = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("dispatch-3"),
        files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert upload.status_code == HTTPStatus.ACCEPTED
    import_id = uuid.UUID(upload.json()["id"])

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        stale_ids = service.sweep_stale_queued_imports(session)
    assert import_id not in stale_ids
    engine.dispose()


def test_a_stuck_processing_row_with_an_expired_lease_is_also_a_sweep_candidate(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Round-2 independent review "Finding 4" backstop for the residual edge case where a Celery
    task's own bounded retry chain gives up entirely (`MaxRetriesExceededError`) before ever
    reclaiming the row: nothing else would redispatch it again otherwise. The sweep also catches
    any `PROCESSING` row whose lease has already expired, not only `QUEUED` rows."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    fake_identity_verifier.issue_token("dispatch-4", make_identity(subject="uid-dispatch-4"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("dispatch-4"))
    assert bootstrap.status_code == HTTPStatus.OK
    upload = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("dispatch-4"),
        files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert upload.status_code == HTTPStatus.ACCEPTED
    import_id = uuid.UUID(upload.json()["id"])

    with session_factory() as session:
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
        stale_ids = service.sweep_stale_queued_imports(session)
    assert import_id in stale_ids
    engine.dispose()


# -- the self-rescheduling sweep TASK itself -------------------------------------------------------


def test_the_sweep_task_dispatches_every_stale_import_and_reschedules_exactly_once(
    identity_client: TestClient,
    fake_identity_verifier: FakeIdentityVerifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the TASK wrapper itself, not just `service.sweep_stale_queued_imports` in isolation:
    it dispatches every id the sweep returns via `self.app.send_task`, and reschedules its own next
    run EXACTLY once per invocation (round-2 independent review "Finding 4": never a hot loop,
    never unbounded)."""
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)

    fake_identity_verifier.issue_token("dispatch-5", make_identity(subject="uid-dispatch-5"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("dispatch-5"))
    assert bootstrap.status_code == HTTPStatus.OK
    upload = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("dispatch-5"),
        files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert upload.status_code == HTTPStatus.ACCEPTED
    import_id = uuid.UUID(upload.json()["id"])

    with session_factory() as session:
        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == import_id)
            .values(
                created_at=datetime.now(UTC)
                - timedelta(seconds=DISPATCH_RECOVERY_THRESHOLD_SECONDS + 5)
            )
        )
        session.commit()

    sent: list[tuple[str, list[str]]] = []
    rescheduled: list[float | None] = []

    class _FakeApp:
        def send_task(self, name: str, args: list[str]) -> None:
            sent.append((name, args))

    def _fake_apply_async(*, countdown: float | None = None) -> None:
        rescheduled.append(countdown)

    monkeypatch.setattr(tasks, "_session_factory", lambda: session_factory())
    monkeypatch.setattr(tasks.sweep_stale_resume_import_dispatches_task, "app", _FakeApp())
    monkeypatch.setattr(
        tasks.sweep_stale_resume_import_dispatches_task, "apply_async", _fake_apply_async
    )

    tasks.sweep_stale_resume_import_dispatches_task.run()

    assert sent == [(RESUME_IMPORT_PROCESS_TASK_NAME, [str(import_id)])]
    assert len(rescheduled) == 1  # exactly one reschedule per invocation -- never recursive/hot
    engine.dispose()
