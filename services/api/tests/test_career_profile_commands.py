"""Unit tests for `tapjob_api.career_profile.commands.CareerProfilePatchCommand` -- the validated
domain command `service.update_career_profile` itself requires (round-3 independent review
"BLOCKER 1"). These prove the same headline/summary/targetRoles rules the HTTP
`UpdateCareerProfileRequest` enforces also live in the DOMAIN layer, reachable by a future channel
adapter that never goes through `router.py`/HTTP at all. Pure Pydantic, no database.
"""

import pytest
from pydantic import ValidationError

from tapjob_api.career_profile.commands import CareerProfilePatchCommand


def test_headline_whitespace_only_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(headline="   ")


def test_headline_overlong_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(headline="x" * 201)


def test_headline_rejects_control_characters() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(headline="Engineer\x00Hacker")


def test_headline_rejects_an_internal_newline() -> None:
    # round-2 independent review "BLOCKER 5"'s control-char-before-collapse discipline applies
    # here too -- a label never silently swallows a literal newline.
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(headline="Senior\nEngineer")


def test_headline_is_trimmed_and_unicode_preserved() -> None:
    command = CareerProfilePatchCommand(headline="  Engenheira de Software 👩‍💻  ")
    assert command.headline == "Engenheira de Software 👩‍💻"


def test_summary_whitespace_only_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(summary="\n\t  \n")


def test_summary_preserves_legitimate_internal_newlines() -> None:
    command = CareerProfilePatchCommand(summary="Line one.\nLine two.")
    assert command.summary == "Line one.\nLine two."


def test_target_role_blank_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(target_roles=["Backend Engineer", "   "])


def test_target_role_rejects_an_internal_newline() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(target_roles=["Backend\nEngineer"])


def test_target_role_rejects_an_internal_tab() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(target_roles=["Backend\tEngineer"])


def test_target_role_overlong_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(target_roles=["x" * 101])


def test_too_many_target_roles_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand(target_roles=[f"role-{i}" for i in range(21)])


def test_target_roles_are_deduplicated_case_insensitively_preserving_first_spelling() -> None:
    command = CareerProfilePatchCommand(
        target_roles=["Backend Engineer", "backend engineer", "Data Scientist"]
    )
    assert command.target_roles == ["Backend Engineer", "Data Scientist"]


def test_target_roles_never_merges_semantically_different_titles() -> None:
    command = CareerProfilePatchCommand(target_roles=["JS Developer", "JavaScript Developer"])
    assert command.target_roles == ["JS Developer", "JavaScript Developer"]


def test_target_roles_preserve_legitimate_unicode() -> None:
    command = CareerProfilePatchCommand(target_roles=["Engenheira de Dados 👩‍💻"])
    assert command.target_roles == ["Engenheira de Dados 👩‍💻"]


def test_a_command_with_no_mutable_field_at_all_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CareerProfilePatchCommand()


def test_an_explicit_null_field_alone_still_counts_as_a_mutable_field() -> None:
    command = CareerProfilePatchCommand(headline=None)
    assert "headline" in command.model_fields_set
