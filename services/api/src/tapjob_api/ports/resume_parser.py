"""ResumeParser: provider-neutral resume-document extraction boundary
(docs/adr/0005-internal-ports-capability-model.md).

A deterministic, non-AI implementation exists starting WP1.3
(`tapjob_api.resume_import.parsing`); no AiGateway-backed adapter is implemented yet. Every
extracted fact is explicitly UNCONFIRMED: this port can suggest candidate facts, it cannot create a
confirmed `ProfileFact` (AGENTS.md §6 Professional Truth Store) -- that confirmation is a user
action the domain layer owns.
"""

from collections.abc import Mapping, Sequence
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


# A bounded, JSON-shaped candidate value (WP1.3, docs/adr/0011-resume-import.md "ResumeParser port
# evolution"): the original `Mapping[str, str | int | float | bool]` was too shallow for several
# canonical `FactValue` shapes (`docs/adr/0010-career-profile-truth-store.md`) that nest structure
# -- e.g. `ExperienceFactValue.start`/`end` are themselves `{"year": int, "month": int | None}`
# objects, and `achievements`/`technologies` are lists of strings. This alias widens
# `ExtractedFact.value` to admit that nesting while remaining a plain, provider-neutral JSON shape
# -- it does NOT import (and thereby does not couple this port to) `tapjob_api.career_profile`'s
# own typed `FactValue` union; the resume-import domain layer is what validates a candidate's
# `value` against the real `FactValue` model matching its `fact_type` (never this port, and never a
# bare `Any`/unchecked cast). "Bounded" here is a depth/size discipline enforced by whatever
# constructs an `ExtractedFact` (the parser) and re-checked by the domain-layer validator -- the
# Python type system cannot itself express a depth limit, so this is a documented expectation, not
# a runtime guarantee of this dataclass alone.
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Mapping[str, "JsonValue"] | Sequence["JsonValue"]


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """One atomic, unconfirmed candidate fact. `fact_type` deliberately stays a plain string
    aligned with `docs/DATA_MODEL.md`'s `ProfileFact.type` values rather than this port importing
    (and thereby owning) the domain's own fact-type enum."""

    fact_type: str
    value: Mapping[str, JsonValue]
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
