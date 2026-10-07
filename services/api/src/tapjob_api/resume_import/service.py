"""Use-case layer for WP1.3 (docs/adr/0011-resume-import.md, D-053). Channel-first, same discipline
as `tapjob_api.career_profile.service`: every function here is a plain, ownership-scoped call over
an already-authenticated `User` -- nothing accepts a caller-supplied `user_id`, nothing here knows
about HTTP, and a future WhatsApp/mobile channel calls the exact same functions `router.py` does.

Transaction convention: `create_resume_import` commits its own row (so the HTTP response can return
immediately and the worker task is only enqueued AFTER that commit -- never before, or a fast
worker could look up a row the API transaction has not actually persisted yet).
`process_resume_import` and `finalize_resume_import` each own their own commit boundary too, since
each represents a single atomic unit of work a caller (the Celery task, the HTTP router) needs to
know succeeded or failed as a whole.
"""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.career_profile import service as career_profile_service
from tapjob_api.career_profile.models import FactSourceKind, FactStatus, FactType, ProfileFact
from tapjob_api.identity.models import User
from tapjob_api.ports.resume_parser import ExtractedFact
from tapjob_api.resume_import.candidates import (
    CandidateKind,
    ValidatedCandidate,
    validate_candidate,
)
from tapjob_api.resume_import.commands import FinalizeResumeImportCommand, ReviewDecisionKind
from tapjob_api.resume_import.errors import (
    CandidateDecisionCoverageError,
    CandidateValueInvalidError,
    DocumentTooLargeError,
    EncryptedDocumentError,
    ImportLeaseActiveError,
    MalformedDocumentError,
    NoReviewableCandidatesError,
    ResumeImportError,
    ResumeImportNotFoundError,
    ResumeImportNotReadyError,
    TextNotExtractableError,
    TransientProcessingError,
    UnsupportedDocumentTypeError,
)
from tapjob_api.resume_import.extraction import extract_docx_text, extract_pdf_text
from tapjob_api.resume_import.file_validation import PDF_MEDIA_TYPE, DocumentKind, validate_upload
from tapjob_api.resume_import.limits import (
    DISPATCH_RECOVERY_THRESHOLD_SECONDS,
    MAX_CANDIDATES_PER_IMPORT,
    MAX_ORIGINAL_FILENAME_CHARS,
    MAX_PROCESSING_ATTEMPTS,
    PROCESSING_LEASE_SECONDS,
)
from tapjob_api.resume_import.models import (
    CandidateReviewState,
    ImportFailureCode,
    ImportStatus,
    Resume,
    ResumeImport,
    ResumeImportCandidate,
    ResumeVersion,
)
from tapjob_api.resume_import.parsing import PARSER_IDENTITY, parse_resume_text
from tapjob_api.resume_import.schemas import MasterResumeSnapshot
from tapjob_api.resume_import.storage import (
    ObjectStorageError,
    ObjectStoragePort,
    compute_checksum,
    new_storage_key,
)

_FAILURE_CODE_BY_ERROR: dict[type[Exception], ImportFailureCode] = {
    UnsupportedDocumentTypeError: ImportFailureCode.UNSUPPORTED_DOCUMENT_TYPE,
    DocumentTooLargeError: ImportFailureCode.DOCUMENT_TOO_LARGE,
    MalformedDocumentError: ImportFailureCode.MALFORMED_DOCUMENT,
    EncryptedDocumentError: ImportFailureCode.ENCRYPTED_DOCUMENT,
    TextNotExtractableError: ImportFailureCode.TEXT_NOT_EXTRACTABLE,
    NoReviewableCandidatesError: ImportFailureCode.NO_REVIEWABLE_CANDIDATES,
}


# -- upload / lookup ---------------------------------------------------------------------------


def create_resume_import(
    session: Session,
    *,
    user: User,
    content: bytes,
    original_filename: str,
    declared_content_type: str,
    storage: ObjectStoragePort,
) -> ResumeImport:
    """Validates the upload (docs/adr/0011-...md "file security") BEFORE anything is stored.
    `original_filename` is retained only as display metadata, truncated to a bound -- NEVER used to
    derive `storage_key` (docs/adr/0011-...md "import record")."""
    validated = validate_upload(
        content=content, declared_content_type=declared_content_type, filename=original_filename
    )
    extension = ".pdf" if validated.kind is DocumentKind.PDF else ".docx"
    storage_key = new_storage_key(user_id=user.id, extension=extension)
    stored = storage.put(
        storage_key=storage_key, content=content, content_type=validated.media_type
    )

    resume_import = ResumeImport(
        user_id=user.id,
        status=ImportStatus.QUEUED,
        original_filename=original_filename[:MAX_ORIGINAL_FILENAME_CHARS],
        media_type=validated.media_type,
        byte_size=stored.byte_size,
        checksum_sha256=stored.checksum_sha256,
        storage_key=stored.storage_key,
    )
    session.add(resume_import)
    try:
        session.flush()
    except Exception:
        # Compensating delete: a DB failure after a successful storage write must never leave an
        # orphaned, unreferenced object behind forever (docs/adr/0011-...md "object storage
        # failure semantics"). Best-effort only -- a process crash between this delete call and
        # its completion could still leak the object; an accepted, documented residual risk, not
        # pretended away.
        with contextlib.suppress(ObjectStorageError):
            storage.delete(storage_key=stored.storage_key)
        raise
    return resume_import


def find_owned_import(session: Session, *, user: User, import_id: uuid.UUID) -> ResumeImport | None:
    """Ownership-scoped (docs/adr/0011-...md "ownership / IDOR") -- a resume import belonging to a
    different user is indistinguishable from a nonexistent one."""
    return session.scalar(
        select(ResumeImport).where(ResumeImport.id == import_id, ResumeImport.user_id == user.id)
    )


def should_redispatch_processing(
    resume_import: ResumeImport, *, now: datetime | None = None
) -> bool:
    """True when a `QUEUED` import has sat unclaimed long enough that its most recent dispatch
    (either the original one at upload time, or the last sweep redispatch) is presumed lost
    (round-1 independent review "Finding 3": `create_resume_import`'s row, durable at `QUEUED` in
    the SAME transaction as its own creation, IS the recorded intent to process -- no separate
    outbox table is needed).

    The ONLY caller is now the server-owned sweep (`sweep_stale_queued_imports`,
    `tasks.sweep_stale_resume_import_dispatches_task`) -- round-2 independent review "Finding 4":
    this is no longer triggered as a side effect of a client's own `GET` poll, which previously (a)
    gave an indefinitely stuck row no recovery path at all if the client simply stopped polling,
    and (b) made an ordinary read cause a broker-dispatching side effect, with no persisted
    backoff, every single time it was called.

    `last_dispatch_attempt_at` (falling back to `created_at` when never redispatched) is this
    function's own bounded-backoff bookkeeping: a row the sweep already redispatched is not
    considered stale again until ANOTHER full `DISPATCH_RECOVERY_THRESHOLD_SECONDS` has passed, so
    a persistent broker outage causes one redispatch attempt per threshold period, never a hot
    loop. The worker's own idempotent claim (`claim_import_for_processing`) makes a duplicate
    dispatch always safe regardless, so this is purely a recovery signal, never itself a source of
    double-processing. Pure/no I/O."""
    if resume_import.status != ImportStatus.QUEUED:
        return False
    current = now or datetime.now(UTC)
    last_signal = resume_import.last_dispatch_attempt_at or resume_import.created_at
    age_seconds = (current - last_signal).total_seconds()
    return age_seconds > DISPATCH_RECOVERY_THRESHOLD_SECONDS


def sweep_stale_queued_imports(session: Session, *, now: datetime | None = None) -> list[uuid.UUID]:
    """Server-owned dispatch-recovery sweep (round-2 independent review "Finding 4"): scans for
    every import that looks stuck with no active owner and stamps its own
    `last_dispatch_attempt_at` bookkeeping (so the SAME row is not re-selected again before another
    full backoff period passes), returning the ids a caller should redispatch. This function itself
    never calls Celery/`send_task` -- it has no Celery dependency at all, same discipline as every
    other function in this module (`tasks.sweep_stale_resume_import_dispatches_task` is the one
    caller, and the one place that actually enqueues anything, exactly mirroring
    `router._dispatch_processing`'s own best-effort-with-logging pattern for the worker side).

    Two distinct candidate shapes, both bounded, both safe to redispatch via the worker's own
    idempotent claim:
    1. `QUEUED` and `should_redispatch_processing` judges it stale (the original dispatch, or the
       last sweep redispatch, is presumed lost).
    2. `PROCESSING` with an EXPIRED lease -- the backstop for the residual edge case where a Celery
       task's own bounded retry chain (`tasks.process_resume_import_task`) gave up entirely (its
       own `MaxRetriesExceededError`) before ever reclaiming the row itself: nothing else would
       ever redispatch it again otherwise. Throttled by the SAME `last_dispatch_attempt_at`
       bookkeeping (never `created_at`, which has no bearing on a row that was already claimed at
       least once).

    Bounded by construction: only ever considers rows not already in a terminal/actively-claimed
    state (a real, naturally small set in practice), so this never scans an unbounded table."""
    current = now or datetime.now(UTC)
    candidates = list(
        session.scalars(
            select(ResumeImport).where(
                or_(
                    ResumeImport.status == ImportStatus.QUEUED,
                    and_(
                        ResumeImport.status == ImportStatus.PROCESSING,
                        ResumeImport.processing_lease_expires_at.is_not(None),
                        ResumeImport.processing_lease_expires_at < current,
                    ),
                )
            )
        )
    )
    stale_ids: list[uuid.UUID] = []
    for candidate in candidates:
        if candidate.status == ImportStatus.QUEUED:
            if not should_redispatch_processing(candidate, now=current):
                continue
        else:
            last_signal = candidate.last_dispatch_attempt_at
            if (
                last_signal is not None
                and (current - last_signal).total_seconds() <= DISPATCH_RECOVERY_THRESHOLD_SECONDS
            ):
                continue
        stale_ids.append(candidate.id)
        session.execute(
            update(ResumeImport)
            .where(ResumeImport.id == candidate.id)
            .values(last_dispatch_attempt_at=current)
        )
    session.commit()
    return stale_ids


def list_candidates(
    session: Session, *, resume_import: ResumeImport
) -> list[ResumeImportCandidate]:
    return list(
        session.scalars(
            select(ResumeImportCandidate)
            .where(ResumeImportCandidate.resume_import_id == resume_import.id)
            .order_by(ResumeImportCandidate.created_at.asc())
        )
    )


# -- worker-side processing ---------------------------------------------------------------------


def claim_import_for_processing(
    session: Session, *, import_id: uuid.UUID
) -> tuple[ResumeImport, uuid.UUID] | None:
    """Atomic conditional `UPDATE` claiming EITHER a fresh `QUEUED` row OR an already-`PROCESSING`
    row whose lease has expired -- a lease, not a one-way latch (round-1 independent review
    "Finding 2"): a worker that crashes after claiming a row leaves it reclaimable once
    `processing_lease_expires_at` passes, rather than stuck in `PROCESSING` forever. Bounded by
    `processing_attempts < MAX_PROCESSING_ATTEMPTS` so a pathological document cannot be reclaimed
    forever either. Returns `None` when no row matches -- `process_resume_import` distinguishes
    WHY (already terminal / attempts exhausted with no active owner / a genuinely still-active
    lease) itself.

    On success, returns `(row, lease_token)` -- `lease_token` is a FRESH, random value generated
    HERE, in Python, before the query even runs (round-2 independent review "Finding 2"), never
    read back from the ORM row afterward: this sidesteps the exact SQLAlchemy `expire_on_commit`
    hazard `processing_attempts`-based fencing already had to work around, by construction, since
    the caller already holds the real value as a plain local variable rather than an attribute that
    could silently refresh to someone else's newer claim. This token is the real OWNERSHIP
    identity for every subsequent state transition (`_owner_transition`) -- a fresh one is
    generated on EVERY successful claim, so a previous holder's token is always invalidated the
    moment anyone else reclaims the row, regardless of `processing_attempts` coincidences."""
    now = datetime.now(UTC)
    token = uuid.uuid4()
    updated = session.execute(
        update(ResumeImport)
        .where(
            ResumeImport.id == import_id,
            ResumeImport.processing_attempts < MAX_PROCESSING_ATTEMPTS,
            or_(
                ResumeImport.status == ImportStatus.QUEUED,
                and_(
                    ResumeImport.status == ImportStatus.PROCESSING,
                    ResumeImport.processing_lease_expires_at.is_not(None),
                    ResumeImport.processing_lease_expires_at < now,
                ),
            ),
        )
        .values(
            status=ImportStatus.PROCESSING,
            processing_lease_expires_at=now + timedelta(seconds=PROCESSING_LEASE_SECONDS),
            processing_attempts=ResumeImport.processing_attempts + 1,
            processing_lease_token=token,
        )
        .returning(ResumeImport)
    )
    row = updated.scalars().one_or_none()
    if row is None:
        return None
    return row, token


def _owner_transition(
    session: Session, *, import_id: uuid.UUID, fencing_token: uuid.UUID, values: dict[str, Any]
) -> bool:
    """The ONE place every state transition OUT of `PROCESSING` happens (round-2 independent
    review "Finding 2": "the fencing needs to identify the POSSE of that lease" -- never only an
    attempt-count coincidence). Requires ALL THREE, atomically, in a single conditional `UPDATE`:
    the row's id, `status == PROCESSING` (never applies to an already-terminal/requeued row, even
    if a token somehow matched), and `processing_lease_token == fencing_token` (the exact value
    THIS caller's own claim generated). A worker whose lease expired while it was still slowly (not
    dead) running, and which is only now trying to write its outcome, presents its OWN now-stale
    token -- which a reclaiming attempt's fresh claim has already overwritten -- so this match
    fails and the stale write becomes a safe no-op the caller must `session.rollback()` on.
    Returns `False` when the fencing check did not match."""
    result = session.execute(
        update(ResumeImport)
        .where(
            ResumeImport.id == import_id,
            ResumeImport.status == ImportStatus.PROCESSING,
            ResumeImport.processing_lease_token == fencing_token,
        )
        .values(**values)
        .returning(ResumeImport.id)
    )
    return result.scalar_one_or_none() is not None


def _fail_exhausted(
    session: Session,
    *,
    import_id: uuid.UUID,
    expected_attempts: int,
    expected_status: ImportStatus,
    failure_code: ImportFailureCode = ImportFailureCode.INTERNAL_ERROR,
) -> bool:
    """Terminally fails a row with NO currently active owner at all (round-2 independent review
    "Finding 2"): either `processing_attempts` is exhausted with the row sitting `QUEUED` (every
    attempt already consumed, nothing currently claimed), or `PROCESSING` with an EXPIRED lease and
    exhausted attempts (no further reclaim is even possible). Fenced on `(processing_attempts,
    status)` rather than a lease token, because by definition there IS no active lease/token to
    fence on in this path -- two duplicate deliveries racing to fail the SAME exhausted row both
    observe the identical snapshot and either one succeeding is an equally correct, idempotent
    outcome (the `.values()` they'd both apply are identical)."""
    result = session.execute(
        update(ResumeImport)
        .where(
            ResumeImport.id == import_id,
            ResumeImport.processing_attempts == expected_attempts,
            ResumeImport.status == expected_status,
        )
        .values(
            status=ImportStatus.FAILED,
            failure_code=failure_code,
            processing_lease_expires_at=None,
            processing_lease_token=None,
        )
        .returning(ResumeImport.id)
    )
    return result.scalar_one_or_none() is not None


def process_resume_import(
    session: Session, *, import_id: uuid.UUID, storage: ObjectStoragePort
) -> None:
    """Retry-safe via a processing LEASE, not a one-way latch (docs/adr/0011-...md "worker
    idempotency", round-1 independent review "Finding 2", round-2 independent review "Finding 2").
    Distinct outcomes when the claim itself does not succeed: (1) the row is already terminal
    (`READY_FOR_REVIEW`/`FINALIZED`/`FAILED`) -- a genuine safe no-op; (2) a DIFFERENT delivery/
    attempt is presumed genuinely still running under a STILL-ACTIVE lease, REGARDLESS of whether
    `processing_attempts` happens to already be at its bound -- `ImportLeaseActiveError` is raised
    so the Celery task wrapper retries later, never silently swallowed as success and never
    marking a row `FAILED` out from under a legitimate in-flight attempt just because the attempt
    BUDGET is exhausted; (3) otherwise, there is no active owner at all (attempts exhausted, lease
    expired or never held) -- this delivery marks the row terminally `FAILED` itself, since no
    future claim could ever succeed either.

    A known `ResumeImportError` (bad document, or `NoReviewableCandidatesError`) is a normal,
    deterministic outcome -- recorded as terminal `FAILED` with a normalized reason code, never
    retried. A `TransientProcessingError` (storage failure, OR a retrieved object failing its own
    size/checksum integrity check -- round-2 independent review "Hardening 9") releases the row
    back to `QUEUED` for a bounded, immediate retry instead. A truly unexpected exception
    (including `celery.exceptions.SoftTimeLimitExceeded`, a plain `Exception` subclass in this
    pinned Celery version -- round-2 independent review "Finding 3") is recorded as terminal
    `FAILED` first (so a retry is a safe no-op, never a repeat attempt against the same
    pathological input) and then re-raised, purely for observability (Sentry/Celery logs) -- it
    never leaves the row in an ambiguous, non-terminal state."""
    claim_result = claim_import_for_processing(session, import_id=import_id)
    if claim_result is None:
        session.commit()
        current = session.get(ResumeImport, import_id)
        if current is None or current.status in (
            ImportStatus.READY_FOR_REVIEW,
            ImportStatus.FINALIZED,
            ImportStatus.FAILED,
        ):
            return
        now = datetime.now(UTC)
        active_lease_expiry = current.processing_lease_expires_at
        if (
            current.status == ImportStatus.PROCESSING
            and active_lease_expiry is not None
            and active_lease_expiry > now
        ):
            # A genuinely different, currently active owner holds this lease right now (round-2
            # independent review "Finding 2") -- never fail it out from under them just because
            # the attempt budget happens to already be exhausted; wait and check back instead.
            raise ImportLeaseActiveError(retry_at=active_lease_expiry)
        if current.processing_attempts >= MAX_PROCESSING_ATTEMPTS:
            # No active owner (status is QUEUED with every attempt consumed, or PROCESSING with an
            # EXPIRED lease and no budget left to reclaim it) -- terminally stuck, fail it.
            _fail_exhausted(
                session,
                import_id=import_id,
                expected_attempts=current.processing_attempts,
                expected_status=current.status,
            )
            session.commit()
            return
        # Defensive, not expected to be reachable in practice: Postgres row-level locking means a
        # concurrent winning claim's transaction is fully committed (or rolled back) before this
        # re-`SELECT` can observe it, so by the time `current` is read, it already reflects either
        # a terminal state, an active lease, or an exhausted-attempts state -- all handled above.
        # Never silently return as if this were a safe no-op; treat it the same as an active lease
        # so a redelivery keeps checking back rather than the import vanishing from view.
        raise ImportLeaseActiveError(retry_at=active_lease_expiry or now)

    claimed, fencing_token = claim_result
    # Captured as a plain int, immediately, from the SAME already-claimed row -- used only to
    # decide fail-vs-retry bounds below, never as the fencing identity itself any more (that is
    # `fencing_token`, generated fresh in Python by `claim_import_for_processing` itself, so it
    # never depends on re-reading a possibly-expired ORM attribute either).
    attempt_number: int = claimed.processing_attempts
    session.commit()  # the PROCESSING claim/lease is durable before any slow work runs

    try:
        try:
            content = storage.get(storage_key=claimed.storage_key)
        except ObjectStorageError as error:
            raise TransientProcessingError from error
        if (
            len(content) != claimed.byte_size
            or compute_checksum(content) != claimed.checksum_sha256
        ):
            # The retrieved object does not match what was actually uploaded -- at least as
            # plausibly a transient truncated/corrupted read as genuine tampering, and a retry is
            # cheap and safe either way (round-2 independent review "Hardening 9"). Reuses the
            # EXACT same bounded-retry machinery a flaky storage backend already has: a PERSISTENT
            # mismatch still converges to terminal FAILED once attempts are exhausted, never a
            # second, parallel retry mechanism.
            raise TransientProcessingError(
                "retrieved object does not match its recorded byte_size/checksum_sha256"
            )
        text = (
            extract_pdf_text(content)
            if claimed.media_type == PDF_MEDIA_TYPE
            else extract_docx_text(content)
        )
        extracted = parse_resume_text(text)
        validated: list[ValidatedCandidate] = []
        for fact in extracted:
            candidate = validate_candidate(fact)
            if candidate is not None:
                validated.append(candidate)
            if len(validated) >= MAX_CANDIDATES_PER_IMPORT:
                break
        if not validated:
            # Text extraction succeeded, but nothing reviewable was recognized -- a KNOWN domain
            # outcome (round-1 independent review "Finding 8"), never READY_FOR_REVIEW: a caller
            # can never finalize an import with zero pending candidates
            # (`FinalizeResumeImportCommand.decisions`'s own `min_length=1`), so reaching that
            # state would be a permanently stuck import.
            raise NoReviewableCandidatesError
        for candidate in validated:
            session.add(
                ResumeImportCandidate(
                    resume_import_id=claimed.id,
                    user_id=claimed.user_id,
                    candidate_kind=candidate.kind,
                    value_json=candidate.value_json,
                    confidence=candidate.confidence,
                    source_excerpt=candidate.source_excerpt,
                )
            )
        succeeded = _owner_transition(
            session,
            import_id=claimed.id,
            fencing_token=fencing_token,
            values={
                "status": ImportStatus.READY_FOR_REVIEW,
                "parser_identity": PARSER_IDENTITY,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
        )
        if not succeeded:
            # Lease fencing failed: another delivery already reclaimed this import (our own lease
            # must have expired while we were still genuinely working). Discard everything this
            # invocation was about to commit, including the candidates just added above -- the
            # reclaiming attempt is now the sole authority over this import's outcome.
            session.rollback()
            return
        session.commit()
    except TransientProcessingError:
        session.rollback()
        if attempt_number >= MAX_PROCESSING_ATTEMPTS:
            if _owner_transition(
                session,
                import_id=import_id,
                fencing_token=fencing_token,
                values={
                    "status": ImportStatus.FAILED,
                    "failure_code": ImportFailureCode.INTERNAL_ERROR,
                    "processing_lease_expires_at": None,
                    "processing_lease_token": None,
                },
            ):
                session.commit()
            else:
                session.rollback()
            return
        if _owner_transition(
            session,
            import_id=import_id,
            fencing_token=fencing_token,
            values={
                "status": ImportStatus.QUEUED,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
        ):
            session.commit()
        else:
            session.rollback()
        raise
    except ResumeImportError as error:
        session.rollback()
        failure_code = _FAILURE_CODE_BY_ERROR.get(type(error), ImportFailureCode.INTERNAL_ERROR)
        if _owner_transition(
            session,
            import_id=import_id,
            fencing_token=fencing_token,
            values={
                "status": ImportStatus.FAILED,
                "failure_code": failure_code,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
        ):
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        if _owner_transition(
            session,
            import_id=import_id,
            fencing_token=fencing_token,
            values={
                "status": ImportStatus.FAILED,
                "failure_code": ImportFailureCode.INTERNAL_ERROR,
                "processing_lease_expires_at": None,
                "processing_lease_token": None,
            },
        ):
            session.commit()
        else:
            session.rollback()
        raise


# -- finalization -------------------------------------------------------------------------------


def _revalidate_edited_value(
    candidate_kind: str, edited_value: dict[str, Any]
) -> dict[str, Any] | None:
    """An edit goes through the EXACT same fail-closed validation a freshly-parsed candidate did
    (docs/adr/0011-...md "review decisions") -- never a second, looser code path."""
    fake = ExtractedFact(fact_type=candidate_kind, value=edited_value, confidence=1.0)
    revalidated = validate_candidate(fake)
    return revalidated.value_json if revalidated is not None else None


def _create_master_resume_version(
    session: Session,
    *,
    user: User,
    profile_version: int,
    headline: str | None,
    summary: str | None,
    fact_ids: list[str],
    source_import_id: str,
) -> tuple[Resume, ResumeVersion]:
    """At most one MASTER resume per user (docs/adr/0011-...md "master resume creation") -- same
    get-or-create/`IntegrityError`-retry race pattern as
    `career_profile.service._get_or_create_career_profile`. `ResumeVersion` is immutable: a new
    finalization always creates a NEW version row, never updates an old one."""
    resume = session.scalar(
        select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True))
    )
    if resume is None:
        resume = Resume(user_id=user.id, title="Master Resume", is_master=True)
        try:
            with session.begin_nested():
                session.add(resume)
                session.flush()
        except IntegrityError:
            resolved = session.scalar(
                select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True))
            )
            if resolved is None:
                raise
            resume = resolved

    next_version = (
        session.scalar(
            select(func.max(ResumeVersion.version)).where(ResumeVersion.resume_id == resume.id)
        )
        or 0
    ) + 1
    snapshot = MasterResumeSnapshot(
        headline=headline, summary=summary, fact_ids=fact_ids, source_import_id=source_import_id
    )
    resume_version = ResumeVersion(
        resume_id=resume.id,
        version=next_version,
        target_job_id=None,
        profile_version=profile_version,
        content_json=snapshot.model_dump(mode="json", by_alias=True),
        generated_by="resume_import",
    )
    session.add(resume_version)
    session.flush()
    return resume, resume_version


class FinalizeResult:
    """`is_replay=True` means this call observed an ALREADY-`FINALIZED` import (idempotent retry,
    docs/adr/0011-...md "finalization idempotency") -- the caller returns the SAME result, never
    repeats any write."""

    __slots__ = ("is_replay", "resume_import", "resume_version")

    def __init__(
        self, *, resume_import: ResumeImport, resume_version: ResumeVersion, is_replay: bool
    ) -> None:
        self.resume_import = resume_import
        self.resume_version = resume_version
        self.is_replay = is_replay


def finalize_resume_import(
    session: Session, *, user: User, import_id: uuid.UUID, command: FinalizeResumeImportCommand
) -> FinalizeResult:
    """High-integrity, atomic (docs/adr/0011-...md "finalization"). `SELECT ... FOR UPDATE` on the
    `resume_imports` row is this function's ENTIRE concurrency guard for two concurrent finalize
    calls against the SAME import -- correctly refresh-safe here because, unlike the WP1.2 hotfix
    case (ADR-0010 "Addendum - round 4"), this is always a brand-new `Session`'s FIRST load of this
    particular row (one session per HTTP request), so there is no pre-existing identity-mapped
    stale copy for the lock to fail to refresh. One logical finalization is exactly one
    `profile_version` transition (`career_profile_service.bump_profile_version`, never N independent
    bumps for N accepted facts) and exactly one new `ResumeVersion` -- rejected/invalid decisions
    never reach the database at all."""
    locked = session.execute(
        select(ResumeImport)
        .where(ResumeImport.id == import_id, ResumeImport.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()
    if locked is None:
        raise ResumeImportNotFoundError

    if locked.status == ImportStatus.FINALIZED:
        resume_version = session.scalar(
            select(ResumeVersion).where(ResumeVersion.id == locked.finalized_resume_version_id)
        )
        if resume_version is None:  # pragma: no cover -- defensive; the CHECK constraint guarantees
            # `finalized_resume_version_id` is set whenever `status == 'finalized'`.
            raise AssertionError("finalized import missing its own resume version")
        return FinalizeResult(resume_import=locked, resume_version=resume_version, is_replay=True)

    if locked.status != ImportStatus.READY_FOR_REVIEW:
        raise ResumeImportNotReadyError

    pending = list(
        session.scalars(
            select(ResumeImportCandidate).where(
                ResumeImportCandidate.resume_import_id == locked.id,
                ResumeImportCandidate.review_state == CandidateReviewState.PENDING,
            )
        )
    )
    pending_by_id = {candidate.id: candidate for candidate in pending}
    decided_ids = {decision.candidate_id for decision in command.decisions}
    if decided_ids != set(pending_by_id):
        raise CandidateDecisionCoverageError

    accepted_fact_candidates: list[tuple[ResumeImportCandidate, dict[str, Any]]] = []
    headline_decided = False
    summary_decided = False
    headline_value: str | None = None
    summary_value: str | None = None
    for decision in command.decisions:
        candidate = pending_by_id[decision.candidate_id]
        if decision.decision == ReviewDecisionKind.REJECT:
            candidate.review_state = CandidateReviewState.REJECTED
            continue

        final_value_json = candidate.value_json
        if decision.decision == ReviewDecisionKind.EDIT_ACCEPT:
            if decision.edited_value is None:  # pragma: no cover -- the command's own
                # `_edited_value_matches_decision` validator already guarantees this.
                raise CandidateValueInvalidError
            revalidated = _revalidate_edited_value(candidate.candidate_kind, decision.edited_value)
            if revalidated is None:
                raise CandidateValueInvalidError
            final_value_json = revalidated

        candidate.review_state = CandidateReviewState.ACCEPTED
        candidate.value_json = final_value_json
        if candidate.candidate_kind == CandidateKind.HEADLINE.value:
            text = final_value_json.get("text")
            headline_decided, headline_value = True, text if isinstance(text, str) else None
        elif candidate.candidate_kind == CandidateKind.SUMMARY.value:
            text = final_value_json.get("text")
            summary_decided, summary_value = True, text if isinstance(text, str) else None
        else:
            accepted_fact_candidates.append((candidate, final_value_json))
    session.flush()

    bump_kwargs: dict[str, Any] = {}
    if headline_decided:
        bump_kwargs["headline"] = headline_value
    if summary_decided:
        bump_kwargs["summary"] = summary_value
    # Raises `ProfileVersionConflictError` on a stale `expected_profile_version` -- nothing above
    # this point was ever committed, so the session-close-without-commit rollback discards the
    # candidate review-state changes too (docs/adr/0011-...md "concurrency").
    profile = career_profile_service.bump_profile_version(
        session, user=user, expected_version=command.expected_profile_version, **bump_kwargs
    )
    if headline_decided or summary_decided:
        career_profile_service.record_profile_revision(session, profile=profile)

    current = datetime.now(UTC)
    fact_ids: list[str] = []
    for candidate, value_json in accepted_fact_candidates:
        fact_id = uuid.uuid4()
        session.add(
            ProfileFact(
                id=fact_id,
                user_id=user.id,
                career_profile_id=profile.id,
                fact_type=FactType(candidate.candidate_kind),
                value_json=value_json,
                source_kind=FactSourceKind.RESUME_IMPORT,
                source_ref=str(locked.id),
                evidence_ref=f"resume_import:{locked.id}:candidate:{candidate.id}",
                confidence=candidate.confidence,
                user_confirmed=True,
                confirmed_at=current,
                status=FactStatus.ACTIVE,
                lineage_id=fact_id,
                active_from_profile_version=profile.profile_version,
            )
        )
        fact_ids.append(str(fact_id))
    session.flush()

    resume, resume_version = _create_master_resume_version(
        session,
        user=user,
        profile_version=profile.profile_version,
        headline=profile.headline,
        summary=profile.summary,
        fact_ids=fact_ids,
        source_import_id=str(locked.id),
    )

    session.execute(
        update(ResumeImport)
        .where(ResumeImport.id == locked.id, ResumeImport.status == ImportStatus.READY_FOR_REVIEW)
        .values(
            status=ImportStatus.FINALIZED,
            finalized_resume_id=resume.id,
            finalized_resume_version_id=resume_version.id,
            failure_code=None,
        )
    )
    session.flush()
    session.refresh(locked)
    return FinalizeResult(resume_import=locked, resume_version=resume_version, is_replay=False)


def get_master_resume(session: Session, *, user: User) -> tuple[Resume, ResumeVersion] | None:
    """Read-only -- never creates anything (same discipline as
    `career_profile.service.get_career_profile`)."""
    resume = session.scalar(
        select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True))
    )
    if resume is None:
        return None
    resume_version = session.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.resume_id == resume.id)
        .order_by(ResumeVersion.version.desc())
        .limit(1)
    )
    if resume_version is None:  # pragma: no cover -- defensive; a master `Resume` row is only ever
        # created in the same transaction as its first `ResumeVersion`.
        raise AssertionError("master resume exists with no ResumeVersion")
    return resume, resume_version
