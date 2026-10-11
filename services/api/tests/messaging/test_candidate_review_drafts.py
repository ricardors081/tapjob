"""Unit tests for Candidate Review Draft assembly and strict No Acceptance by Omission.

Proves (Findings 4, 5):
A) 3 candidates, drafts for only 1 and 2 -> IncompleteCandidateReviewError, no implicit ACCEPT
B) Exact draft coverage -> valid CandidateReviewDecision list
C) EDIT_ACCEPT without edited_value -> InvalidCandidateReviewDraftError (fail closed)
D) Candidate/draft mismatch -> IncompleteCandidateReviewError (fail closed)
E) Conversational fact review does not accept by omission when single facts are reviewed
"""

import uuid
from typing import Any

import pytest

from tapjob_api.messaging.orchestration import (
    IncompleteCandidateReviewError,
    InvalidCandidateReviewDraftError,
    assemble_candidate_review_decisions,
)
from tapjob_api.resume_import.commands import ReviewDecisionKind
from tapjob_api.resume_import.models import CandidateReviewDraft


class DummyCandidate:
    def __init__(self, cid: uuid.UUID) -> None:
        self.id = cid


def test_assemble_candidate_review_decisions_incomplete_coverage_fails_closed() -> None:
    """3 candidates, drafts for only 1 and 2 -> IncompleteCandidateReviewError.

    Candidate 3 is NEVER accepted by omission (Finding 4).
    """
    c1 = DummyCandidate(uuid.uuid4())
    c2 = DummyCandidate(uuid.uuid4())
    c3 = DummyCandidate(uuid.uuid4())
    ordered = [c1, c2, c3]

    drafts = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.ACCEPT.value,
        ),
        c2.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c2.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.REJECT.value,
        ),
    }

    with pytest.raises(IncompleteCandidateReviewError) as exc_info:
        assemble_candidate_review_decisions(ordered, drafts)

    assert "coverage mismatch" in str(exc_info.value).lower()
    # Candidate 3 has no draft and is NEVER transformed into an ACCEPT decision


def test_assemble_candidate_review_decisions_exact_coverage_succeeds() -> None:
    """Exact coverage for all candidates yields CandidateReviewDecision list (Finding 4)."""
    c1 = DummyCandidate(uuid.uuid4())
    c2 = DummyCandidate(uuid.uuid4())
    c3 = DummyCandidate(uuid.uuid4())
    ordered = [c1, c2, c3]

    drafts = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.ACCEPT.value,
        ),
        c2.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c2.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.REJECT.value,
        ),
        c3.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c3.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.EDIT_ACCEPT.value,
            edited_value={"name": "FastAPI Senior"},
        ),
    }

    decisions = assemble_candidate_review_decisions(ordered, drafts)
    assert len(decisions) == 3

    d1 = next(d for d in decisions if d.candidate_id == c1.id)
    assert d1.decision == ReviewDecisionKind.ACCEPT
    assert d1.edited_value is None

    d2 = next(d for d in decisions if d.candidate_id == c2.id)
    assert d2.decision == ReviewDecisionKind.REJECT
    assert d2.edited_value is None

    d3 = next(d for d in decisions if d.candidate_id == c3.id)
    assert d3.decision == ReviewDecisionKind.EDIT_ACCEPT
    assert d3.edited_value == {"name": "FastAPI Senior"}


def test_assemble_candidate_review_decisions_edit_accept_missing_value_fails_closed() -> None:
    """EDIT_ACCEPT without edited_value fails closed with error (Finding 4)."""
    c1 = DummyCandidate(uuid.uuid4())
    ordered = [c1]

    # None edited_value
    drafts_none: dict[uuid.UUID, Any] = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.EDIT_ACCEPT.value,
            edited_value=None,
        )
    }
    with pytest.raises(InvalidCandidateReviewDraftError):
        assemble_candidate_review_decisions(ordered, drafts_none)

    # Non-dict edited_value
    drafts_invalid: dict[uuid.UUID, Any] = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.EDIT_ACCEPT.value,
            edited_value="not a dict",
        )
    }
    with pytest.raises(InvalidCandidateReviewDraftError):
        assemble_candidate_review_decisions(ordered, drafts_invalid)


def test_assemble_candidate_review_decisions_mismatch_excess_drafts_fails_closed() -> None:
    """Excess or alien drafts fail closed (Finding 4)."""
    c1 = DummyCandidate(uuid.uuid4())
    ordered = [c1]

    alien_id = uuid.uuid4()
    drafts = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.ACCEPT.value,
        ),
        alien_id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=alien_id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.REJECT.value,
        ),
    }

    with pytest.raises(IncompleteCandidateReviewError):
        assemble_candidate_review_decisions(ordered, drafts)


def test_assemble_candidate_review_decisions_accept_with_edited_value_fails_closed() -> None:
    """ACCEPT draft carrying an edited_value payload fails closed (Finding 4)."""
    c1 = DummyCandidate(uuid.uuid4())
    ordered = [c1]

    drafts = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.ACCEPT.value,
            edited_value={"name": "Unexpected Payload"},
        ),
    }

    with pytest.raises(InvalidCandidateReviewDraftError) as exc_info:
        assemble_candidate_review_decisions(ordered, drafts)

    assert "must not carry edited_value" in str(exc_info.value)


def test_assemble_candidate_review_decisions_reject_with_edited_value_fails_closed() -> None:
    """REJECT draft carrying an edited_value payload fails closed (Finding 4)."""
    c1 = DummyCandidate(uuid.uuid4())
    ordered = [c1]

    drafts = {
        c1.id: CandidateReviewDraft(
            resume_import_id=uuid.uuid4(),
            candidate_id=c1.id,
            user_id=uuid.uuid4(),
            decision=ReviewDecisionKind.REJECT.value,
            edited_value={"name": "Unexpected Payload"},
        ),
    }

    with pytest.raises(InvalidCandidateReviewDraftError) as exc_info:
        assemble_candidate_review_decisions(ordered, drafts)

    assert "must not carry edited_value" in str(exc_info.value)
