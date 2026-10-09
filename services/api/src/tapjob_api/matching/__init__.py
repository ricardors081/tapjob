"""TapJob Matching Engine domain module (WP2.2 / ADR-0014).

Provides deterministic opportunity matching, signals extraction, pure integer scoring,
hard filter gates, grounded explainability, and concurrency-safe persistence.
"""

from tapjob_api.matching.engine import evaluate_match
from tapjob_api.matching.fingerprint import compute_job_match_fingerprint
from tapjob_api.matching.models import MatchResult
from tapjob_api.matching.service import get_or_compute_match
from tapjob_api.matching.signals import (
    derive_candidate_match_signals,
    derive_job_match_signals,
)
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    MINIMUM_ELIGIBLE_SCORE,
    CandidateMatchSignals,
    CriticalUnknown,
    DerivedJobMatchSignals,
    DeterministicMatchCore,
    HardFilterId,
    HardFilterOutcome,
    HardFilterResult,
    MatchEligibility,
    ScoreComponents,
)

__all__ = [
    "MATCHING_POLICY_VERSION",
    "MINIMUM_ELIGIBLE_SCORE",
    "CandidateMatchSignals",
    "CriticalUnknown",
    "DerivedJobMatchSignals",
    "DeterministicMatchCore",
    "HardFilterId",
    "HardFilterOutcome",
    "HardFilterResult",
    "MatchEligibility",
    "MatchResult",
    "ScoreComponents",
    "compute_job_match_fingerprint",
    "derive_candidate_match_signals",
    "derive_job_match_signals",
    "evaluate_match",
    "get_or_compute_match",
]
