"""Unit tests for SemanticResumeParser."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from tapjob_api.ports._common import AdapterHealth, AdapterHealthStatus, CallContext
from tapjob_api.ports.ai_gateway import (
    AiGatewayCapability,
    AiRequest,
    AiResult,
    AiSafetyStatus,
    AiUsage,
)
from tapjob_api.ports.capabilities import CapabilityManifest, CapabilityReport, CapabilityStatus
from tapjob_api.ports.resume_parser import ResumeDocument
from tapjob_api.resume_import.errors import TextNotExtractableError
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser
from tapjob_api.resume_import.semantic_schema import (
    SemanticEducation,
    SemanticExperience,
    SemanticHeadline,
    SemanticLanguage,
    SemanticPublication,
    SemanticResumeExtraction,
    SemanticSkill,
    SemanticSummary,
)


class FakeAiGateway:
    def __init__(self, extraction: SemanticResumeExtraction) -> None:
        self.provider = "fake-openai"
        self._extraction = extraction
        self.last_request: AiRequest[Any] | None = None

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
        self.last_request = request
        output = request.validate_output(self._extraction.model_dump(mode="json"))
        return AiResult(
            output=output,
            usage=AiUsage(
                input_tokens=100, output_tokens=100, latency_ms=10.0, estimated_cost_micros=None
            ),
            safety_status=AiSafetyStatus.ALLOWED,
            prompt=request.prompt,
        )


def _ctx() -> CallContext:
    return CallContext.with_timeout(correlation_id="test-corr", timeout_seconds=10.0)


def test_empty_document_raises_text_not_extractable() -> None:
    async def scenario() -> None:
        gateway = FakeAiGateway(SemanticResumeExtraction())
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(content=b"", media_type="text/plain", filename="empty.txt")
        with pytest.raises(TextNotExtractableError):
            await parser.parse(doc, _ctx())

    asyncio.run(scenario())


def test_headline_vs_candidate_name_separation() -> None:
    async def scenario() -> None:
        raw_text = (
            "Ricardo Santos Silva\n"
            "Computer Engineer | Data & AI\n"
            "Experience:\n"
            "Analista de Dados Senior na Inpasa"
        )
        # 1. When headline matches candidate name -> dropped
        bad_extraction = SemanticResumeExtraction(
            candidate_name="Ricardo Santos Silva",
            professional_headline=SemanticHeadline(
                headline="Ricardo Santos Silva",
                source_excerpt="Ricardo Santos Silva",
            ),
        )
        gateway = FakeAiGateway(bad_extraction)
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=raw_text.encode("utf-8"),
            media_type="text/plain",
            filename="cv.txt",
        )
        parsed = await parser.parse(doc, _ctx())
        assert not any(f.fact_type == "headline" for f in parsed.facts)

        # 2. When headline is actual professional headline -> preserved
        good_extraction = SemanticResumeExtraction(
            candidate_name="Ricardo Santos Silva",
            professional_headline=SemanticHeadline(
                headline="Computer Engineer | Data & AI",
                source_excerpt="Computer Engineer | Data & AI",
            ),
        )
        gateway._extraction = good_extraction
        parsed2 = await parser.parse(doc, _ctx())
        headline_facts = [f for f in parsed2.facts if f.fact_type == "headline"]
        assert len(headline_facts) == 1
        assert headline_facts[0].value["text"] == "Computer Engineer | Data & AI"
        assert headline_facts[0].source_excerpt == "Computer Engineer | Data & AI"

    asyncio.run(scenario())


def test_grounding_validation_strips_hallucinated_excerpt() -> None:
    async def scenario() -> None:
        raw_text = (
            "Profissional de Dados com 10 anos de experiência em Python e SQL na Empresa Alpha."
        )
        extraction = SemanticResumeExtraction(
            skills=[
                SemanticSkill(name="Python", source_excerpt="Python e SQL"),
                SemanticSkill(name="Kubernetes", source_excerpt="Totalmente inventado no texto"),
            ]
        )
        gateway = FakeAiGateway(extraction)
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=raw_text.encode("utf-8"),
            media_type="text/plain",
            filename="cv.txt",
        )
        parsed = await parser.parse(doc, _ctx())
        skills = {f.value["name"]: f for f in parsed.facts if f.fact_type == "skill"}

        assert skills["Python"].source_excerpt == "Python e SQL"
        # Hallucinated excerpt was stripped:
        assert skills["Kubernetes"].source_excerpt is None

    asyncio.run(scenario())


def test_skill_deduplication_and_technologies_merge() -> None:
    async def scenario() -> None:
        raw_text = "Experiência na Inpasa usando Python e Docker. Competências: python, SQL."
        extraction = SemanticResumeExtraction(
            experiences=[
                SemanticExperience(
                    employer="Inpasa",
                    title="Analista",
                    technologies=["Python", "Docker"],
                    source_excerpt="Experiência na Inpasa",
                )
            ],
            skills=[
                SemanticSkill(name="python", source_excerpt="Competências: python"),
                SemanticSkill(name="SQL", source_excerpt="SQL"),
            ],
        )
        gateway = FakeAiGateway(extraction)
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=raw_text.encode("utf-8"),
            media_type="text/plain",
            filename="cv.txt",
        )
        parsed = await parser.parse(doc, _ctx())
        skill_facts = [f for f in parsed.facts if f.fact_type == "skill"]
        skill_names = [
            str(f.value["name"]).lower()
            for f in skill_facts
            if isinstance(f.value, dict) and "name" in f.value
        ]

        # 'python' should appear exactly once, not twice
        assert skill_names.count("python") == 1
        assert "docker" in skill_names
        assert "sql" in skill_names

    asyncio.run(scenario())


def test_full_extraction_mapping() -> None:
    async def scenario() -> None:
        raw_text = (
            "Ricardo Santos Silva\n"
            "Headline: Lead Engineer\n"
            "Summary: Professional in AI systems.\n"
            "Experience: TechCorp - Senior Engineer (2020-2024)\n"
            "Education: Universidade Ceuma - Computação (2015-2019)\n"
            "Languages: Português, Inglês\n"
            "Publication: AI Paper - IEEE (2023)\n"
        )
        extraction = SemanticResumeExtraction(
            candidate_name="Ricardo Santos Silva",
            professional_headline=SemanticHeadline(
                headline="Lead Engineer", source_excerpt="Lead Engineer"
            ),
            professional_summary=SemanticSummary(
                summary="Professional in AI systems.", source_excerpt="Professional in AI systems."
            ),
            experiences=[
                SemanticExperience(
                    employer="TechCorp",
                    title="Senior Engineer",
                    start_year=2020,
                    end_year=2024,
                    source_excerpt="TechCorp - Senior Engineer",
                )
            ],
            education=[
                SemanticEducation(
                    institution="Universidade Ceuma",
                    degree="Graduação",
                    field_of_study="Computação",
                    start_year=2015,
                    end_year=2019,
                    source_excerpt="Universidade Ceuma - Computação",
                )
            ],
            languages=[
                SemanticLanguage(
                    language="Português", proficiency="Nativo", source_excerpt="Português"
                ),
                SemanticLanguage(
                    language="Inglês", proficiency="Avançado", source_excerpt="Inglês"
                ),
            ],
            publications=[
                SemanticPublication(
                    title="AI Paper",
                    publisher="IEEE",
                    date="2023",
                    source_excerpt="AI Paper - IEEE (2023)",
                )
            ],
        )
        gateway = FakeAiGateway(extraction)
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=raw_text.encode("utf-8"),
            media_type="text/plain",
            filename="cv.txt",
        )
        parsed = await parser.parse(doc, _ctx())
        fact_types = [f.fact_type for f in parsed.facts]
        assert "headline" in fact_types
        assert "summary" in fact_types
        assert "experience" in fact_types
        assert "education" in fact_types
        assert "language" in fact_types
        assert "publication" in fact_types

    asyncio.run(scenario())


def test_create_default_resume_parser() -> None:
    from tapjob_api.core.config import Environment, RuntimeSettings
    from tapjob_api.resume_import.parsing import DeterministicResumeParser
    from tapjob_api.resume_import.service import create_default_resume_parser

    # Unset OpenAI key -> Deterministic parser
    settings_no_ai = RuntimeSettings(app_env=Environment.LOCAL, openai_api_key=None)
    parser_default = create_default_resume_parser(settings_no_ai)
    assert isinstance(parser_default, DeterministicResumeParser)

    # Set OpenAI key -> Semantic parser
    settings_with_ai = RuntimeSettings(app_env=Environment.LOCAL, openai_api_key="sk-test-fake-key")
    parser_ai = create_default_resume_parser(settings_with_ai)
    assert isinstance(parser_ai, SemanticResumeParser)
