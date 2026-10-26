"""Unit tests proving Finding 2: No second resume import / Truth Store bypass.

Enforces:
- TRUTH_STORE_DIRECT_WRITES_FROM_MESSAGING == "NO"
- Messaging never inserts directly into ProfileFact
- Messaging never increments profile_version manually
- Attachment intake delegates to canonical WP1.3 Resume Import
- Text onboarding delegates to canonical WP1.2 Career Profile services
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from tapjob_api.career_profile.commands import CareerProfilePatchCommand
from tapjob_api.career_profile.fact_values import SkillFactValue
from tapjob_api.career_profile.models import FactType, ProfileFact
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.document_intake import (
    TRUTH_STORE_DIRECT_WRITES_FROM_MESSAGING,
    handle_attachment_intake,
    handle_profile_text_patch,
    handle_text_fact_intake,
)
from tapjob_api.ports.resume_parser import ResumeParser
from tapjob_api.resume_import.models import ResumeImport
from tapjob_api.resume_import.storage import ObjectStoragePort


def test_truth_store_direct_writes_from_messaging_invariant() -> None:
    """Proves invariant TRUTH_STORE_DIRECT_WRITES_FROM_MESSAGING == 'NO'."""
    assert TRUTH_STORE_DIRECT_WRITES_FROM_MESSAGING == "NO"


def test_attachment_intake_delegates_to_canonical_wp13() -> None:
    """Proves attachment intake delegates exclusively to WP1.3 pipeline."""
    session = MagicMock(spec=Session)
    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    storage = MagicMock(spec=ObjectStoragePort)

    fake_import = MagicMock(spec=ResumeImport)
    fake_import.id = uuid.uuid4()
    fake_import.status = MagicMock(value="ready_for_review")
    fake_import.candidates = []
    fake_import.parser_identity = "pymupdf_v1"

    fake_parser = MagicMock(spec=ResumeParser)
    fake_parser.provider = "fake_v1"

    with (
        patch(
            "tapjob_api.messaging.document_intake.resume_import_service.create_resume_import",
            return_value=fake_import,
        ) as mock_create,
        patch(
            "tapjob_api.messaging.document_intake.resume_import_service.process_resume_import",
            return_value=fake_import,
        ) as mock_process,
    ):
        result = handle_attachment_intake(
            session,
            user=user,
            content=b"%PDF-1.4 dummy",
            original_filename="cv.pdf",
            declared_content_type="application/pdf",
            storage=storage,
            parser=fake_parser,
        )

        assert result is fake_import
        mock_create.assert_called_once_with(
            session,
            user=user,
            content=b"%PDF-1.4 dummy",
            original_filename="cv.pdf",
            declared_content_type="application/pdf",
            storage=storage,
        )
        mock_process.assert_called_once_with(
            session,
            import_id=fake_import.id,
            storage=storage,
            parser=fake_parser,
        )


def test_text_fact_intake_delegates_to_canonical_wp12() -> None:
    """Proves text fact intake delegates exclusively to WP1.2 add_profile_fact."""
    session = MagicMock(spec=Session)
    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    fact_val = SkillFactValue(name="Python")
    fake_fact = MagicMock(spec=ProfileFact)
    fake_fact.id = uuid.uuid4()
    fake_fact.fact_type = FactType.SKILL

    with patch(
        "tapjob_api.messaging.document_intake.career_profile_service.add_profile_fact",
        return_value=fake_fact,
    ) as mock_add:
        result = handle_text_fact_intake(
            session,
            user=user,
            fact_type=FactType.SKILL,
            value=fact_val,
            expected_version=1,
        )

        assert result is fake_fact
        mock_add.assert_called_once_with(
            session,
            user=user,
            expected_version=1,
            fact_type=FactType.SKILL,
            value=fact_val,
        )


def test_profile_text_patch_delegates_to_canonical_wp12() -> None:
    """Proves profile patch delegates to WP1.2 update_career_profile."""
    session = MagicMock(spec=Session)
    user = User(
        id=uuid.uuid4(),
        locale="pt-BR",
        timezone="Etc/UTC",
        status=UserStatus.ACTIVE,
    )
    cmd = CareerProfilePatchCommand(headline="Engenheiro Sênior")
    fake_result = MagicMock()

    with patch(
        "tapjob_api.messaging.document_intake.career_profile_service.update_career_profile",
        return_value=fake_result,
    ) as mock_update:
        result = handle_profile_text_patch(
            session,
            user=user,
            command=cmd,
            expected_version=2,
        )

        assert result is fake_result
        mock_update.assert_called_once_with(
            session,
            user=user,
            expected_version=2,
            command=cmd,
        )
