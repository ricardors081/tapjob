"""Format Independence Semantic Eval Runner for Resume Understanding Engine.

Evaluates semantic extraction across 8 layout/format variations (Variants A through H)
using the configured live AI provider.

Guarantees:
- If OPENAI_API_KEY is missing, prints REAL_PROVIDER_FORMAT_EVAL=NOT_RUN_MISSING_SECRET
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

# 8 Layout Variations (Variants A-H)
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

Educação:
Universidade de São Paulo
Engenharia de Computação
2012 - 2017

Habilidades Técnicas:
Python, Go, Kubernetes

Idiomas:
Português e Inglês
"""

FIXTURE_H_SKILLS_ONLY_IN_EXPERIENCE = """
Ana Carolina Prado
Senior Backend Engineer | Cloud & Distributed Systems
Resumo: Engenheira de software sênior com 8 anos de experiência.

Experiência Profissional:
Inpasa Brasil — Senior Backend Engineer (2021 - Atual)
Responsável por migração de monólito para microsserviços utilizando as linguagens
Python e Go, orquestrados em clusters Kubernetes na nuvem.

Formação:
Universidade de São Paulo — Bacharelado em Engenharia de Computação (2012 - 2017)

Idiomas:
Português e Inglês
"""

FIXTURES: list[tuple[str, str]] = [
    ("A_traditional_headings", FIXTURE_A_TRADITIONAL_HEADINGS),
    ("B_english_headings", FIXTURE_B_ENGLISH_HEADINGS),
    ("C_creative_headings", FIXTURE_C_CREATIVE_HEADINGS),
    ("D_no_headings", FIXTURE_D_NO_HEADINGS),
    ("E_reordered_sections", FIXTURE_E_REORDERED),
    ("F_one_line_experience", FIXTURE_F_ONE_LINE_EXPERIENCE),
    ("G_split_title_company", FIXTURE_G_SPLIT_TITLE_COMPANY),
    ("H_skills_only_in_experience", FIXTURE_H_SKILLS_ONLY_IN_EXPERIENCE),
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
        correlation_id=f"eval-{name}",
        timeout_seconds=30.0,
    )

    parsed = await parser.parse(doc, ctx)

    # Check 1: Headline
    has_headline = any(
        f.fact_type == "headline"
        and isinstance(f.value, dict)
        and "Backend" in str(f.value.get("text", ""))
        for f in parsed.facts
    )

    # Check 2: Employer
    has_employer = any(
        f.fact_type == "experience"
        and isinstance(f.value, dict)
        and "Inpasa" in str(f.value.get("employer", ""))
        for f in parsed.facts
    )

    # Check 3: Title
    has_title = any(
        f.fact_type == "experience"
        and isinstance(f.value, dict)
        and "Backend Engineer" in str(f.value.get("title", ""))
        for f in parsed.facts
    )

    # Check 4: Education
    has_education = any(
        f.fact_type == "education"
        and isinstance(f.value, dict)
        and "São Paulo" in str(f.value.get("institution", ""))
        for f in parsed.facts
    )

    # Check 5: Languages
    langs = [
        str(f.value.get("language", ""))
        for f in parsed.facts
        if f.fact_type == "language" and isinstance(f.value, dict)
    ]
    has_languages = any("Portugu" in lang_str for lang_str in langs) and any(
        "Ingl" in lang_str for lang_str in langs
    )

    # Check 6: Skills
    skills = {
        str(f.value.get("name", "")).lower()
        for f in parsed.facts
        if f.fact_type == "skill" and isinstance(f.value, dict)
    }
    has_python = "python" in skills
    has_go = "go" in skills
    has_k8s = "kubernetes" in skills or any("kuber" in s for s in skills)
    skills_passed = (has_python and has_go) or (has_python and has_k8s)

    checks = [
        ("headline", has_headline),
        ("employer", has_employer),
        ("title", has_title),
        ("education", has_education),
        ("languages", has_languages),
        ("skills", skills_passed),
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
        _emit("REAL_PROVIDER_FORMAT_EVAL=NOT_RUN_MISSING_SECRET")
        _emit("REASON=OPENAI_API_KEY environment variable is not configured.")
        return 0

    model = os.environ.get("AI_RESUME_EXTRACTION_MODEL", "gpt-4o-mini").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

    gateway = OpenAiGateway(api_key=api_key, model=model, base_url=base_url)
    parser = SemanticResumeParser(gateway)

    _emit("================================================================================")
    _emit("CAREER UNDERSTANDING ENGINE — FORMAT INDEPENDENCE EVALUATION (REAL PROVIDER)")
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
            _emit(f"[{status_str}] {name:30} Score: {score_pct:3}% ({passed}/{total} checks)")
        except Exception as exc:
            _emit(f"[ERROR] {name:30} Failed with exception: {exc.__class__.__name__}")
            results.append({"variant": name, "score": 0.0, "passed_count": 0, "total_checks": 6})

    total_score = sum(r["score"] for r in results) / len(results) if results else 0.0
    overall_pct = int(total_score * 100)
    _emit("--------------------------------------------------------------------------------")
    _emit(f"Overall Accuracy: {overall_pct}% (Threshold: {int(ACCURACY_THRESHOLD * 100)}%)")

    if total_score >= ACCURACY_THRESHOLD:
        _emit("REAL_PROVIDER_FORMAT_EVAL=PASS")
        return 0
    else:
        _emit("REAL_PROVIDER_FORMAT_EVAL=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
