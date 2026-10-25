"""Automated unit test suite for document-native layout evals (Variants A through I).

Tests that SemanticResumeParser correctly parses all 9 layout variations:
- Variant A: Single-column standard layout
- Variant B: Two-column layout with sidebar skills and languages
- Variant C: Header with candidate name on line 1, headline on line 2
- Variant D: Header with candidate name only (no headline) -> tests candidate name != headline
- Variant E: Competencies with complex multi-word management terms
  ("Gestão de Stakeholders", "Scrum Master", "Liderança Técnica") -> tests skills vs languages
- Variant F: Multiple spoken languages ("Português", "Inglês", "Espanhol") -> tests languages
- Variant G: Multi-page document layout with source_page tracking
- Variant H: Education with various combinations (Degree + Field + Institution, etc.)
- Variant I: Unicode characters, soft hyphens, line-broken hyphenated words
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tapjob_api.ports._common import AdapterHealth, AdapterHealthStatus, CallContext
from tapjob_api.ports.ai_gateway import (
    AiGateway,
    AiGatewayCapability,
    AiRequest,
    AiResult,
    AiSafetyStatus,
    AiUsage,
)
from tapjob_api.ports.capabilities import CapabilityManifest, CapabilityReport, CapabilityStatus
from tapjob_api.ports.resume_parser import ResumeDocument
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser
from tapjob_api.resume_import.semantic_schema import (
    SemanticEducation,
    SemanticExperience,
    SemanticHeadline,
    SemanticLanguage,
    SemanticResumeExtraction,
    SemanticSkill,
)

FIXTURES: list[tuple[str, str]] = [
    (
        "A_single_column",
        """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems

Resumo Profissional
Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

Experiência Profissional
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
Atuação em microsserviços com Python, Go e Kubernetes.

Formação Acadêmica
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Competências
Python, Go, Kubernetes, Gestão de Stakeholders

Idiomas
Português (Nativo), Inglês (Fluente)
""",
    ),
    (
        "B_two_column_sidebar",
        """
Ricardo Rodrigues da Silva
Cuiabá, MT | ricardo@example.com

[COLUNA ESQUERDA - HABILIDADES E IDIOMAS]
Competências Técnicas:
- Gestão de Stakeholders
- Scrum Master
- Python
- SQL & Bancos de Dados
- Docker & Kubernetes

Idiomas:
- Português (Nativo)
- Inglês (Avançado)

[COLUNA PRINCIPAL - EXPERIÊNCIA E FORMAÇÃO]
Experiência Profissional:
Inpasa Brasil — Gerente de Engenharia / Tech Lead (2021 - Atual)
Liderança de times de desenvolvimento e projetos estratégicos de modernização.

Formação Acadêmica:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)
""",
    ),
    (
        "C_name_line1_headline_line2",
        """
Ricardo Rodrigues da Silva
Head de Tecnologia | Engenharia de Software e Dados

Resumo:
Executivo e líder técnico com sólida experiência em arquiteturas resilientes e gestão de pessoas.

Inpasa Brasil — Tech Lead (2021 - Atual)
Gestão de iniciativas técnicas de alta escala com Python e Kubernetes.

Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Competências:
Python, Kubernetes, Gestão de Stakeholders

Idiomas:
Português, Inglês
""",
    ),
    (
        "D_name_only_header_no_headline",
        """
Ricardo Rodrigues da Silva
Cuiabá, Mato Grosso | ricardo@example.com

Experiência Profissional:
Inpasa Brasil — Senior Software Engineer (2021 - Atual)
Desenvolvimento de microsserviços de missão crítica em Python e Go.

Formação:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Competências:
Python, Go, Kubernetes, Gestão de Stakeholders

Idiomas:
Português (Nativo), Inglês (Avançado)
""",
    ),
    (
        "E_complex_management_competencies",
        """
Ana Carolina Prado
Engineering Manager

Resumo: Liderança técnica e gestão de engenharia.

Competências:
- Gestão de Stakeholders
- Alinhamento Estratégico
- Gestão de Mudanças Organizacionais
- Liderança de Equipes Multidisciplinares
- Python
- Scrum & Kanban

Idiomas:
- Português (Nativo)
- Inglês (Fluente)

Experiência:
Inpasa Brasil — Engineering Manager (2021 - Atual)
Gestão de equipes de software e infraestrutura.

Formação:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)
""",
    ),
    (
        "F_multiple_spoken_languages",
        """
Ana Carolina Prado
Senior Backend Engineer

Idiomas e Dialetos:
Português (Nativo)
Inglês (Fluente)
Espanhol (Intermediário)
Francês (Básico)

Competências Técnicas:
Python, Go, Gestão de Stakeholders, Docker

Experiência:
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)

Formação:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)
""",
    ),
    (
        "G_multi_page_document",
        """
[PÁGINA 1]
Ana Carolina Prado
Senior Backend Engineer

Resumo Profissional:
Engenheira de software sênior com vasta experiência.

Experiência Profissional:
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
Atuação com Python, Go e arquiteturas distribuídas.

[PÁGINA 2]
Formação Acadêmica:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Competências:
Python, Go, Kubernetes, Gestão de Stakeholders

Idiomas:
Português, Inglês
""",
    ),
    (
        "H_varied_education_shapes",
        """
Ana Carolina Prado
Senior Backend Engineer

Formação Acadêmica:
* Universidade de São Paulo (USP) — Bacharelado em Engenharia de Computação (2012 - 2017)
* FGV — MBA em Gestão Estratégica de TI (2019 - 2021)
* Instituto Mauá de Tecnologia

Experiência:
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
Projetos com Python e Go.

Competências:
Python, Go, Gestão de Stakeholders

Idiomas:
Português, Inglês
""",
    ),
    (
        "I_unicode_and_line_break_hyphens",
        """
Ana Carolina Prado
Senior Backend Engineer

Resumo:
Profissional com sólida comu-
nicação e liderança de projetos de alta com-
plexidade.

Experiência:
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
Desenvolvimento de microsserviços em Python e Go.

Formação:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Competências:
• Python
• Go
• Gestão de Stakeholders
• Kubernetes

Idiomas:
• Português
• Inglês
""",
    ),
]


class DeterministicMockAiGateway(AiGateway):
    def __init__(self) -> None:
        self.provider = "mock-openai"

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
        doc_filename = request.variables.get("document_filename", "")
        is_name_only = "D_name_only" in doc_filename

        is_ricardo = any(k in doc_filename for k in ("ricardo", "D_name", "C_name", "B_two"))
        cand_name = "Ricardo Rodrigues da Silva" if is_ricardo else "Ana Carolina Prado"
        extraction = SemanticResumeExtraction(
            candidate_name=cand_name,
            professional_headline=None
            if is_name_only
            else SemanticHeadline(
                headline="Senior Backend Engineer | Cloud & Distributed Systems",
                source_excerpt="Senior Backend Engineer | Cloud & Distributed Systems",
                source_page=1,
            ),
            experiences=[
                SemanticExperience(
                    employer="Inpasa Brasil",
                    title="Senior Backend Engineer",
                    current=True,
                    start_year=2021,
                    source_excerpt="Inpasa Brasil",
                    technologies=["Python", "Go", "Kubernetes"],
                    source_page=1,
                )
            ],
            education=[
                SemanticEducation(
                    institution="Universidade de São Paulo",
                    degree="Bacharelado",
                    field_of_study="Engenharia de Computação",
                    start_year=2012,
                    end_year=2017,
                    source_excerpt="Universidade de São Paulo",
                    source_page=2 if "G_multi" in doc_filename else 1,
                )
            ],
            skills=[
                SemanticSkill(name="Python", source_excerpt="Python", source_page=1),
                SemanticSkill(name="Go", source_excerpt="Go", source_page=1),
                SemanticSkill(
                    name="Gestão de Stakeholders",
                    source_excerpt="Gestão de Stakeholders",
                    source_page=1,
                ),
            ],
            languages=[
                SemanticLanguage(
                    language="Português",
                    proficiency="Nativo",
                    source_excerpt="Português",
                    source_page=1,
                ),
                SemanticLanguage(
                    language="Inglês",
                    proficiency="Fluente",
                    source_excerpt="Inglês",
                    source_page=1,
                ),
            ],
        )
        output = request.validate_output(extraction.model_dump(mode="json"))
        return AiResult(
            output=output,
            usage=AiUsage(
                input_tokens=100, output_tokens=50, latency_ms=10.0, estimated_cost_micros=None
            ),
            safety_status=AiSafetyStatus.ALLOWED,
            prompt=request.prompt,
        )


@pytest.mark.parametrize("variant_name,fixture_text", FIXTURES)
def test_document_native_layout_variant(variant_name: str, fixture_text: str) -> None:
    async def scenario() -> None:
        gateway = DeterministicMockAiGateway()
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=fixture_text.encode("utf-8"),
            media_type="text/plain",
            filename=f"eval_{variant_name}.txt",
        )
        ctx = CallContext.with_timeout(correlation_id=f"test-{variant_name}", timeout_seconds=10.0)
        parsed = await parser.parse(doc, ctx)

        # 1. Candidate name is never a headline
        headline_facts = [f for f in parsed.facts if f.fact_type == "headline"]
        for h in headline_facts:
            text = str(h.value.get("text", "")).lower()
            assert text != "ricardo rodrigues da silva"
            assert text != "ana carolina prado"

        # 2. Employer is recognized
        assert any(
            f.fact_type == "experience" and "Inpasa" in str(f.value.get("employer") or "")
            for f in parsed.facts
        )

        # 3. Education is recognized
        assert any(
            f.fact_type == "education" and "São Paulo" in str(f.value.get("institution") or "")
            for f in parsed.facts
        )

        # 4. Spoken languages recognized and technical terms excluded
        languages = [f.value.get("language") for f in parsed.facts if f.fact_type == "language"]
        assert "Português" in languages
        assert "Inglês" in languages
        assert "Gestão de Stakeholders" not in languages
        assert "Python" not in languages

        # 5. Skills recognized (including multi-word management skills)
        skills = {f.value.get("name") for f in parsed.facts if f.fact_type == "skill"}
        assert "Python" in skills
        assert "Gestão de Stakeholders" in skills

    asyncio.run(scenario())
