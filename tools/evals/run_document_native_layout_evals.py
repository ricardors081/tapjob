"""Document-Native Layout Independence Semantic Eval Runner for Resume Understanding Engine.

Evaluates semantic extraction across 9 layout/format variations (Variants A through I)
using real synthetic PDF binary documents and the configured live AI provider.

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
from pathlib import Path
from typing import Any

# Ensure services/api/tests is on sys.path for fixtures
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_PATH = REPO_ROOT / "services" / "api" / "tests"
if str(FIXTURES_PATH) not in sys.path:
    sys.path.insert(0, str(FIXTURES_PATH))

from fixtures.synthetic_resumes import get_layout_pdf_bytes  # noqa: E402

from tapjob_api.ai_gateway.openai_gateway import OpenAiGateway  # noqa: E402
from tapjob_api.ports._common import CallContext  # noqa: E402
from tapjob_api.ports.resume_parser import ResumeDocument  # noqa: E402
from tapjob_api.resume_import.semantic_parser import SemanticResumeParser  # noqa: E402

ACCURACY_THRESHOLD = 0.85

LAYOUT_FIXTURES: list[tuple[str, str]] = [
    ("A_single_column", "A"),
    ("B_two_column_sidebar", "B"),
    ("C_sidebar_left", "C"),
    ("D_name_only_header_no_headline", "D"),
    ("E_managerial_and_technical_skills", "E"),
    ("F_multiple_spoken_languages", "F"),
    ("G_multi_page_document", "G"),
    ("H_non_linear_education_cards", "H"),
    ("I_modular_grid_blocks", "I"),
]


async def evaluate_single_fixture(
    parser: SemanticResumeParser,
    name: str,
    variant_code: str,
) -> dict[str, Any]:
    pdf_bytes = get_layout_pdf_bytes(variant_code)
    doc = ResumeDocument(
        content=pdf_bytes,
        media_type="application/pdf",
        filename=f"eval_{name.lower()}.pdf",
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
        if any(
            n in htext
            for n in (
                "carlos eduardo",
                "beatriz costa",
                "lucas ferreira",
                "mariana lima",
                "rodrigo santos",
                "thiago rocha",
                "camila souza",
                "juliana martins",
                "rafael alves",
            )
        ):
            name_not_headline = False

    # Check 2: Employer
    has_employer = any(
        f.fact_type == "experience" and isinstance(f.value, dict) and bool(f.value.get("employer"))
        for f in parsed.facts
    )

    # Check 3: Education
    has_education = any(
        f.fact_type == "education"
        and isinstance(f.value, dict)
        and bool(f.value.get("institution"))
        for f in parsed.facts
    )

    # Check 4: Languages (only natural spoken languages)
    languages = [
        str(f.value.get("language", "")).lower()
        for f in parsed.facts
        if f.fact_type == "language" and isinstance(f.value, dict)
    ]
    has_natural_languages = len(languages) >= 1
    no_skill_in_language = not any(
        any(k in lang for k in ("stakeholder", "python", "docker", "sql", "scrum"))
        for lang in languages
    )
    languages_valid = has_natural_languages and no_skill_in_language

    # Check 5: Skills
    skills = {
        str(f.value.get("name", "")).lower()
        for f in parsed.facts
        if f.fact_type == "skill" and isinstance(f.value, dict)
    }
    skills_passed = len(skills) >= 1

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
        _emit("ALL_LAYOUT_FIXTURES_PASS=NOT_RUN")
        _emit("FAILED_FIXTURES=NOT_RUN")
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
    for name, code in LAYOUT_FIXTURES:
        try:
            res = await evaluate_single_fixture(parser, name, code)
            results.append(res)
            score_pct = int(res["score"] * 100)
            status_str = "PASS" if res["score"] >= ACCURACY_THRESHOLD else "FAIL"
            passed = res["passed_count"]
            total = res["total_checks"]
            _emit(f"[{status_str}] {name:35} Score: {score_pct:3}% ({passed}/{total} checks)")
        except Exception as exc:
            _emit(f"[ERROR] {name:35} Failed with exception: {exc.__class__.__name__}")
            results.append({"variant": name, "score": 0.0, "passed_count": 0, "total_checks": 5})

    failed_fixtures = [r["variant"] for r in results if r.get("score", 0.0) < ACCURACY_THRESHOLD]
    all_pass = len(failed_fixtures) == 0 and len(results) == len(LAYOUT_FIXTURES)
    total_score = sum(r["score"] for r in results) / len(results) if results else 0.0
    overall_pct = int(total_score * 100)

    _emit("--------------------------------------------------------------------------------")
    _emit(f"ALL_LAYOUT_FIXTURES_PASS={'YES' if all_pass else 'NO'}")
    _emit(f"FAILED_FIXTURES={','.join(failed_fixtures) if failed_fixtures else 'NONE'}")
    _emit(f"Average Accuracy: {overall_pct}% (Threshold: {int(ACCURACY_THRESHOLD * 100)}%)")

    if all_pass:
        _emit("REAL_PROVIDER_DOCUMENT_NATIVE_EVAL=PASS")
        return 0
    else:
        _emit("REAL_PROVIDER_DOCUMENT_NATIVE_EVAL=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
