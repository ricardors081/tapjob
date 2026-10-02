"""ApplyConnector: provider-neutral application-submission boundary
(docs/adr/0005-internal-ports-capability-model.md).

No submission is implemented in this checkpoint (WP5.1). Covers only what a backend adapter
itself executes -- `OFFICIAL_API` discovery/submission. `ASSISTED_EXTENSION` runs in the browser
extension (not this port); `DEEP_LINK`, `MANUAL` and `DISABLED` are domain-resolver decisions
(docs/APPLY_ENGINE.md §3) derived from this port's `capabilities()`, not separate methods here.

`SubmissionRequest` only ever carries `ConfirmedAnswer`s a user has already explicitly confirmed
(AGENTS.md §6-7, docs/APPLY_ENGINE.md §6-7); this port cannot be used to submit an inferred or
guessed sensitive answer.

Evidence note: `SubmissionOutcome(status=SUBMITTED, ...)` must never be constructible without
`SubmissionEvidence` (docs/APPLY_ENGINE.md §10: "never mark Applied unless submission evidence
exists"). A caller cannot read "Applied" off `status` alone -- the type itself makes a
status-without-evidence combination impossible to construct, not merely undocumented.
`SubmissionEvidence` never carries a raw HTTP body, token, secret or provider exception: only a
stable identifier/reference the domain can later use to look the application up.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext
from tapjob_api.ports.capabilities import CapabilityManifest


class ApplyMode(StrEnum):
    """The five canonical modes (AGENTS.md §7, docs/APPLY_ENGINE.md §2). Only `OFFICIAL_API` maps
    to a method this Protocol actually exposes; the other four are resolved by the domain layer
    from `capabilities()` plus other context this port does not see (e.g. whether a hosted
    application URL exists at all)."""

    OFFICIAL_API = "official_api"
    ASSISTED_EXTENSION = "assisted_extension"
    DEEP_LINK = "deep_link"
    MANUAL = "manual"
    DISABLED = "disabled"


class ApplyCapability(StrEnum):
    FORM_DISCOVERY = "form_discovery"
    OFFICIAL_SUBMISSION = "official_submission"


@dataclass(frozen=True, slots=True)
class ExternalApplicationRef:
    provider: str
    external_job_id: str


@dataclass(frozen=True, slots=True)
class ApplicationForm:
    form_version: str
    question_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConfirmedAnswer:
    """`confirmation_ref` points at the domain's own record of the user's explicit confirmation;
    this port never infers or guesses a value for a sensitive question
    (docs/APPLY_ENGINE.md §6)."""

    question_key: str
    value: str
    confirmation_ref: str

    def __post_init__(self) -> None:
        if not self.confirmation_ref.strip():
            raise ValueError(
                "ConfirmedAnswer.confirmation_ref must be a non-blank reference to the domain's "
                "own confirmation record -- an empty or whitespace-only value proves no explicit "
                "confirmation exists (docs/APPLY_ENGINE.md §6)."
            )


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    application_ref: ExternalApplicationRef
    kit_ref: str
    form_version: str
    answers: tuple[ConfirmedAnswer, ...]
    idempotency_key: str


class SubmissionOutcomeStatus(StrEnum):
    SUBMITTED = "submitted"
    REJECTED_BY_PROVIDER = "rejected_by_provider"
    # Mandatory, not optional (docs/APPLY_ENGINE.md §9-10): a timeout/ambiguous provider response
    # must never be reported as SUBMITTED, and the caller must never mark an Application as
    # Applied without this being ruled out.
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, slots=True)
class SubmissionEvidence:
    """A stable, opaque reference proving a submission actually reached the provider -- never the
    raw HTTP body, an auth token/secret, or a provider exception (docs/APPLY_ENGINE.md §10). At
    least one of the two fields must carry a non-blank value; a provider that returns neither, or
    returns only whitespace, has not produced evidence, regardless of what `status` claims."""

    provider_application_id: str | None = None
    provider_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        if self.provider_application_id is not None and not self.provider_application_id.strip():
            raise ValueError(
                "SubmissionEvidence.provider_application_id must be non-blank when provided -- "
                "a whitespace-only value is not a usable reference (docs/APPLY_ENGINE.md §10)."
            )
        if self.provider_receipt_ref is not None and not self.provider_receipt_ref.strip():
            raise ValueError(
                "SubmissionEvidence.provider_receipt_ref must be non-blank when provided -- "
                "a whitespace-only value is not a usable reference (docs/APPLY_ENGINE.md §10)."
            )
        if not (self.provider_application_id or self.provider_receipt_ref):
            raise ValueError(
                "SubmissionEvidence requires a non-blank provider_application_id or "
                "provider_receipt_ref -- an evidence record with neither proves nothing "
                "(docs/APPLY_ENGINE.md §10)."
            )


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """`status=SUBMITTED` without `evidence` is a contradiction this type refuses to hold
    (docs/APPLY_ENGINE.md §10): a caller can never read "Applied" off `status` alone. Neither
    `REJECTED_BY_PROVIDER` nor `OUTCOME_UNKNOWN` requires evidence -- a rejection or an ambiguous
    outcome is not a successful submission, so there is nothing to prove and no fake evidence
    should be invented to satisfy this type."""

    status: SubmissionOutcomeStatus
    evidence: SubmissionEvidence | None = None

    def __post_init__(self) -> None:
        if self.status is SubmissionOutcomeStatus.SUBMITTED and self.evidence is None:
            raise ValueError(
                "SubmissionOutcome(status=SUBMITTED) requires evidence -- never mark Applied "
                "unless submission evidence exists (docs/APPLY_ENGINE.md §10)."
            )


class ApplyConnector(Protocol):
    provider: str

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[ApplyCapability]: ...

    async def fetch_form(
        self, ref: ExternalApplicationRef, ctx: CallContext
    ) -> ApplicationForm: ...

    async def submit(self, request: SubmissionRequest, ctx: CallContext) -> SubmissionOutcome: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...
