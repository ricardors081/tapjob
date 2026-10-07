"""Unit tests for `tapjob_api.resume_import.commands` (docs/adr/0011-resume-import.md "review
decisions"). Pure Pydantic, no database -- proves the domain command enforces its own rules
regardless of which channel constructs it.
"""

import uuid

import pytest
from pydantic import ValidationError

from tapjob_api.resume_import.commands import (
    CandidateReviewDecision,
    FinalizeResumeImportCommand,
    ReviewDecisionKind,
)


def test_accept_decision_with_no_edited_value_is_valid() -> None:
    decision = CandidateReviewDecision(
        candidate_id=uuid.uuid4(), decision=ReviewDecisionKind.ACCEPT
    )
    assert decision.edited_value is None


def test_edit_accept_without_an_edited_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateReviewDecision(candidate_id=uuid.uuid4(), decision=ReviewDecisionKind.EDIT_ACCEPT)


def test_accept_decision_with_an_edited_value_is_rejected() -> None:
    """`editedValue` is only meaningful alongside `edit_accept` -- no silent acceptance-by-omission
    via a stray field on a plain accept (docs/adr/0011-...md "review decisions")."""
    with pytest.raises(ValidationError):
        CandidateReviewDecision(
            candidate_id=uuid.uuid4(),
            decision=ReviewDecisionKind.ACCEPT,
            edited_value={"name": "Python"},
        )


def test_edit_accept_with_an_edited_value_is_valid() -> None:
    decision = CandidateReviewDecision(
        candidate_id=uuid.uuid4(),
        decision=ReviewDecisionKind.EDIT_ACCEPT,
        edited_value={"kind": "skill", "name": "Python"},
    )
    assert decision.edited_value == {"kind": "skill", "name": "Python"}


def test_finalize_command_requires_at_least_one_decision() -> None:
    with pytest.raises(ValidationError):
        FinalizeResumeImportCommand(expected_profile_version=1, decisions=[])


def test_duplicate_accept_decisions_for_the_same_candidate_id_are_rejected() -> None:
    candidate_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        FinalizeResumeImportCommand(
            expected_profile_version=0,
            decisions=[
                CandidateReviewDecision(
                    candidate_id=candidate_id, decision=ReviewDecisionKind.ACCEPT
                ),
                CandidateReviewDecision(
                    candidate_id=candidate_id, decision=ReviewDecisionKind.ACCEPT
                ),
            ],
        )


def test_accept_and_reject_decisions_for_the_same_candidate_id_are_rejected() -> None:
    """Round-1 independent review "Finding 4": a duplicate `candidateId` across a mix of decisions
    previously survived `service.py`'s own set-based coverage check unnoticed, risking a
    `ProfileFact` created from a candidate whose own stored `review_state` ends up `rejected`."""
    candidate_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        FinalizeResumeImportCommand(
            expected_profile_version=0,
            decisions=[
                CandidateReviewDecision(
                    candidate_id=candidate_id, decision=ReviewDecisionKind.ACCEPT
                ),
                CandidateReviewDecision(
                    candidate_id=candidate_id, decision=ReviewDecisionKind.REJECT
                ),
            ],
        )


def test_edit_accept_and_accept_decisions_for_the_same_candidate_id_are_rejected() -> None:
    candidate_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        FinalizeResumeImportCommand(
            expected_profile_version=0,
            decisions=[
                CandidateReviewDecision(
                    candidate_id=candidate_id,
                    decision=ReviewDecisionKind.EDIT_ACCEPT,
                    edited_value={"kind": "skill", "name": "Python"},
                ),
                CandidateReviewDecision(
                    candidate_id=candidate_id, decision=ReviewDecisionKind.ACCEPT
                ),
            ],
        )


def test_unique_candidate_ids_with_full_coverage_are_valid() -> None:
    command = FinalizeResumeImportCommand(
        expected_profile_version=0,
        decisions=[
            CandidateReviewDecision(candidate_id=uuid.uuid4(), decision=ReviewDecisionKind.ACCEPT),
            CandidateReviewDecision(candidate_id=uuid.uuid4(), decision=ReviewDecisionKind.REJECT),
        ],
    )
    assert len(command.decisions) == 2


def test_decisions_count_cannot_exceed_the_candidate_cap() -> None:
    from tapjob_api.resume_import.limits import MAX_CANDIDATES_PER_IMPORT

    with pytest.raises(ValidationError):
        FinalizeResumeImportCommand(
            expected_profile_version=0,
            decisions=[
                CandidateReviewDecision(
                    candidate_id=uuid.uuid4(), decision=ReviewDecisionKind.REJECT
                )
                for _ in range(MAX_CANDIDATES_PER_IMPORT + 1)
            ],
        )


def test_finalize_command_accepts_camel_case_alias_wire_shape() -> None:
    command = FinalizeResumeImportCommand.model_validate(
        {
            "expectedProfileVersion": 2,
            "decisions": [{"candidateId": str(uuid.uuid4()), "decision": "reject"}],
        }
    )
    assert command.expected_profile_version == 2
    assert command.decisions[0].decision == ReviewDecisionKind.REJECT
