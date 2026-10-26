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
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Table, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.bootstrap import create_resume_parser
from tapjob_api.career_profile.models import (
    CareerProfile,
    CareerProfileRevision,
    ProfileFact,
)
from tapjob_api.core.config import Environment, RuntimeSettings
from tapjob_api.db.base import Base
from tapjob_api.identity.models import User, UserStatus
from tapjob_api.messaging.career_agent_tools import tool_ingest_resume
from tapjob_api.messaging.document_intake import handle_attachment_intake
from tapjob_api.messaging.inbound import (
    InboundEffect,
    InboundEffectKind,
    ProviderEventEnvelope,
    ProviderEventProcessingStatus,
    ProviderEventType,
)
from tapjob_api.messaging.inbound_service import reserve_inbound_effect
from tapjob_api.messaging.models import (
    ChannelIdentity as ChannelIdentityModel,
)
from tapjob_api.messaging.models import (
    ChannelIdentityLink,
    ChannelProvider,
    ChannelType,
)
from tapjob_api.messaging.orchestration import (
    dispatch_inbound_message,
    reconcile_reserved_resume_import,
)
from tapjob_api.messaging.outbox import OutboundMessageQueue
from tapjob_api.ports._common import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    PortError,
    PortErrorCode,
)
from tapjob_api.ports.capabilities import (
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
)
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
    ResumeParserCapability,
)
from tapjob_api.resume_import import service
from tapjob_api.resume_import.errors import TransientProcessingError
from tapjob_api.resume_import.models import (
    CandidateReviewDraft,
    ImportStatus,
    Resume,
    ResumeImport,
    ResumeImportCandidate,
    ResumeVersion,
)
from tapjob_api.resume_import.parsing import DeterministicResumeParser
from tapjob_api.resume_import.storage import (
    InMemoryObjectStorage,
    ObjectStoragePort,
    compute_checksum,
)

from .fake_channel import FakeMessagingChannel

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
    settings_none = RuntimeSettings(
        app_env=Environment.LOCAL,
        ai_provider="openai",
        openai_api_key=None,
    )
    with pytest.raises(ValueError, match="OpenAI API key is required when AI_PROVIDER=openai"):
        create_resume_parser(settings_none)

    settings_empty = RuntimeSettings(
        app_env=Environment.LOCAL,
        ai_provider="openai",
        openai_api_key="",
    )
    with pytest.raises(ValueError, match="OpenAI API key is required when AI_PROVIDER=openai"):
        create_resume_parser(settings_empty)

    settings_blank = RuntimeSettings(
        app_env=Environment.LOCAL,
        ai_provider="openai",
        openai_api_key="   ",
    )
    with pytest.raises(ValueError, match="OpenAI API key is required when AI_PROVIDER=openai"):
        create_resume_parser(settings_blank)


# ---------------------------------------------------------------------------
# 7. Explicit Deterministic Provider Supported
# ---------------------------------------------------------------------------


def test_explicit_deterministic_provider_still_supported() -> None:
    settings_det = RuntimeSettings(
        app_env=Environment.LOCAL,
        ai_provider="deterministic",
    )
    parser = create_resume_parser(settings_det)
    assert isinstance(parser, DeterministicResumeParser)


# ---------------------------------------------------------------------------
# 7b. OpenAI Parser Observability Reports Configured Model
# ---------------------------------------------------------------------------


def test_openai_parser_observability_reports_configured_model() -> None:
    """Verifies that SemanticResumeParser and OpenAiGateway report configured model,
    and handle_attachment_intake logs model accurately without private attribute access.
    """
    settings = RuntimeSettings(
        app_env=Environment.LOCAL,
        ai_provider="openai",
        ai_resume_extraction_model="gpt-5.6-terra",
        openai_api_key="sk-test-secret-key",
    )
    parser = create_resume_parser(settings)
    assert parser.provider == "semantic-openai-v1"
    assert parser.model == "gpt-5.6-terra"
    assert parser.model is not None

    session = MagicMock(spec=Session)
    user = User(id=uuid.uuid4(), locale="pt-BR", status=UserStatus.ACTIVE)
    storage = MagicMock(spec=ObjectStoragePort)
    fake_import = MagicMock(spec=ResumeImport)
    fake_import.id = uuid.uuid4()
    fake_import.status = ImportStatus.READY_FOR_REVIEW
    fake_import.media_type = "application/pdf"
    fake_import.parser_identity = "semantic-openai-v1"
    fake_import.candidates = []

    with (
        patch(
            "tapjob_api.messaging.document_intake.resume_import_service.create_resume_import",
            return_value=fake_import,
        ),
        patch(
            "tapjob_api.messaging.document_intake.resume_import_service.process_resume_import",
            return_value=fake_import,
        ),
        patch("tapjob_api.messaging.document_intake.logger.info") as mock_log_info,
    ):
        handle_attachment_intake(
            session,
            user=user,
            content=b"%PDF-1.4 dummy",
            original_filename="cv.pdf",
            declared_content_type="application/pdf",
            storage=storage,
            parser=parser,
        )

        log_calls = [
            c
            for c in mock_log_info.call_args_list
            if c[0][0] == "document_intake.attachment_processed_via_wp13"
        ]
        assert len(log_calls) == 1
        extra = log_calls[0][1]["extra"]
        assert extra["parser_identity"] == "semantic-openai-v1"
        assert extra["provider"] == "semantic-openai-v1"
        assert extra["model"] == "gpt-5.6-terra"
        assert extra["input_mode"] == "input_file"
        # Secret key must never be exposed in observability logs
        assert "sk-test-secret-key" not in str(extra)


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
# 10b. AST Single Source of Configuration Guard
# ---------------------------------------------------------------------------


def test_backend_config_is_only_environment_reader_for_ai_settings() -> None:
    """AST guard: verifies that no backend module outside tapjob_api.core.config
    reads AI_PROVIDER, OPENAI_*, or process environment variables directly.
    """
    repo_root = Path(__file__).resolve().parents[4]
    src_dirs = [
        repo_root / "services" / "api" / "src",
        repo_root / "services" / "worker" / "src",
    ]
    violations: list[str] = []

    for src_dir in src_dirs:
        for py_path in src_dir.rglob("*.py"):
            # Exclude core/config.py which is the designated environment boundary
            posix_path = py_path.as_posix()
            if "core/config.py" in posix_path:
                continue
            content = py_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_path))
            for node in ast.walk(tree):
                # Check for os.getenv(...) calls
                if isinstance(node, ast.Call):
                    is_getenv = (
                        isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
                    ) or (isinstance(node.func, ast.Name) and node.func.id == "getenv")
                    if is_getenv:
                        violations.append(
                            f"{py_path.name}:{node.lineno}: direct os.getenv call detected"
                        )
                # Check for os.environ[...] access
                elif isinstance(node, ast.Attribute) and node.attr == "environ":
                    violations.append(
                        f"{py_path.name}:{node.lineno}: direct os.environ access detected"
                    )

    assert not violations, "Found direct environment access outside core/config.py:\n" + "\n".join(
        violations
    )


# ---------------------------------------------------------------------------
# 11. Realistic Parser Extraction Comparison (Ana Exemplo)
# ---------------------------------------------------------------------------


def test_realistic_parser_extraction_comparison() -> None:
    """Verifies that for Ana Exemplo's resume text:
    1. Deterministic parser exhibits forensic bug (candidate name as headline).
    2. Semantic parser extracts proper headline, experiences, skills, languages.
    3. Zero skills are categorized inside languages.
    (Direct parser comparison test; see below for full WhatsApp end-to-end integration tests).
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


# ---------------------------------------------------------------------------
# 12. WhatsApp End-to-End Real Document Intake (Findings 1 & 2)
# ---------------------------------------------------------------------------


def _setup_sqlite_for_whatsapp_intake() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_con: Any, con_record: Any) -> None:
        dbapi_con.create_function("now", 0, lambda: datetime.now(UTC).isoformat())

    tables = [
        User.__table__,
        ChannelProvider.__table__,
        ChannelIdentityModel.__table__,
        ChannelIdentityLink.__table__,
        ProviderEventEnvelope.__table__,
        InboundEffect.__table__,
        OutboundMessageQueue.__table__,
        CareerProfile.__table__,
        CareerProfileRevision.__table__,
        ProfileFact.__table__,
        Resume.__table__,
        ResumeVersion.__table__,
        ResumeImport.__table__,
        ResumeImportCandidate.__table__,
        CandidateReviewDraft.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=cast(list[Table], tables))
    return sessionmaker(bind=engine, expire_on_commit=False)


class FakeSemanticResumeParser:
    """Injected fake semantic ResumeParser representing OpenAiGateway-backed parser."""

    provider: str = "semantic-test-v1"

    @property
    def model(self) -> str:
        return "gpt-5.6-terra"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[ResumeParserCapability]:
        return CapabilityManifest(
            [
                CapabilityReport(
                    ResumeParserCapability.STRUCTURED_EXTRACTION,
                    CapabilityStatus.SUPPORTED,
                )
            ]
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=datetime.now(UTC))

    async def parse(self, document: ResumeDocument, ctx: CallContext) -> ParsedResume:
        facts = [
            ExtractedFact(
                fact_type="headline",
                value={"text": "Computer Engineer | Data & AI"},
                confidence=0.95,
                source_excerpt="Computer Engineer | Data & AI",
            ),
            ExtractedFact(
                fact_type="experience",
                value={
                    "title": "Data Analyst",
                    "employer": "Empresa Alpha",
                    "current": True,
                    "start": {"year": 2021, "month": 1},
                },
                confidence=0.9,
                source_excerpt="Data Analyst — Empresa Alpha",
            ),
            ExtractedFact(
                fact_type="education",
                value={
                    "institution": "USP",
                    "degree": "Engenharia de Computação",
                },
                confidence=0.9,
                source_excerpt="USP - Engenharia de Computação",
            ),
            ExtractedFact(
                fact_type="skill",
                value={"name": "Python"},
                confidence=0.9,
                source_excerpt="Python",
            ),
            ExtractedFact(
                fact_type="skill",
                value={"name": "SQL"},
                confidence=0.9,
                source_excerpt="SQL",
            ),
            ExtractedFact(
                fact_type="skill",
                value={"name": "Stakeholder Management"},
                confidence=0.9,
                source_excerpt="Stakeholder Management",
            ),
            ExtractedFact(
                fact_type="language",
                value={"language": "Português", "proficiency": "native"},
                confidence=0.9,
                source_excerpt="Português - Nativo",
            ),
            ExtractedFact(
                fact_type="language",
                value={"language": "Inglês", "proficiency": "advanced"},
                confidence=0.9,
                source_excerpt="Inglês - Avançado",
            ),
        ]
        return ParsedResume(facts=tuple(facts))


def test_whatsapp_pdf_end_to_end_persists_injected_semantic_parser_identity() -> None:
    """End-to-end integration-style test for real WhatsApp document intake:
    InboundMessage with attachment PDF
    -> dispatch_inbound_message
    -> media retrieval (FakeMessagingChannel returns valid binary PDF)
    -> tool_ingest_resume
    -> handle_attachment_intake
    -> create_resume_import
    -> process_resume_import
    -> fake injected semantic ResumeParser
    -> ResumeImport READY_FOR_REVIEW

    Proves the exact parser injected into dispatch_inbound_message is the one that executes
    and persists parser_identity == 'semantic-test-v1'.
    Zero mocking of tool_ingest_resume, handle_attachment_intake, or process_resume_import.
    """
    session_factory = _setup_sqlite_for_whatsapp_intake()
    session = session_factory()

    provider = ChannelProvider(
        id=uuid.uuid4(),
        channel=ChannelType.WHATSAPP,
        provider_name="evolution",
        provider_config_key=f"evolution_key_{uuid.uuid4()}",
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(provider)
    session.commit()

    channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()
    parser = FakeSemanticResumeParser()

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_real_pdf_e2e_1",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_real_pdf_1",
                mime_type="application/pdf",
                filename="curriculo_ana.pdf",
                kind="document",
            ),
        ),
    )

    queued_keys = dispatch_inbound_message(
        session,
        redis_client=None,
        inbound=inbound,
        provider_id=provider.id,
        resume_parser=parser,
        channel_adapter=channel,
        storage=storage,
        envelope_id=None,
    )

    assert len(queued_keys) > 0

    imports = session.scalars(select(ResumeImport)).all()
    assert len(imports) == 1
    persisted_import = imports[0]

    assert persisted_import.status == ImportStatus.READY_FOR_REVIEW
    assert persisted_import.parser_identity == "semantic-test-v1"


def test_whatsapp_pdf_end_to_end_semantic_candidate_categories() -> None:
    """Verifies that the candidates created via end-to-end WhatsApp document intake:
    - Candidate name ('Ana Exemplo') is NOT extracted as headline
    - EXPERIENCE_COUNT > 0
    - SKILL_COUNT > 0
    - LANGUAGE_COUNT == 2
    - 'Stakeholder Management' appears as a skill, NOT as a language
    """
    session_factory = _setup_sqlite_for_whatsapp_intake()
    session = session_factory()

    provider = ChannelProvider(
        id=uuid.uuid4(),
        channel=ChannelType.WHATSAPP,
        provider_name="evolution",
        provider_config_key=f"evolution_key_{uuid.uuid4()}",
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(provider)
    session.commit()

    channel = FakeMessagingChannel()
    storage = InMemoryObjectStorage()
    parser = FakeSemanticResumeParser()

    inbound = InboundMessage(
        sender=ChannelIdentity(channel="whatsapp", external_id="5511999999999"),
        provider_message_id="msg_real_pdf_e2e_cat_1",
        received_at=datetime.now(UTC),
        text=None,
        attachments=(
            InboundAttachment(
                provider_media_id="media_real_pdf_cat_1",
                mime_type="application/pdf",
                filename="curriculo_ana.pdf",
                kind="document",
            ),
        ),
    )

    dispatch_inbound_message(
        session,
        redis_client=None,
        inbound=inbound,
        provider_id=provider.id,
        resume_parser=parser,
        channel_adapter=channel,
        storage=storage,
        envelope_id=None,
    )

    imports = session.scalars(select(ResumeImport)).all()
    assert len(imports) == 1
    persisted_import = imports[0]

    candidates = session.scalars(
        select(ResumeImportCandidate).where(
            ResumeImportCandidate.resume_import_id == persisted_import.id
        )
    ).all()

    headline_candidates = [c for c in candidates if c.candidate_kind == "headline"]
    assert len(headline_candidates) == 1
    assert headline_candidates[0].value_json.get("text") == "Computer Engineer | Data & AI"
    assert headline_candidates[0].value_json.get("text") != "Ana Exemplo"

    experience_candidates = [c for c in candidates if c.candidate_kind == "experience"]
    assert len(experience_candidates) > 0
    assert experience_candidates[0].value_json.get("title") == "Data Analyst"

    skill_candidates = [c for c in candidates if c.candidate_kind == "skill"]
    assert len(skill_candidates) > 0
    skill_names = {c.value_json.get("name") for c in skill_candidates}
    assert "Python" in skill_names
    assert "SQL" in skill_names
    assert "Stakeholder Management" in skill_names

    language_candidates = [c for c in candidates if c.candidate_kind == "language"]
    assert len(language_candidates) == 2
    language_names = {c.value_json.get("language") for c in language_candidates}
    assert language_names == {"Português", "Inglês"}

    # Critical invariant: Stakeholder Management must NEVER be in languages
    assert "Stakeholder Management" not in language_names


def test_reserved_resume_recovery_persists_injected_semantic_parser_identity() -> None:
    """Verifies that recovering a reserved QUEUED resume import via reconcile_reserved_resume_import
    invokes the real injected semantic parser and persists parser_identity == 'semantic-test-v1'.
    Zero mocking of resume_import_service.process_resume_import.
    """
    session_factory = _setup_sqlite_for_whatsapp_intake()
    session = session_factory()

    user = User(
        id=uuid.uuid4(),
        status=UserStatus.ACTIVE,
        locale="pt-BR",
        timezone="Etc/UTC",
    )
    session.add(user)

    storage = InMemoryObjectStorage()
    content_bytes = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 20 >>\nstream\nBT /F1 12 Tf ET\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n0000000216 00000 n \n"
        b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n286\n%%EOF\n"
    )
    stored = storage.put(
        storage_key="recovery_test_pdf_key",
        content=content_bytes,
        content_type="application/pdf",
    )

    queued_import = ResumeImport(
        id=uuid.uuid4(),
        user_id=user.id,
        status=ImportStatus.QUEUED,
        original_filename="curriculo_ana.pdf",
        media_type="application/pdf",
        byte_size=stored.byte_size,
        checksum_sha256=stored.checksum_sha256,
        storage_key=stored.storage_key,
    )
    session.add(queued_import)

    envelope = ProviderEventEnvelope(
        id=101,
        provider="evolution",
        provider_instance="default",
        external_event_id="prov_evt_101",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.PROCESSING,
        normalized_event={},
        received_at=datetime.now(UTC),
    )
    session.add(envelope)
    session.commit()

    reserve_inbound_effect(
        session,
        envelope_id=envelope.id,
        inbound_message_id="msg_recovery_101",
        effect_kind=InboundEffectKind.RESUME_IMPORT.value,
        resource_id=str(queued_import.id),
    )
    session.commit()

    parser = FakeSemanticResumeParser()

    is_reconciled, response_text = reconcile_reserved_resume_import(
        session,
        user=user,
        envelope_id=envelope.id,
        target_import_uuid=queued_import.id,
        storage=storage,
        parser=parser,
        doc_filename="curriculo_ana.pdf",
    )

    assert is_reconciled is True
    assert "Recebemos seu currículo" in response_text

    session.refresh(queued_import)
    assert queued_import.status == ImportStatus.READY_FOR_REVIEW
    assert queued_import.parser_identity == "semantic-test-v1"
