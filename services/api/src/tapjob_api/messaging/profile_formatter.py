"""Typed formatter for WhatsApp profile summary query.

Renders user profile truth store facts organized semantically by FactType.
Guarantees:
- Never outputs raw enum values ("language", "education", "experience", etc.)
- Groups confirmed facts into readable, clean WhatsApp native markdown sections
- Formats each fact type using its canonical fields
"""

from __future__ import annotations

from typing import Any

from tapjob_api.career_profile.models import FactType
from tapjob_api.career_profile.service import TruthSnapshot

_EDUCATION_STATUS_LABELS: dict[str, str] = {
    "completed": "Concluído",
    "concluído": "Concluído",
    "concluido": "Concluído",
    "in_progress": "Em andamento",
    "em_andamento": "Em andamento",
    "em andamento": "Em andamento",
    "incomplete": "Incompleto",
    "incompleto": "Incompleto",
}


def _format_experience_item(v: dict[str, Any]) -> str:
    title = v.get("title") or "Experiência Profissional"
    employer = v.get("employer")
    is_current = v.get("current", v.get("isCurrent", False))

    base = f"{title} — {employer}" if employer else title
    if is_current:
        base += " (Atual)"
    return f"- {base}"


def _format_education_item(v: dict[str, Any]) -> str:
    institution = (v.get("institution") or "").strip()
    degree = (v.get("degree") or "").strip()
    field = (v.get("fieldOfStudy") or v.get("field_of_study") or "").strip()

    title_part = ""
    if degree and field:
        title_part = f"{degree} em {field}"
    elif degree:
        title_part = degree
    elif field:
        title_part = field

    if title_part and institution and institution != "Instituição":
        desc = f"{title_part} — {institution}"
    elif title_part:
        desc = title_part
    elif institution and institution != "Instituição":
        desc = institution
    else:
        desc = "Formação Acadêmica"

    raw_status = (v.get("status") or "").strip().lower()
    if raw_status:
        status_label = _EDUCATION_STATUS_LABELS.get(raw_status)
        if status_label:
            desc += f" — {status_label}"

    start_year = v.get("start", {}).get("year") if isinstance(v.get("start"), dict) else None
    end_year = v.get("end", {}).get("year") if isinstance(v.get("end"), dict) else None
    if start_year and end_year:
        desc += f" ({start_year} - {end_year})"
    elif end_year:
        desc += f" ({end_year})"
    elif start_year:
        desc += f" ({start_year} - Presente)"

    return f"- {desc}"


def _format_skill_item(v: dict[str, Any]) -> str | None:
    name = v.get("name")
    return name.strip() if name and name.strip() else None


def _format_language_item(v: dict[str, Any]) -> str:
    lang = v.get("language") or "Idioma"
    prof = v.get("proficiency")
    if prof:
        return f"- {lang} — {prof.capitalize()}"
    return f"- {lang}"


def _format_certification_item(v: dict[str, Any]) -> str:
    name = v.get("name") or "Certificação"
    issuer = v.get("issuer")
    if issuer:
        return f"- {name} — {issuer}"
    return f"- {name}"


def _format_publication_item(v: dict[str, Any]) -> str:
    title = v.get("title") or "Publicação"
    publisher = v.get("publisher")
    date = v.get("date")
    out = title
    if publisher:
        out += f" — {publisher}"
    if date:
        out += f" ({date})"
    return f"- {out}"


def _format_project_item(v: dict[str, Any]) -> str:
    name = v.get("name") or "Projeto"
    role = v.get("role")
    if role:
        return f"- {name} — {role}"
    return f"- {name}"


def format_profile_query_summary(snapshot: TruthSnapshot) -> str:
    """Formats a TruthSnapshot for WhatsApp conversation, grouped by FactType."""
    blocks: list[str] = ["👤 *Seu Perfil Profissional*", ""]

    if snapshot.headline:
        blocks.append("*Título profissional*")
        blocks.append(snapshot.headline)
        blocks.append("")

    if snapshot.summary:
        blocks.append("*Resumo profissional*")
        blocks.append(snapshot.summary)
        blocks.append("")

    if snapshot.target_roles:
        roles_str = ", ".join(snapshot.target_roles)
        blocks.append("*Cargos de interesse*")
        blocks.append(roles_str)
        blocks.append("")

    # Group facts by canonical FactType
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fact in snapshot.facts:
        f_type = fact.fact_type.value if hasattr(fact.fact_type, "value") else str(fact.fact_type)
        v = fact.value_json if isinstance(fact.value_json, dict) else {}
        grouped.setdefault(f_type, []).append(v)

    # 💼 Experiência
    exp_list = grouped.get(FactType.EXPERIENCE.value, [])
    if exp_list:
        blocks.append("💼 *Experiência*")
        for item in exp_list[:6]:
            blocks.append(_format_experience_item(item))
        if len(exp_list) > 6:
            blocks.append(f"_... e mais {len(exp_list) - 6} experiências registradas_")
        blocks.append("")

    # 🎓 Formação
    edu_list = grouped.get(FactType.EDUCATION.value, [])
    if edu_list:
        blocks.append("🎓 *Formação*")
        for item in edu_list:
            blocks.append(_format_education_item(item))
        blocks.append("")

    # 🛠️ Competências
    skill_list = grouped.get(FactType.SKILL.value, [])
    if skill_list:
        blocks.append("🛠️ *Competências*")
        names = [name for item in skill_list if (name := _format_skill_item(item))]
        if names:
            max_skills = 15
            displayed = names[:max_skills]
            for s in displayed:
                blocks.append(f"- {s}")
            if len(names) > max_skills:
                blocks.append(f"_... e mais {len(names) - max_skills} competências_")
        blocks.append("")

    # 🌎 Idiomas
    lang_list = grouped.get(FactType.LANGUAGE.value, [])
    if lang_list:
        blocks.append("🌎 *Idiomas*")
        for item in lang_list:
            blocks.append(_format_language_item(item))
        blocks.append("")

    # 📚 Certificações / Cursos
    cert_list = grouped.get(FactType.CERTIFICATION.value, [])
    if cert_list:
        blocks.append("📚 *Certificações / Cursos*")
        for item in cert_list:
            blocks.append(_format_certification_item(item))
        blocks.append("")

    # 📝 Publicações
    pub_list = grouped.get(FactType.PUBLICATION.value, [])
    if pub_list:
        blocks.append("📝 *Publicações*")
        for item in pub_list:
            blocks.append(_format_publication_item(item))
        blocks.append("")

    # 🚀 Projetos
    proj_list = grouped.get(FactType.PROJECT.value, [])
    if proj_list:
        blocks.append("🚀 *Projetos*")
        for item in proj_list:
            blocks.append(_format_project_item(item))
        blocks.append("")

    if not snapshot.facts:
        blocks.append("_Ainda não há competências ou experiências confirmadas no seu perfil._")
        blocks.append("")

    # Tip for missing fields
    missing_items: list[str] = []
    if not snapshot.headline:
        missing_items.append("título profissional")
    if not snapshot.summary:
        missing_items.append("resumo profissional")
    if not snapshot.target_roles:
        missing_items.append("cargos de interesse")
    if not snapshot.facts:
        missing_items.append("experiências / competências")

    if missing_items:
        blocks.append(f"💡 *Para completar seu perfil, ainda faltam:* {', '.join(missing_items)}.")

    return "\n".join(blocks).strip()
