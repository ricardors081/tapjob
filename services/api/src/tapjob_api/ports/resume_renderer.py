"""ResumeRenderer: provider-neutral resume-rendering boundary
(docs/adr/0005-internal-ports-capability-model.md).

No real rendering engine is implemented in this checkpoint (WP3.1). `content` is an opaque mapping
because the canonical resume-content shape does not exist yet (Resume Studio, WP3.1): this port
does not invent one, and will take a properly typed domain model once that work package defines
it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext
from tapjob_api.ports.capabilities import CapabilityManifest


class ResumeRendererCapability(StrEnum):
    RENDER = "render"


class ResumeRenderFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


@dataclass(frozen=True, slots=True)
class RenderableResumeContent:
    content: Mapping[str, object]
    format: ResumeRenderFormat


@dataclass(frozen=True, slots=True)
class RenderedResumeArtifact:
    format: ResumeRenderFormat
    content: bytes
    media_type: str


class ResumeRenderer(Protocol):
    provider: str

    async def capabilities(
        self, ctx: CallContext
    ) -> CapabilityManifest[ResumeRendererCapability]: ...

    async def render(
        self, content: RenderableResumeContent, ctx: CallContext
    ) -> RenderedResumeArtifact: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...
