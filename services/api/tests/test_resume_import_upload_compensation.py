"""Integration tests: real PostgreSQL, upload-transaction compensation (docs/adr/0011-resume-
import.md "object storage failure semantics", round-2 independent review "Finding 6"). A DB
failure ANYWHERE in the upload transaction -- the initial flush, the audit-event write, or the
final commit itself -- must never leave an orphaned, unreferenced object durably sitting in
storage with no `ResumeImport` row able to ever find/retain/delete it again. Zero real R2 --
`InMemoryObjectStorage` (via `identity_client`'s own app state) throughout.
"""

import logging
import uuid as uuid_module
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine

from tapjob_api.core.config import ApiSettings
from tapjob_api.db.session import create_session_factory
from tapjob_api.resume_import.models import ResumeImport
from tapjob_api.resume_import.storage import ObjectStorageError, ObjectStoragePort

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
IMPORTS_PATH = "/api/v1/me/resume-imports"


def _resume_storage(client: TestClient) -> ObjectStoragePort:
    app = client.app
    assert isinstance(app, FastAPI)
    return cast(ObjectStoragePort, app.state.resume_storage)


def _minimal_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF"


@contextmanager
def _failing_commit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patches `sqlalchemy.orm.Session.commit` to raise on its NEXT call only -- simulates the
    final transaction commit itself failing (e.g. a connectivity blip, a deferred constraint), as
    opposed to the service layer's own earlier, narrower flush failure (already covered by `Finding
    1`'s original compensation test)."""
    from sqlalchemy.orm import Session

    original_commit = Session.commit
    state = {"raised": False}

    def _raising_commit(self: Session) -> None:
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("simulated commit failure")
        original_commit(self)

    monkeypatch.setattr(Session, "commit", _raising_commit)
    try:
        yield
    finally:
        monkeypatch.setattr(Session, "commit", original_commit)


@contextmanager
def _failing_flush(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patches `Session.flush` to raise exactly when a pending `ResumeImport` is actually being
    flushed -- simulates `service.create_resume_import`'s OWN initial flush failing (scenario A)
    without also tripping an UNRELATED autoflush earlier in the same request (e.g. the read
    `identity_deps.get_current_user` issues to resolve the bearer token, before this route's own
    code ever runs)."""
    from sqlalchemy.orm import Session

    from tapjob_api.resume_import.models import ResumeImport

    original_flush = Session.flush
    state = {"raised": False}

    def _raising_flush(self: Session, objects: object = None) -> None:
        if not state["raised"] and any(isinstance(obj, ResumeImport) for obj in self.new):
            state["raised"] = True
            raise RuntimeError("simulated initial flush failure")
        original_flush(self, objects)  # type: ignore[arg-type]

    monkeypatch.setattr(Session, "flush", _raising_flush)
    try:
        yield
    finally:
        monkeypatch.setattr(Session, "flush", original_flush)


def test_an_initial_db_flush_failure_triggers_a_compensating_delete(
    identity_client: TestClient,
    fake_identity_verifier: FakeIdentityVerifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario A of round-2 independent review "Finding 6" (the narrower failure window round-1
    already compensated for, re-verified here as a real HTTP-level test, not only unit-level)."""
    fake_identity_verifier.issue_token("compensate-0", make_identity(subject="uid-compensate-0"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("compensate-0"))
    assert bootstrap.status_code == HTTPStatus.OK

    storage = _resume_storage(identity_client)
    put_keys: list[str] = []
    original_put = storage.put

    def _recording_put(*, storage_key: str, content: bytes, content_type: str):  # type: ignore[no-untyped-def]
        stored = original_put(storage_key=storage_key, content=content, content_type=content_type)
        put_keys.append(stored.storage_key)
        return stored

    monkeypatch.setattr(storage, "put", _recording_put)

    with (
        _failing_flush(monkeypatch),
        pytest.raises(RuntimeError, match="simulated initial flush failure"),
    ):
        identity_client.post(
            IMPORTS_PATH,
            headers=bearer_header("compensate-0"),
            files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
    assert len(put_keys) == 1
    with pytest.raises(ObjectStorageError):
        storage.get(storage_key=put_keys[0])


def test_a_final_commit_failure_after_a_durable_storage_write_triggers_a_compensating_delete(
    identity_client: TestClient,
    fake_identity_verifier: FakeIdentityVerifier,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scenario C of round-2 independent review "Finding 6": the row's own flush already
    succeeded, but the FINAL commit (after the audit-event write) fails -- the object was already
    durably written to storage by then, so it must be compensated, never left orphaned."""
    fake_identity_verifier.issue_token("compensate-1", make_identity(subject="uid-compensate-1"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("compensate-1"))
    assert bootstrap.status_code == HTTPStatus.OK

    storage = _resume_storage(identity_client)
    put_keys: list[str] = []
    original_put = storage.put

    def _recording_put(*, storage_key: str, content: bytes, content_type: str):  # type: ignore[no-untyped-def]
        stored = original_put(storage_key=storage_key, content=content, content_type=content_type)
        put_keys.append(stored.storage_key)
        return stored

    monkeypatch.setattr(storage, "put", _recording_put)

    with (
        caplog.at_level(logging.ERROR),
        _failing_commit(monkeypatch),
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        identity_client.post(
            IMPORTS_PATH,
            headers=bearer_header("compensate-1"),
            files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
    assert len(put_keys) == 1
    with pytest.raises(ObjectStorageError):
        storage.get(storage_key=put_keys[0])
    assert any("compensating delete" in record.message for record in caplog.records)


def test_a_compensating_delete_failure_never_masks_the_original_db_failure(
    identity_client: TestClient,
    fake_identity_verifier: FakeIdentityVerifier,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scenario D of round-2 independent review "Finding 6": if the compensating delete ITSELF
    fails, the original DB/commit failure is still what the caller sees -- never masked by a
    secondary cleanup error -- and the leak is still logged/observable, not silently swallowed."""
    fake_identity_verifier.issue_token("compensate-2", make_identity(subject="uid-compensate-2"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("compensate-2"))
    assert bootstrap.status_code == HTTPStatus.OK

    storage = _resume_storage(identity_client)
    put_keys: list[str] = []
    original_put = storage.put

    def _recording_put(*, storage_key: str, content: bytes, content_type: str):  # type: ignore[no-untyped-def]
        stored = original_put(storage_key=storage_key, content=content, content_type=content_type)
        put_keys.append(stored.storage_key)
        return stored

    def _failing_delete(*, storage_key: str) -> None:
        raise ObjectStorageError("simulated: the compensating delete itself failed")

    monkeypatch.setattr(storage, "put", _recording_put)
    monkeypatch.setattr(storage, "delete", _failing_delete)

    with (
        caplog.at_level(logging.ERROR),
        _failing_commit(monkeypatch),
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        # The ORIGINAL simulated commit failure is still what propagates -- never masked by the
        # compensating delete's own (different) RuntimeError.
        identity_client.post(
            IMPORTS_PATH,
            headers=bearer_header("compensate-2"),
            files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
    assert len(put_keys) == 1
    # The leak is real and observable (the delete genuinely failed) -- never silently masked.
    assert storage.get(storage_key=put_keys[0]) is not None
    assert any("compensating delete" in record.message for record in caplog.records)


def test_a_successful_upload_leaves_the_object_in_place(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """Scenario E: the ordinary happy path never triggers any compensation at all."""
    fake_identity_verifier.issue_token("compensate-3", make_identity(subject="uid-compensate-3"))
    bootstrap = identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("compensate-3"))
    assert bootstrap.status_code == HTTPStatus.OK

    response = identity_client.post(
        IMPORTS_PATH,
        headers=bearer_header("compensate-3"),
        files={"file": ("resume.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == HTTPStatus.ACCEPTED

    storage = _resume_storage(identity_client)
    # Re-fetch the row's own storage_key via a fresh GET rather than reaching into internals.
    import_id = response.json()["id"]
    get_response = identity_client.get(
        f"{IMPORTS_PATH}/{import_id}", headers=bearer_header("compensate-3")
    )
    assert get_response.status_code == HTTPStatus.OK
    # The object is still retrievable -- no compensation occurred.
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        resume_import = session.get(ResumeImport, uuid_module.UUID(import_id))
        assert resume_import is not None
        assert storage.get(storage_key=resume_import.storage_key) is not None
    engine.dispose()
