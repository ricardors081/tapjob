"""Unit tests for `tapjob_api.resume_import.candidates` (docs/adr/0011-resume-import.md "resume
parser port review", "candidate data"). Proves fail-closed validation: malformed/mismatched provider
output is DROPPED, never persisted as if it were a valid candidate.
"""

from tapjob_api.ports.resume_parser import ExtractedFact
from tapjob_api.resume_import.candidates import validate_candidate


def test_a_well_formed_skill_candidate_validates_and_normalizes() -> None:
    fact = ExtractedFact(
        fact_type="skill", value={"kind": "skill", "name": "  Python  "}, confidence=0.6
    )
    candidate = validate_candidate(fact)
    assert candidate is not None
    assert candidate.value_json["name"] == "Python"
    assert candidate.kind == "skill"


def test_a_value_missing_required_fields_is_dropped_not_persisted() -> None:
    """`CertificationFactValue.issuer` is required -- an extraction missing it fails closed."""
    fact = ExtractedFact(
        fact_type="certification",
        value={"kind": "certification", "name": "Some Cert"},
        confidence=0.5,
    )
    assert validate_candidate(fact) is None


def test_a_kind_mismatch_between_fact_type_and_value_is_dropped() -> None:
    fact = ExtractedFact(
        fact_type="skill", value={"kind": "language", "language": "English"}, confidence=0.5
    )
    assert validate_candidate(fact) is None


def test_an_unknown_fact_type_is_dropped() -> None:
    fact = ExtractedFact(fact_type="not_a_real_type", value={"anything": "goes"}, confidence=0.5)
    assert validate_candidate(fact) is None


def test_headline_candidate_validates_through_the_same_normalizer_as_careerprofile_patch() -> None:
    fact = ExtractedFact(
        fact_type="headline", value={"text": "  Senior Engineer  "}, confidence=0.4
    )
    candidate = validate_candidate(fact)
    assert candidate is not None
    assert candidate.value_json == {"kind": "headline", "text": "Senior Engineer"}


def test_headline_whitespace_only_is_dropped() -> None:
    fact = ExtractedFact(fact_type="headline", value={"text": "   "}, confidence=0.4)
    assert validate_candidate(fact) is None


def test_summary_preserves_internal_newlines() -> None:
    fact = ExtractedFact(
        fact_type="summary", value={"text": "Line one.\nLine two."}, confidence=0.5
    )
    candidate = validate_candidate(fact)
    assert candidate is not None
    assert candidate.value_json["text"] == "Line one.\nLine two."


def test_work_authorization_would_validate_if_well_formed_despite_never_being_parser_output() -> (
    None
):
    """Proves the validator itself does not special-case this type away -- the deterministic
    parser's own choice to never EXTRACT one is the real safety guarantee
    (docs/adr/0011-...md "sensitive/preference fields"), tested separately in
    test_resume_import_parsing.py."""
    fact = ExtractedFact(
        fact_type="work_authorization",
        value={
            "kind": "work_authorization",
            "jurisdiction": "US",
            "questionKey": "us_work_auth",
            "answer": "Yes",
        },
        confidence=1.0,
    )
    candidate = validate_candidate(fact)
    assert candidate is not None


def test_source_excerpt_is_hard_truncated_regardless_of_input_length() -> None:
    from tapjob_api.resume_import.limits import MAX_SOURCE_EXCERPT_CHARS

    fact = ExtractedFact(
        fact_type="skill",
        value={"kind": "skill", "name": "Python"},
        confidence=0.5,
        source_excerpt="x" * (MAX_SOURCE_EXCERPT_CHARS * 3),
    )
    candidate = validate_candidate(fact)
    assert candidate is not None
    assert len(candidate.source_excerpt or "") <= MAX_SOURCE_EXCERPT_CHARS


def test_experience_candidate_with_current_and_technologies_and_no_start() -> None:
    fact = ExtractedFact(
        fact_type="experience",
        value={
            "kind": "experience",
            "employer": "Inpasa Brasil",
            "title": "Senior Backend Engineer",
            "current": True,
            "technologies": ["Python", "Go", "Kubernetes"],
        },
        confidence=0.9,
    )
    candidate = validate_candidate(fact)
    assert candidate is not None
    assert candidate.value_json["employer"] == "Inpasa Brasil"
    assert candidate.value_json["title"] == "Senior Backend Engineer"
    assert candidate.value_json["current"] is True
    assert candidate.value_json["start"] is None
    assert candidate.value_json["technologies"] == ["Python", "Go", "Kubernetes"]


def test_experience_candidate_current_with_end_date_is_rejected() -> None:
    fact = ExtractedFact(
        fact_type="experience",
        value={
            "kind": "experience",
            "employer": "Inpasa Brasil",
            "title": "Senior Backend Engineer",
            "current": True,
            "end": {"year": 2024, "month": 5},
        },
        confidence=0.9,
    )
    assert validate_candidate(fact) is None
