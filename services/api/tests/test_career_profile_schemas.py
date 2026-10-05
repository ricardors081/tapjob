"""Unit tests for `tapjob_api.career_profile.schemas` validation rules -- the
`AddProfileFactRequest` cross-field check that `value.kind` must match the declared `factType`, the
per-item bound/normalization on `UpdateCareerProfileRequest.target_roles`, and the "no mutable field
at all" rejection (docs/adr/0010-career-profile-truth-store.md "value JSON safety", "profile
patch"). Pure Pydantic, no database.
"""

import pytest
from pydantic import ValidationError

from tapjob_api.career_profile.schemas import AddProfileFactRequest, UpdateCareerProfileRequest


def test_matching_value_kind_and_fact_type_is_accepted() -> None:
    body = AddProfileFactRequest.model_validate(
        {
            "expectedProfileVersion": 0,
            "factType": "skill",
            "value": {"kind": "skill", "name": "Python"},
        }
    )

    assert body.fact_type.value == "skill"
    assert body.value.kind == "skill"


def test_mismatched_value_kind_and_fact_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AddProfileFactRequest.model_validate(
            {
                "expectedProfileVersion": 0,
                "factType": "language",
                "value": {"kind": "skill", "name": "Python"},
            }
        )


def test_each_target_role_is_individually_bounded_not_just_the_list_length() -> None:
    # The list-level `max_length` alone would not catch one enormous role string -- a payload-bomb
    # vector the per-item validator closes (docs/adr/0010-...md "value JSON safety").
    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate(
            {"expectedProfileVersion": 0, "targetRoles": ["x" * 101]}
        )


def test_a_reasonably_sized_target_role_is_accepted() -> None:
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "targetRoles": ["backend engineer"]}
    )

    assert body.target_roles == ["backend engineer"]


def test_target_roles_are_deduplicated_case_insensitively_preserving_first_spelling() -> None:
    body = UpdateCareerProfileRequest.model_validate(
        {
            "expectedProfileVersion": 0,
            "targetRoles": ["Backend Engineer", "backend engineer", "Data Scientist"],
        }
    )

    assert body.target_roles == ["Backend Engineer", "Data Scientist"]


def test_target_roles_never_merges_semantically_different_titles() -> None:
    # "JS" and "JavaScript" remain distinct -- this is trim/dedupe-by-exact-match only, never a
    # semantic occupation taxonomy (docs/adr/0010-...md "target roles").
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "targetRoles": ["JS Developer", "JavaScript Developer"]}
    )

    assert body.target_roles == ["JS Developer", "JavaScript Developer"]


def test_a_blank_target_role_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate(
            {"expectedProfileVersion": 0, "targetRoles": ["Backend Engineer", "   "]}
        )


def test_target_roles_are_trimmed_and_internal_whitespace_collapsed() -> None:
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "targetRoles": ["  Backend   Engineer  "]}
    )

    assert body.target_roles == ["Backend Engineer"]


def test_target_role_with_an_internal_newline_is_rejected() -> None:
    # round-2 independent review "BLOCKER 5": the previous `" ".join(role.split())` idiom would
    # have silently collapsed "Backend\nEngineer" into "Backend Engineer" instead of rejecting it.
    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate(
            {"expectedProfileVersion": 0, "targetRoles": ["Backend\nEngineer"]}
        )


def test_target_role_with_an_internal_tab_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate(
            {"expectedProfileVersion": 0, "targetRoles": ["Backend\tEngineer"]}
        )


def test_target_role_preserves_legitimate_unicode() -> None:
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "targetRoles": ["Engenheira de Dados 👩‍💻"]}
    )

    assert body.target_roles == ["Engenheira de Dados 👩‍💻"]


def test_a_request_with_no_mutable_field_at_all_is_rejected() -> None:
    # `profile_version` represents a change in professional truth -- a body that only sets
    # `expectedProfileVersion` is not a logical mutation (round-1 independent review "BLOCKER 3").
    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate({"expectedProfileVersion": 0})


def test_an_explicit_null_field_alone_still_counts_as_a_mutable_field() -> None:
    # Distinguishes "absent" (rejected above) from "explicitly sent, even as null" (a real,
    # intentional clear).
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "headline": None}
    )

    assert "headline" in body.model_fields_set


def test_headline_is_trimmed_and_blank_is_rejected() -> None:
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "headline": "  Senior Engineer  "}
    )
    assert body.headline == "Senior Engineer"

    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate({"expectedProfileVersion": 0, "headline": "   "})


def test_summary_preserves_internal_newlines_but_rejects_blank() -> None:
    body = UpdateCareerProfileRequest.model_validate(
        {"expectedProfileVersion": 0, "summary": "Line one.\nLine two."}
    )
    assert body.summary == "Line one.\nLine two."

    with pytest.raises(ValidationError):
        UpdateCareerProfileRequest.model_validate({"expectedProfileVersion": 0, "summary": "\n\t "})
