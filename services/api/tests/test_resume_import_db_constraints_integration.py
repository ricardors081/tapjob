"""Integration tests: real-PostgreSQL-enforced invariants that the service layer already respects
but that the schema itself must also guarantee (docs/adr/0011-resume-import.md "import record",
round-1 independent review "Hardening 9"). These tests deliberately bypass
`tapjob_api.resume_import.service` and write directly through a raw ORM `Session` -- the point is
to prove the DATABASE itself rejects an invariant violation, not merely that the service layer
happens to avoid constructing one. Real PostgreSQL/Redis.
"""

import uuid
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.core.config import ApiSettings
from tapjob_api.resume_import.models import ResumeImportCandidate

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
IMPORTS_PATH = "/api/v1/me/resume-imports"


def _bootstrap(
    client: TestClient, verifier: FakeIdentityVerifier, *, token: str, subject: str
) -> str:
    verifier.issue_token(token, make_identity(subject=subject))
    response = client.post(BOOTSTRAP_PATH, headers=bearer_header(token))
    assert response.status_code == HTTPStatus.OK
    return str(response.json()["user"]["id"])


def _upload_resume_import_id(client: TestClient, token: str) -> uuid.UUID:
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


def _make_bare_candidate(
    *, user_id: uuid.UUID, resume_import_id: uuid.UUID
) -> ResumeImportCandidate:
    return ResumeImportCandidate(
        id=uuid.uuid4(),
        resume_import_id=resume_import_id,
        user_id=user_id,
        candidate_kind="skill",
        value_json={"kind": "skill", "name": "Python"},
        confidence=0.5,
    )


def test_a_candidate_whose_user_matches_its_imports_real_owner_is_accepted(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    user_a_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-a", subject="uid-a"
    )
    import_a_id = _upload_resume_import_id(identity_client, "token-a")

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            session.add(
                _make_bare_candidate(user_id=uuid.UUID(user_a_id), resume_import_id=import_a_id)
            )
            session.commit()  # must not raise -- the pair genuinely belongs together.
    finally:
        engine.dispose()


def test_a_candidate_whose_user_does_not_match_its_imports_real_owner_is_rejected_by_the_db(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """The structural gap round-1 independent review's "Hardening 9" found: `user_id` and
    `resume_import_id` were previously two INDEPENDENT FKs, so nothing in the schema itself
    rejected a candidate whose `user_id` belongs to User A but whose `resume_import_id` points at
    User B's `ResumeImport`. The composite FK added to fix this must make Postgres reject the
    insert outright, never merely rely on `tapjob_api.resume_import.service` never constructing
    one."""
    _bootstrap(identity_client, fake_identity_verifier, token="token-a", subject="uid-a")
    import_a_id = _upload_resume_import_id(identity_client, "token-a")

    user_b_id = _bootstrap(
        identity_client, fake_identity_verifier, token="token-b", subject="uid-b"
    )

    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session:
            # User B's id, paired with User A's resume_import_id -- a mismatched pair.
            session.add(
                _make_bare_candidate(user_id=uuid.UUID(user_b_id), resume_import_id=import_a_id)
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()
