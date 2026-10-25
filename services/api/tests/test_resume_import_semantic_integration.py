"""Integration tests for Resume Import service with Semantic Parser and Fallback."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tapjob_api.core.config import ApiSettings
from tapjob_api.db.session import create_session_factory
from tapjob_api.identity.models import User
from tapjob_api.ports._common import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    PortError,
    PortErrorCode,
)
from tapjob_api.ports.ai_gateway import (
    AiGatewayCapability,
    AiRequest,
    AiResult,
    AiSafetyStatus,
    AiUsage,
)
from tapjob_api.ports.capabilities import CapabilityManifest, CapabilityReport, CapabilityStatus
from tapjob_api.resume_import.models import ImportStatus
from tapjob_api.resume_import.parsing import PARSER_IDENTITY
from tapjob_api.resume_import.semantic_parser import SEMANTIC_PARSER_IDENTITY, SemanticResumeParser
from tapjob_api.resume_import.semantic_schema import (
    SemanticExperience,
    SemanticHeadline,
    SemanticResumeExtraction,
    SemanticSkill,
)
from tapjob_api.resume_import.service import (
    create_resume_import,
    process_resume_import,
)
from tapjob_api.resume_import.storage import InMemoryObjectStorage

pytestmark = pytest.mark.integration


class FlakyAiGateway:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.provider = "flaky-openai"
        self.should_fail = should_fail

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[AiGatewayCapability]:
        return CapabilityManifest(
            [
                CapabilityReport(
                    capability=AiGatewayCapability.STRUCTURED_OUTPUT,
                    status=CapabilityStatus.SUPPORTED,
                )
            ]
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=datetime.now(UTC))

    async def generate[OutputT](
        self, request: AiRequest[OutputT], ctx: CallContext
    ) -> AiResult[OutputT]:
        if self.should_fail:
            raise PortError(
                PortErrorCode.UNAVAILABLE,
                provider=self.provider,
                message="OpenAI model endpoint is down",
            )
        extraction = SemanticResumeExtraction(
            candidate_name="Julia Teste",
            professional_headline=SemanticHeadline(
                headline="Data Scientist", source_excerpt="Data Scientist"
            ),
            experiences=[
                SemanticExperience(
                    employer="Alpha Analytics",
                    title="Data Scientist",
                    technologies=["Python", "SQL"],
                    source_excerpt="Alpha Analytics",
                )
            ],
            skills=[SemanticSkill(name="Python", source_excerpt="Python")],
        )
        output = request.validate_output(extraction.model_dump(mode="json"))
        return AiResult(
            output=output,
            usage=AiUsage(
                input_tokens=100, output_tokens=50, latency_ms=25.0, estimated_cost_micros=None
            ),
            safety_status=AiSafetyStatus.ALLOWED,
            prompt=request.prompt,
        )


RESUME_TEXT = """
Julia Teste
Data Scientist

Experiência:
Alpha Analytics — Data Scientist (2020 - Atual)
Atuação em modelos preditivos com Python e SQL.

Competências:
Python, SQL
"""


@pytest.fixture
def db_session() -> Iterator[Session]:
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    factory = create_session_factory(engine)
    session = factory()
    yield session
    session.close()


def test_process_resume_import_success_with_semantic_parser(db_session: Session) -> None:
    user = User(id=uuid.uuid4(), locale="pt-BR")
    db_session.add(user)
    db_session.commit()

    storage = InMemoryObjectStorage()
    imp = create_resume_import(
        db_session,
        user=user,
        content=RESUME_TEXT.encode("utf-8"),
        original_filename="curriculo.txt",
        declared_content_type="text/plain",
        storage=storage,
    )

    gateway = FlakyAiGateway(should_fail=False)
    semantic_parser = SemanticResumeParser(gateway)

    process_resume_import(
        db_session,
        import_id=imp.id,
        storage=storage,
        parser=semantic_parser,
    )

    db_session.refresh(imp)
    assert imp.status == ImportStatus.READY_FOR_REVIEW
    assert imp.parser_identity == SEMANTIC_PARSER_IDENTITY
    assert len(imp.candidates) > 0


def test_process_resume_import_fallback_when_semantic_fails(db_session: Session) -> None:
    user = User(id=uuid.uuid4(), locale="pt-BR")
    db_session.add(user)
    db_session.commit()

    storage = InMemoryObjectStorage()
    imp = create_resume_import(
        db_session,
        user=user,
        content=RESUME_TEXT.encode("utf-8"),
        original_filename="curriculo.txt",
        declared_content_type="text/plain",
        storage=storage,
    )

    gateway = FlakyAiGateway(should_fail=True)
    failing_semantic_parser = SemanticResumeParser(gateway)

    # When failing semantic parser is passed, it should automatically fallback to deterministic
    process_resume_import(
        db_session,
        import_id=imp.id,
        storage=storage,
        parser=failing_semantic_parser,
    )

    db_session.refresh(imp)
    assert imp.status == ImportStatus.READY_FOR_REVIEW
    assert imp.parser_identity == PARSER_IDENTITY
    assert len(imp.candidates) > 0
