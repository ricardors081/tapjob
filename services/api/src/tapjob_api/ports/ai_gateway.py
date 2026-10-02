"""AiGateway: provider-neutral LLM boundary (docs/adr/0005-internal-ports-capability-model.md).

No OpenAI/Anthropic/other provider is implemented in this checkpoint. Product logic must never
call a provider SDK directly (docs/ARCHITECTURE.md §9) -- only through this port.

Security note: `UntrustedContent` delimits hostile input (job descriptions, e-mail bodies, scraped
pages) from trusted `variables`, but delimiting untrusted content in a prompt is NOT, by itself, a
sufficient defense against prompt injection (docs/AI_AGENT.md §7, docs/SECURITY_PRIVACY.md §7). It
is one input to a layered defense (tool allowlists, structured-output validation, policy checks)
that this port's future callers must still apply; this port does not claim to solve prompt
injection on its own.

Grounding note: `FactRef` only ever references an existing, already-confirmed `ProfileFact` id
(AGENTS.md §6). This port never accepts a fact VALUE as "grounding" -- only a reference to a fact
the domain has already stored and confirmed -- so a provider's output cannot smuggle in a
fabricated fact merely by claiming to be grounded.

Structured-output note: `AiRequest.validate_output` is the ONLY path from a provider's raw response
to `OutputT` (docs/adr/0005-internal-ports-capability-model.md §7). It is a validator the caller
supplies, not a passive type marker: `generate()` must call it on the provider's actual raw output
and return exactly what it produces, never `cast()` a raw response to `OutputT` and never construct
`OutputT` by calling it as a zero-argument constructor. A validator that rejects its input is a
normal, expected outcome -- `generate()` must turn that rejection into
`PortError(PortErrorCode.PROVIDER_ERROR, ...)`, never let it escape as the validator's own
exception type, and never fall back to returning unvalidated data.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext
from tapjob_api.ports.capabilities import CapabilityManifest


class AiGatewayCapability(StrEnum):
    STRUCTURED_OUTPUT = "structured_output"


class AiPurpose(StrEnum):
    """A stable, reviewable use-case vocabulary. Provider/model selection per purpose is owned by
    configuration outside the domain (docs/ARCHITECTURE.md §9), never hardcoded per call site."""

    RESUME_EXTRACTION = "resume_extraction"
    JOB_MATCH_EXPLANATION = "job_match_explanation"
    RESUME_TAILORING = "resume_tailoring"
    INTERVIEW_PREP = "interview_prep"
    INBOX_EVENT_CLASSIFICATION = "inbox_event_classification"


@dataclass(frozen=True, slots=True)
class PromptRef:
    id: str
    version: str


@dataclass(frozen=True, slots=True)
class UntrustedContent:
    """Hostile input (docs/AI_AGENT.md §7): job descriptions, e-mail bodies, scraped pages,
    attached documents. `source_kind` is a short label (e.g. "job_description"), not a schema."""

    source_kind: str
    content: str


@dataclass(frozen=True, slots=True)
class FactRef:
    fact_id: str


@dataclass(frozen=True, slots=True)
class AiRequest[OutputT]:
    """`validate_output` proves the raw-output -> `OutputT` contract instead of merely asserting it
    via a type marker: it is a callable the caller supplies (e.g. a Pydantic model's
    `model_validate`, wrapped to raise `ValueError` on rejection), and `generate()` must call it on
    the provider's actual raw response. A validator that raises on invalid input is how a provider
    producing a malformed structured output becomes a normalized `PortError`, not a crash or a
    silently-wrong `OutputT`."""

    purpose: AiPurpose
    prompt: PromptRef
    variables: Mapping[str, str]
    untrusted: tuple[UntrustedContent, ...]
    grounding: tuple[FactRef, ...]
    validate_output: Callable[[object], OutputT]


class AiSafetyStatus(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    FLAGGED = "flagged"


@dataclass(frozen=True, slots=True)
class AiUsage:
    """Cost is an integer in micro-units of the given currency, never a float
    (docs/ENGINEERING_STANDARDS.md §11), and is None when the adapter cannot report it."""

    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float | None
    estimated_cost_micros: int | None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class AiResult[OutputT]:
    output: OutputT
    usage: AiUsage
    safety_status: AiSafetyStatus
    prompt: PromptRef


class AiGateway(Protocol):
    """`generate()` must obtain `AiResult.output` by calling `request.validate_output` on the
    provider's raw response -- never by casting the raw response to `OutputT`, and never by
    constructing `OutputT` independently of what the provider actually returned. A validator
    rejection must surface as `PortError(PortErrorCode.PROVIDER_ERROR, ...)`."""

    provider: str

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[AiGatewayCapability]: ...

    async def generate[OutputT](
        self, request: AiRequest[OutputT], ctx: CallContext
    ) -> AiResult[OutputT]: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...
