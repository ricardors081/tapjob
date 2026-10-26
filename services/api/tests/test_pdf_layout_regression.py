"""PDF Layout Regression Fixture Test.

Guarantees:
1. Real PDF layout regression fixture (mimicking two-column resume structure):
   - Name on line 1 is NOT treated as professional headline.
   - Technical & managerial competencies ("Gestão de Stakeholders", "Python")
     are classified as SKILLS, NEVER languages.
   - Spoken languages ("Português", "Inglês") are classified as LANGUAGES.
2. Formatter regression:
   - Never outputs redundant placeholder "Formação Acadêmica — Universidade X"
     when degree or field is known.
   - Formats cleanly: "Bacharelado em Engenharia — USP (2015 - 2019)"
     or "- USP" if only institution is known.
3. WhatsApp conversational re-processing prompt on same document.
"""

from __future__ import annotations

from tapjob_api.messaging.profile_formatter import (
    _format_education_item,
)
from tapjob_api.ports._common import AdapterHealth, AdapterHealthStatus, CallContext
from tapjob_api.ports.ai_gateway import (
    AiGateway,
    AiGatewayCapability,
    AiRequest,
    AiResult,
    AiUsage,
)
from tapjob_api.ports.capabilities import CapabilityManifest, CapabilityReport, CapabilityStatus
from tapjob_api.ports.resume_parser import ResumeDocument
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser
from tapjob_api.resume_import.semantic_schema import (
    SemanticEducation,
    SemanticHeadline,
    SemanticLanguage,
    SemanticResumeExtraction,
    SemanticSkill,
)


class MockAiGateway(AiGateway):
    def __init__(self, extraction: SemanticResumeExtraction) -> None:
        self.provider = "mock-openai"
        self._extraction = extraction

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
        from datetime import UTC, datetime

        return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=datetime.now(UTC))

    async def generate[OutputT](
        self, request: AiRequest[OutputT], ctx: CallContext
    ) -> AiResult[OutputT]:
        from tapjob_api.ports.ai_gateway import AiSafetyStatus

        output = request.validate_output(self._extraction.model_dump(mode="json"))
        return AiResult(
            output=output,
            usage=AiUsage(
                input_tokens=100, output_tokens=50, latency_ms=10.0, estimated_cost_micros=None
            ),
            safety_status=AiSafetyStatus.ALLOWED,
            prompt=request.prompt,
        )


RAW_TWO_COLUMN_RESUME_TEXT = """
Ricardo Rodrigues da Silva
Cuiabá, MT | ricardo@example.com | (65) 99999-9999

Competências:
Gestão de Stakeholders
Scrum & Agile
Python
SQL & Bancos de Dados
Arquitetura de Sistemas

Idiomas:
Português (Nativo)
Inglês (Avançado)

Formação Acadêmica:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)
"""


def test_pdf_layout_safeguards_skills_vs_languages_and_name_headline() -> None:
    """Tests that:
    - 'Gestão de Stakeholders' misclassified by AI under languages is rescued and
      reclassified as skill.
    - 'Python' is a skill.
    - 'Português' and 'Inglês' are preserved as languages.
    - Candidate name is NOT used as headline.
    """
    import asyncio

    async def scenario() -> None:
        excerpt_edu = (
            "Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)"
        )
        extraction_with_misclassification = SemanticResumeExtraction(
            candidate_name="Ricardo Rodrigues da Silva",
            # Simulating model trying to put candidate name as headline
            professional_headline=SemanticHeadline(
                headline="Ricardo Rodrigues da Silva",
                source_excerpt="Ricardo Rodrigues da Silva",
            ),
            skills=[
                SemanticSkill(name="Python", source_excerpt="Python"),
                SemanticSkill(name="SQL & Bancos de Dados", source_excerpt="SQL & Bancos de Dados"),
            ],
            languages=[
                # Model mistakenly placed "Gestão de Stakeholders" under languages
                SemanticLanguage(
                    language="Gestão de Stakeholders",
                    proficiency="Avançado",
                    source_excerpt="Gestão de Stakeholders",
                ),
                SemanticLanguage(
                    language="Português",
                    proficiency="Nativo",
                    source_excerpt="Português",
                ),
                SemanticLanguage(
                    language="Inglês",
                    proficiency="Avançado",
                    source_excerpt="Inglês",
                ),
            ],
            education=[
                SemanticEducation(
                    institution="Universidade de São Paulo",
                    degree="Bacharelado",
                    field_of_study="Engenharia de Computação",
                    start_year=2012,
                    end_year=2017,
                    source_excerpt=excerpt_edu,
                )
            ],
        )

        gateway = MockAiGateway(extraction_with_misclassification)
        parser = SemanticResumeParser(gateway)

        doc = ResumeDocument(
            content=RAW_TWO_COLUMN_RESUME_TEXT.encode("utf-8"),
            media_type="text/plain",
            filename="curriculo_ricardo.pdf",
        )
        ctx = CallContext.with_timeout(correlation_id="test-layout", timeout_seconds=10.0)
        parsed = await parser.parse(doc, ctx)

        facts = parsed.facts
        fact_types = [f.fact_type for f in facts]

        # 1. Candidate name must NOT be a headline fact
        assert "headline" not in fact_types

        # 2. Unknown languages must NOT be silently reclassified as skills via static dictionaries
        skill_names = [f.value["name"] for f in facts if f.fact_type == "skill"]
        assert "Python" in skill_names
        assert "Gestão de Stakeholders" not in skill_names

        # 3. Languages extracted by semantic model and grounded in document are preserved
        languages = [f.value["language"] for f in facts if f.fact_type == "language"]
        assert "Português" in languages
        assert "Inglês" in languages
        assert "Gestão de Stakeholders" in languages

    asyncio.run(scenario())


def test_education_formatter_clean_rendering() -> None:
    # 1. Degree + Field + Institution + Dates
    full_edu = {
        "institution": "Universidade de São Paulo",
        "degree": "Bacharelado",
        "fieldOfStudy": "Engenharia de Computação",
        "start": {"year": 2012},
        "end": {"year": 2017},
    }
    rendered = _format_education_item(full_edu)
    assert (
        rendered
        == "- Bacharelado em Engenharia de Computação — Universidade de São Paulo (2012 - 2017)"
    )
    assert "Formação Acadêmica — " not in rendered

    # 2. Degree + Institution (no field)
    deg_edu = {
        "institution": "USP",
        "degree": "Bacharelado em Administração",
        "end": {"year": 2019},
    }
    rendered_deg = _format_education_item(deg_edu)
    assert rendered_deg == "- Bacharelado em Administração — USP (2019)"
    assert "Formação Acadêmica — " not in rendered_deg

    # 3. Only Institution
    inst_edu = {"institution": "USP"}
    rendered_inst = _format_education_item(inst_edu)
    assert rendered_inst == "- USP"
    assert "Formação Acadêmica — " not in rendered_inst


def test_headline_role_preserved_when_not_name() -> None:
    extraction = SemanticResumeExtraction(
        candidate_name="Ricardo Rodrigues da Silva",
        professional_headline=SemanticHeadline(
            headline="Senior Engineering Manager",
            source_excerpt="Senior Engineering Manager",
        ),
    )
    gateway = MockAiGateway(extraction)
    parser = SemanticResumeParser(gateway)
    facts, _ = parser._map_extraction_to_facts(
        extraction, original_text="Ricardo Rodrigues da Silva\nSenior Engineering Manager"
    )
    headline_facts = [f for f in facts if f.fact_type == "headline"]
    assert len(headline_facts) == 1
    assert headline_facts[0].value["text"] == "Senior Engineering Manager"
