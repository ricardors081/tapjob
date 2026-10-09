"""Tests for Derived Job Match Signals (ADR-0014 Decision 5).

Verifies:
- Deterministic required, preferred, and unclassified skills extraction
- Heading-less job descriptions default tech tokens to unclassified_skills (never required_skills)
- Closed taxonomy whitelist enforcement (TECH_TAXONOMY_V1, max 50 skills)
- Seniority keyword extraction into JobSeniorityLevel
- Salary parsing and SalaryPeriod enum mapping (BRL, USD, EUR)
- Isolated '$' without currency qualifier fails closed to None
- Work authorization extraction:
  - "must be legally authorized" sets requires_existing_authorization=True
  - "no visa sponsorship" sets sponsorship_offered=False
  - "visa sponsorship available" sets sponsorship_offered=True
"""

import uuid

from tapjob_api.career_profile.fact_values import SalaryPeriod
from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.signals import derive_job_match_signals
from tapjob_api.matching.types import MATCHING_POLICY_VERSION, JobSeniorityLevel


def _make_job(description_cleaned: str, title: str = "Software Engineer") -> Job:
    job = Job()
    job.id = uuid.uuid4()
    job.title = title
    job.title_normalized = title.lower()
    job.location_normalized = "sao paulo, sp"
    job.workplace_type = JobWorkplaceType.REMOTE
    job.description_cleaned = description_cleaned
    return job


def test_skills_heading_extraction_and_unclassified() -> None:
    text = """
    Sobre a vaga:
    Buscamos pessoas engenheiras para nosso time. Usamos python e docker no dia a dia.

    Requisitos:
    - Conhecimento solido em python
    - Experiencia com fastapi e postgresql

    Diferenciais:
    - Experiencia com kubernetes e aws
    """
    job = _make_job(text)
    signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)

    assert "python" in signals.required_skills
    assert "fastapi" in signals.required_skills
    assert "postgresql" in signals.required_skills

    assert "kubernetes" in signals.preferred_skills
    assert "aws" in signals.preferred_skills

    # docker was outside headings, so it should be unclassified
    assert "docker" in signals.unclassified_skills
    # Required skills are never invented from unclassified
    assert "docker" not in signals.required_skills


def test_headingless_job_description_defaults_to_unclassified() -> None:
    text = "Estamos contratando desenvolvedor com experiência em python, react e postgresql."
    job = _make_job(text)
    signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)

    assert len(signals.required_skills) == 0
    assert len(signals.preferred_skills) == 0
    assert "python" in signals.unclassified_skills
    assert "react" in signals.unclassified_skills
    assert "postgresql" in signals.unclassified_skills


def test_closed_taxonomy_whitelist() -> None:
    text = "Requisitos: python, banana, abacaxi, microservices, kubernetes"
    job = _make_job(text)
    signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)

    assert "python" in signals.required_skills
    assert "kubernetes" in signals.required_skills
    # Arbitrary nouns not in closed taxonomy
    assert "banana" not in signals.required_skills
    assert "abacaxi" not in signals.required_skills


def test_seniority_extraction() -> None:
    cases = [
        ("Vaga de Estágio em Desenvolvimento", JobSeniorityLevel.INTERN),
        ("Desenvolvedor Junior Python", JobSeniorityLevel.JUNIOR),
        ("Engenheiro de Software Pleno", JobSeniorityLevel.MID),
        ("Senior Backend Engineer", JobSeniorityLevel.SENIOR),
        ("Tech Lead / Líder Técnico", JobSeniorityLevel.LEAD),
        ("Principal Architect / Staff Engineer", JobSeniorityLevel.STAFF_PRINCIPAL),
        ("Pessoa Desenvolvedora", None),
    ]

    for title, expected in cases:
        job = _make_job("Atividades normais de desenvolvimento", title=title)
        signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
        assert signals.seniority_signal == expected, f"Failed for {title}"


def test_salary_parsing_and_period_mapping() -> None:
    cases = [
        (
            "Faixa salarial: R$ 8.000 a R$ 12.000 por mês",
            "BRL",
            800000,
            1200000,
            SalaryPeriod.MONTHLY,
        ),
        ("Salário: R$ 5.000 / mês", "BRL", 500000, 500000, SalaryPeriod.MONTHLY),
        (
            "Salary: USD 120,000 - 150,000 per year",
            "USD",
            12000000,
            15000000,
            SalaryPeriod.ANNUAL,
        ),
        (
            "Compensation: € 60.000 - € 80.000 / ano",
            "EUR",
            6000000,
            8000000,
            SalaryPeriod.ANNUAL,
        ),
        ("Hourly rate: USD 50 - 75 per hour", "USD", 5000, 7500, SalaryPeriod.HOURLY),
    ]

    for text, currency, min_minor, max_minor, period in cases:
        job = _make_job(text)
        signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
        assert signals.salary_requirement is not None, f"Failed parsing: {text}"
        assert signals.salary_requirement.currency == currency
        assert signals.salary_requirement.min_minor == min_minor
        assert signals.salary_requirement.max_minor == max_minor
        assert signals.salary_requirement.period == period


def test_isolated_dollar_fails_closed_to_none() -> None:
    text = "Compensation: $ 5,000 - $ 8,000 per month"
    job = _make_job(text)
    signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)

    # Isolated $ without currency code fails closed
    assert signals.salary_requirement is None


def test_work_authorization_extraction() -> None:
    # 1. Authorization required, sponsorship unstated -> sponsorship_offered = None
    text1 = "Candidates must be legally authorized to work in Brazil."
    job1 = _make_job(text1)
    sig1 = derive_job_match_signals(job1, MATCHING_POLICY_VERSION)
    assert sig1.work_authorization_requirement is not None
    assert sig1.work_authorization_requirement.requires_existing_authorization is True
    assert sig1.work_authorization_requirement.sponsorship_offered is None
    assert sig1.work_authorization_requirement.jurisdiction == "BR"

    # 2. Authorization required, explicit negative sponsorship -> sponsorship_offered = False
    text2 = "Must be legally authorized to work. We do not sponsor visas / no visa sponsorship."
    job2 = _make_job(text2)
    sig2 = derive_job_match_signals(job2, MATCHING_POLICY_VERSION)
    assert sig2.work_authorization_requirement is not None
    assert sig2.work_authorization_requirement.requires_existing_authorization is True
    assert sig2.work_authorization_requirement.sponsorship_offered is False

    # 3. Explicit positive sponsorship -> sponsorship_offered = True
    text3 = "Visa sponsorship available for international candidates."
    job3 = _make_job(text3)
    sig3 = derive_job_match_signals(job3, MATCHING_POLICY_VERSION)
    assert sig3.work_authorization_requirement is not None
    assert sig3.work_authorization_requirement.sponsorship_offered is True

    # 4. No work auth text -> None
    text4 = "Ótimo ambiente de trabalho e benefícios flexíveis."
    job4 = _make_job(text4)
    sig4 = derive_job_match_signals(job4, MATCHING_POLICY_VERSION)
    assert sig4.work_authorization_requirement is None


def test_work_auth_jurisdiction_bounded_inference_finding_2() -> None:
    # Must not match 'us' inside 'must', 'business', 'customer', etc.
    job_generic = _make_job("Must be legally authorized to work.")
    sig_generic = derive_job_match_signals(job_generic, MATCHING_POLICY_VERSION)
    assert sig_generic.work_authorization_requirement is not None
    assert sig_generic.work_authorization_requirement.jurisdiction is None

    job_business = _make_job("Business analyst. Must be legally authorized to work.")
    sig_business = derive_job_match_signals(job_business, MATCHING_POLICY_VERSION)
    assert sig_business.work_authorization_requirement is not None
    assert sig_business.work_authorization_requirement.jurisdiction is None

    job_customer = _make_job("Customer success role. Must be legally authorized to work.")
    sig_customer = derive_job_match_signals(job_customer, MATCHING_POLICY_VERSION)
    assert sig_customer.work_authorization_requirement is not None
    assert sig_customer.work_authorization_requirement.jurisdiction is None

    job_us = _make_job("Must be legally authorized to work in the US.")
    sig_us = derive_job_match_signals(job_us, MATCHING_POLICY_VERSION)
    assert sig_us.work_authorization_requirement is not None
    assert sig_us.work_authorization_requirement.jurisdiction == "US"

    job_usa = _make_job("Must be legally authorized to work in the United States.")
    sig_usa = derive_job_match_signals(job_usa, MATCHING_POLICY_VERSION)
    assert sig_usa.work_authorization_requirement is not None
    assert sig_usa.work_authorization_requirement.jurisdiction == "US"

    job_br = _make_job("Candidatos devem possuir autorização para trabalhar no Brasil.")
    sig_br = derive_job_match_signals(job_br, MATCHING_POLICY_VERSION)
    assert sig_br.work_authorization_requirement is not None
    assert sig_br.work_authorization_requirement.jurisdiction == "BR"


def test_money_parsing_exactness_finding_3() -> None:
    # Test minor units exactness without floating point issues
    cases = [
        ("Salário: R$ 0,29 por mês", "BRL", 29, 29),
        ("Salary: USD 0.29 per hour", "USD", 29, 29),
        ("Salário: R$ 8.000,29 por mês", "BRL", 800029, 800029),
        ("Salary: USD 8,000.29 per month", "USD", 800029, 800029),
        ("Faixa: R$ 8.000,29 a R$ 12.500,50 por mês", "BRL", 800029, 1250050),
        ("Range: USD 8,000.29 - 10,000.00 / mo", "USD", 800029, 1000000),
    ]
    for text, curr, exp_min, exp_max in cases:
        job = _make_job(text)
        sig = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
        assert sig.salary_requirement is not None, f"Failed for {text}"
        assert sig.salary_requirement.currency == curr
        assert sig.salary_requirement.min_minor == exp_min
        assert sig.salary_requirement.max_minor == exp_max


def test_zero_float_ast_inspection_finding_3() -> None:
    import ast
    from pathlib import Path

    base_path = Path(__file__).parent.parent / "src" / "tapjob_api" / "matching"
    for filename in ["signals.py", "scorer.py"]:
        file_path = base_path / filename
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "float":
                msg = f"Prohibited use of 'float' found in {filename} at line {node.lineno}"
                raise AssertionError(msg)
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                msg = (
                    f"Prohibited float literal {node.value} "
                    f"found in {filename} at line {node.lineno}"
                )
                raise AssertionError(msg)


def test_jurisdiction_extraction_legal_clause_finding_3() -> None:
    # 1. "Brazil team. Must be legally authorized to work in the US." => US
    job1 = _make_job("Brazil team. Must be legally authorized to work in the US.")
    sig1 = derive_job_match_signals(job1, MATCHING_POLICY_VERSION)
    assert sig1.work_authorization_requirement is not None
    assert sig1.work_authorization_requirement.jurisdiction == "US"

    # 2. "US company hiring Brazil team. Must be legally authorized to work." => None
    job2 = _make_job("US company hiring Brazil team. Must be legally authorized to work.")
    sig2 = derive_job_match_signals(job2, MATCHING_POLICY_VERSION)
    assert sig2.work_authorization_requirement is not None
    assert sig2.work_authorization_requirement.jurisdiction is None

    # 3. "Contrato PJ. Must be legally authorized to work." => None
    job3 = _make_job("Contrato PJ. Must be legally authorized to work.")
    sig3 = derive_job_match_signals(job3, MATCHING_POLICY_VERSION)
    assert sig3.work_authorization_requirement is not None
    assert sig3.work_authorization_requirement.jurisdiction is None

    # 4. "Regime CLT. Must be legally authorized to work." => None
    job4 = _make_job("Regime CLT. Must be legally authorized to work.")
    sig4 = derive_job_match_signals(job4, MATCHING_POLICY_VERSION)
    assert sig4.work_authorization_requirement is not None
    assert sig4.work_authorization_requirement.jurisdiction is None

    # 5. "Authorized to work in Brazil or the United States" => None / ambiguous
    job5 = _make_job("Authorized to work in Brazil or the United States.")
    sig5 = derive_job_match_signals(job5, MATCHING_POLICY_VERSION)
    assert sig5.work_authorization_requirement is not None
    assert sig5.work_authorization_requirement.jurisdiction is None


def test_jurisdiction_directly_bound_to_legal_requirement() -> None:
    # 1. "Candidates must be legally authorized to work for our Brazil team."
    #    "We do not sponsor visas." => None
    job1 = _make_job(
        "Candidates must be legally authorized to work for our Brazil team.\n"
        "We do not sponsor visas."
    )
    sig1 = derive_job_match_signals(job1, MATCHING_POLICY_VERSION)
    assert sig1.work_authorization_requirement is not None
    assert sig1.work_authorization_requirement.jurisdiction is None
    assert sig1.work_authorization_requirement.sponsorship_offered is False

    # 2. "Must be legally authorized to work in our Brazil office."
    #    "We do not sponsor visas." => None
    job2 = _make_job(
        "Must be legally authorized to work in our Brazil office.\nWe do not sponsor visas."
    )
    sig2 = derive_job_match_signals(job2, MATCHING_POLICY_VERSION)
    assert sig2.work_authorization_requirement is not None
    assert sig2.work_authorization_requirement.jurisdiction is None
    assert sig2.work_authorization_requirement.sponsorship_offered is False

    # 3. "Must be legally authorized to work for a US customer."
    #    "We do not sponsor visas." => None
    job3 = _make_job(
        "Must be legally authorized to work for a US customer.\nWe do not sponsor visas."
    )
    sig3 = derive_job_match_signals(job3, MATCHING_POLICY_VERSION)
    assert sig3.work_authorization_requirement is not None
    assert sig3.work_authorization_requirement.jurisdiction is None
    assert sig3.work_authorization_requirement.sponsorship_offered is False

    # 4. "Must be legally authorized to work in Brazil."
    #    "We do not sponsor visas." => BR
    job4 = _make_job("Must be legally authorized to work in Brazil.\nWe do not sponsor visas.")
    sig4 = derive_job_match_signals(job4, MATCHING_POLICY_VERSION)
    assert sig4.work_authorization_requirement is not None
    assert sig4.work_authorization_requirement.jurisdiction == "BR"
    assert sig4.work_authorization_requirement.sponsorship_offered is False

    # 5. "Must be legally authorized to work in the US."
    #    "We do not sponsor visas." => US
    job5 = _make_job("Must be legally authorized to work in the US.\nWe do not sponsor visas.")
    sig5 = derive_job_match_signals(job5, MATCHING_POLICY_VERSION)
    assert sig5.work_authorization_requirement is not None
    assert sig5.work_authorization_requirement.jurisdiction == "US"
    assert sig5.work_authorization_requirement.sponsorship_offered is False

    # 6. "Autorização para trabalhar no Brasil." => BR
    job6 = _make_job("Autorização para trabalhar no Brasil.")
    sig6 = derive_job_match_signals(job6, MATCHING_POLICY_VERSION)
    assert sig6.work_authorization_requirement is not None
    assert sig6.work_authorization_requirement.jurisdiction == "BR"

    # 7. "Must be legally authorized to work within our US organization." => None
    job7 = _make_job("Must be legally authorized to work within our US organization.")
    sig7 = derive_job_match_signals(job7, MATCHING_POLICY_VERSION)
    assert sig7.work_authorization_requirement is not None
    assert sig7.work_authorization_requirement.jurisdiction is None

    # 8. Corporate team / company mentions prior to legal clause
    job8a = _make_job("Brazil team. Must be legally authorized to work in the US.")
    sig8a = derive_job_match_signals(job8a, MATCHING_POLICY_VERSION)
    assert sig8a.work_authorization_requirement is not None
    assert sig8a.work_authorization_requirement.jurisdiction == "US"

    job8b = _make_job("US company. Must be legally authorized to work in Brazil.")
    sig8b = derive_job_match_signals(job8b, MATCHING_POLICY_VERSION)
    assert sig8b.work_authorization_requirement is not None
    assert sig8b.work_authorization_requirement.jurisdiction == "BR"

    # 9. Coordinated multi-jurisdiction fail closed
    job9 = _make_job("Authorized to work in Brazil or the United States.")
    sig9 = derive_job_match_signals(job9, MATCHING_POLICY_VERSION)
    assert sig9.work_authorization_requirement is not None
    assert sig9.work_authorization_requirement.jurisdiction is None


def test_multi_currency_and_conflicting_periods_fail_closed_finding_4() -> None:
    # 1. "BRL 10.000 / month or USD 2,000 / month" => salary_requirement=None
    job1 = _make_job("BRL 10.000 / month or USD 2,000 / month")
    sig1 = derive_job_match_signals(job1, MATCHING_POLICY_VERSION)
    assert sig1.salary_requirement is None

    # 2. "USD 100,000 annually. BRL 500,000 annually." => None
    job2 = _make_job("USD 100,000 annually. BRL 500,000 annually.")
    sig2 = derive_job_match_signals(job2, MATCHING_POLICY_VERSION)
    assert sig2.salary_requirement is None

    # 3. Conflicting periods => period=None
    job3 = _make_job("Salary: USD 100,000 / month or annually")
    sig3 = derive_job_match_signals(job3, MATCHING_POLICY_VERSION)
    assert sig3.salary_requirement is not None
    assert sig3.salary_requirement.currency == "USD"
    assert sig3.salary_requirement.period is None

    # 4. Single BRL range works
    job_brl = _make_job("Salário: R$ 10.000 a R$ 15.000 por mês")
    sig_brl = derive_job_match_signals(job_brl, MATCHING_POLICY_VERSION)
    assert sig_brl.salary_requirement is not None
    assert sig_brl.salary_requirement.currency == "BRL"
    assert sig_brl.salary_requirement.period == SalaryPeriod.MONTHLY

    # 5. Single USD range works
    job_usd = _make_job("Salary: USD 100,000 - USD 120,000 annually")
    sig_usd = derive_job_match_signals(job_usd, MATCHING_POLICY_VERSION)
    assert sig_usd.salary_requirement is not None
    assert sig_usd.salary_requirement.currency == "USD"
    assert sig_usd.salary_requirement.period == SalaryPeriod.ANNUAL

    # 6. Single EUR range works
    job_eur = _make_job("Salary: EUR 5.000 - EUR 7.000 per month")
    sig_eur = derive_job_match_signals(job_eur, MATCHING_POLICY_VERSION)
    assert sig_eur.salary_requirement is not None
    assert sig_eur.salary_requirement.currency == "EUR"
    assert sig_eur.salary_requirement.period == SalaryPeriod.MONTHLY


def test_location_preference_without_workplace_type_fact_ids_finding_5() -> None:
    from datetime import UTC, datetime

    from tapjob_api.career_profile.models import (
        FactSourceKind,
        FactStatus,
        FactType,
        ProfileFact,
    )
    from tapjob_api.career_profile.service import TruthSnapshot
    from tapjob_api.matching.signals import derive_candidate_match_signals

    now = datetime.now(UTC)
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    fact_loc_only = uuid.uuid4()
    fact_wp = uuid.uuid4()

    facts = [
        # Pure location preference with NO workplace types
        ProfileFact(
            id=fact_loc_only,
            user_id=user_id,
            career_profile_id=profile_id,
            fact_type=FactType.PREFERENCE,
            value_json={
                "kind": "preference",
                "preference": {
                    "preferenceKind": "location",
                    "country": "BR",
                    "city": "Sao Paulo",
                    "workplaceTypes": [],
                },
            },
            source_kind=FactSourceKind.USER_DIRECT,
            user_confirmed=True,
            confirmed_at=now,
            status=FactStatus.ACTIVE,
            lineage_id=fact_loc_only,
            active_from_profile_version=1,
        ),
        # Workplace preference with REMOTE
        ProfileFact(
            id=fact_wp,
            user_id=user_id,
            career_profile_id=profile_id,
            fact_type=FactType.PREFERENCE,
            value_json={
                "kind": "preference",
                "preference": {
                    "preferenceKind": "workplace_type",
                    "workplaceTypes": ["remote"],
                },
            },
            source_kind=FactSourceKind.USER_DIRECT,
            user_confirmed=True,
            confirmed_at=now,
            status=FactStatus.ACTIVE,
            lineage_id=fact_wp,
            active_from_profile_version=1,
        ),
    ]

    snapshot = TruthSnapshot(
        user_id=user_id,
        profile_version=1,
        headline="Dev",
        summary=None,
        facts=facts,
        target_roles=["Dev"],
    )

    candidate_signals = derive_candidate_match_signals(
        snapshot, MATCHING_POLICY_VERSION, now.date()
    )
    # Pure location preference must NOT be added to workplace_preference_fact_ids
    assert fact_loc_only not in candidate_signals.workplace_preference_fact_ids
    # Workplace preference fact C MUST be in workplace_preference_fact_ids
    assert fact_wp in candidate_signals.workplace_preference_fact_ids
