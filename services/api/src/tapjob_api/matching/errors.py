"""Exceptions for WP2.2 Matching Engine."""


class DeterministicMatchMismatchError(Exception):
    """Raised when concurrent evaluation converges on a divergent digest (invariant violation)."""

    def __init__(self, expected_digest: str, found_digest: str) -> None:
        super().__init__(
            f"Deterministic match convergence mismatch: "
            f"expected {expected_digest}, found {found_digest}"
        )
        self.expected_digest = expected_digest
        self.found_digest = found_digest


class MatchJobNotFoundError(Exception):
    """Raised when job is not found, closed, expired, or private to another user."""
