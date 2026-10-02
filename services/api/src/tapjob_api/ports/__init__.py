"""Internal, provider-neutral ports (docs/adr/0005-internal-ports-capability-model.md, D-048).

`MatchingEngine` is deliberately NOT a port here and must never be added to `__all__`: it remains
internal/domain logic (docs/IMPLEMENTATION_PLAN.md WP0.2-B). Interface extraction for it is
deferred to WP2.2, only if a real substitution need arises -- see the ADR.

No adapter (Greenhouse, Evolution, OpenAI, Gmail, ...) is implemented anywhere in this package.
"""

from tapjob_api.ports._common import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    Deadline,
    PortError,
    PortErrorCode,
    ResultMetadata,
)
from tapjob_api.ports.ai_gateway import (
    AiGateway,
    AiGatewayCapability,
    AiPurpose,
    AiRequest,
    AiResult,
    AiSafetyStatus,
    AiUsage,
    FactRef,
    PromptRef,
    UntrustedContent,
)
from tapjob_api.ports.apply_connector import (
    ApplicationForm,
    ApplyCapability,
    ApplyConnector,
    ApplyMode,
    ConfirmedAnswer,
    ExternalApplicationRef,
    SubmissionEvidence,
    SubmissionOutcome,
    SubmissionOutcomeStatus,
    SubmissionRequest,
)
from tapjob_api.ports.capabilities import (
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
)
from tapjob_api.ports.inbox_connector import (
    InboxAccountRef,
    InboxCapability,
    InboxChangeBatch,
    InboxConnector,
    InboxMessageBody,
    InboxMessageSummary,
)
from tapjob_api.ports.job_source import (
    DiscoveredJob,
    DiscoveryPage,
    ExternalJobRef,
    JobDetails,
    JobSource,
    JobSourceCapability,
)
from tapjob_api.ports.messaging_channel import (
    ChannelIdentity,
    InboundMessage,
    MessagingCapability,
    MessagingChannel,
    OutboundMessage,
    SendReceipt,
)
from tapjob_api.ports.resume_parser import (
    ExtractedFact,
    ParsedResume,
    ResumeDocument,
    ResumeParser,
    ResumeParserCapability,
)
from tapjob_api.ports.resume_renderer import (
    RenderableResumeContent,
    RenderedResumeArtifact,
    ResumeRenderer,
    ResumeRendererCapability,
    ResumeRenderFormat,
)

__all__ = [
    "AdapterHealth",
    "AdapterHealthStatus",
    "AiGateway",
    "AiGatewayCapability",
    "AiPurpose",
    "AiRequest",
    "AiResult",
    "AiSafetyStatus",
    "AiUsage",
    "ApplicationForm",
    "ApplyCapability",
    "ApplyConnector",
    "ApplyMode",
    "CallContext",
    "CapabilityManifest",
    "CapabilityReport",
    "CapabilityStatus",
    "ChannelIdentity",
    "ConfirmedAnswer",
    "Deadline",
    "DiscoveredJob",
    "DiscoveryPage",
    "ExternalApplicationRef",
    "ExternalJobRef",
    "ExtractedFact",
    "FactRef",
    "InboundMessage",
    "InboxAccountRef",
    "InboxCapability",
    "InboxChangeBatch",
    "InboxConnector",
    "InboxMessageBody",
    "InboxMessageSummary",
    "JobDetails",
    "JobSource",
    "JobSourceCapability",
    "MessagingCapability",
    "MessagingChannel",
    "OutboundMessage",
    "ParsedResume",
    "PortError",
    "PortErrorCode",
    "PromptRef",
    "RenderableResumeContent",
    "RenderedResumeArtifact",
    "ResultMetadata",
    "ResumeDocument",
    "ResumeParser",
    "ResumeParserCapability",
    "ResumeRenderFormat",
    "ResumeRenderer",
    "ResumeRendererCapability",
    "SendReceipt",
    "SubmissionEvidence",
    "SubmissionOutcome",
    "SubmissionOutcomeStatus",
    "SubmissionRequest",
    "UntrustedContent",
]
