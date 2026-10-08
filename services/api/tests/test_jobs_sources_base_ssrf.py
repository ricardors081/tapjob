"""Security and compliance test suite for SSRF protection and streaming body bounds
in ATS HTTP client (WP2.1-B).

Tests:
- HTTPS requirement: rejection of http:// and unapproved schemes
- Rejection of userinfo in credentials
- Provider official host validation (greenhouse, lever, ashby)
- DNS resolution and IP address validation:
  - localhost
  - 127.0.0.1
  - ::1
  - private IPv4 (10.0.0.1, 192.168.1.1, 172.16.0.1)
  - private IPv6 (fc00::1)
  - link-local (169.254.0.1)
  - cloud metadata (169.254.169.254, 169.254.169.253, fd00:ec2::254)
  - IPv4-mapped IPv6 (::ffff:127.0.0.1)
  - split-brain mixed DNS resolution (one public, one private) fails closed
- Manual redirect loop SSRF protection:
  - public URL redirecting to private/metadata IP rejected
  - public URL redirecting to http:// rejected
  - redirect loop / cycle rejected
- Streaming response decoded size limits:
  - Content-Length > 5 MiB rejected fast
  - Chunked body > 5 MiB rejected during stream iteration
  - Decompression bomb (gzip expanding beyond 5 MiB) aborted immediately
  - Body exactly at 5 MiB limit accepted
  - Body below 5 MiB limit accepted
"""

import asyncio
import gzip
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tapjob_api.jobs.sources.base import (
    MAX_ATS_RESPONSE_BYTES,
    resolve_and_validate_ats_dns,
    send_ats_request,
    validate_ats_url,
)
from tapjob_api.ports import CallContext, PortError, PortErrorCode


def _ctx() -> CallContext:
    return CallContext.with_timeout("test-corr", 10.0)


# ==============================================================================
# 1. URL SYNTAX & SCHEME & USERINFO TESTS
# ==============================================================================


def test_validate_ats_url_rejects_http_scheme() -> None:
    for bad_url in (
        "http://boards-api.greenhouse.io/v1/boards/acme",
        "http://api.lever.co/v0/postings/acme",
        "http://api.ashbyhq.com/posting-api/job-board/acme",
    ):
        with pytest.raises(PortError) as exc_info:
            validate_ats_url(bad_url, provider="greenhouse")
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "HTTPS is required" in exc_info.value.message


def test_validate_ats_url_rejects_userinfo() -> None:
    for bad_url in (
        "https://admin:secret@boards-api.greenhouse.io/v1/boards/acme",
        "https://user@api.lever.co/v0/postings/acme",
    ):
        with pytest.raises(PortError) as exc_info:
            validate_ats_url(bad_url, provider="greenhouse")
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "userinfo" in exc_info.value.message


def test_validate_ats_url_rejects_empty_or_missing_host() -> None:
    with pytest.raises(PortError) as exc_info:
        validate_ats_url("   ", provider="greenhouse")
    assert exc_info.value.code == PortErrorCode.INVALID_REQUEST

    with pytest.raises(PortError) as exc_info:
        validate_ats_url("https:///path", provider="greenhouse")
    assert exc_info.value.code == PortErrorCode.INVALID_REQUEST


# ==============================================================================
# 2. PROVIDER OFFICIAL HOST VALIDATION
# ==============================================================================


def test_validate_ats_url_allows_official_hosts_per_provider() -> None:
    # Greenhouse
    scheme, host, port = validate_ats_url(
        "https://boards-api.greenhouse.io/v1/boards/acme", provider="greenhouse"
    )
    assert scheme == "https"
    assert host == "boards-api.greenhouse.io"
    assert port == 443

    # Lever US & EU
    scheme, host, port = validate_ats_url("https://api.lever.co/v0/postings/acme", provider="lever")
    assert host == "api.lever.co"

    scheme, host, port = validate_ats_url(
        "https://api.eu.lever.co/v0/postings/acme", provider="lever"
    )
    assert host == "api.eu.lever.co"

    # Ashby
    scheme, host, port = validate_ats_url(
        "https://api.ashbyhq.com/posting-api/job-board/acme", provider="ashby"
    )
    assert host == "api.ashbyhq.com"


def test_validate_ats_url_rejects_unauthorized_hosts() -> None:
    unauthorized = (
        "https://evil.attacker.com/v1/boards/acme",
        "https://localhost/v1/boards/acme",
        "https://127.0.0.1/v1/boards/acme",
        "https://169.254.169.254/latest/meta-data",
        "https://boards-api.greenhouse.io.attacker.com/v1",
    )
    for url in unauthorized:
        with pytest.raises(PortError) as exc_info:
            validate_ats_url(url, provider="greenhouse")
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "not an authorized ATS endpoint" in exc_info.value.message


# ==============================================================================
# 3. DNS PRE-RESOLUTION & IP VALIDATION (ANTI-SSRF)
# ==============================================================================


@pytest.mark.parametrize(
    "prohibited_ip",
    [
        "127.0.0.1",
        "127.255.255.255",
        "::1",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "fc00::1",
        "fd00::1",
        "169.254.0.1",
        "169.254.169.254",  # AWS/GCP Metadata
        "169.254.169.253",
        "fe80::1",
        "fd00:ec2::254",  # AWS IMDSv6
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_dns_resolution_rejects_prohibited_ips(prohibited_ip: str) -> None:
    async def scenario() -> None:
        fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (prohibited_ip, 443))]
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(return_value=fake_addrinfo)
            with pytest.raises(PortError) as exc_info:
                await resolve_and_validate_ats_dns(
                    "boards-api.greenhouse.io", 443, provider="greenhouse"
                )
            assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
            assert "prohibited" in exc_info.value.message

    asyncio.run(scenario())


def test_dns_resolution_fails_closed_on_mixed_public_and_private_answers() -> None:
    async def scenario() -> None:
        # One valid public IP and one private RFC1918 IP
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(return_value=fake_addrinfo)
            with pytest.raises(PortError) as exc_info:
                await resolve_and_validate_ats_dns(
                    "boards-api.greenhouse.io", 443, provider="greenhouse"
                )
            assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
            assert "prohibited" in exc_info.value.message

    asyncio.run(scenario())


def test_dns_resolution_accepts_valid_public_ips() -> None:
    async def scenario() -> None:
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443)),
        ]
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(return_value=fake_addrinfo)
            ips = await resolve_and_validate_ats_dns(
                "boards-api.greenhouse.io", 443, provider="greenhouse"
            )
            assert ips == ["93.184.215.14", "2606:4700:4700::1111"]

    asyncio.run(scenario())


# ==============================================================================
# 4. MANUAL REDIRECT SSRF TESTS
# ==============================================================================


def test_redirect_to_private_ip_is_blocked() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == "https://boards-api.greenhouse.io/v1/start":
                return httpx.Response(302, headers={"Location": "https://169.254.169.254/secret"})
            return httpx.Response(200, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/start",
                provider="greenhouse",
                ctx=_ctx(),
            )
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "not an authorized ATS endpoint" in exc_info.value.message

    asyncio.run(scenario())


def test_redirect_to_http_is_blocked() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == "https://boards-api.greenhouse.io/v1/start":
                return httpx.Response(
                    302, headers={"Location": "http://boards-api.greenhouse.io/v1/target"}
                )
            return httpx.Response(200, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/start",
                provider="greenhouse",
                ctx=_ctx(),
            )
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "HTTPS is required" in exc_info.value.message

    asyncio.run(scenario())


def test_redirect_loop_is_detected_and_blocked() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "hop1" in url_str:
                return httpx.Response(
                    302, headers={"Location": "https://boards-api.greenhouse.io/v1/hop2"}
                )
            return httpx.Response(
                302, headers={"Location": "https://boards-api.greenhouse.io/v1/hop1"}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/hop1",
                provider="greenhouse",
                ctx=_ctx(),
            )
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR
        assert "redirect loop" in exc_info.value.message

    asyncio.run(scenario())


# ==============================================================================
# 5. STREAMING RESPONSE BODY BOUNDS TESTS (5 MiB LIMIT)
# ==============================================================================


def test_content_length_exceeding_limit_fails_fast() -> None:
    async def scenario() -> None:
        headers = {"content-length": str(MAX_ATS_RESPONSE_BYTES + 1000)}
        mock_resp = httpx.Response(
            200,
            headers=headers,
            content=b"",
            request=httpx.Request("GET", "https://boards-api.greenhouse.io/v1/jobs"),
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_resp))

        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/jobs",
                provider="greenhouse",
                ctx=_ctx(),
            )
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR
        assert "exceeded max allowed bytes" in exc_info.value.message

    asyncio.run(scenario())


def test_chunked_body_exceeding_limit_aborts_during_stream() -> None:
    async def scenario() -> None:
        # No Content-Length header, body stream exceeds 5 MiB
        oversized = b"X" * (MAX_ATS_RESPONSE_BYTES + 500)

        class ChunkedStream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield oversized[:1000]
                yield oversized[1000:]

        mock_resp = httpx.Response(
            200,
            headers={"content-type": "application/json", "transfer-encoding": "chunked"},
            stream=ChunkedStream(),
            request=httpx.Request("GET", "https://boards-api.greenhouse.io/v1/jobs"),
        )
        mock_resp.headers.pop("content-length", None)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_resp))

        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/jobs",
                provider="greenhouse",
                ctx=_ctx(),
            )
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR
        assert "exceeded maximum size" in exc_info.value.message

    asyncio.run(scenario())


def test_decompression_bomb_aborts_immediately_on_stream_expansion() -> None:
    async def scenario() -> None:
        # A small compressed payload that decompresses into > 5 MiB
        large_uncompressed = b"A" * (MAX_ATS_RESPONSE_BYTES + 4096)
        compressed_small = gzip.compress(large_uncompressed)

        assert len(compressed_small) < 50000  # Wire bytes are tiny

        mock_resp = httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
            content=compressed_small,
            request=httpx.Request("GET", "https://boards-api.greenhouse.io/v1/jobs"),
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_resp))

        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/jobs",
                provider="greenhouse",
                ctx=_ctx(),
            )
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR
        assert "exceeded maximum size" in exc_info.value.message

    asyncio.run(scenario())


def test_body_exactly_at_limit_is_accepted() -> None:
    async def scenario() -> None:
        body = b"B" * MAX_ATS_RESPONSE_BYTES
        mock_resp = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
            request=httpx.Request("GET", "https://boards-api.greenhouse.io/v1/jobs"),
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_resp))

        resp = await send_ats_request(
            client,
            "GET",
            "https://boards-api.greenhouse.io/v1/jobs",
            provider="greenhouse",
            ctx=_ctx(),
        )
        assert resp.status_code == 200
        assert len(resp.content) == MAX_ATS_RESPONSE_BYTES

    asyncio.run(scenario())


def test_body_below_limit_is_accepted() -> None:
    async def scenario() -> None:
        payload = b'{"jobs": []}'
        mock_resp = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=payload,
            request=httpx.Request("GET", "https://boards-api.greenhouse.io/v1/jobs"),
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_resp))

        resp = await send_ats_request(
            client,
            "GET",
            "https://boards-api.greenhouse.io/v1/jobs",
            provider="greenhouse",
            ctx=_ctx(),
        )
        assert resp.status_code == 200
        assert resp.content == payload

    asyncio.run(scenario())


def test_redirect_deadline_recomputed_per_hop_and_aborts_when_expired() -> None:
    async def scenario() -> None:
        from datetime import timedelta

        # Initial deadline 10s in the future
        deadline_ctx = CallContext.with_timeout("test-deadline", timeout_seconds=10.0)

        hop_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal hop_count
            hop_count += 1
            if str(request.url) == "https://boards-api.greenhouse.io/v1/hop1":
                # Advance deadline artificially to the past before hop2
                object.__setattr__(
                    deadline_ctx.deadline, "at", datetime.now(UTC) - timedelta(seconds=1)
                )
                return httpx.Response(
                    302, headers={"Location": "https://boards-api.greenhouse.io/v1/hop2"}
                )
            return httpx.Response(200, json={"ok": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(PortError) as exc_info:
            await send_ats_request(
                client,
                "GET",
                "https://boards-api.greenhouse.io/v1/hop1",
                provider="greenhouse",
                ctx=deadline_ctx,
            )
        assert exc_info.value.code == PortErrorCode.TIMEOUT
        assert "deadline has already expired before request dispatch" in exc_info.value.message
        # Hop 2 was never dispatched because deadline was recomputed before hop 2
        assert hop_count == 1

    asyncio.run(scenario())
