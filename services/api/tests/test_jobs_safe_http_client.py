"""Unit tests for SafeHttpClient anti-SSRF protections and streaming safety bounds (WP2.1-A)."""

import asyncio
import contextlib
import gzip
import ipaddress
import os
import socket
import ssl
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tapjob_api.jobs.errors import (
    JobContentTypeUnsupportedError,
    JobUrlDestinationForbiddenError,
    JobUrlInvalidError,
    JobUrlTlsFailedError,
    JobUrlTooLargeError,
    JobUrlUnreachableError,
)
from tapjob_api.jobs.http_client import (
    MAX_JOB_URL_DECODED_BYTES,
    SafeHttpClient,
    is_safe_public_ip,
)


@pytest.mark.parametrize(
    ("ip_str", "is_safe"),
    [
        # Loopback
        ("127.0.0.1", False),
        ("127.255.255.255", False),
        ("::1", False),
        # RFC1918 Private
        ("10.0.0.1", False),
        ("172.16.0.1", False),
        ("172.31.255.255", False),
        ("192.168.1.1", False),
        # IPv6 ULA
        ("fc00::1", False),
        ("fd00::1", False),
        # Link-local
        ("169.254.0.1", False),
        ("169.254.169.254", False),  # AWS/GCP Metadata
        ("169.254.169.253", False),
        ("fe80::1", False),
        ("fd00:ec2::254", False),  # AWS IMDSv6
        # CGNAT
        ("100.64.0.1", False),
        ("100.127.255.255", False),
        # 0.0.0.0/8
        ("0.0.0.0", False),  # noqa: S104
        ("0.1.2.3", False),
        # Multicast
        ("224.0.0.1", False),
        ("ff02::1", False),
        # Reserved / Broadcast
        ("240.0.0.1", False),
        ("255.255.255.255", False),
        # Documentation & Benchmarking
        ("192.0.2.1", False),
        ("198.51.100.1", False),
        ("203.0.113.1", False),
        ("198.18.0.1", False),
        ("2001:db8::1", False),
        # IPv4-mapped IPv6
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:169.254.169.254", False),
        # Valid Public IP space
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("93.184.215.14", True),
        ("2606:4700:4700::1111", True),
    ],
)
def test_is_safe_public_ip_comprehensive_matrix(ip_str: str, is_safe: bool) -> None:
    ip_obj = ipaddress.ip_address(ip_str)
    assert is_safe_public_ip(ip_obj) is is_safe


def test_validate_url_rejects_unapproved_schemes() -> None:
    client = SafeHttpClient()
    for bad_url in (
        "file:///etc/passwd",
        "ftp://ftp.example.com/file",
        "gopher://gopher.example.com",
        "data:text/html,<h1>bad</h1>",
        "ws://example.com/socket",
        "javascript:alert(1)",
    ):
        with pytest.raises(JobUrlInvalidError):
            client.parse_and_validate_url(bad_url)


def test_validate_url_rejects_userinfo() -> None:
    client = SafeHttpClient()
    with pytest.raises(JobUrlInvalidError):
        client.parse_and_validate_url("https://user:password@example.com/jobs/1")
    with pytest.raises(JobUrlInvalidError):
        client.parse_and_validate_url("https://user@example.com/jobs/1")


def test_resolve_and_validate_dns_fails_closed_on_prohibited_ip() -> None:
    async def _test() -> None:
        client = SafeHttpClient()
        fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(return_value=fake_addrinfo)
            with pytest.raises(JobUrlDestinationForbiddenError):
                await client.resolve_and_validate_dns("localhost", 443)

    asyncio.run(_test())


def test_resolve_and_validate_dns_fails_closed_on_mixed_dns_answers() -> None:
    async def _test() -> None:
        # If DNS returns one public IP and one private IP, destination must be rejected
        client = SafeHttpClient()
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),  # public
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),  # private RFC1918
        ]

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(return_value=fake_addrinfo)
            with pytest.raises(JobUrlDestinationForbiddenError):
                await client.resolve_and_validate_dns("split-brain.example.com", 443)

    asyncio.run(_test())


def test_streaming_bounds_rejects_uncompressed_body_exceeding_2mib() -> None:
    async def _test() -> None:
        client = SafeHttpClient(max_decoded_bytes=100)  # Low bound for test

        oversized_content = b"A" * 150
        mock_resp = httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=oversized_content,
            request=httpx.Request("GET", "https://example.com/jobs/1"),
        )

        with (
            patch.object(client, "resolve_and_validate_dns", return_value=["93.184.215.14"]),
            patch("httpx.AsyncClient.stream") as mock_stream,
        ):
            mock_stream.return_value.__aenter__.return_value = mock_resp
            with pytest.raises(JobUrlTooLargeError):
                await client.fetch("https://example.com/jobs/1")

    asyncio.run(_test())


def test_decompression_bomb_aborts_immediately_on_stream_expansion() -> None:
    async def _test() -> None:
        # A tiny 1 KB compressed gzip payload expands to > 2 MiB
        client = SafeHttpClient(max_decoded_bytes=MAX_JOB_URL_DECODED_BYTES)
        decompressed_large = b"0" * (MAX_JOB_URL_DECODED_BYTES + 1024)
        compressed_small = gzip.compress(decompressed_large)

        assert len(compressed_small) < 10000  # Wire bytes are tiny

        # Mock response returning compressed bytes with Content-Encoding
        mock_resp = httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-encoding": "gzip",
            },
            content=compressed_small,
            request=httpx.Request("GET", "https://example.com/jobs/bomb"),
        )

        with (
            patch.object(client, "resolve_and_validate_dns", return_value=["93.184.215.14"]),
            patch("httpx.AsyncClient.stream") as mock_stream,
        ):
            mock_stream.return_value.__aenter__.return_value = mock_resp
            with pytest.raises(JobUrlTooLargeError):
                await client.fetch("https://example.com/jobs/bomb")

    asyncio.run(_test())


def test_environment_proxy_variables_are_ignored() -> None:
    async def _test() -> None:
        os.environ["HTTP_PROXY"] = "http://evil-proxy:8080"
        os.environ["HTTPS_PROXY"] = "http://evil-proxy:8080"
        os.environ["ALL_PROXY"] = "socks5://evil-proxy:1080"

        try:
            client = SafeHttpClient()
            mock_resp = httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><body><h1>Job Posting</h1></body></html>",
                request=httpx.Request("GET", "https://example.com/jobs/1"),
            )

            with (
                patch.object(client, "resolve_and_validate_dns", return_value=["93.184.215.14"]),
                patch("httpx.AsyncClient.__init__", return_value=None) as mock_client_init,
                patch("httpx.AsyncClient.stream") as mock_stream,
            ):
                mock_stream.return_value.__aenter__.return_value = mock_resp
                with contextlib.suppress(Exception):
                    await client.fetch("https://example.com/jobs/1")
                # Verify trust_env=False was passed explicitly
                assert mock_client_init.call_args is not None
                assert mock_client_init.call_args.kwargs.get("trust_env") is False
        finally:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)
            os.environ.pop("ALL_PROXY", None)

    asyncio.run(_test())


def test_redirect_to_private_ip_is_blocked() -> None:
    async def _test() -> None:
        client = SafeHttpClient()

        resp_redirect = httpx.Response(
            302,
            headers={"Location": "https://internal.corp/secret"},
            request=httpx.Request("GET", "https://public.example.com/job"),
        )

        # First destination resolves public IP; second destination resolves private RFC1918 IP
        dns_resolutions = [
            ["93.184.215.14"],  # Initial public host
        ]

        async def mock_dns(host: str, port: int) -> list[str]:
            if "internal.corp" in host:
                raise JobUrlDestinationForbiddenError("Forbidden internal IP")
            return dns_resolutions.pop(0)

        with (
            patch.object(client, "resolve_and_validate_dns", side_effect=mock_dns),
            patch("httpx.AsyncClient.stream") as mock_stream,
        ):
            mock_stream.return_value.__aenter__.return_value = resp_redirect
            with pytest.raises(JobUrlDestinationForbiddenError):
                await client.fetch("https://public.example.com/job")

    asyncio.run(_test())


def test_max_redirects_exceeded_raises_unreachable() -> None:
    async def _test() -> None:
        client = SafeHttpClient()

        resp_redirect = httpx.Response(
            302,
            headers={"Location": "https://example.com/next"},
            request=httpx.Request("GET", "https://example.com/job"),
        )

        with (
            patch.object(client, "resolve_and_validate_dns", return_value=["93.184.215.14"]),
            patch("httpx.AsyncClient.stream") as mock_stream,
        ):
            mock_stream.return_value.__aenter__.return_value = resp_redirect
            with pytest.raises(JobUrlUnreachableError):
                await client.fetch("https://example.com/job")

    asyncio.run(_test())


def test_unsupported_content_type_raises_error() -> None:
    async def _test() -> None:
        client = SafeHttpClient()
        mock_resp = httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4...",
            request=httpx.Request("GET", "https://example.com/resume.pdf"),
        )

        with (
            patch.object(client, "resolve_and_validate_dns", return_value=["93.184.215.14"]),
            patch("httpx.AsyncClient.stream") as mock_stream,
        ):
            mock_stream.return_value.__aenter__.return_value = mock_resp
            with pytest.raises(JobContentTypeUnsupportedError):
                await client.fetch("https://example.com/resume.pdf")

    asyncio.run(_test())


def test_tls_certificate_verification_failure_raises_tls_error() -> None:
    async def _test() -> None:
        client = SafeHttpClient()

        cert_err = ssl.SSLCertVerificationError("cert failed")
        with (
            patch.object(client, "resolve_and_validate_dns", return_value=["93.184.215.14"]),
            patch("httpx.AsyncClient.stream", side_effect=cert_err),
            pytest.raises(JobUrlTlsFailedError),
        ):
            await client.fetch("https://invalid-tls.example.com/job")

    asyncio.run(_test())


def test_total_timeout_enforced_raises_unreachable_error() -> None:
    async def _test() -> None:
        client = SafeHttpClient(total_timeout_seconds=0.1)

        async def _hanging_dns(*args: Any, **kwargs: Any) -> list[str]:
            await asyncio.sleep(0.5)
            return ["93.184.215.14"]

        with (
            patch.object(client, "resolve_and_validate_dns", side_effect=_hanging_dns),
            pytest.raises(JobUrlUnreachableError) as exc_info,
        ):
            await client.fetch("https://example.com/hanging-job")

        assert "timed out" in str(exc_info.value).lower()

    asyncio.run(_test())
