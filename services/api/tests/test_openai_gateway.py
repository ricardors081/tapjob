"""Unit tests for OpenAiGateway adapter implementing AiGateway."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel

from tapjob_api.ai_gateway.openai_gateway import OpenAiGateway
from tapjob_api.ports._common import CallContext, Deadline, PortError, PortErrorCode
from tapjob_api.ports.ai_gateway import (
    AiGatewayCapability,
    AiPurpose,
    AiRequest,
    PromptRef,
    UntrustedContent,
)


class DummyOutput(BaseModel):
    name: str
    score: int


def _call_ctx(timeout_seconds: float = 5.0) -> CallContext:
    return CallContext.with_timeout(
        correlation_id="test-corr-123",
        timeout_seconds=timeout_seconds,
    )


def test_capabilities_and_health() -> None:
    async def scenario() -> None:
        gateway_unconfigured = OpenAiGateway(api_key=None)
        ctx = _call_ctx()
        manifest = await gateway_unconfigured.capabilities(ctx)
        from tapjob_api.ports.capabilities import CapabilityStatus

        assert (
            manifest.status_for(AiGatewayCapability.STRUCTURED_OUTPUT) == CapabilityStatus.SUPPORTED
        )

        health_unconfigured = await gateway_unconfigured.health(ctx)
        from tapjob_api.ports._common import AdapterHealthStatus

        assert health_unconfigured.status == AdapterHealthStatus.UNAVAILABLE
        assert health_unconfigured.reason_code == "missing_api_key"

        gateway_configured = OpenAiGateway(api_key="sk-test-secret-123")
        health_configured = await gateway_configured.health(ctx)
        assert health_configured.status == AdapterHealthStatus.HEALTHY

    asyncio.run(scenario())


def test_generate_missing_api_key_raises_unavailable() -> None:
    async def scenario() -> None:
        gateway = OpenAiGateway(api_key="")
        ctx = _call_ctx()
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, ctx)
        assert exc_info.value.code == PortErrorCode.UNAVAILABLE

    asyncio.run(scenario())


def test_generate_deadline_expired_raises_timeout() -> None:
    async def scenario() -> None:
        gateway = OpenAiGateway(api_key="sk-test")
        expired_deadline = Deadline(at=datetime(2020, 1, 1, tzinfo=UTC))
        ctx = CallContext(correlation_id="test", deadline=expired_deadline)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, ctx)
        assert exc_info.value.code == PortErrorCode.TIMEOUT

    asyncio.run(scenario())


def test_generate_successful_structured_output() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer sk-test"
            assert request.headers.get("x-correlation-id") == "test-corr-123"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"name": "Senior Engineer", "score": 98}'}}
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)

        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={"role": "Engineer"},
            untrusted=(UntrustedContent(source_kind="resume_doc", content="Resume details"),),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        result = await gateway.generate(req, _call_ctx())
        assert result.output.name == "Senior Engineer"
        assert result.output.score == 98
        assert result.usage.input_tokens == 50
        assert result.usage.output_tokens == 20

    asyncio.run(scenario())


def test_generate_auth_failure_raises_auth_failed() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-bad", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.AUTH_FAILED

    asyncio.run(scenario())


def test_generate_rate_limit_raises_rate_limited() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"retry-after": "15"},
                json={"error": {"message": "Rate limit exceeded"}},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.RATE_LIMITED
        assert exc_info.value.retry_after_seconds == 15.0

    asyncio.run(scenario())


def test_generate_server_error_raises_unavailable() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.UNAVAILABLE
        assert exc_info.value.transient is True

    asyncio.run(scenario())


def test_generate_malformed_json_raises_provider_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not valid json"}}]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())


def test_generate_validator_rejection_raises_provider_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # Valid JSON but fails DummyOutput schema validation (missing score)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"name": "Valid Name"}'}}]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())


def test_generate_refusal_raises_invalid_request() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "refused",
                    "refusal": "I cannot fulfill this extraction request for safety reasons.",
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "refused" in str(exc_info.value.message).lower()

    asyncio.run(scenario())


def test_generate_responses_api_canonical_rest_shape() -> None:
    """Requirement 2: Proves canonical Responses API REST shape
    with output[].content[].type == 'output_text'."""

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/responses")
            payload = json.loads(request.read().decode("utf-8"))
            assert "input" in payload
            assert payload.get("text", {}).get("format", {}).get("type") == "json_schema"
            assert payload.get("text", {}).get("format", {}).get("strict") is True
            canonical_body = {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"name": "Alice", "score": 95}',
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 50, "output_tokens": 20},
            }
            return httpx.Response(200, json=canonical_body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        # Default base_url https://api.openai.com/v1 resolves to /responses automatically
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        result = await gateway.generate(req, _call_ctx())
        assert result.output.name == "Alice"
        assert result.output.score == 95
        assert result.usage.input_tokens == 50
        assert result.usage.output_tokens == 20

    asyncio.run(scenario())


def test_generate_responses_api_refusal_in_output_content() -> None:
    """Requirement 2: Proves refusal extraction when output[].content[].type == 'refusal'."""

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/responses")
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "refusal",
                                    "refusal": "Refusal from message content",
                                }
                            ],
                        }
                    ],
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "refused" in str(exc_info.value.message).lower()

    asyncio.run(scenario())


def test_generate_chat_completions_compatibility() -> None:
    """Requirement 1: Chat Completions functions as explicit compatibility option."""

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/chat/completions")
            payload = json.loads(request.read().decode("utf-8"))
            assert "messages" in payload
            assert "response_format" in payload
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": '{"name": "Bob", "score": 88}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 15},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(
            api_key="sk-test",
            endpoint="/chat/completions",
            http_client=client,
        )
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        result = await gateway.generate(req, _call_ctx())
        assert result.output.name == "Bob"
        assert result.output.score == 88

    asyncio.run(scenario())


def test_generate_incomplete_raises_provider_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status": "incomplete"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = OpenAiGateway(api_key="sk-test", http_client=client)
        req = AiRequest[DummyOutput](
            purpose=AiPurpose.RESUME_EXTRACTION,
            prompt=PromptRef(id="test", version="1.0"),
            variables={},
            untrusted=(),
            grounding=(),
            validate_output=DummyOutput.model_validate,
        )
        with pytest.raises(PortError) as exc_info:
            await gateway.generate(req, _call_ctx())
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())
