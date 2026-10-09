"""Pure integer scoring engine for WP2.2 Matching Engine (ADR-0014 Decision 6).

Guarantees:
- ZERO float types
- ZERO uses of Python builtin round()
- round_half_up_ratio pure integer division
- Basis points (0..10,000) integer arithmetic scaled to 0..100
"""

from __future__ import annotations

import re

from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.gate import _matches_location
from tapjob_api.matching.signals import (
    DOMAIN_TAXONOMY_V1,
    canonicalize_domain_token,
    normalize_text_token,
)
from tapjob_api.matching.types import (
    CandidateMatchSignals,
    CandidateSeniorityLevel,
    CriticalUnknown,
    DerivedJobMatchSignals,
    HardFilterOutcome,
    HardFilterResult,
    JobSeniorityLevel,
    MatchEligibility,
    ScoreComponents,
)


def round_half_up_ratio(numerator: int, denominator: int) -> int:
    """Pure integer round-half-up division for non-negative integers.

    Formula: (numerator * 2 + denominator) // (2 * denominator)
    Guarantees exact round-half-up:
      0.5 -> 1, 1.5 -> 2, 2.5 -> 3, 33.5 -> 34, 66.5 -> 67.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if numerator <= 0:
        return 0
    return (numerator * 2 + denominator) // (2 * denominator)


# -- Title Normalization & Tokenization --

_STOP_WORDS = frozenset(
    {
        "de",
        "do",
        "da",
        "e",
        "em",
        "para",
        "com",
        "a",
        "o",
        "of",
        "and",
        "in",
        "to",
        "for",
        "with",
        "the",
    }
)

_TITLE_ALIASES = {
    "desenvolvedor": "developer",
    "desenvolvedora": "developer",
    "dev": "developer",
    "engenheiro": "engineer",
    "engenheira": "engineer",
    "analista": "analyst",
    "gerente": "manager",
    "estagiario": "intern",
    "estagiaria": "intern",
}

_SENIORITY_MODIFIERS = frozenset(
    {
        "junior",
        "pleno",
        "senior",
        "lead",
        "sr",
        "jr",
        "pl",
        "intern",
        "estagio",
    }
)


def tokenize_title(title: str | None) -> set[str]:
    """Normalizes title, strips stopwords and seniority modifiers, resolves aliases."""
    if not title:
        return set()
    normalized = normalize_text_token(title)
    words = re.findall(r"\w+", normalized)
    tokens: set[str] = set()
    for w in words:
        if w in _STOP_WORDS:
            continue
        if w in _SENIORITY_MODIFIERS:
            continue
        alias = _TITLE_ALIASES.get(w, w)
        tokens.add(alias)
    return tokens


def compute_title_dice(candidate_tokens: set[str], job_tokens: set[str]) -> int:
    """Computes Dice similarity coefficient in scale 0..100 using integer arithmetic."""
    total_tokens = len(candidate_tokens) + len(job_tokens)
    if total_tokens == 0:
        return 0
    overlap = len(candidate_tokens & job_tokens)
    return round_half_up_ratio(200 * overlap, total_tokens)


def compute_title_score(candidate: CandidateMatchSignals, job: Job) -> int:
    """Evaluates Title Alignment (25% — 2,500 bps).

    Compares job title tokens against candidate target_roles (100% weight)
    and previous titles (90% weight via round_half_up_ratio).
    """
    job_tokens = tokenize_title(job.title_normalized)
    if not job_tokens:
        return 0

    best_target_score = 0
    for target in candidate.target_roles:
        target_tokens = tokenize_title(target)
        dice = compute_title_dice(target_tokens, job_tokens)
        if dice > best_target_score:
            best_target_score = dice

    best_past_score = 0
    for exp in candidate.experiences:
        exp_tokens = tokenize_title(exp.title_normalized)
        dice = compute_title_dice(exp_tokens, job_tokens)
        if dice > best_past_score:
            best_past_score = dice

    # 90% weighting for past titles
    weighted_past_score = round_half_up_ratio(best_past_score * 90, 100)

    if best_target_score >= weighted_past_score:
        return best_target_score
    return weighted_past_score


# -- Skills Scoring --


def compute_skills_score(
    candidate: CandidateMatchSignals, job_signals: DerivedJobMatchSignals
) -> int:
    """Evaluates Skill Overlap (35% — 3,500 bps) via the 6-case decision tree."""
    s_c = candidate.skill_names
    r = set(job_signals.required_skills)
    p = set(job_signals.preferred_skills)
    u = set(job_signals.unclassified_skills)

    len_r = len(r)
    len_p = len(p)
    len_u = len(u)

    req_score = round_half_up_ratio(100 * len(s_c & r), len_r) if len_r > 0 else 0
    pref_score = round_half_up_ratio(100 * len(s_c & p), len_p) if len_p > 0 else 0
    unclass_score = round_half_up_ratio(100 * len(s_c & u), len_u) if len_u > 0 else 0

    # Case 1: R > 0, P > 0
    if len_r > 0 and len_p > 0:
        return round_half_up_ratio(req_score * 75 + pref_score * 25, 100)

    # Case 2: R > 0, P == 0
    if len_r > 0 and len_p == 0:
        return req_score

    # Case 3: R == 0, P > 0, U == 0
    if len_r == 0 and len_p > 0 and len_u == 0:
        return 25 + round_half_up_ratio(pref_score * 50, 100)

    # Case 4: R == 0, P > 0, U > 0
    if len_r == 0 and len_p > 0 and len_u > 0:
        return round_half_up_ratio(unclass_score * 60 + pref_score * 40, 100)

    # Case 5: R == 0, P == 0, U > 0
    if len_r == 0 and len_p == 0 and len_u > 0:
        return unclass_score

    # Case 6: R == 0, P == 0, U == 0 (neutral imputation)
    return 50


# -- Experience & Domain Scoring --


def compute_experience_score(
    candidate: CandidateMatchSignals,
    job_or_signals: Job | DerivedJobMatchSignals,
) -> int:
    """Evaluates Experience & Domain Keywords (20% — 2,000 bps)."""
    # Zero Experience Precedence Rule
    if candidate.total_experience_months == 0 or len(candidate.experiences) == 0:
        return 0

    if isinstance(job_or_signals, DerivedJobMatchSignals):
        job_domain_tokens = set(job_or_signals.domain_tokens)
    else:
        words = re.findall(r"[A-Za-z0-9#+.-]+", job_or_signals.description_cleaned or "")
        job_domain_tokens = set()
        for w in words:
            dom = canonicalize_domain_token(w)
            if dom and dom in DOMAIN_TAXONOMY_V1:
                job_domain_tokens.add(dom)

    if not job_domain_tokens:
        base_domain_score = 50
    else:
        overlap = len(candidate.domain_token_names & job_domain_tokens)
        base_domain_score = round_half_up_ratio(100 * overlap, len(job_domain_tokens))

    # Adequacy factor: clamp(round_half_up_ratio(months * 100, 24), 50, 100)
    adequacy_percent = min(
        100,
        max(50, round_half_up_ratio(candidate.total_experience_months * 100, 24)),
    )
    return round_half_up_ratio(base_domain_score * adequacy_percent, 100)


# -- Seniority Scoring --

_SENIORITY_MATRIX: dict[tuple[CandidateSeniorityLevel, JobSeniorityLevel | None], int] = {
    (CandidateSeniorityLevel.INTERN, JobSeniorityLevel.INTERN): 100,
    (CandidateSeniorityLevel.INTERN, JobSeniorityLevel.JUNIOR): 80,
    (CandidateSeniorityLevel.INTERN, JobSeniorityLevel.MID): 30,
    (CandidateSeniorityLevel.INTERN, JobSeniorityLevel.SENIOR): 0,
    (CandidateSeniorityLevel.INTERN, JobSeniorityLevel.LEAD): 0,
    (CandidateSeniorityLevel.INTERN, JobSeniorityLevel.STAFF_PRINCIPAL): 0,
    (CandidateSeniorityLevel.JUNIOR, JobSeniorityLevel.INTERN): 80,
    (CandidateSeniorityLevel.JUNIOR, JobSeniorityLevel.JUNIOR): 100,
    (CandidateSeniorityLevel.JUNIOR, JobSeniorityLevel.MID): 70,
    (CandidateSeniorityLevel.JUNIOR, JobSeniorityLevel.SENIOR): 20,
    (CandidateSeniorityLevel.JUNIOR, JobSeniorityLevel.LEAD): 0,
    (CandidateSeniorityLevel.JUNIOR, JobSeniorityLevel.STAFF_PRINCIPAL): 0,
    (CandidateSeniorityLevel.MID, JobSeniorityLevel.INTERN): 50,
    (CandidateSeniorityLevel.MID, JobSeniorityLevel.JUNIOR): 80,
    (CandidateSeniorityLevel.MID, JobSeniorityLevel.MID): 100,
    (CandidateSeniorityLevel.MID, JobSeniorityLevel.SENIOR): 75,
    (CandidateSeniorityLevel.MID, JobSeniorityLevel.LEAD): 35,
    (CandidateSeniorityLevel.MID, JobSeniorityLevel.STAFF_PRINCIPAL): 10,
    (CandidateSeniorityLevel.SENIOR, JobSeniorityLevel.INTERN): 20,
    (CandidateSeniorityLevel.SENIOR, JobSeniorityLevel.JUNIOR): 50,
    (CandidateSeniorityLevel.SENIOR, JobSeniorityLevel.MID): 80,
    (CandidateSeniorityLevel.SENIOR, JobSeniorityLevel.SENIOR): 100,
    (CandidateSeniorityLevel.SENIOR, JobSeniorityLevel.LEAD): 80,
    (CandidateSeniorityLevel.SENIOR, JobSeniorityLevel.STAFF_PRINCIPAL): 60,
    (CandidateSeniorityLevel.LEAD, JobSeniorityLevel.INTERN): 10,
    (CandidateSeniorityLevel.LEAD, JobSeniorityLevel.JUNIOR): 30,
    (CandidateSeniorityLevel.LEAD, JobSeniorityLevel.MID): 60,
    (CandidateSeniorityLevel.LEAD, JobSeniorityLevel.SENIOR): 90,
    (CandidateSeniorityLevel.LEAD, JobSeniorityLevel.LEAD): 100,
    (CandidateSeniorityLevel.LEAD, JobSeniorityLevel.STAFF_PRINCIPAL): 80,
    (CandidateSeniorityLevel.STAFF_PRINCIPAL, JobSeniorityLevel.INTERN): 0,
    (CandidateSeniorityLevel.STAFF_PRINCIPAL, JobSeniorityLevel.JUNIOR): 10,
    (CandidateSeniorityLevel.STAFF_PRINCIPAL, JobSeniorityLevel.MID): 40,
    (CandidateSeniorityLevel.STAFF_PRINCIPAL, JobSeniorityLevel.SENIOR): 75,
    (CandidateSeniorityLevel.STAFF_PRINCIPAL, JobSeniorityLevel.LEAD): 90,
    (CandidateSeniorityLevel.STAFF_PRINCIPAL, JobSeniorityLevel.STAFF_PRINCIPAL): 100,
}


def compute_seniority_score(
    candidate: CandidateMatchSignals, job_signals: DerivedJobMatchSignals
) -> int:
    """Evaluates Seniority Alignment (10% — 1,000 bps) via 7x7 score matrix."""
    if (
        candidate.derived_seniority == CandidateSeniorityLevel.UNKNOWN
        or job_signals.seniority_signal is None
    ):
        return 50
    return _SENIORITY_MATRIX.get((candidate.derived_seniority, job_signals.seniority_signal), 50)


# -- Workplace & Location Scoring --


def compute_workplace_location_score(candidate: CandidateMatchSignals, job: Job) -> int:
    """Evaluates Workplace & Location (10% — 1,000 bps): 50% workplace + 50% location."""
    # Workplace score
    if (
        job.workplace_type == JobWorkplaceType.UNKNOWN
        or len(candidate.acceptable_workplace_types) == 0
    ):
        wp_score = 50
    elif job.workplace_type in candidate.acceptable_workplace_types:
        wp_score = 100
    else:
        wp_score = 0

    # Location score
    if job.workplace_type == JobWorkplaceType.REMOTE:
        loc_score = 100
    elif not job.location_normalized or not candidate.acceptable_locations:
        loc_score = 50
    else:
        is_match, is_comp, _ = _matches_location(job.location_normalized, candidate)
        if is_match:
            loc_score = 100
        elif is_comp:
            loc_score = 0
        else:
            loc_score = 50

    return round_half_up_ratio(wp_score + loc_score, 2)


# -- Full Score Computation --


def compute_scores_and_eligibility(
    candidate: CandidateMatchSignals,
    job_signals: DerivedJobMatchSignals,
    job: Job,
    hard_filter_results: tuple[HardFilterResult, ...],
    critical_unknowns: tuple[CriticalUnknown, ...],
) -> tuple[int, MatchEligibility, ScoreComponents]:
    """Computes pure integer component scores, overall score, and final eligibility."""
    title_score = compute_title_score(candidate, job)
    skills_score = compute_skills_score(candidate, job_signals)
    experience_score = compute_experience_score(candidate, job_signals)
    seniority_score = compute_seniority_score(candidate, job_signals)
    wp_loc_score = compute_workplace_location_score(candidate, job)

    components = ScoreComponents(
        title=title_score,
        skills=skills_score,
        experience=experience_score,
        seniority=seniority_score,
        workplace_location=wp_loc_score,
    )

    # Check for any HARD_BLOCKER
    has_hard_blocker = any(
        res.outcome == HardFilterOutcome.HARD_BLOCKER for res in hard_filter_results
    )

    if has_hard_blocker:
        return 0, MatchEligibility.INELIGIBLE, components

    # Basis points fixed-point arithmetic (0..10,000)
    raw_score_bps = (
        title_score * 2500
        + skills_score * 3500
        + experience_score * 2000
        + seniority_score * 1000
        + wp_loc_score * 1000
    )
    raw_score = round_half_up_ratio(raw_score_bps, 10000)
    overall_score = min(100, max(0, raw_score))

    # Eligibility Decision Table
    if critical_unknowns:
        if overall_score >= 50:
            eligibility = MatchEligibility.REVIEW_REQUIRED
        else:
            eligibility = MatchEligibility.INELIGIBLE
    else:
        if overall_score >= 50:
            eligibility = MatchEligibility.ELIGIBLE
        else:
            eligibility = MatchEligibility.INELIGIBLE

    return overall_score, eligibility, components
