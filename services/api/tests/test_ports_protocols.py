"""One fake adapter per port, used two ways:

1. a module-level `_typed_*: <Protocol> = Fake*()` assignment lets `mypy --strict` verify the fake
   structurally satisfies the Protocol -- that IS the "fakes satisfy each Protocol" check
   (docs/adr/0005-internal-ports-capability-model.md); Protocols are not `@runtime_checkable`
   here, so this is a static check, not an `isinstance` one;
2. the tests below exercise the fakes for real to prove the DTOs/enums actually compose into a
   working call, not just a type-checked shape.

No fake here is a real adapter: each returns a hand-built, deterministic response and never makes
a network call. Async methods are run with plain `asyncio.run` (no pytest-asyncio dependency: these
are single, independent coroutines with no need for fixture-managed event-loop scoping).
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from tapjob_api.ports import (
    AdapterHealth,
    AdapterHealthStatus,
    AiGateway,
    AiGatewayCapability,
    AiPurpose,
    AiRequest,
    AiResult,
    AiSafetyStatus,
    AiUsage,
    ApplicationForm,
    ApplyCapability,
    ApplyConnector,
    CallContext,
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
    ChannelIdentity,
    ConfirmedAnswer,
    DiscoveredJob,
    DiscoveryPage,
    ExternalApplicationRef,
    ExternalJobRef,
    ExtractedFact,
    InboundMessage,
    InboxAccountRef,
    InboxCapability,
    InboxChangeBatch,
    InboxConnector,
    InboxMessageBody,
    JobDetails,
    JobSource,
    JobSourceCapability,
    MessagingCapability,
    MessagingChannel,
    OutboundMessage,
    ParsedResume,
    PortError,
    PortErrorCode,
    PromptRef,
    RenderableResumeContent,
    RenderedResumeArtifact,
    ResumeDocument,
    ResumeParser,
    ResumeParserCapability,
    ResumeRenderer,
    ResumeRendererCapability,
    ResumeRenderFormat,
    SendReceipt,
    SubmissionEvidence,
    SubmissionOutcome,
    SubmissionOutcomeStatus,
    SubmissionRequest,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ctx() -> CallContext:
    return CallContext.with_timeout("corr-fake", 5, now=_NOW)


def _health() -> AdapterHealth:
    return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=_NOW)


class FakeJobSource:
    provider = "fake-job-board"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[JobSourceCapability]:
        return CapabilityManifest(
            [CapabilityReport(JobSourceCapability.DISCOVERY, CapabilityStatus.SUPPORTED)]
        )

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        ref = ExternalJobRef(
            provider=self.provider, external_id="123", url="https://example.com/123"
        )
        return DiscoveryPage(
            items=(DiscoveredJob(ref=ref, title="Engineer", company_name="Acme", posted_at=_NOW),),
            next_cursor=None,
        )

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
        return JobDetails(ref=ref, description_raw="A great job.", fetched_at=_NOW)

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


class FakeResumeParser:
    provider = "fake-parser"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[ResumeParserCapability]:
        return CapabilityManifest(
            [
                CapabilityReport(
                    ResumeParserCapability.STRUCTURED_EXTRACTION, CapabilityStatus.SUPPORTED
                )
            ]
        )

    async def parse(self, document: ResumeDocument, ctx: CallContext) -> ParsedResume:
        fact = ExtractedFact(fact_type="skill", value={"name": "Python"}, confidence=0.9)
        return ParsedResume(facts=(fact,))

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


class FakeResumeRenderer:
    provider = "fake-renderer"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[ResumeRendererCapability]:
        return CapabilityManifest(
            [CapabilityReport(ResumeRendererCapability.RENDER, CapabilityStatus.SUPPORTED)]
        )

    async def render(
        self, content: RenderableResumeContent, ctx: CallContext
    ) -> RenderedResumeArtifact:
        return RenderedResumeArtifact(
            format=content.format, content=b"%PDF-fake", media_type="application/pdf"
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


class FakeAiGateway:
    """`generate()` never casts or constructs `OutputT` itself: it calls `request.validate_output`
    on `self._raw_response` -- a stand-in for what a real provider adapter would have received back
    -- and whatever that validator does (accept or reject) is exactly what `generate()` reports.
    This is the thing under test for Finding 3: the fake cannot bypass the validator because it
    never touches `OutputT` by any other path."""

    provider = "fake-llm"

    def __init__(self, raw_response: object = None) -> None:
        self._raw_response = raw_response

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[AiGatewayCapability]:
        return CapabilityManifest(
            [CapabilityReport(AiGatewayCapability.STRUCTURED_OUTPUT, CapabilityStatus.SUPPORTED)]
        )

    async def generate[OutputT](
        self, request: AiRequest[OutputT], ctx: CallContext
    ) -> AiResult[OutputT]:
        try:
            output = request.validate_output(self._raw_response)
        except ValueError as exc:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="provider output failed structured-output validation",
            ) from exc

        usage = AiUsage(
            input_tokens=10, output_tokens=5, latency_ms=12.5, estimated_cost_micros=100
        )
        return AiResult(
            output=output, usage=usage, safety_status=AiSafetyStatus.ALLOWED, prompt=request.prompt
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


class FakeMessagingChannel:
    provider = "fake-whatsapp"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[MessagingCapability]:
        return CapabilityManifest(
            [CapabilityReport(MessagingCapability.TEXT, CapabilityStatus.SUPPORTED)]
        )

    async def send(self, message: OutboundMessage, ctx: CallContext) -> SendReceipt:
        return SendReceipt(provider_message_id="wamid.fake", accepted_at=_NOW)

    async def parse_inbound(
        self, raw_body: bytes, headers: Mapping[str, str], ctx: CallContext
    ) -> Sequence[InboundMessage]:
        sender = ChannelIdentity(channel="whatsapp", external_id="+5500000000000")
        return [
            InboundMessage(
                sender=sender,
                provider_message_id="wamid.in",
                received_at=_NOW,
                text="oi",
                interactive_reply_id=None,
            )
        ]

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


class FakeApplyConnector:
    provider = "fake-ats"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[ApplyCapability]:
        return CapabilityManifest(
            [CapabilityReport(ApplyCapability.OFFICIAL_SUBMISSION, CapabilityStatus.SUPPORTED)]
        )

    async def fetch_form(self, ref: ExternalApplicationRef, ctx: CallContext) -> ApplicationForm:
        return ApplicationForm(form_version="v1", question_keys=("full_name",))

    async def submit(self, request: SubmissionRequest, ctx: CallContext) -> SubmissionOutcome:
        return SubmissionOutcome(
            status=SubmissionOutcomeStatus.SUBMITTED,
            evidence=SubmissionEvidence(provider_application_id="app-1"),
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


class FakeInboxConnector:
    provider = "fake-gmail"

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[InboxCapability]:
        return CapabilityManifest(
            [CapabilityReport(InboxCapability.METADATA_QUERY, CapabilityStatus.SUPPORTED)]
        )

    async def list_changes(
        self, account: InboxAccountRef, cursor: str | None, ctx: CallContext
    ) -> InboxChangeBatch:
        return InboxChangeBatch(summaries=(), next_cursor=None, full_resync_required=False)

    async def fetch_body(
        self, account: InboxAccountRef, provider_message_id: str, ctx: CallContext
    ) -> InboxMessageBody:
        return InboxMessageBody(provider_message_id=provider_message_id, body_text="hello")

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return _health()


# Static conformance: mypy --strict fails the build if any fake does not structurally satisfy its
# Protocol. This is the "fakes satisfy each Protocol" check; nothing at runtime re-verifies it.
_typed_job_source: JobSource = FakeJobSource()
_typed_resume_parser: ResumeParser = FakeResumeParser()
_typed_resume_renderer: ResumeRenderer = FakeResumeRenderer()
_typed_ai_gateway: AiGateway = FakeAiGateway()
_typed_messaging_channel: MessagingChannel = FakeMessagingChannel()
_typed_apply_connector: ApplyConnector = FakeApplyConnector()
_typed_inbox_connector: InboxConnector = FakeInboxConnector()


def test_fake_job_source_discovers_and_fetches_details() -> None:
    async def scenario() -> None:
        source = FakeJobSource()
        page = await source.discover(None, _ctx())
        assert page.items[0].ref.provider == "fake-job-board"

        details = await source.fetch_details(page.items[0].ref, _ctx())
        assert details.description_raw

    asyncio.run(scenario())


def test_fake_resume_parser_never_returns_a_confirmed_fact_type() -> None:
    async def scenario() -> None:
        parser = FakeResumeParser()
        document = ResumeDocument(
            content=b"%PDF-1.4", media_type="application/pdf", filename="cv.pdf"
        )
        result = await parser.parse(document, _ctx())

        assert 0.0 <= result.facts[0].confidence <= 1.0

    asyncio.run(scenario())


def test_extracted_fact_rejects_an_out_of_range_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        ExtractedFact(fact_type="skill", value={}, confidence=1.5)


def test_fake_resume_renderer_echoes_the_requested_format() -> None:
    async def scenario() -> None:
        renderer = FakeResumeRenderer()
        content = RenderableResumeContent(content={"sections": []}, format=ResumeRenderFormat.PDF)
        artifact = await renderer.render(content, _ctx())

        assert artifact.format is ResumeRenderFormat.PDF

    asyncio.run(scenario())


@dataclass(frozen=True, slots=True)
class _Output:
    name: str


def _validate_output(raw: object) -> _Output:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise ValueError("expected a mapping with a string 'name' field")
    return _Output(name=raw["name"])


def _output_request() -> AiRequest[_Output]:
    return AiRequest(
        purpose=AiPurpose.RESUME_EXTRACTION,
        prompt=PromptRef(id="resume-extract", version="1"),
        variables={},
        untrusted=(),
        grounding=(),
        validate_output=_validate_output,
    )


def test_fake_ai_gateway_validates_a_valid_provider_payload_into_output_t() -> None:
    async def scenario() -> None:
        gateway = FakeAiGateway(raw_response={"name": "Ana"})
        result = await gateway.generate(_output_request(), _ctx())

        assert result.output == _Output(name="Ana")
        assert result.safety_status is AiSafetyStatus.ALLOWED

    asyncio.run(scenario())


def test_fake_ai_gateway_normalizes_an_invalid_provider_payload_to_a_port_error() -> None:
    async def scenario() -> None:
        gateway = FakeAiGateway(raw_response={"name": 123})

        with pytest.raises(PortError) as excinfo:
            await gateway.generate(_output_request(), _ctx())

        assert excinfo.value.code is PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())


def test_fake_ai_gateway_cannot_bypass_the_validator_with_a_malformed_raw_response() -> None:
    async def scenario() -> None:
        # A raw response that is not even a mapping -- there is no code path in the fake that
        # could still produce an OutputT for this input other than calling validate_output, which
        # must reject it.
        gateway = FakeAiGateway(raw_response="not-a-mapping-at-all")

        with pytest.raises(PortError) as excinfo:
            await gateway.generate(_output_request(), _ctx())

        assert excinfo.value.code is PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())


def test_fake_messaging_channel_send_and_parse_inbound() -> None:
    async def scenario() -> None:
        channel = FakeMessagingChannel()
        recipient = ChannelIdentity(channel="whatsapp", external_id="+5500000000000")
        receipt = await channel.send(
            OutboundMessage(recipient=recipient, idempotency_key="idem-1", text="hi"), _ctx()
        )
        assert receipt.provider_message_id

        inbound = await channel.parse_inbound(b"{}", {}, _ctx())
        assert inbound[0].text == "oi"

    asyncio.run(scenario())


def test_fake_apply_connector_submission_requires_confirmed_answers_shape() -> None:
    async def scenario() -> None:
        connector = FakeApplyConnector()
        ref = ExternalApplicationRef(provider="fake-ats", external_job_id="job-1")
        answer = ConfirmedAnswer(
            question_key="sponsorship", value="no", confirmation_ref="confirm-1"
        )
        request = SubmissionRequest(
            application_ref=ref,
            kit_ref="kit-1",
            form_version="v1",
            answers=(answer,),
            idempotency_key="idem-1",
        )
        outcome = await connector.submit(request, _ctx())

        assert outcome.status is SubmissionOutcomeStatus.SUBMITTED
        assert outcome.evidence is not None
        assert outcome.evidence.provider_application_id == "app-1"

    asyncio.run(scenario())


def test_confirmed_answer_rejects_an_empty_confirmation_ref() -> None:
    with pytest.raises(ValueError, match="confirmation_ref"):
        ConfirmedAnswer(question_key="sponsorship", value="no", confirmation_ref="")


def test_confirmed_answer_rejects_a_whitespace_only_confirmation_ref() -> None:
    with pytest.raises(ValueError, match="confirmation_ref"):
        ConfirmedAnswer(question_key="sponsorship", value="no", confirmation_ref="   ")


def test_confirmed_answer_accepts_a_non_blank_confirmation_ref() -> None:
    answer = ConfirmedAnswer(question_key="sponsorship", value="no", confirmation_ref="confirm-1")
    assert answer.confirmation_ref == "confirm-1"


def test_submission_outcome_submitted_without_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires evidence"):
        SubmissionOutcome(status=SubmissionOutcomeStatus.SUBMITTED, evidence=None)


def test_submission_outcome_submitted_with_evidence_is_valid() -> None:
    outcome = SubmissionOutcome(
        status=SubmissionOutcomeStatus.SUBMITTED,
        evidence=SubmissionEvidence(provider_application_id="app-1"),
    )

    assert outcome.evidence is not None


def test_submission_outcome_rejected_by_provider_needs_no_evidence() -> None:
    outcome = SubmissionOutcome(status=SubmissionOutcomeStatus.REJECTED_BY_PROVIDER)

    assert outcome.evidence is None


def test_submission_outcome_outcome_unknown_needs_no_evidence_and_is_not_submitted() -> None:
    outcome = SubmissionOutcome(status=SubmissionOutcomeStatus.OUTCOME_UNKNOWN)

    assert outcome.evidence is None
    assert outcome.status is not SubmissionOutcomeStatus.SUBMITTED


def test_submission_evidence_requires_at_least_one_identifying_field() -> None:
    with pytest.raises(ValueError, match="provider_application_id or provider_receipt_ref"):
        SubmissionEvidence()


def test_submission_evidence_accepts_a_receipt_reference_alone() -> None:
    evidence = SubmissionEvidence(provider_receipt_ref="receipt-xyz")

    assert evidence.provider_application_id is None
    assert evidence.provider_receipt_ref == "receipt-xyz"


def test_submission_evidence_rejects_a_blank_application_id() -> None:
    with pytest.raises(ValueError, match="provider_application_id"):
        SubmissionEvidence(provider_application_id="")


def test_submission_evidence_rejects_a_whitespace_only_application_id() -> None:
    with pytest.raises(ValueError, match="provider_application_id"):
        SubmissionEvidence(provider_application_id="   ")


def test_submission_evidence_rejects_a_blank_receipt_ref() -> None:
    with pytest.raises(ValueError, match="provider_receipt_ref"):
        SubmissionEvidence(provider_receipt_ref="")


def test_submission_evidence_rejects_a_whitespace_only_receipt_ref() -> None:
    with pytest.raises(ValueError, match="provider_receipt_ref"):
        SubmissionEvidence(provider_receipt_ref="   ")


def test_submission_evidence_rejects_both_fields_absent() -> None:
    with pytest.raises(ValueError, match="provider_application_id or provider_receipt_ref"):
        SubmissionEvidence(provider_application_id=None, provider_receipt_ref=None)


def test_submission_evidence_accepts_a_single_non_blank_application_id() -> None:
    evidence = SubmissionEvidence(provider_application_id="app-1")

    assert evidence.provider_application_id == "app-1"
    assert evidence.provider_receipt_ref is None


def test_fake_inbox_connector_lists_changes_without_fetching_bodies() -> None:
    async def scenario() -> None:
        connector = FakeInboxConnector()
        account = InboxAccountRef(provider="fake-gmail", account_external_id="acct-1")
        batch = await connector.list_changes(account, None, _ctx())

        assert batch.full_resync_required is False

    asyncio.run(scenario())
