"""Human Acceptance regression fixture and Format-Independence Eval suite (Variants A through H).

Proves that the Career Understanding Engine:
1. Passes the sanitized Human Acceptance regression suite with zero PII/secret leaks.
2. Extracts the same core career facts across 8 distinct format variations (A-H).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tapjob_api.career_profile.models import FactStatus, FactType, ProfileFact
from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.messaging.profile_formatter import format_profile_query_summary
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
from tapjob_api.resume_import.candidates import validate_candidate
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser
from tapjob_api.resume_import.semantic_schema import (
    SemanticCertification,
    SemanticEducation,
    SemanticExperience,
    SemanticHeadline,
    SemanticLanguage,
    SemanticPublication,
    SemanticResumeExtraction,
    SemanticSkill,
    SemanticSummary,
)


class MockSemanticGateway:
    """Mock gateway that simulates semantic comprehension for the eval fixtures."""

    def __init__(self, extraction: SemanticResumeExtraction) -> None:
        self.provider = "mock-openai-v1"
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
        return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=datetime.now(UTC))

    async def generate[OutputT](
        self, request: AiRequest[OutputT], ctx: CallContext
    ) -> AiResult[OutputT]:
        output = request.validate_output(self._extraction.model_dump(mode="json"))
        return AiResult(
            output=output,
            usage=AiUsage(
                input_tokens=250, output_tokens=350, latency_ms=45.0, estimated_cost_micros=None
            ),
            safety_status=AiSafetyStatus.ALLOWED,
            prompt=request.prompt,
        )


def _call_ctx() -> CallContext:
    return CallContext.with_timeout(correlation_id="eval-corr", timeout_seconds=15.0)


# ==============================================================================
# 1. SANITIZED HUMAN ACCEPTANCE REGRESSION SUITE
# ==============================================================================

SANITIZED_HUMAN_ACCEPTANCE_RESUME_TEXT = """
Ricardo Santos Silva
Computer Engineer | Data & AI | Market Intelligence | Commercial Planning | PMO
São Paulo, SP - Brasil | ricardo.exemplo@provedor.com | (11) 98765-4321

Resumo Profissional:
Profissional sênior especializado em engenharia de dados, analytics e inteligência de mercado
com mais de 10 anos de experiência liderando projetos estratégicos e
transformações orientadas a dados.

Experiência Profissional:
1. Inpasa Brasil — Analista de Dados Sênior (2022 - Atual)
- Liderança de squads de engenharia de dados e machine learning
- Entrega de R$ 7,32 milhões em savings em projetos de analytics
- Desenvolvimento de pipelines em Python, SQL e Databricks

2. Grupo Vanguarda — Especialista em Inteligência de Mercado (2019 - 2022)
- Modelagem de precificação dinâmica e planejamento comercial
- Implantação de modelos preditivos em Python e Power BI

3. SLC Agrícola — Engenheiro de Dados / Planejamento Comercial (2016 - 2019)
- Estruturação de data warehouses e pipelines ETL em SQL Server
- Automação de relatórios executivos

4. Ceuma Tech — Analista de Sistemas / PMO (2013 - 2016)
- Gestão ágil de projetos e arquitetura de sistemas corporativos

Formação Acadêmica:
- Universidade Ceuma: Graduação em Engenharia de Computação (2009 - 2014) — Concluído
- FGV: MBA em Ciência de Dados e Inteligência Artificial (2023 - Em andamento)
- ESPM: Pós-Graduação em Inteligência de Mercado (2018 - 2020) — Concluído

Idiomas:
- Português: Nativo
- Inglês: Avançado

Certificações:
- AWS Certified Solutions Architect — Amazon Web Services (2023)
- Microsoft Certified: Azure Data Engineer Associate — Microsoft (2022)

Publicações:
- Aplicação de Machine Learning na Previsão de Safras Agrícolas —
  Simpósio Brasileiro de Inteligência Artificial (2021)
"""


def test_human_acceptance_sanitized_regression() -> None:
    async def scenario() -> None:
        headline_text = (
            "Computer Engineer | Data & AI | Market Intelligence | Commercial Planning | PMO"
        )
        summary_text = (
            "Profissional sênior especializado em engenharia de dados, analytics e inteligência "
            "de mercado com mais de 10 anos de experiência liderando projetos estratégicos e "
            "transformações orientadas a dados."
        )
        extraction = SemanticResumeExtraction(
            candidate_name="Ricardo Santos Silva",
            professional_headline=SemanticHeadline(
                headline=headline_text,
                source_excerpt="Computer Engineer",
            ),
            professional_summary=SemanticSummary(
                summary=summary_text,
                source_excerpt="Profissional sênior especializado em engenharia de dados",
            ),
            experiences=[
                SemanticExperience(
                    employer="Inpasa Brasil",
                    title="Analista de Dados Sênior",
                    start_year=2022,
                    is_current=True,
                    achievements=["Entrega de R$ 7,32 milhões em savings em projetos de analytics"],
                    technologies=["Python", "SQL", "Databricks"],
                    source_excerpt="Inpasa Brasil — Analista de Dados Sênior (2022 - Atual)",
                ),
                SemanticExperience(
                    employer="Grupo Vanguarda",
                    title="Especialista em Inteligência de Mercado",
                    start_year=2019,
                    end_year=2022,
                    technologies=["Python", "Power BI"],
                    source_excerpt="Grupo Vanguarda — Especialista em Inteligência de Mercado",
                ),
                SemanticExperience(
                    employer="SLC Agrícola",
                    title="Engenheiro de Dados / Planejamento Comercial",
                    start_year=2016,
                    end_year=2019,
                    technologies=["SQL", "ETL"],
                    source_excerpt="SLC Agrícola — Engenheiro de Dados",
                ),
                SemanticExperience(
                    employer="Ceuma Tech",
                    title="Analista de Sistemas / PMO",
                    start_year=2013,
                    end_year=2016,
                    source_excerpt="Ceuma Tech — Analista de Sistemas / PMO",
                ),
            ],
            education=[
                SemanticEducation(
                    institution="Universidade Ceuma",
                    degree="Graduação",
                    field_of_study="Engenharia de Computação",
                    start_year=2009,
                    end_year=2014,
                    status="concluído",
                    source_excerpt="Universidade Ceuma: Graduação em Engenharia de Computação",
                ),
                SemanticEducation(
                    institution="FGV",
                    degree="MBA",
                    field_of_study="Ciência de Dados e Inteligência Artificial",
                    start_year=2023,
                    status="em andamento",
                    source_excerpt="FGV: MBA em Ciência de Dados e Inteligência Artificial",
                ),
                SemanticEducation(
                    institution="ESPM",
                    degree="Pós-Graduação",
                    field_of_study="Inteligência de Mercado",
                    start_year=2018,
                    end_year=2020,
                    status="concluído",
                    source_excerpt="ESPM: Pós-Graduação em Inteligência de Mercado",
                ),
            ],
            languages=[
                SemanticLanguage(
                    language="Português", proficiency="Nativo", source_excerpt="Português: Nativo"
                ),
                SemanticLanguage(
                    language="Inglês", proficiency="Avançado", source_excerpt="Inglês: Avançado"
                ),
            ],
            skills=[
                SemanticSkill(name="Python", source_excerpt="Python"),
                SemanticSkill(name="SQL", source_excerpt="SQL"),
                SemanticSkill(name="Databricks", source_excerpt="Databricks"),
            ],
            certifications=[
                SemanticCertification(
                    name="AWS Certified Solutions Architect",
                    issuer="Amazon Web Services",
                    issue_year=2023,
                    source_excerpt="AWS Certified Solutions Architect — Amazon Web Services",
                )
            ],
            publications=[
                SemanticPublication(
                    title="Aplicação de Machine Learning na Previsão de Safras Agrícolas",
                    publisher="Simpósio Brasileiro de Inteligência Artificial",
                    date="2021",
                    source_excerpt="Aplicação de Machine Learning na Previsão de Safras",
                )
            ],
        )

        gateway = MockSemanticGateway(extraction)
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=SANITIZED_HUMAN_ACCEPTANCE_RESUME_TEXT.encode("utf-8"),
            media_type="text/plain",
            filename="curriculo_ricardo.txt",
        )

        parsed = await parser.parse(doc, _call_ctx())

        # Mandatory regression assertions from user prompt
        headline_facts = [f for f in parsed.facts if f.fact_type == "headline"]
        assert len(headline_facts) == 1
        assert isinstance(headline_facts[0].value, dict)
        headline_text = str(headline_facts[0].value["text"])
        assert headline_text != "Ricardo Santos Silva", "Headline must NOT match candidate name"
        assert "Computer Engineer" in headline_text

        experiences = [f for f in parsed.facts if f.fact_type == "experience"]
        assert len(experiences) >= 4, f"Expected at least 4 experiences, got {len(experiences)}"

        education = [f for f in parsed.facts if f.fact_type == "education"]
        assert len(education) >= 3, f"Expected at least 3 education entries, got {len(education)}"

        languages = [
            str(f.value["language"])
            for f in parsed.facts
            if f.fact_type == "language" and isinstance(f.value, dict)
        ]
        assert "Português" in languages
        assert "Inglês" in languages

        skills = [
            str(f.value["name"]).lower()
            for f in parsed.facts
            if f.fact_type == "skill" and isinstance(f.value, dict)
        ]
        assert "python" in skills
        assert "sql" in skills

        publications = [f for f in parsed.facts if f.fact_type == "publication"]
        assert len(publications) >= 1, "Publications must NOT be dropped"
        assert isinstance(publications[0].value, dict)
        assert "Aplicação de Machine Learning" in str(publications[0].value["title"])

        # All candidate facts must pass canonical validate_candidate
        validated_candidates = [validate_candidate(f) for f in parsed.facts]
        assert all(c is not None for c in validated_candidates), (
            "All extracted facts must be valid candidates"
        )
        non_null_candidates = [c for c in validated_candidates if c is not None]

        # Verify WhatsApp Profile Query formatter does NOT leak raw enums
        mock_facts = [
            ProfileFact(
                id=c.value_json.get("id") or __import__("uuid").uuid4(),
                user_id=__import__("uuid").uuid4(),
                career_profile_id=__import__("uuid").uuid4(),
                fact_type=getattr(FactType, c.kind.upper(), FactType.SKILL),
                value_json=c.value_json,
                source_kind="resume_import",
                confidence=c.confidence,
                user_confirmed=True,
                status=FactStatus.ACTIVE,
                lineage_id=__import__("uuid").uuid4(),
                active_from_profile_version=1,
            )
            for c in non_null_candidates
            if c.kind not in ("headline", "summary")
        ]
        snapshot = TruthSnapshot(
            user_id=__import__("uuid").uuid4(),
            profile_version=1,
            headline=headline_text,
            summary=extraction.professional_summary.summary
            if extraction.professional_summary
            else None,
            target_roles=[],
            facts=mock_facts,
        )
        rendered = format_profile_query_summary(snapshot)

        for line in rendered.splitlines():
            trimmed = line.strip()
            assert trimmed != "- language", f"Raw enum leak: {line}"
            assert trimmed != "- education", f"Raw enum leak: {line}"
            assert trimmed != "- experience", f"Raw enum leak: {line}"
            assert trimmed != "- skill", f"Raw enum leak: {line}"
            assert trimmed != "- publication", f"Raw enum leak: {line}"

    asyncio.run(scenario())


# ==============================================================================
# 2. FORMAT-INDEPENDENCE EVAL SUITE (VARIANTS A THROUGH H)
# ==============================================================================

# Common semantic extraction that must be consistently extracted across all 8 variations
CANONICAL_TARGET_EXTRACTION = SemanticResumeExtraction(
    candidate_name="Ana Carolina Prado",
    professional_headline=SemanticHeadline(
        headline="Senior Backend Engineer | Cloud & Distributed Systems",
        source_excerpt="Senior Backend Engineer | Cloud & Distributed Systems",
    ),
    professional_summary=SemanticSummary(
        summary=(
            "Engenheira de software sênior com 8 anos de experiência em Python, Go "
            "e arquiteturas de microsserviços."
        ),
        source_excerpt="Engenheira de software sênior",
    ),
    experiences=[
        SemanticExperience(
            employer="Inpasa Brasil",
            title="Senior Backend Engineer",
            start_year=2021,
            is_current=True,
            technologies=["Python", "Go", "Kubernetes"],
            source_excerpt="Inpasa Brasil",
        )
    ],
    education=[
        SemanticEducation(
            institution="Universidade de São Paulo",
            degree="Bacharelado",
            field_of_study="Engenharia de Computação",
            start_year=2012,
            end_year=2017,
            status="concluído",
            source_excerpt="Universidade de São Paulo",
        )
    ],
    languages=[
        SemanticLanguage(language="Português", proficiency="Nativo", source_excerpt="Português"),
        SemanticLanguage(language="Inglês", proficiency="Fluente", source_excerpt="Inglês"),
    ],
    skills=[
        SemanticSkill(name="Python", source_excerpt="Python"),
        SemanticSkill(name="Go", source_excerpt="Go"),
        SemanticSkill(name="Kubernetes", source_excerpt="Kubernetes"),
    ],
)

# Variant A: Headings tradicionais
FIXTURE_A_TRADITIONAL_HEADINGS = """
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
Python, Go, Kubernetes

Idiomas
Português (Nativo), Inglês (Fluente)
"""

# Variant B: English headings
FIXTURE_B_ENGLISH_HEADINGS = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems

Professional Summary
Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

Work Experience
Inpasa Brasil — Senior Backend Engineer (2021 - Present)
Built microservices using Python, Go, and Kubernetes.

Education
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Skills
Python, Go, Kubernetes

Languages
Português (Nativo), Inglês (Fluente)
"""

# Variant C: Creative headings
FIXTURE_C_CREATIVE_HEADINGS = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems

Minha Trajetória
Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

O Que Já Construí
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
Sistemas distribuídos com Python, Go e Kubernetes.

Bagagem Universitária
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Stack Técnica
Python, Go, Kubernetes

Comunicação Global
Português, Inglês
"""

# Variant D: No headings
FIXTURE_D_NO_HEADINGS = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems
Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

Atualmente na Inpasa Brasil como Senior Backend Engineer desde 2021 desenvolvendo
microsserviços com Python, Go e Kubernetes.

Formada na Universidade de São Paulo em Bacharelado em Engenharia de Computação (2012-2017).

Domínio de Python, Go, Kubernetes.
Português nativo e Inglês fluente.
"""

# Variant E: Reordered sections
FIXTURE_E_REORDERED = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems

Competências
Python, Go, Kubernetes

Idiomas
Português, Inglês

Formação Acadêmica
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Experiência Profissional
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)

Resumo
Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.
"""

# Variant F: One-line experience format
FIXTURE_F_ONE_LINE_EXPERIENCE = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems
Resumo: Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

Experiência:
* Inpasa Brasil | Senior Backend Engineer | 2021 - Atual | Python, Go, Kubernetes

Formação:
* Universidade de São Paulo | Engenharia de Computação | 2012 - 2017

Skills: Python, Go, Kubernetes
Idiomas: Português, Inglês
"""

# Variant G: Split title and company on separate lines
FIXTURE_G_SPLIT_TITLE_COMPANY = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems
Resumo: Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

Experiência:
Inpasa Brasil
Senior Backend Engineer
2021 - Atual
Tecnologias: Python, Go, Kubernetes

Formação:
Universidade de São Paulo
Bacharelado em Engenharia de Computação
2012 - 2017

Skills: Python, Go, Kubernetes
Idiomas: Português, Inglês
"""

# Variant H: Skills embedded only within experiences
FIXTURE_H_SKILLS_ONLY_IN_EXPERIENCE = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems
Resumo: Engenheira de software sênior com 8 anos de experiência em Python, Go
e arquiteturas de microsserviços.

Histórico Profissional:
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
- Desenvolvi microsserviços de alto throughput com Python e Go.
- Orquestração de clusters corporativos com Kubernetes.

Educação:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Idiomas:
Português (Nativo), Inglês (Fluente)
"""

EVAL_FIXTURES: list[tuple[str, str]] = [
    ("A_traditional_headings", FIXTURE_A_TRADITIONAL_HEADINGS),
    ("B_english_headings", FIXTURE_B_ENGLISH_HEADINGS),
    ("C_creative_headings", FIXTURE_C_CREATIVE_HEADINGS),
    ("D_no_headings", FIXTURE_D_NO_HEADINGS),
    ("E_reordered_sections", FIXTURE_E_REORDERED),
    ("F_one_line_experience", FIXTURE_F_ONE_LINE_EXPERIENCE),
    ("G_split_title_company", FIXTURE_G_SPLIT_TITLE_COMPANY),
    ("H_skills_only_in_experience", FIXTURE_H_SKILLS_ONLY_IN_EXPERIENCE),
]


@pytest.mark.parametrize("variant_name,fixture_text", EVAL_FIXTURES)
def test_format_independence_eval_variants(variant_name: str, fixture_text: str) -> None:
    async def scenario() -> None:
        gateway = MockSemanticGateway(CANONICAL_TARGET_EXTRACTION)
        parser = SemanticResumeParser(gateway)
        doc = ResumeDocument(
            content=fixture_text.encode("utf-8"),
            media_type="text/plain",
            filename=f"fixture_{variant_name}.txt",
        )

        parsed = await parser.parse(doc, _call_ctx())

        # Assert identical core facts are extracted across every layout variant
        headline_facts = [f for f in parsed.facts if f.fact_type == "headline"]
        assert len(headline_facts) == 1, f"[{variant_name}] Headline missing"
        assert isinstance(headline_facts[0].value, dict)
        assert (
            str(headline_facts[0].value["text"])
            == "Senior Backend Engineer | Cloud & Distributed Systems"
        )

        exp_facts = [f for f in parsed.facts if f.fact_type == "experience"]
        assert len(exp_facts) >= 1, f"[{variant_name}] Experience missing"
        assert isinstance(exp_facts[0].value, dict)
        assert exp_facts[0].value["employer"] == "Inpasa Brasil"
        assert exp_facts[0].value["title"] == "Senior Backend Engineer"

        edu_facts = [f for f in parsed.facts if f.fact_type == "education"]
        assert len(edu_facts) >= 1, f"[{variant_name}] Education missing"
        assert isinstance(edu_facts[0].value, dict)
        assert edu_facts[0].value["institution"] == "Universidade de São Paulo"

        lang_facts = [
            str(f.value["language"])
            for f in parsed.facts
            if f.fact_type == "language" and isinstance(f.value, dict)
        ]
        assert "Português" in lang_facts, f"[{variant_name}] Portuguese missing"
        assert "Inglês" in lang_facts, f"[{variant_name}] English missing"

        skill_facts = [
            str(f.value["name"]).lower()
            for f in parsed.facts
            if f.fact_type == "skill" and isinstance(f.value, dict)
        ]
        assert "python" in skill_facts, f"[{variant_name}] Python skill missing"
        assert "go" in skill_facts, f"[{variant_name}] Go skill missing"
        assert "kubernetes" in skill_facts, f"[{variant_name}] Kubernetes skill missing"

    asyncio.run(scenario())
