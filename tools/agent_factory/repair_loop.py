"""TapJob Agent Factory — Internal repair loop budget manager and escalation controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MAX_INTERNAL_REPAIR_LOOPS = 5


class RepairPhase(StrEnum):
    """Phases of the internal autonomous repair loop."""

    INITIALIZED = "INITIALIZED"
    IMPLEMENTATION = "IMPLEMENTATION"
    TESTING = "TESTING"
    QA_VERIFYING = "QA_VERIFYING"
    INTERNAL_REVIEW = "INTERNAL_REVIEW"
    CORRECTIONS = "CORRECTIONS"
    CI_MONITORING = "CI_MONITORING"
    READY_FOR_EXTERNAL_REVIEW = "READY_FOR_EXTERNAL_REVIEW"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"


class ReviewVerdict(StrEnum):
    """Verdicts issued during internal review."""

    APPROVE = "APPROVE"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"


class RepairLoopError(Exception):
    """Base exception for repair loop budget violations."""


class ReviewerSeparationError(RepairLoopError):
    """Raised when an agent attempts to review or approve its own changes."""


class BudgetExhaustedError(RepairLoopError):
    """Raised when the repair loop budget exceeds MAX_INTERNAL_REPAIR_LOOPS."""


@dataclass
class RepairIterationRecord:
    """Record of a single repair cycle."""

    iteration_index: int
    implementer_role: str
    reviewer_role: str
    test_passed: bool
    verdict: ReviewVerdict
    findings: list[str] = field(default_factory=list)
    iteration_head_sha: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class RepairLoopController:
    """Controls the autonomous self-correction budget and guarantees human escalation."""

    def __init__(
        self,
        task_id: str,
        implementer_role: str,
        reviewer_role: str,
        max_loops: int = MAX_INTERNAL_REPAIR_LOOPS,
    ) -> None:
        if (
            not isinstance(max_loops, int)
            or type(max_loops) is not int
            or max_loops < 1
            or max_loops > MAX_INTERNAL_REPAIR_LOOPS
        ):
            raise ValueError(
                f"max_loops must be an integer between 1 and {MAX_INTERNAL_REPAIR_LOOPS}, "
                f"got {max_loops!r}"
            )

        if implementer_role == reviewer_role:
            raise ReviewerSeparationError(
                f"Role '{implementer_role}' cannot review its own code. "
                "Separation of duties violation."
            )

        self.task_id = task_id
        self.implementer_role = implementer_role
        self.reviewer_role = reviewer_role
        self.max_loops = max_loops
        self.current_iteration = 0
        self.current_phase = RepairPhase.INITIALIZED
        self.records: list[RepairIterationRecord] = []

    def can_merge(self) -> bool:
        """Autonomous agents have ZERO merge authority under baseline governance."""
        return False

    def start_iteration(self) -> int:
        """Begin a new repair cycle and enforce loop bounds."""
        if self.current_iteration >= self.max_loops:
            self.current_phase = RepairPhase.ESCALATE_TO_HUMAN
            raise BudgetExhaustedError(
                f"Repair budget of {self.max_loops} loops exhausted for task '{self.task_id}'. "
                "Must escalate to human operator."
            )

        self.current_iteration += 1
        self.current_phase = RepairPhase.IMPLEMENTATION
        return self.current_iteration

    def record_review_result(
        self,
        test_passed: bool,
        verdict: ReviewVerdict,
        reviewer_role: str | None = None,
        findings: list[str] | None = None,
        iteration_head_sha: str = "",
    ) -> RepairPhase:
        """Record the outcome of the QA and internal review step."""
        actual_reviewer = reviewer_role or self.reviewer_role
        if actual_reviewer == self.implementer_role:
            raise ReviewerSeparationError(
                f"Author '{self.implementer_role}' cannot issue review verdict. "
                "Separation of duties violation."
            )

        record = RepairIterationRecord(
            iteration_index=self.current_iteration,
            implementer_role=self.implementer_role,
            reviewer_role=actual_reviewer,
            test_passed=test_passed,
            verdict=verdict,
            findings=findings or [],
            iteration_head_sha=iteration_head_sha,
        )
        self.records.append(record)

        if test_passed and verdict == ReviewVerdict.APPROVE:
            self.current_phase = RepairPhase.CI_MONITORING
            return self.current_phase

        # If tests failed or changes required:
        if self.current_iteration >= self.max_loops:
            self.current_phase = RepairPhase.ESCALATE_TO_HUMAN
        else:
            self.current_phase = RepairPhase.CORRECTIONS

        return self.current_phase

    def mark_ci_passed(self) -> RepairPhase:
        """Mark CI as green and transition to READY_FOR_EXTERNAL_REVIEW."""
        if self.current_phase != RepairPhase.CI_MONITORING:
            raise RepairLoopError(
                f"Cannot mark CI passed when in phase '{self.current_phase}'. "
                "Must be in CI_MONITORING."
            )
        self.current_phase = RepairPhase.READY_FOR_EXTERNAL_REVIEW
        return self.current_phase
