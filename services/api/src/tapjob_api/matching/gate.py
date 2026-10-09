"""Hard filter gates and capability matrix for WP2.2 (ADR-0014 Decision 4).

Implements the five canonical gates:
- WORK_AUTHORIZATION
- WORKPLACE_TYPE
- LOCATION
- SALARY_MINIMUM
- ROLE_ALIGNMENT

Hard blockers require explicit, structured, and comparable evidence on both sides.
"""

from __future__ import annotations

import re
import uuid

from tapjob_api.career_profile.fact_values import WorkplaceType
from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.types import (
    CandidateLocationPreference,
    CandidateMatchSignals,
    CandidateWorkAuthorizationSignal,
    CriticalUnknown,
    DerivedJobMatchSignals,
    HardFilterId,
    HardFilterOutcome,
    HardFilterResult,
    WorkAuthSemanticValue,
)

ROLE_ALIGNMENT_HARD_BLOCK_ENABLED: bool = False

WORKPLACE_TYPE_MAPPING: dict[WorkplaceType, JobWorkplaceType] = {
    WorkplaceType.REMOTE: JobWorkplaceType.REMOTE,
    WorkplaceType.HYBRID: JobWorkplaceType.HYBRID,
    WorkplaceType.ONSITE: JobWorkplaceType.ON_SITE,
}


_COUNTRY_ALIASES: dict[str, set[str]] = {
    "br": {"br", "brasil", "brazil"},
    "brasil": {"br", "brasil", "brazil"},
    "brazil": {"br", "brasil", "brazil"},
    "us": {"us", "usa", "united states", "eua"},
    "usa": {"us", "usa", "united states", "eua"},
    "united states": {"us", "usa", "united states", "eua"},
}


def _check_preference_match(
    pref: CandidateLocationPreference, job_tokens: set[str]
) -> tuple[bool, bool]:
    """Evaluates a single CandidateLocationPreference against job_tokens.

    Returns (is_match, is_comparable).
    - City-specific preference requires candidate city in job_tokens.
    - Region mismatch prevents match even if country matches.
    - Country match alone cannot satisfy city/region preference.
    """
    cand_city = pref.city.lower() if pref.city else None
    cand_region = pref.region.lower() if pref.region else None
    cand_country = pref.country.lower() if pref.country else None

    def has_country_conflict(expected_country: str) -> bool:
        expected_set = _COUNTRY_ALIASES.get(expected_country, {expected_country})
        for aliases in _COUNTRY_ALIASES.values():
            if not aliases.isdisjoint(job_tokens) and aliases.isdisjoint(expected_set):
                return True
        return False

    if cand_city:
        if cand_city not in job_tokens:
            if cand_country:
                expected_country_set = _COUNTRY_ALIASES.get(cand_country, {cand_country})
                if not expected_country_set.isdisjoint(job_tokens) and len(job_tokens) == 1:
                    # Job specifies only country: insufficient components for city preference
                    return False, False
            return False, True

        # City matches! Check for regional contradiction
        if cand_region and cand_region not in job_tokens:
            other_tokens = set(job_tokens) - {cand_city}
            if cand_country:
                other_tokens -= _COUNTRY_ALIASES.get(cand_country, {cand_country})
            if other_tokens:
                # Job has other specific tokens that contradict candidate region
                return False, True

        if cand_country and has_country_conflict(cand_country):
            return False, True

        return True, True

    if cand_region:
        if cand_region not in job_tokens:
            if cand_country:
                expected_country_set = _COUNTRY_ALIASES.get(cand_country, {cand_country})
                if not expected_country_set.isdisjoint(job_tokens) and len(job_tokens) == 1:
                    return False, False
            return False, True

        if cand_country and has_country_conflict(cand_country):
            return False, True

        return True, True

    if cand_country:
        expected_set = _COUNTRY_ALIASES.get(cand_country, {cand_country})
        if not expected_set.isdisjoint(job_tokens):
            return True, True
        if has_country_conflict(cand_country):
            return False, True
        return False, True

    return False, False


def _matches_location(
    job_location_normalized: str | None, candidate_signals: CandidateMatchSignals
) -> tuple[bool, bool, tuple[uuid.UUID, ...]]:
    """Matches job location tokens against candidate location preferences.

    Returns (is_match, is_comparable, evidence_facts).
    Preserves multiple CandidateLocationPreference as OR alternatives between complete preferences.
    """
    if not job_location_normalized or not candidate_signals.acceptable_locations:
        return False, False, ()

    delimiters = r"[,/\-]"
    job_tokens = {
        token.strip().lower()
        for token in re.split(delimiters, job_location_normalized)
        if token.strip()
    }
    if not job_tokens:
        return False, False, ()

    matched_facts: list[uuid.UUID] = []
    comparable_facts: list[uuid.UUID] = []
    is_any_match = False
    is_any_comparable = False

    for pref in candidate_signals.acceptable_locations:
        is_m, is_c = _check_preference_match(pref, job_tokens)
        if is_m:
            is_any_match = True
            matched_facts.extend(pref.fact_ids)
        elif is_c:
            is_any_comparable = True
            comparable_facts.extend(pref.fact_ids)

    if is_any_match:
        return True, True, tuple(sorted(set(matched_facts)))
    if is_any_comparable:
        return False, True, tuple(sorted(set(comparable_facts)))

    return False, False, ()


def evaluate_hard_filters(
    candidate: CandidateMatchSignals,
    job_signals: DerivedJobMatchSignals,
    job: Job,
    title_score: int,
) -> tuple[tuple[HardFilterResult, ...], tuple[CriticalUnknown, ...]]:
    """Evaluates the 5 canonical hard gates according to the Capability Matrix."""
    results: list[HardFilterResult] = []
    critical_unknowns: list[CriticalUnknown] = []

    # Check Sparse Profile rule: 0 skills AND 0 experiences in candidate profile
    if len(candidate.skills) == 0 and len(candidate.experiences) == 0:
        critical_unknowns.append(CriticalUnknown.SPARSE_PROFILE)

    # 1. WORK_AUTHORIZATION
    auth_req = job_signals.work_authorization_requirement
    if auth_req is None or not auth_req.requires_existing_authorization:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.WORK_AUTHORIZATION,
                outcome=HardFilterOutcome.PASSED,
                reason="Vaga não exige autorização de trabalho específica.",
                source_facts=(),
            )
        )
    elif not auth_req.jurisdiction:
        # Ambiguous jurisdiction: fail-closed to UNKNOWN
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.WORK_AUTHORIZATION,
                outcome=HardFilterOutcome.UNKNOWN,
                reason=(
                    "Vaga exige autorização de trabalho, mas a jurisdição aplicável "
                    "não foi informada."
                ),
                source_facts=(),
            )
        )
        critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
    else:
        req_jurisdiction = auth_req.jurisdiction.upper()
        # Find candidate work authorizations matching this exact jurisdiction
        matched_auths: list[tuple[str, WorkAuthSemanticValue, tuple[uuid.UUID, ...]]] = []
        for (jur, q_key), val in candidate.work_authorizations.items():
            if jur == req_jurisdiction:
                if isinstance(val, CandidateWorkAuthorizationSignal):
                    matched_auths.append((q_key, val.semantic_value, val.fact_ids))
                else:
                    matched_auths.append((q_key, val, ()))

        if not matched_auths:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.WORK_AUTHORIZATION,
                    outcome=HardFilterOutcome.UNKNOWN,
                    reason=(
                        "Candidato não possui registro de autorização de trabalho "
                        f"para a jurisdição {req_jurisdiction}."
                    ),
                    source_facts=(),
                )
            )
            critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
        else:
            has_not_authorized = [
                m for m in matched_auths if m[1] == WorkAuthSemanticValue.NOT_AUTHORIZED
            ]
            requires_sponsor = [
                m for m in matched_auths if m[1] == WorkAuthSemanticValue.REQUIRES_SPONSORSHIP
            ]
            has_authorized = [m for m in matched_auths if m[1] == WorkAuthSemanticValue.AUTHORIZED]
            does_not_require_sponsor = [
                m
                for m in matched_auths
                if m[1] == WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP
            ]

            if auth_req.sponsorship_offered is False:
                if has_not_authorized or requires_sponsor:
                    blocker_facts = tuple(
                        sorted(
                            set(
                                fid for m in (has_not_authorized + requires_sponsor) for fid in m[2]
                            )
                        )
                    )
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.HARD_BLOCKER,
                            reason=(
                                "Candidato não autorizado ou requer visto; vaga exige "
                                "autorização prévia sem patrocínio."
                            ),
                            source_facts=blocker_facts,
                        )
                    )
                elif has_authorized:
                    auth_facts = tuple(sorted(set(fid for m in has_authorized for fid in m[2])))
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.PASSED,
                            reason="Candidato possui autorização de trabalho confirmada.",
                            source_facts=auth_facts,
                        )
                    )
                elif does_not_require_sponsor:
                    dnr_facts = tuple(
                        sorted(set(fid for m in does_not_require_sponsor for fid in m[2]))
                    )
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason=(
                                "Candidato declarou não requerer patrocínio, mas autorização "
                                "legal de trabalho não foi confirmada."
                            ),
                            source_facts=dnr_facts,
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
                else:
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason="Status de autorização de trabalho ambíguo para a jurisdição.",
                            source_facts=(),
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)

            elif auth_req.sponsorship_offered is True:
                if has_not_authorized:
                    auth_facts = tuple(sorted(set(fid for m in has_not_authorized for fid in m[2])))
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason="Candidato não autorizado, mas vaga oferece patrocínio.",
                            source_facts=auth_facts,
                        )
                    )
                elif has_authorized or requires_sponsor:
                    qual_matches = has_authorized if has_authorized else requires_sponsor
                    auth_facts = tuple(sorted(set(fid for m in qual_matches for fid in m[2])))
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.PASSED,
                            reason="Vaga oferece suporte para visto e candidato é elegível.",
                            source_facts=auth_facts,
                        )
                    )
                elif does_not_require_sponsor:
                    dnr_facts = tuple(
                        sorted(set(fid for m in does_not_require_sponsor for fid in m[2]))
                    )
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason=(
                                "Candidato declarou não requerer patrocínio, mas autorização "
                                "legal de trabalho não foi confirmada."
                            ),
                            source_facts=dnr_facts,
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
                else:
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason="Status de autorização de trabalho ambíguo para a jurisdição.",
                            source_facts=(),
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)

            else:
                # sponsorship_offered is None (unstated) -> NO HARD BLOCKER PER ADR-0014
                if has_not_authorized:
                    not_auth_facts = tuple(
                        sorted(set(fid for m in has_not_authorized for fid in m[2]))
                    )
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason=(
                                "Candidato não autorizado, mas disponibilidade de patrocínio "
                                "não foi informada na vaga."
                            ),
                            source_facts=not_auth_facts,
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
                elif requires_sponsor:
                    sponsor_facts = tuple(
                        sorted(set(fid for m in requires_sponsor for fid in m[2]))
                    )
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason=(
                                "Candidato requer visto e disponibilidade de patrocínio "
                                "não foi informada na vaga."
                            ),
                            source_facts=sponsor_facts,
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
                elif has_authorized:
                    auth_facts = tuple(sorted(set(fid for m in has_authorized for fid in m[2])))
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.PASSED,
                            reason="Candidato possui autorização de trabalho confirmada.",
                            source_facts=auth_facts,
                        )
                    )
                elif does_not_require_sponsor:
                    dnr_facts = tuple(
                        sorted(set(fid for m in does_not_require_sponsor for fid in m[2]))
                    )
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason=(
                                "Candidato declarou não requerer patrocínio, mas autorização "
                                "legal de trabalho não foi confirmada."
                            ),
                            source_facts=dnr_facts,
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)
                else:
                    results.append(
                        HardFilterResult(
                            filter_id=HardFilterId.WORK_AUTHORIZATION,
                            outcome=HardFilterOutcome.UNKNOWN,
                            reason=(
                                "Autorização de trabalho não pôde ser comparada conclusivamente."
                            ),
                            source_facts=(),
                        )
                    )
                    critical_unknowns.append(CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS)

    # 2. WORKPLACE_TYPE
    wp_facts = candidate.workplace_preference_fact_ids
    if job.workplace_type == JobWorkplaceType.UNKNOWN:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.WORKPLACE_TYPE,
                outcome=HardFilterOutcome.UNKNOWN,
                reason="Modalidade de trabalho não informada na vaga.",
                source_facts=(),
            )
        )
    elif len(candidate.acceptable_workplace_types) == 0:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.WORKPLACE_TYPE,
                outcome=HardFilterOutcome.UNKNOWN,
                reason="Candidato não declarou preferências de modalidade de trabalho.",
                source_facts=(),
            )
        )
    elif job.workplace_type in candidate.acceptable_workplace_types:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.WORKPLACE_TYPE,
                outcome=HardFilterOutcome.PASSED,
                reason=f"Modalidade {job.workplace_type.value} aceita pelo candidato.",
                source_facts=wp_facts,
            )
        )
    else:
        # Check Acceptable Set Semantics
        remote_only = candidate.acceptable_workplace_types == frozenset({JobWorkplaceType.REMOTE})
        remote_or_hybrid_only = candidate.acceptable_workplace_types == frozenset(
            {JobWorkplaceType.REMOTE, JobWorkplaceType.HYBRID}
        )

        if remote_only and job.workplace_type in (
            JobWorkplaceType.ON_SITE,
            JobWorkplaceType.HYBRID,
        ):
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.WORKPLACE_TYPE,
                    outcome=HardFilterOutcome.HARD_BLOCKER,
                    reason=(
                        "Candidato aceita exclusivamente trabalho remoto; "
                        "vaga é presencial/híbrida."
                    ),
                    source_facts=wp_facts,
                )
            )
        elif remote_or_hybrid_only and job.workplace_type == JobWorkplaceType.ON_SITE:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.WORKPLACE_TYPE,
                    outcome=HardFilterOutcome.HARD_BLOCKER,
                    reason="Candidato aceita apenas remoto ou híbrido; vaga é 100% presencial.",
                    source_facts=wp_facts,
                )
            )
        else:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.WORKPLACE_TYPE,
                    outcome=HardFilterOutcome.SOFT_MISMATCH,
                    reason=(
                        f"Modalidade {job.workplace_type.value} difere da preferência "
                        "do candidato, mas não bloqueia."
                    ),
                    source_facts=wp_facts,
                )
            )

    # 3. LOCATION
    if job.workplace_type == JobWorkplaceType.REMOTE:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.LOCATION,
                outcome=HardFilterOutcome.PASSED,
                reason="Vaga remota; localização não impõe restrição física.",
                source_facts=(),
            )
        )
    elif not job.location_normalized or not candidate.acceptable_locations:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.LOCATION,
                outcome=HardFilterOutcome.UNKNOWN,
                reason="Localização não informada ou não declarada pelo candidato.",
                source_facts=(),
            )
        )
    else:
        is_match, is_comp, evidence_facts = _matches_location(job.location_normalized, candidate)
        if is_match:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.LOCATION,
                    outcome=HardFilterOutcome.PASSED,
                    reason="Localização da vaga compatível com as preferências do candidato.",
                    source_facts=evidence_facts,
                )
            )
        elif is_comp:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.LOCATION,
                    outcome=HardFilterOutcome.SOFT_MISMATCH,
                    reason=(
                        "Localização difere das preferências declaradas "
                        "(sem bloqueio rígido em V1)."
                    ),
                    source_facts=evidence_facts,
                )
            )
        else:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.LOCATION,
                    outcome=HardFilterOutcome.UNKNOWN,
                    reason="Informações insuficientes para provar correspondência de localização.",
                    source_facts=(),
                )
            )

    # 4. SALARY_MINIMUM
    sal_cand = candidate.salary_preference
    sal_job = job_signals.salary_requirement
    sal_facts = sal_cand.fact_ids if sal_cand else ()
    if (
        sal_cand is None
        or sal_job is None
        or sal_cand.currency != sal_job.currency
        or sal_cand.period != sal_job.period
        or sal_job.period is None
    ):
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.SALARY_MINIMUM,
                outcome=HardFilterOutcome.UNKNOWN,
                reason="Salário não informado, período/moeda divergente ou não comparável.",
                source_facts=(),
            )
        )
    else:
        if sal_cand.minor_unit_min is not None and sal_cand.minor_unit_min > sal_job.max_minor:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.SALARY_MINIMUM,
                    outcome=HardFilterOutcome.SOFT_MISMATCH,
                    reason=(
                        "Pretensão salarial mínima do candidato é superior ao limite "
                        "máximo da vaga."
                    ),
                    source_facts=sal_facts,
                )
            )
        else:
            results.append(
                HardFilterResult(
                    filter_id=HardFilterId.SALARY_MINIMUM,
                    outcome=HardFilterOutcome.PASSED,
                    reason="Faixa salarial compatível.",
                    source_facts=sal_facts,
                )
            )

    # 5. ROLE_ALIGNMENT
    if title_score < 50:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.ROLE_ALIGNMENT,
                outcome=HardFilterOutcome.SOFT_MISMATCH,
                reason=(
                    "Baixa similaridade entre o título da vaga e os cargos "
                    "alvo/anteriores do candidato."
                ),
                source_facts=(),
            )
        )
    else:
        results.append(
            HardFilterResult(
                filter_id=HardFilterId.ROLE_ALIGNMENT,
                outcome=HardFilterOutcome.PASSED,
                reason="Título alinhado com o perfil do candidato.",
                source_facts=(),
            )
        )

    return tuple(results), tuple(critical_unknowns)
