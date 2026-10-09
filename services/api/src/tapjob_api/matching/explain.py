"""Grounded explainability generator for WP2.2 Matching Engine (ADR-0014 Decision 3 & Decision 6).

Guarantees:
- Every strength points to explicit ProfileFact.id(s)
- Deterministic template output (zero dependency on LLM)
- Provider-neutral outputs consumable across channels (API, WhatsApp, Mobile)
"""

from __future__ import annotations

import uuid

from tapjob_api.jobs.models import Job
from tapjob_api.matching.types import (
    CandidateMatchSignals,
    DerivedJobMatchSignals,
    HardFilterId,
    HardFilterOutcome,
    HardFilterResult,
    MatchBlocker,
    MatchEligibility,
    MatchExplanation,
    MatchRequirementGap,
    MatchStrength,
)


def generate_grounded_explanation(
    candidate: CandidateMatchSignals,
    job_signals: DerivedJobMatchSignals,
    job: Job,
    hard_filter_results: tuple[HardFilterResult, ...],
    overall_score: int,
    eligibility: MatchEligibility,
) -> tuple[
    tuple[MatchStrength, ...],
    tuple[MatchBlocker, ...],
    tuple[MatchRequirementGap, ...],
    tuple[uuid.UUID, ...],
    MatchExplanation,
]:
    """Generates grounded strengths, blockers, missing requirements,
    and deterministic template explanation.
    """
    strengths: list[MatchStrength] = []
    blockers: list[MatchBlocker] = []
    gaps: list[MatchRequirementGap] = []
    grounded_fact_ids: set[uuid.UUID] = set()

    # 1. Strengths from matched skills
    candidate_skill_by_name = {s.canonical_skill: s for s in candidate.skills}
    job_all_skills = (
        set(job_signals.required_skills)
        | set(job_signals.preferred_skills)
        | set(job_signals.unclassified_skills)
    )

    matched_skills = sorted(candidate.skill_names & job_all_skills)
    for skill_name in matched_skills:
        skill_sig = candidate_skill_by_name.get(skill_name)
        fids = skill_sig.fact_ids if skill_sig else ()
        strengths.append(
            MatchStrength(
                name=skill_name,
                description=f"Habilidade confirmada no perfil: {skill_name}",
                fact_ids=fids,
            )
        )
        grounded_fact_ids.update(fids)

    # Strengths from matched domain tokens (intersected with job domain tokens)
    candidate_domain_by_name = {d.canonical_token: d for d in candidate.domain_tokens}
    job_domain_tokens = set(job_signals.domain_tokens)
    matched_domains = sorted(candidate.domain_token_names & job_domain_tokens)
    for dom_name in matched_domains[:5]:
        dom_sig = candidate_domain_by_name.get(dom_name)
        fids = dom_sig.fact_ids if dom_sig else ()
        strengths.append(
            MatchStrength(
                name=dom_name,
                description=f"Experiência prática demonstrada em {dom_name}",
                fact_ids=fids,
            )
        )
        grounded_fact_ids.update(fids)

    # 2. Blockers and grounded facts from hard filter evaluations
    for res in hard_filter_results:
        if res.source_facts:
            grounded_fact_ids.update(res.source_facts)
        if res.outcome == HardFilterOutcome.HARD_BLOCKER:
            blockers.append(
                MatchBlocker(
                    code=res.filter_id.value,
                    message=res.reason,
                    filter_id=res.filter_id,
                )
            )

    # 3. Missing requirements (exclusively R \ S_C for skills)
    missing_required_skills = sorted(set(job_signals.required_skills) - candidate.skill_names)
    for skill_name in missing_required_skills:
        gaps.append(
            MatchRequirementGap(
                name=skill_name,
                description=f"Requisito obrigatório não comprovado no perfil: {skill_name}",
                is_mandatory=True,
            )
        )

    # Salary deficit (soft mismatch informational gap)
    for res in hard_filter_results:
        if (
            res.filter_id == HardFilterId.SALARY_MINIMUM
            and res.outcome == HardFilterOutcome.SOFT_MISMATCH
        ):
            gaps.append(
                MatchRequirementGap(
                    name="Pretensão Salarial",
                    description=res.reason,
                    is_mandatory=False,
                )
            )

    # 4. Deterministic template explanation
    elig_title = eligibility.value.replace("_", " ").title()
    headline = f"Match de {overall_score}% para {job.title} — {elig_title}"

    if strengths:
        strengths_summary = (
            f"Pontos fortes identificados: {', '.join(s.name for s in strengths[:5])}."
        )
    else:
        strengths_summary = "Nenhum ponto forte direto comprovado para os requisitos desta vaga."

    if blockers:
        gaps_summary = f"Impedimentos identificados: {'; '.join(b.message for b in blockers)}."
    elif gaps:
        gaps_summary = f"Requisitos a desenvolver/confirmar: {', '.join(g.name for g in gaps[:5])}."
    else:
        gaps_summary = "Nenhum gap crítico ou requisito ausente identificado."

    if eligibility == MatchEligibility.ELIGIBLE:
        verdict = f"Candidato elegível com compatibilidade calculada de {overall_score}%."
    elif eligibility == MatchEligibility.REVIEW_REQUIRED:
        verdict = (
            f"Compatibilidade de {overall_score}%, porém há ambiguidades "
            "que requerem revisão manual."
        )
    else:
        if blockers:
            verdict = "Candidato inelegível devido a critério eliminatório (hard blocker)."
        else:
            verdict = f"Compatibilidade insuficiente ({overall_score}% < 50%)."

    explanation = MatchExplanation(
        summary_headline=headline,
        strengths_summary=strengths_summary,
        gaps_summary=gaps_summary,
        verdict_summary=verdict,
    )

    return (
        tuple(strengths),
        tuple(blockers),
        tuple(gaps),
        tuple(sorted(grounded_fact_ids)),
        explanation,
    )
