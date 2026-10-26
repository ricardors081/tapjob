import uuid
from typing import Any

from tapjob_api.career_profile.models import FactStatus, FactType, ProfileFact
from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.messaging.profile_formatter import format_profile_query_summary


def _make_fact(fact_type: FactType, value_json: dict[str, Any]) -> ProfileFact:
    fid = uuid.uuid4()
    fact = ProfileFact(
        id=fid,
        user_id=uuid.uuid4(),
        career_profile_id=uuid.uuid4(),
        fact_type=fact_type,
        value_json=value_json,
        source_kind="user_direct",
        confidence=1.0,
        user_confirmed=True,
        status=FactStatus.ACTIVE,
        lineage_id=fid,
        active_from_profile_version=1,
    )
    return fact


def test_format_empty_profile() -> None:
    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=0,
        headline=None,
        summary=None,
        target_roles=[],
        facts=[],
    )
    rendered = format_profile_query_summary(snapshot)
    assert "👤 *Seu Perfil Profissional*" in rendered
    assert "Ainda não há competências ou experiências confirmadas" in rendered
    assert "💡 *Para completar seu perfil, ainda faltam:*" in rendered
    assert "título profissional" in rendered
    assert "resumo profissional" in rendered


def test_format_full_profile_no_raw_enum_leaks() -> None:
    facts = [
        _make_fact(
            FactType.EXPERIENCE,
            {
                "kind": "experience",
                "employer": "TechCorp",
                "title": "Senior Data Engineer",
                "isCurrent": True,
            },
        ),
        _make_fact(
            FactType.EDUCATION,
            {
                "kind": "education",
                "institution": "Universidade Ceuma",
                "degree": "Engenharia",
                "fieldOfStudy": "Computação",
                "status": "concluído",
            },
        ),
        _make_fact(FactType.SKILL, {"kind": "skill", "name": "Python"}),
        _make_fact(FactType.SKILL, {"kind": "skill", "name": "SQL"}),
        _make_fact(
            FactType.LANGUAGE,
            {"kind": "language", "language": "Português", "proficiency": "nativo"},
        ),
        _make_fact(
            FactType.LANGUAGE,
            {"kind": "language", "language": "Inglês", "proficiency": "avançado"},
        ),
        _make_fact(
            FactType.CERTIFICATION,
            {
                "kind": "certification",
                "name": "AWS Certified Solutions Architect",
                "issuer": "Amazon Web Services",
            },
        ),
        _make_fact(
            FactType.PUBLICATION,
            {
                "kind": "publication",
                "title": "Machine Learning in Agritech",
                "publisher": "IEEE",
                "date": "2024",
            },
        ),
    ]

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline="Senior Data & AI Professional",
        summary="Specialist in data engineering and applied AI systems.",
        target_roles=["Data Engineer", "AI Engineer"],
        facts=facts,
    )

    rendered = format_profile_query_summary(snapshot)

    # Required WhatsApp sections
    assert "👤 *Seu Perfil Profissional*" in rendered
    assert "*Título profissional*\nSenior Data & AI Professional" in rendered
    assert (
        "*Resumo profissional*\nSpecialist in data engineering and applied AI systems." in rendered
    )
    assert "*Cargos de interesse*\nData Engineer, AI Engineer" in rendered

    assert "💼 *Experiência*" in rendered
    assert "- Senior Data Engineer — TechCorp (Atual)" in rendered

    assert "🎓 *Formação*" in rendered
    assert "- Engenharia em Computação — Universidade Ceuma — Concluído" in rendered

    assert "🛠️ *Competências*" in rendered
    assert "- Python" in rendered
    assert "- SQL" in rendered

    assert "🌎 *Idiomas*" in rendered
    assert "- Português — Nativo" in rendered
    assert "- Inglês — Avançado" in rendered

    assert "📚 *Certificações / Cursos*" in rendered
    assert "- AWS Certified Solutions Architect — Amazon Web Services" in rendered

    assert "📝 *Publicações*" in rendered
    assert "- Machine Learning in Agritech — IEEE (2024)" in rendered

    # Strict assertion: NO raw enum leak
    for line in rendered.splitlines():
        trimmed = line.strip()
        assert trimmed != "- language", f"Found raw enum leak: {line}"
        assert trimmed != "- education", f"Found raw enum leak: {line}"
        assert trimmed != "- experience", f"Found raw enum leak: {line}"
        assert trimmed != "- project", f"Found raw enum leak: {line}"
        assert trimmed != "- skill", f"Found raw enum leak: {line}"
        assert trimmed != "- publication", f"Found raw enum leak: {line}"


def test_education_status_ptbr_localization() -> None:
    """Verifies canonical education statuses are mapped to Portuguese:
    - completed -> Concluído
    - in_progress -> Em andamento
    - incomplete -> Incompleto
    """
    facts = [
        _make_fact(
            FactType.EDUCATION,
            {
                "kind": "education",
                "institution": "Universidade de São Paulo",
                "degree": "Bacharelado",
                "fieldOfStudy": "Ciência da Computação",
                "status": "completed",
            },
        ),
        _make_fact(
            FactType.EDUCATION,
            {
                "kind": "education",
                "institution": "Unicamp",
                "degree": "Mestrado",
                "fieldOfStudy": "Engenharia de Software",
                "status": "in_progress",
            },
        ),
        _make_fact(
            FactType.EDUCATION,
            {
                "kind": "education",
                "institution": "UFMG",
                "degree": "Especialização",
                "fieldOfStudy": "Inteligência Artificial",
                "status": "incomplete",
            },
        ),
    ]

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline="Engenheiro de Software",
        summary="Desenvolvedor com foco em IA.",
        target_roles=["Engenheiro de Software"],
        facts=facts,
    )

    rendered = format_profile_query_summary(snapshot)

    assert (
        "- Bacharelado em Ciência da Computação — Universidade de São Paulo — Concluído" in rendered
    )
    assert "- Mestrado em Engenharia de Software — Unicamp — Em andamento" in rendered
    assert "- Especialização em Inteligência Artificial — UFMG — Incompleto" in rendered

    # Strict negative check: raw technical enums MUST NOT appear
    assert "completed" not in rendered.lower() or "concluído" in rendered.lower()
    assert "in_progress" not in rendered
    assert "In_progress" not in rendered
    assert "Completed" not in rendered


def test_education_status_unknown_fail_safe_no_leak() -> None:
    """Verifies unknown/unrecognized status values fail-safe without leaking technical strings."""
    facts = [
        _make_fact(
            FactType.EDUCATION,
            {
                "kind": "education",
                "institution": "MIT",
                "degree": "Graduação",
                "fieldOfStudy": "Robótica",
                "status": "dropped_out_unknown_enum_value",
            },
        ),
    ]

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline="Pesquisador",
        summary=None,
        target_roles=[],
        facts=facts,
    )

    rendered = format_profile_query_summary(snapshot)

    assert "- Graduação em Robótica — MIT" in rendered
    assert "dropped_out_unknown_enum_value" not in rendered
    assert "Dropped_out_unknown_enum_value" not in rendered
