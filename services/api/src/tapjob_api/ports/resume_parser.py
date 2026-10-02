"""ResumeParser: provider-neutral resume-document extraction boundary
(docs/adr/0005-internal-ports-capability-model.md).

No real parser is implemented in this checkpoint (WP1.3). Every extracted fact is explicitly
UNCONFIRMED: this port can suggest candidate facts, it cannot create a confirmed `ProfileFact`
(AGENTS.md §6 Professional Truth Store) -- that confirmation is a user action the domain layer
owns.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext
from tapjob_api.ports.capabilities import CapabilityManifest


class ResumeParserCapability(StrEnum):
    STRUCTURED_EXTRACTION = "structured_extraction"


@dataclass(frozen=True, slots=True)
class ResumeDocument:
    content: bytes
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """One atomic, unconfirmed candidate fact. `fact_type` deliberately stays a plain string
    aligned with `docs/DATA_MODEL.md`'s `ProfileFact.type` values rather than this port importing
    (and thereby owning) the domain's own fact-type enum."""

    fact_type: str
    value: Mapping[str, str | int | float | bool]
    confidence: float
    source_excerpt: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("ExtractedFact.confidence must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class ParsedResume:
    facts: tuple[ExtractedFact, ...]


class ResumeParser(Protocol):
    provider: str

    async def capabilities(
        self, ctx: CallContext
    ) -> CapabilityManifest[ResumeParserCapability]: ...

    async def parse(self, document: ResumeDocument, ctx: CallContext) -> ParsedResume: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...
