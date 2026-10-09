"""Tests for Hard Filter Gates and Capability Matrix (ADR-0014 Decision 4).

Verifies:
- 5 canonical gates: WORK_AUTHORIZATION, WORKPLACE_TYPE, LOCATION, SALARY_MINIMUM, ROLE_ALIGNMENT.
- Capability Matrix fail-closed and no-unproven-blocker invariants.
- WorkplaceType mapping (WorkplaceType.ONSITE <-> JobWorkplaceType.ON_SITE bijection).
- Acceptable Set Semantics for workplace modalities.
- Delimiter-token exact location matching (sinop vs sinopse).
- Question-key-aware work authorization mapping and jurisdiction matching.
- ROLE_ALIGNMENT_HARD_BLOCK_ENABLED = False (V1 never hard-blocks on title).
- SALARY_NUMERIC_SCORE_EFFECT = NONE (candidate min > job max yields SOFT_MISMATCH).
"""

import uuid
from typing import Any

from tapjob_api.career_profile.fact_values import SalaryPeriod, WorkplaceType
from tapjob_api.jobs.models import Job, JobWorkplaceType
from tapjob_api.matching.gate import (
    ROLE_ALIGNMENT_HARD_BLOCK_ENABLED,
    WORKPLACE_TYPE_MAPPING,
    _matches_location,
    evaluate_hard_filters,
)
from tapjob_api.matching.signals import derive_job_match_signals
from tapjob_api.matching.types import (
    MATCHING_POLICY_VERSION,
    CandidateLocationPreference,
    CandidateMatchSignals,
    CandidateSalaryPreference,
    CandidateSeniorityLevel,
    CriticalUnknown,
    DerivedJobMatchSignals,
    HardFilterId,
    HardFilterOutcome,
    JobSalarySignal,
    JobWorkAuthSignal,
    WorkAuthSemanticValue,
)


def _make_dummy_candidate(
    *,
    acceptable_workplace_types: frozenset[JobWorkplaceType] = frozenset(),
    acceptable_locations: tuple[CandidateLocationPreference, ...] = (),
    salary_preference: CandidateSalaryPreference | None = None,
    work_authorizations: dict[tuple[str, str], Any] | None = None,
    workplace_preference_fact_ids: tuple[uuid.UUID, ...] = (),
    skills_count: int = 1,
    exp_count: int = 1,
) -> CandidateMatchSignals:
    from tapjob_api.matching.types import CandidateExperienceSignal, CandidateSkillSignal

    skills = (
        (CandidateSkillSignal(canonical_skill="python", fact_ids=(uuid.uuid4(),)),)
        if skills_count > 0
        else ()
    )
    experiences = (
        (
            CandidateExperienceSignal(
                title="Software Engineer",
                title_normalized="software engineer",
                company_name="Acme",
                duration_months=24,
                fact_id=uuid.uuid4(),
            ),
        )
        if exp_count > 0
        else ()
    )
    return CandidateMatchSignals(
        skills=skills,
        skill_names=frozenset({"python"} if skills_count > 0 else set()),
        experiences=experiences,
        total_experience_months=24 if exp_count > 0 else 0,
        domain_tokens=(),
        domain_token_names=frozenset(),
        target_roles=("Software Engineer",),
        derived_seniority=CandidateSeniorityLevel.MID,
        salary_preference=salary_preference,
        salary_ambiguous=False,
        acceptable_workplace_types=acceptable_workplace_types,
        workplace_preference_fact_ids=workplace_preference_fact_ids,
        acceptable_locations=acceptable_locations,
        work_authorizations=work_authorizations or {},
        confirmed_fact_count=skills_count + exp_count,
    )


def _make_dummy_job_signals(
    *,
    salary: JobSalarySignal | None = None,
    work_auth: JobWorkAuthSignal | None = None,
) -> DerivedJobMatchSignals:
    return DerivedJobMatchSignals(
        required_skills=("python",),
        preferred_skills=(),
        unclassified_skills=(),
        seniority_signal=None,
        salary_requirement=salary,
        work_authorization_requirement=work_auth,
        unknown_signals=(),
    )


def _make_job(
    *,
    workplace_type: JobWorkplaceType = JobWorkplaceType.REMOTE,
    location_normalized: str | None = "sao paulo, sp",
) -> Job:
    job = Job()
    job.id = uuid.uuid4()
    job.title = "Software Engineer"
    job.title_normalized = "software engineer"
    job.workplace_type = workplace_type
    job.location_normalized = location_normalized
    job.description_cleaned = "Python role"
    return job


def test_workplace_type_enum_mapping_bijection() -> None:
    assert WORKPLACE_TYPE_MAPPING[WorkplaceType.REMOTE] == JobWorkplaceType.REMOTE
    assert WORKPLACE_TYPE_MAPPING[WorkplaceType.HYBRID] == JobWorkplaceType.HYBRID
    assert WORKPLACE_TYPE_MAPPING[WorkplaceType.ONSITE] == JobWorkplaceType.ON_SITE


def test_workplace_acceptable_set_semantics() -> None:
    # 1. Candidate remote only vs on-site -> HARD_BLOCKER
    c_remote = _make_dummy_candidate(
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE})
    )
    job_onsite = _make_job(workplace_type=JobWorkplaceType.ON_SITE)
    res, _ = evaluate_hard_filters(c_remote, _make_dummy_job_signals(), job_onsite, title_score=80)
    wp_res = next(r for r in res if r.filter_id == HardFilterId.WORKPLACE_TYPE)
    assert wp_res.outcome == HardFilterOutcome.HARD_BLOCKER

    # 2. Candidate remote or hybrid only vs on-site -> HARD_BLOCKER
    c_remote_hybrid = _make_dummy_candidate(
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE, JobWorkplaceType.HYBRID})
    )
    res, _ = evaluate_hard_filters(
        c_remote_hybrid, _make_dummy_job_signals(), job_onsite, title_score=80
    )
    wp_res = next(r for r in res if r.filter_id == HardFilterId.WORKPLACE_TYPE)
    assert wp_res.outcome == HardFilterOutcome.HARD_BLOCKER

    # 3. Candidate accepts onsite -> PASSED
    c_all = _make_dummy_candidate(
        acceptable_workplace_types=frozenset(
            {JobWorkplaceType.REMOTE, JobWorkplaceType.HYBRID, JobWorkplaceType.ON_SITE}
        )
    )
    res, _ = evaluate_hard_filters(c_all, _make_dummy_job_signals(), job_onsite, title_score=80)
    wp_res = next(r for r in res if r.filter_id == HardFilterId.WORKPLACE_TYPE)
    assert wp_res.outcome == HardFilterOutcome.PASSED

    # 4. Candidate zero workplace preferences -> UNKNOWN (neutral, never blocks)
    c_none = _make_dummy_candidate(acceptable_workplace_types=frozenset())
    res, _ = evaluate_hard_filters(c_none, _make_dummy_job_signals(), job_onsite, title_score=80)
    wp_res = next(r for r in res if r.filter_id == HardFilterId.WORKPLACE_TYPE)
    assert wp_res.outcome == HardFilterOutcome.UNKNOWN


def test_location_delimiter_token_exact_match() -> None:
    # "sinop" does NOT match "sinopse"
    cand_sinop = _make_dummy_candidate(
        acceptable_locations=(CandidateLocationPreference(country=None, region=None, city="sinop"),)
    )
    match, comp, _ = _matches_location("sinopse", cand_sinop)
    assert match is False
    assert comp is True

    # "sinop" matches "sinop, mt"
    match2, comp2, _ = _matches_location("sinop, mt", cand_sinop)
    assert match2 is True
    assert comp2 is True

    # "sao paulo" does NOT match "sao paulo do potengi"
    cand_sp = _make_dummy_candidate(
        acceptable_locations=(
            CandidateLocationPreference(country=None, region=None, city="sao paulo"),
        )
    )
    match3, _, _ = _matches_location("sao paulo do potengi, rn", cand_sp)
    assert match3 is False

    # "sao paulo" matches "sao paulo - sp"
    match4, _, _ = _matches_location("sao paulo - sp", cand_sp)
    assert match4 is True


def test_location_gate_never_hard_blocks_in_v1() -> None:
    # Different city on-site yields SOFT_MISMATCH, NEVER HARD_BLOCKER
    cand = _make_dummy_candidate(
        acceptable_locations=(
            CandidateLocationPreference(country="BR", region="RJ", city="rio de janeiro"),
        )
    )
    job = _make_job(workplace_type=JobWorkplaceType.ON_SITE, location_normalized="sao paulo, sp")
    res, _ = evaluate_hard_filters(cand, _make_dummy_job_signals(), job, title_score=80)
    loc_res = next(r for r in res if r.filter_id == HardFilterId.LOCATION)
    assert loc_res.outcome == HardFilterOutcome.SOFT_MISMATCH


def test_work_authorization_capability_matrix() -> None:
    # Case A: Job requires auth, sponsorship=False, candidate has NOT_AUTHORIZED -> HARD_BLOCKER
    cand_unauth = _make_dummy_candidate(
        work_authorizations={
            ("BR", "legally_authorized_to_work"): WorkAuthSemanticValue.NOT_AUTHORIZED
        }
    )
    job_req_no_sponsor = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=False,
            jurisdiction="BR",
            source_span="Must be authorized. No sponsorship.",
        )
    )
    res_a, _ = evaluate_hard_filters(cand_unauth, job_req_no_sponsor, _make_job(), title_score=80)
    auth_a = next(r for r in res_a if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth_a.outcome == HardFilterOutcome.HARD_BLOCKER

    # Case B: Job requires auth, sponsorship unstated (None), candidate requires sponsorship
    # -> UNKNOWN & WORK_AUTHORIZATION_AMBIGUOUS
    cand_need_sponsor = _make_dummy_candidate(
        work_authorizations={
            ("US", "visa_sponsorship_required"): WorkAuthSemanticValue.REQUIRES_SPONSORSHIP
        }
    )
    job_req_unstated = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=None,
            jurisdiction="US",
            source_span="Must be legally authorized to work in US.",
        )
    )
    res_b, crit_b = evaluate_hard_filters(
        cand_need_sponsor, job_req_unstated, _make_job(), title_score=80
    )
    auth_b = next(r for r in res_b if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth_b.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit_b

    # Case C: Candidate has no work auth fact -> UNKNOWN & WORK_AUTHORIZATION_AMBIGUOUS
    cand_empty = _make_dummy_candidate(work_authorizations={})
    res_c, crit_c = evaluate_hard_filters(cand_empty, job_req_unstated, _make_job(), title_score=80)
    auth_c = next(r for r in res_c if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth_c.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit_c


def test_salary_gate_soft_mismatch_and_no_numeric_block() -> None:
    # Candidate floor > job max (same currency, same period) -> SOFT_MISMATCH
    cand_sal = _make_dummy_candidate(
        salary_preference=CandidateSalaryPreference(
            minor_unit_min=1500000,
            minor_unit_max=2000000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
        )
    )
    job_sal = _make_dummy_job_signals(
        salary=JobSalarySignal(
            min_minor=800000,
            max_minor=1200000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
            source_span="R$ 8.000 a R$ 12.000",
        )
    )
    res, _ = evaluate_hard_filters(cand_sal, job_sal, _make_job(), title_score=80)
    sal_res = next(r for r in res if r.filter_id == HardFilterId.SALARY_MINIMUM)
    assert sal_res.outcome == HardFilterOutcome.SOFT_MISMATCH

    # Different currency -> UNKNOWN (never blocks)
    cand_usd = _make_dummy_candidate(
        salary_preference=CandidateSalaryPreference(
            minor_unit_min=10000000,
            minor_unit_max=12000000,
            currency="USD",
            period=SalaryPeriod.ANNUAL,
        )
    )
    res_diff, _ = evaluate_hard_filters(cand_usd, job_sal, _make_job(), title_score=80)
    sal_diff = next(r for r in res_diff if r.filter_id == HardFilterId.SALARY_MINIMUM)
    assert sal_diff.outcome == HardFilterOutcome.UNKNOWN


def test_role_alignment_hard_block_disabled_in_v1() -> None:
    assert ROLE_ALIGNMENT_HARD_BLOCK_ENABLED is False

    cand = _make_dummy_candidate()
    # Even with low title score 0, outcome is SOFT_MISMATCH, NEVER HARD_BLOCKER
    res, _ = evaluate_hard_filters(cand, _make_dummy_job_signals(), _make_job(), title_score=0)
    role_res = next(r for r in res if r.filter_id == HardFilterId.ROLE_ALIGNMENT)
    assert role_res.outcome == HardFilterOutcome.SOFT_MISMATCH


def test_sparse_profile_critical_unknown() -> None:
    # 0 skills AND 0 experiences -> SPARSE_PROFILE
    cand_sparse = _make_dummy_candidate(skills_count=0, exp_count=0)
    _, crit = evaluate_hard_filters(
        cand_sparse, _make_dummy_job_signals(), _make_job(), title_score=80
    )
    assert CriticalUnknown.SPARSE_PROFILE in crit

    # Has at least 1 skill -> NOT SPARSE_PROFILE
    cand_ok = _make_dummy_candidate(skills_count=1, exp_count=0)
    _, crit_ok = evaluate_hard_filters(
        cand_ok, _make_dummy_job_signals(), _make_job(), title_score=80
    )
    assert CriticalUnknown.SPARSE_PROFILE not in crit_ok


def test_work_authorization_finding_2_jurisdiction_ambiguity_and_matching() -> None:
    # Scenario 1: Job does not state country. BR candidate -> UNKNOWN & AMBIGUOUS
    cand_br = _make_dummy_candidate(
        work_authorizations={("BR", "legally_authorized_to_work"): WorkAuthSemanticValue.AUTHORIZED}
    )
    job_generic = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=False,
            jurisdiction=None,
            source_span="Must be legally authorized to work.",
        )
    )
    res1, crit1 = evaluate_hard_filters(cand_br, job_generic, _make_job(), title_score=80)
    auth1 = next(r for r in res1 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth1.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit1

    # Scenario 2: Job does not state country. Candidate US unauthorized -> UNKNOWN
    cand_us_unauth = _make_dummy_candidate(
        work_authorizations={
            ("US", "legally_authorized_to_work"): WorkAuthSemanticValue.NOT_AUTHORIZED
        }
    )
    res2, crit2 = evaluate_hard_filters(cand_us_unauth, job_generic, _make_job(), title_score=80)
    auth2 = next(r for r in res2 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth2.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit2

    # Scenario 3: Job explicitly requires US country. Candidate has US NOT_AUTHORIZED -> BLOCKER
    job_us_no_sponsor = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=False,
            jurisdiction="US",
            source_span="Must be legally authorized to work in the US. No sponsorship.",
        )
    )
    res3, _ = evaluate_hard_filters(cand_us_unauth, job_us_no_sponsor, _make_job(), title_score=80)
    auth3 = next(r for r in res3 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth3.outcome == HardFilterOutcome.HARD_BLOCKER

    # Scenario 4: Job explicitly requires US country. Candidate has US AUTHORIZED -> PASSED
    cand_us_auth = _make_dummy_candidate(
        work_authorizations={("US", "legally_authorized_to_work"): WorkAuthSemanticValue.AUTHORIZED}
    )
    res4, _ = evaluate_hard_filters(cand_us_auth, job_us_no_sponsor, _make_job(), title_score=80)
    auth4 = next(r for r in res4 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth4.outcome == HardFilterOutcome.PASSED


def test_location_specificity_and_delimited_matching_finding_4() -> None:
    # 1. Candidate São Paulo/SP/BR vs Job São Paulo, SP, Brasil -> Match
    cand_sp = _make_dummy_candidate(
        acceptable_locations=(
            CandidateLocationPreference(country="BR", region="SP", city="sao paulo"),
        )
    )
    m1, c1, _ = _matches_location("sao paulo, sp, brasil", cand_sp)
    assert m1 is True
    assert c1 is True

    # 2. Candidate São Paulo/SP/BR vs Job Rio de Janeiro, RJ, Brasil -> Mismatch
    m2, c2, _ = _matches_location("rio de janeiro, rj, brasil", cand_sp)
    assert m2 is False
    assert c2 is True

    # 3. Candidate Sinop/MT/BR vs Job Sinop, MT -> Match
    cand_sinop = _make_dummy_candidate(
        acceptable_locations=(CandidateLocationPreference(country="BR", region="MT", city="sinop"),)
    )
    m3, c3, _ = _matches_location("sinop, mt", cand_sinop)
    assert m3 is True
    assert c3 is True

    # 4. Candidate São Paulo/SP (region=SP) vs Job São Paulo, RJ -> Mismatch (region contradiction)
    cand_sp_reg = _make_dummy_candidate(
        acceptable_locations=(
            CandidateLocationPreference(country=None, region="SP", city="sao paulo"),
        )
    )
    m4, c4, _ = _matches_location("sao paulo, rj", cand_sp_reg)
    assert m4 is False
    assert c4 is True

    # 5. Candidate country-only BR vs Job São Paulo, SP, Brasil -> Match
    cand_br_only = _make_dummy_candidate(
        acceptable_locations=(CandidateLocationPreference(country="BR", region=None, city=None),)
    )
    m5, c5, _ = _matches_location("sao paulo, sp, brasil", cand_br_only)
    assert m5 is True
    assert c5 is True

    # 6. Candidate São Paulo/SP/BR vs Job Campinas, SP, Brasil (diff city) -> Mismatch
    m6, c6, _ = _matches_location("campinas, sp, brasil", cand_sp)
    assert m6 is False
    assert c6 is True

    # 7. Candidate São Paulo/SP/BR vs Job "Brasil" (insufficient components) -> Unknown
    m7, c7, _ = _matches_location("brasil", cand_sp)
    assert m7 is False
    assert c7 is False


def test_hard_filter_source_facts_grounded_finding_6() -> None:
    from tapjob_api.matching.types import CandidateWorkAuthorizationSignal

    fact_id_auth = uuid.uuid4()
    fact_id_wp = uuid.uuid4()
    fact_id_loc = uuid.uuid4()
    fact_id_sal = uuid.uuid4()

    # 1. Work auth hard blocker includes auth fact ID
    auth_sig = CandidateWorkAuthorizationSignal(
        jurisdiction="US",
        question_key="legally_authorized_to_work",
        semantic_value=WorkAuthSemanticValue.NOT_AUTHORIZED,
        fact_ids=(fact_id_auth,),
    )
    cand = _make_dummy_candidate(
        work_authorizations={("US", "legally_authorized_to_work"): auth_sig},
        acceptable_workplace_types=frozenset({JobWorkplaceType.REMOTE}),
        workplace_preference_fact_ids=(fact_id_wp,),
        acceptable_locations=(
            CandidateLocationPreference(
                country="BR", region="SP", city="sao paulo", fact_ids=(fact_id_loc,)
            ),
        ),
        salary_preference=CandidateSalaryPreference(
            minor_unit_min=1500000,
            minor_unit_max=2000000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
            fact_ids=(fact_id_sal,),
        ),
    )

    job_us_no_sponsor = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=False,
            jurisdiction="US",
            source_span="Must be authorized in the US. No sponsorship.",
        ),
        salary=JobSalarySignal(
            min_minor=800000,
            max_minor=1200000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
            source_span="R$ 8.000 a 12.000",
        ),
    )
    job_onsite_rio = _make_job(
        workplace_type=JobWorkplaceType.ON_SITE,
        location_normalized="rio de janeiro, rj",
    )

    res, _ = evaluate_hard_filters(cand, job_us_no_sponsor, job_onsite_rio, title_score=80)

    # Work Auth gate
    auth_gate = next(r for r in res if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth_gate.outcome == HardFilterOutcome.HARD_BLOCKER
    assert auth_gate.source_facts == (fact_id_auth,)

    # Workplace Type gate
    wp_gate = next(r for r in res if r.filter_id == HardFilterId.WORKPLACE_TYPE)
    assert wp_gate.outcome == HardFilterOutcome.HARD_BLOCKER
    assert wp_gate.source_facts == (fact_id_wp,)

    # Location gate
    loc_gate = next(r for r in res if r.filter_id == HardFilterId.LOCATION)
    assert loc_gate.outcome == HardFilterOutcome.SOFT_MISMATCH
    assert loc_gate.source_facts == (fact_id_loc,)

    # Salary gate
    sal_gate = next(r for r in res if r.filter_id == HardFilterId.SALARY_MINIMUM)
    assert sal_gate.outcome == HardFilterOutcome.SOFT_MISMATCH
    assert sal_gate.source_facts == (fact_id_sal,)


def test_work_authorization_finding_1_no_sponsorship_evidence_no_hard_blocker() -> None:
    from tapjob_api.matching.types import CandidateWorkAuthorizationSignal

    fact_id_not_auth = uuid.uuid4()
    fact_id_req_sponsor = uuid.uuid4()

    # 1. explicit US requirement + candidate US NOT_AUTHORIZED + sponsorship=False => HARD_BLOCKER
    cand_not_auth = _make_dummy_candidate(
        work_authorizations={
            ("US", "legally_authorized_to_work"): CandidateWorkAuthorizationSignal(
                jurisdiction="US",
                question_key="legally_authorized_to_work",
                semantic_value=WorkAuthSemanticValue.NOT_AUTHORIZED,
                fact_ids=(fact_id_not_auth,),
            )
        }
    )
    job_us_no_sponsor = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=False,
            jurisdiction="US",
            source_span="Must be authorized to work in the US. No sponsorship.",
        )
    )
    res1, _ = evaluate_hard_filters(cand_not_auth, job_us_no_sponsor, _make_job(), title_score=80)
    auth1 = next(r for r in res1 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth1.outcome == HardFilterOutcome.HARD_BLOCKER
    assert auth1.source_facts == (fact_id_not_auth,)

    # 2. explicit US requirement + candidate US REQUIRES_SPONSORSHIP + sponsorship=False
    # => HARD_BLOCKER
    cand_req_sponsor = _make_dummy_candidate(
        work_authorizations={
            ("US", "visa_sponsorship_required"): CandidateWorkAuthorizationSignal(
                jurisdiction="US",
                question_key="visa_sponsorship_required",
                semantic_value=WorkAuthSemanticValue.REQUIRES_SPONSORSHIP,
                fact_ids=(fact_id_req_sponsor,),
            )
        }
    )
    res2, _ = evaluate_hard_filters(
        cand_req_sponsor, job_us_no_sponsor, _make_job(), title_score=80
    )
    auth2 = next(r for r in res2 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth2.outcome == HardFilterOutcome.HARD_BLOCKER
    assert auth2.source_facts == (fact_id_req_sponsor,)

    # 3. explicit US requirement + candidate US NOT_AUTHORIZED + sponsorship=None
    # => UNKNOWN + CriticalUnknown
    job_us_none_sponsor = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=None,
            jurisdiction="US",
            source_span="Must be authorized to work in the US.",
        )
    )
    res3, crit3 = evaluate_hard_filters(
        cand_not_auth, job_us_none_sponsor, _make_job(), title_score=80
    )
    auth3 = next(r for r in res3 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth3.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit3
    assert auth3.source_facts == (fact_id_not_auth,)

    # 4. explicit US requirement + candidate US REQUIRES_SPONSORSHIP + sponsorship=None
    # => UNKNOWN + CriticalUnknown
    res4, crit4 = evaluate_hard_filters(
        cand_req_sponsor, job_us_none_sponsor, _make_job(), title_score=80
    )
    auth4 = next(r for r in res4 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth4.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit4
    assert auth4.source_facts == (fact_id_req_sponsor,)


def test_work_authorization_finding_2_does_not_require_sponsorship_not_authorized() -> None:
    from tapjob_api.matching.types import CandidateWorkAuthorizationSignal

    fact_id_dnr = uuid.uuid4()
    fact_id_auth = uuid.uuid4()

    job_req_auth = _make_dummy_job_signals(
        work_auth=JobWorkAuthSignal(
            requires_existing_authorization=True,
            sponsorship_offered=False,
            jurisdiction="US",
            source_span="Must be authorized to work in the US. No sponsorship.",
        )
    )

    # 1. visa_sponsorship_required=false (DOES_NOT_REQUIRE_SPONSORSHIP)
    # => NÃO PASS por esse fato isoladamente (UNKNOWN + CriticalUnknown)
    cand_dnr_only = _make_dummy_candidate(
        work_authorizations={
            ("US", "visa_sponsorship_required"): CandidateWorkAuthorizationSignal(
                jurisdiction="US",
                question_key="visa_sponsorship_required",
                semantic_value=WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP,
                fact_ids=(fact_id_dnr,),
            )
        }
    )
    res1, crit1 = evaluate_hard_filters(cand_dnr_only, job_req_auth, _make_job(), title_score=80)
    auth1 = next(r for r in res1 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth1.outcome == HardFilterOutcome.UNKNOWN
    assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit1
    assert auth1.source_facts == (fact_id_dnr,)

    # 2. legally_authorized_to_work=yes => PASS quando demais condições são satisfeitas
    cand_auth_only = _make_dummy_candidate(
        work_authorizations={
            ("US", "legally_authorized_to_work"): CandidateWorkAuthorizationSignal(
                jurisdiction="US",
                question_key="legally_authorized_to_work",
                semantic_value=WorkAuthSemanticValue.AUTHORIZED,
                fact_ids=(fact_id_auth,),
            )
        }
    )
    res2, _ = evaluate_hard_filters(cand_auth_only, job_req_auth, _make_job(), title_score=80)
    auth2 = next(r for r in res2 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth2.outcome == HardFilterOutcome.PASSED
    assert auth2.source_facts == (fact_id_auth,)

    # 3. legally_authorized_to_work=yes + visa_sponsorship_required=false
    # => PASS grounded no AUTHORIZED fact
    cand_both = _make_dummy_candidate(
        work_authorizations={
            ("US", "legally_authorized_to_work"): CandidateWorkAuthorizationSignal(
                jurisdiction="US",
                question_key="legally_authorized_to_work",
                semantic_value=WorkAuthSemanticValue.AUTHORIZED,
                fact_ids=(fact_id_auth,),
            ),
            ("US", "visa_sponsorship_required"): CandidateWorkAuthorizationSignal(
                jurisdiction="US",
                question_key="visa_sponsorship_required",
                semantic_value=WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP,
                fact_ids=(fact_id_dnr,),
            ),
        }
    )
    res3, _ = evaluate_hard_filters(cand_both, job_req_auth, _make_job(), title_score=80)
    auth3 = next(r for r in res3 if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
    assert auth3.outcome == HardFilterOutcome.PASSED
    # Grounded on the AUTHORIZED fact only
    assert auth3.source_facts == (fact_id_auth,)


def test_location_and_workplace_source_facts_exact_finding_5() -> None:
    fact_id_sp = uuid.uuid4()
    fact_id_sinop = uuid.uuid4()

    # São Paulo (fact A) + Sinop (fact B)
    cand = _make_dummy_candidate(
        acceptable_locations=(
            CandidateLocationPreference(
                country="BR", region="SP", city="sao paulo", fact_ids=(fact_id_sp,)
            ),
            CandidateLocationPreference(
                country="BR", region="MT", city="sinop", fact_ids=(fact_id_sinop,)
            ),
        )
    )

    job_sinop = _make_job(
        workplace_type=JobWorkplaceType.ON_SITE,
        location_normalized="sinop, mt",
    )
    job_sig = _make_dummy_job_signals()

    res, _ = evaluate_hard_filters(cand, job_sig, job_sinop, title_score=80)
    loc_gate = next(r for r in res if r.filter_id == HardFilterId.LOCATION)
    assert loc_gate.outcome == HardFilterOutcome.PASSED
    # PASS must contain ONLY fact B (Sinop), NOT fact A (São Paulo)
    assert loc_gate.source_facts == (fact_id_sinop,)


def test_work_authorization_unproven_jurisdiction_never_hard_blocks() -> None:
    """End-to-end regression:

    Cases where jurisdiction cannot be proven must never produce a HARD_BLOCKER:
    Case 1: 'Candidates must be legally authorized to work for our Brazil team.'
            'We do not sponsor visas.'
    Case 2: 'Must be legally authorized to work in our Brazil office.'
            'We do not sponsor visas.'
    Candidate: BR NOT_AUTHORIZED + sponsorship=False
    => WORK_AUTHORIZATION UNKNOWN
    => NEVER HARD_BLOCKER
    """
    cases = [
        # 1. Team country false positive
        (
            "Candidates must be legally authorized to work for our Brazil team.\n"
            "We do not sponsor visas."
        ),
        # 2. Office country false positive
        ("Must be legally authorized to work in our Brazil office.\nWe do not sponsor visas."),
        # 3. Preceding sentence team mention
        (
            "Join our Brazil team and candidates must be legally authorized to work.\n"
            "We do not sponsor visas."
        ),
    ]

    cand_br_unauth = _make_dummy_candidate(
        work_authorizations={
            ("BR", "legally_authorized_to_work"): WorkAuthSemanticValue.NOT_AUTHORIZED,
            ("BR", "requires_sponsorship"): WorkAuthSemanticValue.DOES_NOT_REQUIRE_SPONSORSHIP,
        }
    )

    for text in cases:
        job = _make_job()
        job.description_cleaned = text

        job_signals = derive_job_match_signals(job, MATCHING_POLICY_VERSION)
        assert job_signals.work_authorization_requirement is not None
        assert job_signals.work_authorization_requirement.requires_existing_authorization is True
        assert job_signals.work_authorization_requirement.sponsorship_offered is False
        assert job_signals.work_authorization_requirement.jurisdiction is None

        res, crit = evaluate_hard_filters(cand_br_unauth, job_signals, job, title_score=80)
        auth_filter = next(r for r in res if r.filter_id == HardFilterId.WORK_AUTHORIZATION)
        assert auth_filter.outcome == HardFilterOutcome.UNKNOWN
        assert CriticalUnknown.WORK_AUTHORIZATION_AMBIGUOUS in crit
        assert not any(r.outcome == HardFilterOutcome.HARD_BLOCKER for r in res)
