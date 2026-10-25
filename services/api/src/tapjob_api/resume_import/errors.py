"""Normalized WP1.3 failure taxonomy (docs/adr/0011-resume-import.md "error taxonomy"). A raw
parser/library exception never reaches `router.py`/the client -- every failure path here is caught
and mapped to one of these, then to a stable `ErrorCode`/reason code
(docs/ENGINEERING_STANDARDS.md §8).
"""

from datetime import datetime
from typing import Any


class ResumeImportError(Exception):
    """Base for every WP1.3 domain error."""


class UnsupportedDocumentTypeError(ResumeImportError):
    """Declared/sniffed type is not PDF or DOCX (docs/adr/0011-...md "file upload boundary")."""


class DocumentTooLargeError(ResumeImportError):
    """Upload exceeds `limits.MAX_UPLOAD_BYTES`, or a DOCX container exceeds its own uncompressed-
    size/member-count/compression-ratio bounds (docs/adr/0011-...md "DOCX extraction")."""


class MalformedDocumentError(ResumeImportError):
    """Container/structure does not match its declared type (e.g. a `.pdf` without a PDF signature,
    a `.docx` that is not a well-formed ZIP/OPC package)."""


class EncryptedDocumentError(ResumeImportError):
    """Password-protected/encrypted document -- TapJob never attempts password recovery
    (docs/adr/0011-...md "password-protected / encrypted documents")."""


class TextNotExtractableError(ResumeImportError):
    """No meaningful text layer was found (e.g. a scanned/image-only PDF) -- OCR is explicitly out
    of scope for WP1.3 (docs/adr/0011-...md "scanned PDF / OCR")."""


class NoReviewableCandidatesError(ResumeImportError):
    """Text WAS extracted, but the deterministic parser recognized zero reviewable candidates from
    it (round-1 independent review "Finding 8") -- a KNOWN, normalized domain outcome, never
    `INTERNAL_ERROR`. Without this, an import could reach `READY_FOR_REVIEW` with zero candidates,
    which `FinalizeResumeImportCommand.decisions`'s own `min_length=1` makes impossible to ever
    finalize -- a permanently stuck state. Terminal `FAILED`, same as any other known parsing
    outcome; never retried automatically."""


class ResumeImportNotFoundError(ResumeImportError):
    """Ownership-scoped lookup failure -- indistinguishable from "belongs to another user"
    (docs/adr/0011-...md "ownership / IDOR"), same discipline as WP1.2's
    `ProfileFactNotFoundError`."""


class ResumeImportNotReadyError(ResumeImportError):
    """`finalize` called while the import is not yet `READY_FOR_REVIEW` (still queued/processing,
    or already failed)."""


class CandidateDecisionCoverageError(ResumeImportError):
    """`decisions` did not cover exactly the set of `PENDING` candidates for this import --
    (docs/adr/0011-...md "review decisions": "no acceptance by omission"). Also raised when a
    decision references a candidate id that does not belong to this import at all."""


class CandidateValueInvalidError(ResumeImportError):
    """An `edit_accept` decision's `editedValue` failed validation against the candidate's own
    canonical shape (docs/adr/0011-...md "review decisions": edited values go through the SAME
    `FactValue`/top-level validation as any other mutation, never arbitrary JSON)."""


class ImportLeaseActiveError(ResumeImportError):
    """Raised by `process_resume_import` when a claim attempt finds the row already `PROCESSING`
    under a STILL-ACTIVE lease (round-1 independent review "Finding 2") -- a genuinely different
    delivery/attempt may actually be running right now. Never silently treated as a successful
    no-op: the Celery task wrapper (`tasks.py`) turns this into an explicit, bounded retry.

    Also raised, regardless of `processing_attempts` (round-2 independent review "Finding 2"), when
    a DUPLICATE delivery observes attempt #N genuinely still actively running under its own valid
    lease -- never marking the row terminally `FAILED` out from under a legitimate in-flight
    attempt just because the attempt BUDGET happens to already be exhausted.

    Carries `retry_at` -- the active lease's own real `processing_lease_expires_at` -- so the
    Celery task wrapper can compute a countdown from the ACTUAL remaining lease lifetime (round-2
    independent review "Finding 1"), rather than a fixed guess that could retry either needlessly
    early (wasted attempts) or, worse, exhaust its own retry budget before the lease genuinely
    expires."""

    def __init__(self, *, retry_at: datetime) -> None:
        self.retry_at = retry_at
        super().__init__(
            f"resume import has an active processing lease until {retry_at.isoformat()}"
        )


class TransientProcessingError(ResumeImportError):
    """Raised by `process_resume_import` for a failure presumed RECOVERABLE -- a storage read
    error, OR a retrieved object that does not match its own recorded `byte_size`/
    `checksum_sha256` (round-2 independent review "Hardening 9": a corrupted/truncated read is at
    least as plausible an explanation as genuine tampering, and a retry is cheap and safe either
    way) -- never a document-CONTENT problem, which is always a terminal, normalized `FAILED`
    instead. The row is released back to `QUEUED` (bounded by `limits.MAX_PROCESSING_ATTEMPTS`,
    already consumed at claim time) before this is raised, so an immediate bounded Celery retry can
    reclaim it right away rather than waiting out the full processing lease
    (docs/adr/0011-...md "worker idempotency"). A PERSISTENT mismatch (e.g. genuine, permanent
    tampering/corruption) still converges to terminal `FAILED` once `MAX_PROCESSING_ATTEMPTS` is
    exhausted, via the exact same bounded-retry machinery a flaky storage backend already uses --
    never a second, parallel retry mechanism."""


class CrossDocumentConflictError(ResumeImportError):
    """Raised when finalize_resume_import encounters unresolved cross-document conflicts between
    accepted candidates and existing active profile facts."""

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        self.conflicts = conflicts
        super().__init__(
            f"Cannot finalize resume import: {len(conflicts)} "
            "cross-document conflict(s) require review"
        )
