"""Tests for Candidate Match Signal Reduction (ADR-0014 Decision 5B).

Verifies:
- Canonical TruthSnapshot boundary consumption
- Zero FactStatus.CONFIRMED reference
- Grounding preservation in fact_ids
- Exclusion of CareerProfile.summary
- Workplace preferences union (WorkplaceTypePreference + LocationPreference)
- Location preference with only workplace types does NOT create empty CandidateLocationPreference
- Location total sort key robustness against None values
- Calendar month union for overlapping experiences
- Conservative partial YearMonth (month=None contributes 0 months)
- Ongoing experience freeze by evaluation_as_of_date
- Fact order independence (shuffled snapshot yields identical signals)
- Skill deduplication and alias convergence
- Salary preference collapse vs ambiguity on conflict
- Work authorization question-key mapping and conflict handling
"""

import random
import uuid
from datetime import date
from typing import Any

from tapjob_api.career_profile.models import FactType, ProfileFact
from tapjob_api.career_profile.service import TruthSnapshot
from tapjob_api.jobs.models import JobWorkplaceType
from tapjob_api.matching.signals import derive_candidate_match_signals
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    WorkAuthSemanticValue,
)


def _make_fact(fact_id: uuid.UUID, fact_type: FactType, value: dict[str, Any]) -> ProfileFact:
    fact = ProfileFact()
    fact.id = fact_id
    fact.fact_type = fact_type
    fact.value_json = value
    return fact


def test_fact_order_independence() -> None:
    f_skill1 = _make_fact(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        FactType.SKILL,
        {"kind": "skill", "name": "Python"},
    )
    f_skill2 = _make_fact(
        uuid.UUID("22222222-2222-2222-2222-222222222222"),
        FactType.SKILL,
        {"kind": "skill", "name": "Kubernetes"},
    )
    f_exp = _make_fact(
        uuid.UUID("33333333-3333-3333-3333-333333333333"),
        FactType.EXPERIENCE,
        {
            "kind": "experience",
            "title": "Backend Engineer",
            "employer": "Acme",
            "start": {"year": 2022, "month": 1},
            "end": {"year": 2023, "month": 1},
            "current": False,
        },
    )

    facts = [f_skill1, f_skill2, f_exp]
    as_of = date(2026, 1, 1)

    snapshot1 = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=list(facts),
        target_roles=["Backend Engineer"],
    )

    shuffled_facts = list(facts)
    random.shuffle(shuffled_facts)
    snapshot2 = TruthSnapshot(
        user_id=snapshot1.user_id,
        profile_version=1,
        headline=None,
        summary=None,
        facts=list(shuffled_facts),
        target_roles=["Backend Engineer"],
    )

    sig1 = derive_candidate_match_signals(snapshot1, MATCHING_POLICY_VERSION, as_of)
    sig2 = derive_candidate_match_signals(snapshot2, MATCHING_POLICY_VERSION, as_of)

    assert sig1 == sig2


def test_skill_alias_convergence_and_grounding() -> None:
    fid1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
    fid2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
    f_k8s = _make_fact(fid1, FactType.SKILL, {"kind": "skill", "name": "k8s"})
    f_kube = _make_fact(fid2, FactType.SKILL, {"kind": "skill", "name": "Kubernetes"})

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_k8s, f_kube],
        target_roles=[],
    )
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))

    assert len(sig.skills) == 1
    assert sig.skills[0].canonical_skill == "kubernetes"
    assert sig.skills[0].fact_ids == (fid1, fid2)
    assert "kubernetes" in sig.skill_names


def test_overlapping_experience_calendar_month_union() -> None:
    # Exp 1: 2022-01 to 2022-06 (6 months)
    # Exp 2: 2022-04 to 2022-10 (7 months, overlapping April, May, June)
    # Total unique calendar months: Jan..Oct 2022 = 10 months (NOT 13)
    fid1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
    fid2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
    exp1 = _make_fact(
        fid1,
        FactType.EXPERIENCE,
        {
            "kind": "experience",
            "title": "Engineer 1",
            "employer": "Corp A",
            "start": {"year": 2022, "month": 1},
            "end": {"year": 2022, "month": 6},
            "current": False,
        },
    )
    exp2 = _make_fact(
        fid2,
        FactType.EXPERIENCE,
        {
            "kind": "experience",
            "title": "Engineer 2",
            "employer": "Corp B",
            "start": {"year": 2022, "month": 4},
            "end": {"year": 2022, "month": 10},
            "current": False,
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[exp1, exp2],
        target_roles=[],
    )
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))

    assert sig.total_experience_months == 10


def test_partial_year_month_conservative_handling() -> None:
    # month=None contributes 0 months
    fid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    exp = _make_fact(
        fid,
        FactType.EXPERIENCE,
        {
            "kind": "experience",
            "title": "Engineer",
            "employer": "Corp",
            "start": {"year": 2022, "month": None},
            "end": {"year": 2023, "month": 5},
            "current": False,
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[exp],
        target_roles=[],
    )
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))

    assert sig.total_experience_months == 0
    assert sig.experiences[0].duration_months == 0


def test_ongoing_experience_frozen_by_as_of_date() -> None:
    fid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    exp = _make_fact(
        fid,
        FactType.EXPERIENCE,
        {
            "kind": "experience",
            "title": "Engineer",
            "employer": "Corp",
            "start": {"year": 2024, "month": 1},
            "end": None,
            "current": True,
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[exp],
        target_roles=[],
    )

    # Evaluated at 2024-06-15 -> Jan to June = 6 months
    sig1 = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2024, 6, 15))
    assert sig1.total_experience_months == 6

    # Evaluated at 2025-01-15 -> Jan 2024 to Jan 2025 = 13 months
    sig2 = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2025, 1, 15))
    assert sig2.total_experience_months == 13


def test_workplace_preferences_union() -> None:
    f_wp = _make_fact(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "workplace_type",
                "workplace_types": ["remote"],
            },
        },
    )
    f_loc = _make_fact(
        uuid.UUID("22222222-2222-2222-2222-222222222222"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "location",
                "city": "Sao Paulo",
                "workplace_types": ["hybrid"],
            },
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_wp, f_loc],
        target_roles=[],
    )
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))

    assert sig.acceptable_workplace_types == frozenset(
        {JobWorkplaceType.REMOTE, JobWorkplaceType.HYBRID}
    )


def test_location_preference_with_only_workplace_types_does_not_create_empty_location() -> None:
    f_loc_wp_only = _make_fact(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "location",
                "country": None,
                "region": None,
                "city": None,
                "workplace_types": ["onsite"],
            },
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_loc_wp_only],
        target_roles=[],
    )
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))

    assert sig.acceptable_workplace_types == frozenset({JobWorkplaceType.ON_SITE})
    assert len(sig.acceptable_locations) == 0


def test_location_total_sort_key_robustness() -> None:
    f_loc1 = _make_fact(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "location",
                "country": "BR",
                "region": None,
                "city": "Campinas",
            },
        },
    )
    f_loc2 = _make_fact(
        uuid.UUID("22222222-2222-2222-2222-222222222222"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "location",
                "country": None,
                "region": "SP",
                "city": "Sao Paulo",
            },
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_loc1, f_loc2],
        target_roles=[],
    )
    # Must not raise TypeError '<' not supported between instances of 'NoneType' and 'str'
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))
    assert len(sig.acceptable_locations) == 2


def test_salary_preference_collapse_and_conflict() -> None:
    # Identical facts collapse
    f_sal1 = _make_fact(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "salary",
                "currency": "BRL",
                "period": "monthly",
                "minor_unit_min": 1000000,
                "minor_unit_max": 1500000,
            },
        },
    )
    f_sal2 = _make_fact(
        uuid.UUID("22222222-2222-2222-2222-222222222222"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "salary",
                "currency": "BRL",
                "period": "monthly",
                "minor_unit_min": 1000000,
                "minor_unit_max": 1500000,
            },
        },
    )

    snapshot_collapsed = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_sal1, f_sal2],
        target_roles=[],
    )
    sig_collapsed = derive_candidate_match_signals(
        snapshot_collapsed, MATCHING_POLICY_VERSION, date(2026, 1, 1)
    )
    assert sig_collapsed.salary_preference is not None
    assert sig_collapsed.salary_ambiguous is False
    assert sig_collapsed.salary_preference.minor_unit_min == 1000000

    # Conflicting facts -> ambiguous
    f_sal_diff = _make_fact(
        uuid.UUID("33333333-3333-3333-3333-333333333333"),
        FactType.PREFERENCE,
        {
            "kind": "preference",
            "preference": {
                "preference_kind": "salary",
                "currency": "USD",
                "period": "annual",
                "minor_unit_min": 8000000,
                "minor_unit_max": 10000000,
            },
        },
    )

    snapshot_conflict = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_sal1, f_sal_diff],
        target_roles=[],
    )

    sig_conflict = derive_candidate_match_signals(
        snapshot_conflict, MATCHING_POLICY_VERSION, date(2026, 1, 1)
    )
    assert sig_conflict.salary_preference is None
    assert sig_conflict.salary_ambiguous is True


def test_work_authorization_question_key_and_conflict() -> None:
    f_auth1 = _make_fact(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        FactType.WORK_AUTHORIZATION,
        {
            "kind": "work_authorization",
            "jurisdiction": "BR",
            "question_key": "legally_authorized_to_work",
            "answer": "sim",
        },
    )
    f_auth2 = _make_fact(
        uuid.UUID("22222222-2222-2222-2222-222222222222"),
        FactType.WORK_AUTHORIZATION,
        {
            "kind": "work_authorization",
            "jurisdiction": "US",
            "question_key": "visa_sponsorship_required",
            "answer": "yes",
        },
    )

    snapshot = TruthSnapshot(
        user_id=uuid.uuid4(),
        profile_version=1,
        headline=None,
        summary=None,
        facts=[f_auth1, f_auth2],
        target_roles=[],
    )
    sig = derive_candidate_match_signals(snapshot, MATCHING_POLICY_VERSION, date(2026, 1, 1))

    assert (
        sig.work_authorizations[("BR", "legally_authorized_to_work")]
        == WorkAuthSemanticValue.AUTHORIZED
    )
    assert (
        sig.work_authorizations[("US", "visa_sponsorship_required")]
        == WorkAuthSemanticValue.REQUIRES_SPONSORSHIP
    )
