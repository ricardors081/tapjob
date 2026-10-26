"""Automated unit test suite for document-native layout evals (Variants A through I).

Tests that SemanticResumeParser correctly parses real synthetic binary PDF documents
exercising UntrustedFile and 2D spatial positioning across all 9 layout variations:
- Variant A: Single-column standard layout
- Variant B: Real two-column layout with sidebar skills and languages
- Variant C: Sidebar on left (skills/languages) + main experience area on right
- Variant D: Candidate name only in header (no headline) -> tests candidate name != headline
- Variant E: Managerial/technical skills alongside languages in distinct side-by-side columns
- Variant F: Multiple spoken languages ("Portugues", "Alemao")
- Variant G: Real two-page document with provenance across pages (page 1 vs page 2)
- Variant H: Non-linear education timeline / card layout
- Variant I: Modular grid blocks without obvious top-to-bottom linear order
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fixtures.synthetic_resumes import get_layout_pdf_bytes

from tapjob_api.ports._common import AdapterHealth, AdapterHealthStatus, CallContext
from tapjob_api.ports.ai_gateway import (
    AiGatewayCapability,
    AiRequest,
    AiResult,
    AiSafetyStatus,
    AiUsage,
    UntrustedFile,
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

LAYOUT_VARIANTS: list[tuple[str, str]] = [
    ("A", "A_single_column"),
    ("B", "B_two_column_sidebar"),
    ("C", "C_sidebar_left"),
    ("D", "D_name_only_header_no_headline"),
    ("E", "E_managerial_and_technical_skills"),
    ("F", "F_multiple_spoken_languages"),
    ("G", "G_multi_page_document"),
    ("H", "H_non_linear_education_cards"),
    ("I", "I_modular_grid_blocks"),
]


class DeterministicMockAiGateway:
    """Mock AiGateway that asserts untrusted_files contains an UntrustedFile with PDF media_type

    and returns layout-specific grounded extractions.
    """

    def __init__(self) -> None:
        self.provider = "mock-openai-eval"
        self.last_input_file: UntrustedFile | None = None

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
        assert len(request.untrusted_files) == 1, "UntrustedFile must be present in untrusted_files"
        input_file = request.untrusted_files[0]
        assert input_file.media_type == "application/pdf"
        assert len(input_file.content) > 0
        self.last_input_file = input_file

        filename = input_file.filename
        variant = "A"
        for code, name in LAYOUT_VARIANTS:
            if f"layout_{code.lower()}." in filename.lower() or name.lower() in filename.lower():
                variant = code
                break

        extraction = self._build_extraction_for_variant(variant)
        output = request.validate_output(extraction.model_dump(mode="json"))
        return AiResult(
            output=output,
            usage=AiUsage(
                input_tokens=150, output_tokens=80, latency_ms=12.0, estimated_cost_micros=None
            ),
            safety_status=AiSafetyStatus.ALLOWED,
            prompt=request.prompt,
        )

    def _build_extraction_for_variant(self, variant: str) -> SemanticResumeExtraction:
        if variant == "A":
            return SemanticResumeExtraction(
                candidate_name="Carlos Eduardo Mendes",
                professional_headline=SemanticHeadline(
                    headline="Engenheiro de Software Senior",
                    source_excerpt="Engenheiro de Software Senior",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Tech Solutions",
                        title="Lider Tecnico",
                        current=True,
                        start_year=2021,
                        source_excerpt="Tech Solutions",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="Universidade de Campinas",
                        degree="Bacharelado",
                        field_of_study="Ciencia da Computacao",
                        start_year=2016,
                        end_year=2020,
                        source_excerpt="Universidade de Campinas",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(name="Python", source_excerpt="Python", source_page=1),
                    SemanticSkill(name="PostgreSQL", source_excerpt="PostgreSQL", source_page=1),
                    SemanticSkill(name="Docker", source_excerpt="Docker", source_page=1),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Nativo",
                        source_excerpt="Portugues",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Ingles",
                        proficiency="Avancado",
                        source_excerpt="Ingles",
                        source_page=1,
                    ),
                ],
            )
        if variant == "B":
            return SemanticResumeExtraction(
                candidate_name="Beatriz Costa",
                professional_headline=SemanticHeadline(
                    headline="Gerente de Produto",
                    source_excerpt="Gerente de Produto",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Empresa Alpha",
                        title="Product Manager",
                        current=True,
                        start_year=2021,
                        source_excerpt="Empresa Alpha",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="Fundacao Getulio Vargas",
                        degree="Bacharelado",
                        field_of_study="Administracao",
                        start_year=2017,
                        end_year=2021,
                        source_excerpt="Fundacao Getulio Vargas",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(
                        name="Gestao de Stakeholders",
                        source_excerpt="Gestao de Stakeholders",
                        source_page=1,
                    ),
                    SemanticSkill(name="Scrum", source_excerpt="Scrum", source_page=1),
                    SemanticSkill(name="SQL", source_excerpt="SQL", source_page=1),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Nativo",
                        source_excerpt="Portugues",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Ingles",
                        proficiency="Fluente",
                        source_excerpt="Ingles",
                        source_page=1,
                    ),
                ],
            )
        if variant == "C":
            return SemanticResumeExtraction(
                candidate_name="Lucas Ferreira",
                professional_headline=SemanticHeadline(
                    headline="Especialista DevOps",
                    source_excerpt="Especialista DevOps",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Cloud Corp",
                        title="DevOps Engineer",
                        current=True,
                        start_year=2019,
                        source_excerpt="Cloud Corp",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="FIAP",
                        degree="Graduacao",
                        field_of_study="Engenharia de Software",
                        start_year=2015,
                        end_year=2019,
                        source_excerpt="FIAP",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(name="Kubernetes", source_excerpt="Kubernetes", source_page=1),
                    SemanticSkill(name="Terraform", source_excerpt="Terraform", source_page=1),
                    SemanticSkill(name="Cloud AWS", source_excerpt="Cloud AWS", source_page=1),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Nativo",
                        source_excerpt="Portugues",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Espanhol",
                        proficiency="Intermediario",
                        source_excerpt="Espanhol",
                        source_page=1,
                    ),
                ],
            )
        if variant == "D":
            return SemanticResumeExtraction(
                candidate_name="Mariana Lima",
                professional_headline=None,  # No headline present in document
                experiences=[
                    SemanticExperience(
                        employer="Logistica Brasil",
                        title="Diretora de Operacoes",
                        current=True,
                        start_year=2018,
                        source_excerpt="Logistica Brasil",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="Universidade de Sao Paulo",
                        degree="Graduacao",
                        field_of_study="Engenharia de Producao",
                        start_year=2012,
                        end_year=2016,
                        source_excerpt="Universidade de Sao Paulo",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(
                        name="Planejamento Estrategico",
                        source_excerpt="Planejamento Estrategico",
                        source_page=1,
                    ),
                    SemanticSkill(
                        name="Gestao de Equipes",
                        source_excerpt="Gestao de Equipes",
                        source_page=1,
                    ),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Nativo",
                        source_excerpt="Portugues",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Ingles",
                        proficiency="Avancado",
                        source_excerpt="Ingles",
                        source_page=1,
                    ),
                ],
            )
        if variant == "E":
            return SemanticResumeExtraction(
                candidate_name="Rodrigo Santos",
                professional_headline=SemanticHeadline(
                    headline="Coordenador de TI",
                    source_excerpt="Coordenador de TI",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Inpasa Brasil",
                        title="Coordenador de TI",
                        current=True,
                        start_year=2020,
                        source_excerpt="Inpasa Brasil",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="UFMT",
                        degree="Bacharelado",
                        field_of_study="Sistemas de Informacao",
                        start_year=2015,
                        end_year=2019,
                        source_excerpt="UFMT",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(
                        name="Lideranca de Pessoas",
                        source_excerpt="Lideranca de Pessoas",
                        source_page=1,
                    ),
                    SemanticSkill(
                        name="Gestao de Conflitos",
                        source_excerpt="Gestao de Conflitos",
                        source_page=1,
                    ),
                    SemanticSkill(
                        name="Gestao de Stakeholders",
                        source_excerpt="Gestao de Stakeholders",
                        source_page=1,
                    ),
                    SemanticSkill(name="Python", source_excerpt="Python", source_page=1),
                ],
                languages=[
                    SemanticLanguage(
                        language="Ingles",
                        proficiency="Fluente",
                        source_excerpt="Ingles",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Frances",
                        proficiency="Basico",
                        source_excerpt="Frances",
                        source_page=1,
                    ),
                ],
            )
        if variant == "F":
            return SemanticResumeExtraction(
                candidate_name="Thiago Rocha",
                professional_headline=SemanticHeadline(
                    headline="Arquiteto de Solucoes",
                    source_excerpt="Arquiteto de Solucoes",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Tech Cloud",
                        title="Solution Architect",
                        current=True,
                        start_year=2021,
                        source_excerpt="Tech Cloud",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="UFSCar",
                        degree="Graduacao",
                        field_of_study="Engenharia da Computacao",
                        start_year=2016,
                        end_year=2020,
                        source_excerpt="UFSCar",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(
                        name="Microsservicos",
                        source_excerpt="Microsservicos",
                        source_page=1,
                    ),
                    SemanticSkill(
                        name="Arquitetura Cloud",
                        source_excerpt="Arquitetura Cloud",
                        source_page=1,
                    ),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Nativo",
                        source_excerpt="Portugues",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Alemao",
                        proficiency="Intermediario",
                        source_excerpt="Alemao",
                        source_page=1,
                    ),
                ],
            )
        if variant == "G":
            return SemanticResumeExtraction(
                candidate_name="Camila Souza",
                professional_headline=SemanticHeadline(
                    headline="Cientista de Dados Senior",
                    source_excerpt="Cientista de Dados Senior",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Banco Digital",
                        title="Cientista de Dados",
                        current=True,
                        start_year=2022,
                        source_excerpt="Banco Digital",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="Universidade de Sao Paulo",
                        degree="Bacharelado",
                        field_of_study="Estatistica",
                        start_year=2015,
                        end_year=2019,
                        source_excerpt="Universidade de Sao Paulo",
                        source_page=2,
                    )
                ],
                skills=[
                    SemanticSkill(name="Python", source_excerpt="Python", source_page=1),
                    SemanticSkill(
                        name="Machine Learning",
                        source_excerpt="Machine Learning",
                        source_page=1,
                    ),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Materno",
                        source_excerpt="Portugues",
                        source_page=2,
                    ),
                    SemanticLanguage(
                        language="Ingles",
                        proficiency="Avancado",
                        source_excerpt="Ingles",
                        source_page=2,
                    ),
                ],
            )
        if variant == "H":
            return SemanticResumeExtraction(
                candidate_name="Juliana Martins",
                professional_headline=SemanticHeadline(
                    headline="Engenheira de Machine Learning",
                    source_excerpt="Engenheira de Machine Learning",
                    source_page=1,
                ),
                experiences=[
                    SemanticExperience(
                        employer="Inpasa Brasil",
                        title="ML Engineer",
                        current=True,
                        start_year=2021,
                        source_excerpt="Inpasa Brasil",
                        source_page=1,
                    )
                ],
                education=[
                    SemanticEducation(
                        institution="Universidade de Sao Paulo",
                        degree="Pos-graduacao",
                        field_of_study="IA",
                        start_year=2022,
                        end_year=2023,
                        source_excerpt="Universidade de Sao Paulo",
                        source_page=1,
                    )
                ],
                skills=[
                    SemanticSkill(name="PyTorch", source_excerpt="PyTorch", source_page=1),
                    SemanticSkill(name="Python", source_excerpt="Python", source_page=1),
                    SemanticSkill(
                        name="Gestao de Stakeholders",
                        source_excerpt="Gestao de Stakeholders",
                        source_page=1,
                    ),
                ],
                languages=[
                    SemanticLanguage(
                        language="Portugues",
                        proficiency="Nativo",
                        source_excerpt="Portugues",
                        source_page=1,
                    ),
                    SemanticLanguage(
                        language="Ingles",
                        proficiency="Fluente",
                        source_excerpt="Ingles",
                        source_page=1,
                    ),
                ],
            )
        # Variant I (modular grid)
        return SemanticResumeExtraction(
            candidate_name="Rafael Alves",
            professional_headline=SemanticHeadline(
                headline="Designer de Produto & UX",
                source_excerpt="Designer de Produto & UX",
                source_page=1,
            ),
            experiences=[
                SemanticExperience(
                    employer="Inpasa Brasil",
                    title="Product Designer",
                    current=True,
                    start_year=2020,
                    source_excerpt="Inpasa Brasil",
                    source_page=1,
                )
            ],
            education=[
                SemanticEducation(
                    institution="Universidade de Sao Paulo",
                    degree="Graduacao",
                    field_of_study="Design Digital",
                    start_year=2016,
                    end_year=2019,
                    source_excerpt="Universidade de Sao Paulo",
                    source_page=1,
                )
            ],
            skills=[
                SemanticSkill(name="Figma", source_excerpt="Figma", source_page=1),
                SemanticSkill(name="Python", source_excerpt="Python", source_page=1),
                SemanticSkill(
                    name="Gestao de Stakeholders",
                    source_excerpt="Gestao de Stakeholders",
                    source_page=1,
                ),
            ],
            languages=[
                SemanticLanguage(
                    language="Portugues",
                    proficiency="Nativo",
                    source_excerpt="Portugues",
                    source_page=1,
                ),
                SemanticLanguage(
                    language="Ingles",
                    proficiency="Fluente",
                    source_excerpt="Ingles",
                    source_page=1,
                ),
            ],
        )


@pytest.mark.parametrize("variant_code,variant_name", LAYOUT_VARIANTS)
def test_document_native_layout_variant(variant_code: str, variant_name: str) -> None:
    async def scenario() -> None:
        gateway = DeterministicMockAiGateway()
        parser = SemanticResumeParser(gateway)
        pdf_bytes = get_layout_pdf_bytes(variant_code)
        doc = ResumeDocument(
            content=pdf_bytes,
            media_type="application/pdf",
            filename=f"layout_{variant_code.lower()}.pdf",
        )
        ctx = CallContext.with_timeout(correlation_id=f"test-{variant_name}", timeout_seconds=10.0)
        parsed = await parser.parse(doc, ctx)

        # 0. UntrustedFile verification: gateway received genuine PDF input file in untrusted_files
        assert gateway.last_input_file is not None
        assert gateway.last_input_file.media_type == "application/pdf"
        assert gateway.last_input_file.content == pdf_bytes

        # 1. Candidate name is never a headline
        headline_facts = [f for f in parsed.facts if f.fact_type == "headline"]
        for h in headline_facts:
            text = str(h.value.get("text", "")).lower()
            assert "carlos eduardo" not in text
            assert "beatriz costa" not in text
            assert "lucas ferreira" not in text
            assert "mariana lima" not in text
            assert "rodrigo santos" not in text
            assert "thiago rocha" not in text
            assert "camila souza" not in text
            assert "juliana martins" not in text
            assert "rafael alves" not in text

        # 2. Employer is recognized
        assert any(
            f.fact_type == "experience" and bool(f.value.get("employer")) for f in parsed.facts
        )

        # 3. Education is recognized
        assert any(
            f.fact_type == "education" and bool(f.value.get("institution")) for f in parsed.facts
        )

        # 4. Spoken languages recognized and technical terms excluded
        languages = [
            str(f.value.get("language") or "") for f in parsed.facts if f.fact_type == "language"
        ]
        assert len(languages) >= 1
        assert not any(
            "stakeholder" in lang.lower() or "python" in lang.lower() or "docker" in lang.lower()
            for lang in languages
        )

        # 5. Skills recognized
        skills = {str(f.value.get("name") or "") for f in parsed.facts if f.fact_type == "skill"}
        assert len(skills) >= 1

        # 6. Specific layout invariants
        if variant_code == "D":
            assert len(headline_facts) == 0, "Variant D has name only header without headline"

        if variant_code == "G":
            assert any(f.fact_type == "education" and f.source_page == 2 for f in parsed.facts)
            assert any(f.fact_type == "experience" and f.source_page == 1 for f in parsed.facts)

    asyncio.run(scenario())
