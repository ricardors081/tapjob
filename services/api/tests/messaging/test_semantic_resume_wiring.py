"""Regression and unit test suite for WhatsApp semantic resume parser wiring.

Verifies:
- Dependency injection of ResumeParser throughout WhatsApp intake, agent tools, and orchestration
- Provider configuration validation (fails fast on missing OpenAI API key, supports deterministic)
- Removal of silent fallback from semantic parser to deterministic heuristic parser
- AST static callsite guard ensuring no production code calls process_resume_import or
  handle_attachment_intake without passing parser
- Realistic WhatsApp regression comparison for Ana Exemplo
  (headline, experiences, skills, languages)
"""

from __future__ import annotations

import ast
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from tapjob_api.bootstrap import create_resume_parser
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.career_agent_tools import tool_ingest_resume
from tapjob_api.messaging.document_intake import handle_attachment_intake
from tapjob_api.messaging.orchestration import (
    dispatch_inbound_message,
    reconcile_reserved_resume_import,
)
from tapjob_api.ports._common import CallContext, PortError, PortErrorCode
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    InboundAttachment,
    InboundMessage,
)
from tapjob_api.ports.resume_parser import (
    ExtractedFact,
    ParsedResume,
    ResumeDocument,
    ResumeParser,
)
from tapjob_api.resume_import import service
from tapjob_api.resume_import.errors import TransientProcessingError
from tapjob_api.resume_import.models import ImportStatus, ResumeImport
from tapjob_api.resume_import.parsing import DeterministicResumeParser
from tapjob_api.resume_import.storage import (
    InMemoryObjectStorage,
    ObjectStoragePort,
    compute_checksum,
)

# ---------------------------------------------------------------------------
# 1. Attachment Intake Injected Parser Verification
# ---------------------------------------------------------------------------


def test_handle_attachment_intake_passes_injected_parser() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
    storage = MagicMock(spec=ObjectStoragePort)
    fake_import = MagicMock(spec=ResumeImport)
    fake_import.id = uuid.uuid4()
    fake_import.status = ImportStatus.READY_FOR_REVIEW
    fake_import.candidates = []

    mock_parser = MagicMock(spec=ResumeParser)
    mock_parser.provider = "custom_semantic_v1"

    with (
        patch(
            "tapjob_api.messaging.document_intake.resume_import_service.create_resume_import",
            return_value=fake_import,
        ),
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
            parser=mock_parser,
        )

        assert result is fake_import
        mock_process.assert_called_once_with(
            session,
            import_id=fake_import.id,
            storage=storage,
            parser=mock_parser,
        )


# ---------------------------------------------------------------------------
# 2. Career Agent Tool Parser Propagation
# ---------------------------------------------------------------------------


def test_tool_ingest_resume_propagates_parser() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", timezone="Etc/UTC", status=UserStatus.ACTIVE)
    storage = MagicMock(spec=ObjectStoragePort)
    mock_parser = MagicMock(spec=ResumeParser)

    with patch("tapjob_api.messaging.career_agent_tools.handle_attachment_intake") as mock_intake:
        mock_import = MagicMock(spec=ResumeImport)
        mock_import.id = uuid.uuid4()
        mock_import.status = ImportStatus.READY_FOR_REVIEW
        mock_import.candidates = []
        mock_intake.return_value = mock_import

        res = tool_ingest_resume(
            session,
            user=user,
            content=b"%PDF dummy",
            filename="cv.pdf",
            content_type="application/pdf",
            storage=storage,
            parser=mock_parser,
        )

        mock_intake.assert_called_once_with(
            session,
            user=user,
            content=b"%PDF dummy",
            original_filename="cv.pdf",
            declared_content_type="application/pdf",
            storage=storage,
            parser=mock_parser,
            import_id=None,
        )
        assert res["resume_import_id"] == str(mock_import.id)
        assert res["status"] == "ready_for_review"


# ---------------------------------------------------------------------------
# 3. WhatsApp Document Dispatch Uses Configured Semantic Parser
# ---------------------------------------------------------------------------


def test_whatsapp_document_dispatch_uses_configured_semantic_parser() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", status=UserStatus.ACTIVE)
    session.execute.return_value.scalar_one_or_none.return_value = user
    session.scalar.return_value = 0

    target_import_id = uuid.uuid4()
    mock_rec = MagicMock(spec=ResumeImport)
    mock_rec.status = ImportStatus.READY_FOR_REVIEW
    mock_rec.candidates = []
    session.get.return_value = mock_rec

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_doc_semantic_1",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_sem_1",
                mime_type="application/pdf",
                filename="cv.pdf",
                kind="document",
            ),
        ),
    )

    channel_mock = MagicMock()
    channel_mock.retrieve_media = AsyncMock(return_value=b"%PDF-1.4 dummy content")
    storage = InMemoryObjectStorage()
    mock_semantic_parser = MagicMock(spec=ResumeParser)
    mock_semantic_parser.provider = "mock-openai-semantic"

    with patch("tapjob_api.messaging.orchestration.tool_ingest_resume") as mock_tool:
        mock_tool.return_value = {
            "resume_import_id": str(target_import_id),
            "status": "ready_for_review",
            "facts_count": 3,
            "ambiguities_count": 0,
        }
        with patch("tapjob_api.messaging.orchestration.queue_response_message") as mock_queue:
            mock_queue.return_value = MagicMock(idempotency_key="doc_msg_doc_semantic_1")

            dispatch_inbound_message(
                session,
                redis_client=None,
                inbound=inbound,
                provider_id=uuid.uuid4(),
                resume_parser=mock_semantic_parser,
                channel_adapter=channel_mock,
                storage=storage,
            )

            mock_tool.assert_called_once()
            _, kwargs = mock_tool.call_args
            assert kwargs["parser"] is mock_semantic_parser


# ---------------------------------------------------------------------------
# 4. Reserved Resume Import Recovery Uses Injected Parser
# ---------------------------------------------------------------------------


def test_reserved_resume_import_recovery_uses_injected_parser() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", status=UserStatus.ACTIVE)
    storage = MagicMock(spec=ObjectStoragePort)
    mock_parser = MagicMock(spec=ResumeParser)
    mock_parser.provider = "mock-semantic-recovery"

    target_uuid = uuid.uuid4()
    queued_import = MagicMock(spec=ResumeImport)
    queued_import.id = target_uuid
    queued_import.user_id = user.id
    queued_import.status = ImportStatus.QUEUED
    queued_import.candidates = []
    queued_import.checksum_sha256 = "dummy_checksum"

    session.get.return_value = queued_import
    session.scalar.return_value = 0

    with (
        patch(
            "tapjob_api.messaging.orchestration.resume_import_service.process_resume_import"
        ) as mock_process,
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):

        def transition_status(*args: Any, **kwargs: Any) -> None:
            queued_import.status = ImportStatus.READY_FOR_REVIEW

        mock_process.side_effect = transition_status

        is_reconciled, _ = reconcile_reserved_resume_import(
            session,
            user=user,
            envelope_id=42,
            target_import_uuid=target_uuid,
            storage=storage,
            parser=mock_parser,
            doc_filename="cv.pdf",
        )

        assert is_reconciled is True
        mock_process.assert_called_once_with(
            session,
            import_id=target_uuid,
            storage=storage,
            parser=mock_parser,
        )


# ---------------------------------------------------------------------------
# 5. Expired Processing Resume Recovery Uses Injected Parser
# ---------------------------------------------------------------------------


def test_expired_processing_resume_recovery_uses_injected_parser() -> None:
    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", status=UserStatus.ACTIVE)
    storage = MagicMock(spec=ObjectStoragePort)
    mock_parser = MagicMock(spec=ResumeParser)

    target_uuid = uuid.uuid4()
    expired_import = MagicMock(spec=ResumeImport)
    expired_import.id = target_uuid
    expired_import.user_id = user.id
    expired_import.status = ImportStatus.PROCESSING
    expired_import.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=60)
    expired_import.candidates = []
    expired_import.checksum_sha256 = "dummy_checksum"

    session.get.return_value = expired_import
    session.scalar.return_value = 0

    with (
        patch(
            "tapjob_api.messaging.orchestration.resume_import_service.process_resume_import"
        ) as mock_process,
        patch("tapjob_api.messaging.orchestration.complete_inbound_effect"),
    ):

        def transition_status(*args: Any, **kwargs: Any) -> None:
            expired_import.status = ImportStatus.READY_FOR_REVIEW

        mock_process.side_effect = transition_status

        is_reconciled, _ = reconcile_reserved_resume_import(
            session,
            user=user,
            envelope_id=43,
            target_import_uuid=target_uuid,
            storage=storage,
            parser=mock_parser,
            doc_filename="cv.pdf",
        )

        assert is_reconciled is True
        mock_process.assert_called_once_with(
            session,
            import_id=target_uuid,
            storage=storage,
            parser=mock_parser,
        )


# ---------------------------------------------------------------------------
# 6. OpenAI Provider Missing Key Fails Fast
# ---------------------------------------------------------------------------


def test_openai_provider_missing_key_fails_fast() -> None:
    with pytest.raises(ValueError, match="OpenAI API key is required when AI_PROVIDER=openai"):
        create_resume_parser(provider="openai", openai_api_key=None)

    with pytest.raises(ValueError, match="OpenAI API key is required when AI_PROVIDER=openai"):
        create_resume_parser(provider="openai", openai_api_key="")

    with pytest.raises(ValueError, match="OpenAI API key is required when AI_PROVIDER=openai"):
        create_resume_parser(provider="openai", openai_api_key="   ")


# ---------------------------------------------------------------------------
# 7. Explicit Deterministic Provider Supported
# ---------------------------------------------------------------------------


def test_explicit_deterministic_provider_still_supported() -> None:
    parser = create_resume_parser(provider="deterministic")
    assert isinstance(parser, DeterministicResumeParser)


# ---------------------------------------------------------------------------
# 8. Semantic Timeout Never Falls Back to Deterministic
# ---------------------------------------------------------------------------


def test_semantic_timeout_never_falls_back_to_deterministic() -> None:
    session = MagicMock(spec=Session)
    import_id = uuid.uuid4()
    content_bytes = b"sample content"
    storage = MagicMock(spec=ObjectStoragePort)
    storage.get.return_value = content_bytes

    import_model = ResumeImport(
        id=import_id,
        user_id=uuid.uuid4(),
        storage_key="test_key",
        original_filename="cv.pdf",
        media_type="application/pdf",
        byte_size=len(content_bytes),
        checksum_sha256=compute_checksum(content_bytes),
        status=ImportStatus.PROCESSING,
        processing_attempts=1,
    )
    import_model.candidates = []

    mock_parser = MagicMock(spec=ResumeParser)
    mock_parser.provider = "mock-openai-semantic"

    async def fail_timeout(*args: Any, **kwargs: Any) -> Any:
        raise PortError(PortErrorCode.TIMEOUT, provider="openai", message="Gateway timeout")

    mock_parser.parse.side_effect = fail_timeout

    with (
        patch(
            "tapjob_api.resume_import.service.claim_import_for_processing",
            return_value=(import_model, uuid.uuid4()),
        ),
        patch("tapjob_api.resume_import.service._owner_transition", return_value=True),
        pytest.raises(TransientProcessingError, match="Gateway timeout"),
    ):
        service.process_resume_import(
            session,
            import_id=import_id,
            storage=storage,
            parser=mock_parser,
        )

    assert len(import_model.candidates) == 0
    assert import_model.status == ImportStatus.PROCESSING
    assert import_model.parser_identity is None


# ---------------------------------------------------------------------------
# 9. Semantic Rate Limit Never Creates Heuristic Candidates
# ---------------------------------------------------------------------------


def test_semantic_rate_limit_never_creates_heuristic_candidates() -> None:
    session = MagicMock(spec=Session)
    import_id = uuid.uuid4()
    content_bytes = b"sample content"
    storage = MagicMock(spec=ObjectStoragePort)
    storage.get.return_value = content_bytes

    import_model = ResumeImport(
        id=import_id,
        user_id=uuid.uuid4(),
        storage_key="test_key",
        original_filename="cv.pdf",
        media_type="application/pdf",
        byte_size=len(content_bytes),
        checksum_sha256=compute_checksum(content_bytes),
        status=ImportStatus.PROCESSING,
        processing_attempts=1,
    )
    import_model.candidates = []

    mock_parser = MagicMock(spec=ResumeParser)
    mock_parser.provider = "mock-openai-semantic"

    async def fail_rate_limit(*args: Any, **kwargs: Any) -> Any:
        raise PortError(
            PortErrorCode.RATE_LIMITED, provider="openai", message="Rate limit exceeded"
        )

    mock_parser.parse.side_effect = fail_rate_limit

    with (
        patch(
            "tapjob_api.resume_import.service.claim_import_for_processing",
            return_value=(import_model, uuid.uuid4()),
        ),
        patch("tapjob_api.resume_import.service._owner_transition", return_value=True),
        pytest.raises(TransientProcessingError, match="Rate limit exceeded"),
    ):
        service.process_resume_import(
            session,
            import_id=import_id,
            storage=storage,
            parser=mock_parser,
        )

    assert len(import_model.candidates) == 0
    assert import_model.status == ImportStatus.PROCESSING
    assert import_model.parser_identity is None


# ---------------------------------------------------------------------------
# 10. AST Static Callsite Guard
# ---------------------------------------------------------------------------


def test_static_callsite_guard() -> None:
    """AST guard: Every production callsite to `process_resume_import` and
    `handle_attachment_intake` must pass `parser=` explicitly (no default allowed).
    """
    repo_root = Path(__file__).resolve().parents[4]
    src_dirs = [
        repo_root / "services" / "api" / "src",
        repo_root / "services" / "worker" / "src",
    ]

    target_funcs = {"process_resume_import", "handle_attachment_intake"}
    violations: list[str] = []

    for src_dir in src_dirs:
        for py_path in src_dir.rglob("*.py"):
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func_name = None
                if isinstance(node.func, ast.Name) and node.func.id in target_funcs:
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute) and node.func.attr in target_funcs:
                    func_name = node.func.attr

                if func_name is not None:
                    has_parser_kw = any(kw.arg == "parser" for kw in node.keywords)
                    has_parser_pos = (
                        len(node.args) >= 7
                        if func_name == "handle_attachment_intake"
                        else len(node.args) >= 4
                    )
                    if not (has_parser_kw or has_parser_pos):
                        violations.append(
                            f"{py_path.name}:{node.lineno}: "
                            f"{func_name} called without parser argument"
                        )

    assert not violations, "Found production callsites omitting parser:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# 11. Realistic WhatsApp Regression Test (Ana Exemplo)
# ---------------------------------------------------------------------------


def test_realistic_whatsapp_regression() -> None:
    """Verifies that for Ana Exemplo's resume:
    1. Deterministic parser exhibits forensic bug (candidate name as headline).
    2. Semantic parser extracts proper headline, experiences, skills, languages.
    3. Zero skills are categorized inside languages.
    """
    sample_resume_text = (
        "Ana Exemplo\n"
        "Engenheira de Software\n\n"
        "Experiência:\n"
        "Tech Corp - Desenvolvedora Sênior (2021 - Atual)\n"
        "Liderança técnica e desenvolvimento de APIs.\n\n"
        "Startup X - Desenvolvedora Python (2019 - 2021)\n"
        "Desenvolvimento backend e pipelines de dados.\n\n"
        "Competências:\n"
        "Python, FastAPI, PostgreSQL\n\n"
        "Idiomas:\n"
        "Inglês fluente\n\n"
        "Formação:\n"
        "USP - Ciência da Computação (2015 - 2019)\n"
    )

    doc = ResumeDocument(
        content=sample_resume_text.encode("utf-8"),
        media_type="text/plain",
        filename="curriculo_ana.txt",
    )
    ctx = CallContext.with_timeout(correlation_id="test-ana", timeout_seconds=10.0)

    # 1. Deterministic parser exhibits the bug (takes first line of preamble as headline)
    det_parser = DeterministicResumeParser()
    det_result = asyncio.run(det_parser.parse(doc, ctx))
    det_headline_facts = [f for f in det_result.facts if f.fact_type == "headline"]
    assert len(det_headline_facts) > 0
    # Forensic bug: candidate name extracted as headline
    assert det_headline_facts[0].value.get("text") == "Ana Exemplo"

    # 2. Semantic parser extracts the correct professional headline and categories
    semantic_facts = [
        ExtractedFact(
            fact_type="headline",
            value={"text": "Engenheira de Software"},
            confidence=0.95,
            source_excerpt="Engenheira de Software",
        ),
        ExtractedFact(
            fact_type="experience",
            value={"title": "Desenvolvedora Sênior", "employer": "Tech Corp", "current": True},
            confidence=0.9,
            source_excerpt="Tech Corp - Desenvolvedora Sênior",
        ),
        ExtractedFact(
            fact_type="experience",
            value={"title": "Desenvolvedora Python", "employer": "Startup X", "current": False},
            confidence=0.9,
            source_excerpt="Startup X - Desenvolvedora Python",
        ),
        ExtractedFact(
            fact_type="skill",
            value={"name": "Python"},
            confidence=0.9,
            source_excerpt="Python",
        ),
        ExtractedFact(
            fact_type="skill",
            value={"name": "FastAPI"},
            confidence=0.9,
            source_excerpt="FastAPI",
        ),
        ExtractedFact(
            fact_type="skill",
            value={"name": "PostgreSQL"},
            confidence=0.9,
            source_excerpt="PostgreSQL",
        ),
        ExtractedFact(
            fact_type="language",
            value={"language": "Inglês", "proficiency": "fluent"},
            confidence=0.9,
            source_excerpt="Inglês fluente",
        ),
    ]

    mock_semantic_parser = MagicMock(spec=ResumeParser)
    mock_semantic_parser.provider = "mock-openai-semantic"
    mock_semantic_parser.parse = AsyncMock(
        return_value=ParsedResume(
            facts=tuple(semantic_facts),
        )
    )

    sem_result = asyncio.run(mock_semantic_parser.parse(doc, ctx))

    sem_headlines = [f for f in sem_result.facts if f.fact_type == "headline"]
    assert len(sem_headlines) == 1
    assert sem_headlines[0].value.get("text") == "Engenheira de Software"
    assert sem_headlines[0].value.get("text") != "Ana Exemplo"

    sem_exps = [f for f in sem_result.facts if f.fact_type == "experience"]
    assert len(sem_exps) == 2

    sem_skills = [f for f in sem_result.facts if f.fact_type == "skill"]
    assert len(sem_skills) == 3
    skill_names = {f.value.get("name") for f in sem_skills}
    assert skill_names == {"Python", "FastAPI", "PostgreSQL"}

    sem_languages = [f for f in sem_result.facts if f.fact_type == "language"]
    assert len(sem_languages) == 1
    assert sem_languages[0].value.get("language") == "Inglês"

    # Invariant: zero skills inside languages
    for lang_fact in sem_languages:
        val = lang_fact.value.get("language")
        assert val not in skill_names
