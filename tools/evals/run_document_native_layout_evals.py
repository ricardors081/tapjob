"""Document-Native Layout Independence Semantic Eval Runner for Resume Understanding Engine.

Evaluates semantic extraction across 9 layout/format variations (Variants A through I)
using the configured live AI provider.

Guarantees:
- If OPENAI_API_KEY is missing, prints REAL_PROVIDER_DOCUMENT_NATIVE_EVAL=NOT_RUN_MISSING_SECRET
  and exits cleanly without claiming false PASS.
- Sanitizes all report outputs (zero PII, zero raw provider text dumps).
- Enforces an 85% accuracy threshold across essential career facts.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from tapjob_api.ai_gateway.openai_gateway import OpenAiGateway
from tapjob_api.ports._common import CallContext
from tapjob_api.ports.resume_parser import ResumeDocument
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser

# 9 Layout Variations (Variants A-I)
FIXTURE_A_SINGLE_COLUMN = """
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
"""

FIXTURE_B_TWO_COLUMN_SIDEBAR = """
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
"""

FIXTURE_C_NAME_LINE1_HEADLINE_LINE2 = """
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
"""

FIXTURE_D_NAME_ONLY_HEADER_NO_HEADLINE = """
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
"""

FIXTURE_E_COMPLEX_MANAGEMENT_COMPETENCIES = """
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
"""

FIXTURE_F_MULTIPLE_SPOKEN_LANGUAGES = """
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
"""

FIXTURE_G_MULTI_PAGE_DOCUMENT = """
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
"""

FIXTURE_H_VARIED_EDUCATION_SHAPES = """
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
"""

FIXTURE_I_UNICODE_AND_LINE_BREAK_HYPHENS = """
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
"""

FIXTURES: list[tuple[str, str]] = [
    ("A_single_column", FIXTURE_A_SINGLE_COLUMN),
    ("B_two_column_sidebar", FIXTURE_B_TWO_COLUMN_SIDEBAR),
    ("C_name_line1_headline_line2", FIXTURE_C_NAME_LINE1_HEADLINE_LINE2),
    ("D_name_only_header_no_headline", FIXTURE_D_NAME_ONLY_HEADER_NO_HEADLINE),
    ("E_complex_management_competencies", FIXTURE_E_COMPLEX_MANAGEMENT_COMPETENCIES),
    ("F_multiple_spoken_languages", FIXTURE_F_MULTIPLE_SPOKEN_LANGUAGES),
    ("G_multi_page_document", FIXTURE_G_MULTI_PAGE_DOCUMENT),
    ("H_varied_education_shapes", FIXTURE_H_VARIED_EDUCATION_SHAPES),
    ("I_unicode_and_line_break_hyphens", FIXTURE_I_UNICODE_AND_LINE_BREAK_HYPHENS),
]

ACCURACY_THRESHOLD = 0.85


async def evaluate_single_fixture(
    parser: SemanticResumeParser,
    name: str,
    text: str,
) -> dict[str, Any]:
    doc = ResumeDocument(
        content=text.encode("utf-8"),
        media_type="text/plain",
        filename=f"eval_{name}.txt",
    )
    ctx = CallContext.with_timeout(
        correlation_id=f"eval-doc-{name}",
        timeout_seconds=30.0,
    )

    parsed = await parser.parse(doc, ctx)

    # Check 1: Candidate name not used as headline
    headline_facts = [
        f for f in parsed.facts if f.fact_type == "headline" and isinstance(f.value, dict)
    ]
    name_not_headline = True
    for hf in headline_facts:
        htext = str(hf.value.get("text", "")).lower()
        if ("ricardo" in htext or "ana carolina" in htext) and not any(
            kw in htext for kw in ("engineer", "manager", "head", "lead", "tecnologia")
        ):
            name_not_headline = False

    # Check 2: Employer
    has_employer = any(
        f.fact_type == "experience"
        and isinstance(f.value, dict)
        and "Inpasa" in str(f.value.get("employer", ""))
        for f in parsed.facts
    )

    # Check 3: Education
    has_education = any(
        f.fact_type == "education"
        and isinstance(f.value, dict)
        and (
            "São Paulo" in str(f.value.get("institution", ""))
            or "USP" in str(f.value.get("institution", ""))
        )
        for f in parsed.facts
    )

    # Check 4: Languages (only natural spoken languages)
    languages = [
        str(f.value.get("language", "")).lower()
        for f in parsed.facts
        if f.fact_type == "language" and isinstance(f.value, dict)
    ]
    has_natural_languages = any("portugu" in lang for lang in languages) and any(
        "ingl" in lang for lang in languages
    )
    no_skill_in_language = not any(
        "stakeholder" in lang or "python" in lang or "sql" in lang for lang in languages
    )
    languages_valid = has_natural_languages and no_skill_in_language

    # Check 5: Skills (technical and management)
    skills = {
        str(f.value.get("name", "")).lower()
        for f in parsed.facts
        if f.fact_type == "skill" and isinstance(f.value, dict)
    }
    has_python = "python" in skills
    has_stakeholders = any("stakeholder" in s for s in skills)
    skills_passed = has_python and has_stakeholders

    checks = [
        ("name_not_headline", name_not_headline),
        ("employer", has_employer),
        ("education", has_education),
        ("languages_valid", languages_valid),
        ("skills_passed", skills_passed),
    ]

    passed_count = sum(1 for _, ok in checks if ok)
    score = passed_count / len(checks)

    return {
        "variant": name,
        "score": score,
        "passed_count": passed_count,
        "total_checks": len(checks),
        "details": {k: ok for k, ok in checks},
    }


def _emit(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


async def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        _emit("REAL_PROVIDER_DOCUMENT_NATIVE_EVAL=NOT_RUN_MISSING_SECRET")
        _emit("REASON=OPENAI_API_KEY environment variable is not configured.")
        return 0

    model = os.environ.get("AI_RESUME_EXTRACTION_MODEL", "gpt-4o-mini").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

    gateway = OpenAiGateway(api_key=api_key, model=model, base_url=base_url)
    parser = SemanticResumeParser(gateway)

    _emit("================================================================================")
    _emit("CAREER UNDERSTANDING ENGINE — DOCUMENT-NATIVE LAYOUT EVALUATION (REAL PROVIDER)")
    _emit(f"Model: {model}")
    _emit("================================================================================")

    results: list[dict[str, Any]] = []
    for name, text in FIXTURES:
        try:
            res = await evaluate_single_fixture(parser, name, text)
            results.append(res)
            score_pct = int(res["score"] * 100)
            status_str = "PASS" if res["score"] >= ACCURACY_THRESHOLD else "FAIL"
            passed = res["passed_count"]
            total = res["total_checks"]
            _emit(f"[{status_str}] {name:35} Score: {score_pct:3}% ({passed}/{total} checks)")
        except Exception as exc:
            _emit(f"[ERROR] {name:35} Failed with exception: {exc.__class__.__name__}")
            results.append({"variant": name, "score": 0.0, "passed_count": 0, "total_checks": 5})

    total_score = sum(r["score"] for r in results) / len(results) if results else 0.0
    overall_pct = int(total_score * 100)
    _emit("--------------------------------------------------------------------------------")
    _emit(f"Overall Accuracy: {overall_pct}% (Threshold: {int(ACCURACY_THRESHOLD * 100)}%)")

    if total_score >= ACCURACY_THRESHOLD:
        _emit("REAL_PROVIDER_DOCUMENT_NATIVE_EVAL=PASS")
        return 0
    else:
        _emit("REAL_PROVIDER_DOCUMENT_NATIVE_EVAL=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
