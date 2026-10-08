"""Shared HTTP, SSRF-safe transport, streaming bounds, and error mapping
for ATS connectors (WP2.1-B).

Security and operational invariants:
- Translates external provider transport/HTTP errors to provider-neutral `PortError`.
- Enforces deadline budgets from `CallContext`.
- Strict SSRF protection: HTTPS only, official provider hosts only, DNS pre-resolution,
  validation of all IPs against private/loopback/link-local/metadata ranges, connection pinning.
- Manual redirect loops (up to 3 hops) with complete destination re-validation per hop.
- Streaming response body consumption enforcing maximum 5 MiB decoded/uncompressed limit.
- Enforces trust_env=False to prevent proxy hijacking.
- Never leaks credentials, raw tokens, or internal IPs in error messages.
"""

import asyncio
import email.utils
import ipaddress
import json
import logging
import socket
import urllib.parse
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import httpx

from tapjob_api.jobs.http_client import (
    PinnedAsyncHTTPTransport,
    is_safe_public_ip,
)
from tapjob_api.ports import CallContext, PortError, PortErrorCode

logger = logging.getLogger(__name__)

MAX_ATS_RESPONSE_BYTES = 5_242_880  # 5 MiB
MAX_ATS_REDIRECTS = 3
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 10.0

OFFICIAL_ATS_HOSTS_BY_PROVIDER: dict[str, frozenset[str]] = {
    "greenhouse": frozenset({"boards-api.greenhouse.io"}),
    "lever": frozenset({"api.lever.co", "api.eu.lever.co"}),
    "ashby": frozenset({"api.ashbyhq.com"}),
}


def parse_retry_after(header_value: str | None) -> float | None:
    """Parse HTTP Retry-After header as either seconds or HTTP-date."""
    if not header_value or not header_value.strip():
        return None
    val = header_value.strip()
    try:
        return float(val)
    except ValueError:
        pass
    try:
        parsed_tuple = email.utils.parsedate_to_datetime(val)
        if parsed_tuple.tzinfo is None:
            parsed_tuple = parsed_tuple.replace(tzinfo=UTC)
        diff = (parsed_tuple.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        return max(0.0, diff)
    except Exception:
        return None


def calculate_effective_timeout(
    ctx: CallContext | None,
    default_read: float = DEFAULT_READ_TIMEOUT_SECONDS,
    default_connect: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    *,
    provider: str = "ats",
) -> httpx.Timeout:
    """Calculate effective httpx.Timeout taking CallContext deadline into account."""
    if ctx is not None:
        remaining = ctx.deadline.remaining_seconds()
        if remaining <= 0:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=provider,
                message="CallContext deadline has already expired before request dispatch",
                transient=True,
            )
        connect = min(default_connect, remaining)
        read = min(default_read, remaining)
        return httpx.Timeout(timeout=remaining, connect=connect, read=read)
    return httpx.Timeout(timeout=default_read, connect=default_connect, read=default_read)


def validate_ats_url(
    url: str,
    *,
    provider: str,
    allow_custom_hosts: bool = False,
) -> tuple[str, str, int]:
    """Validate ATS endpoint URL syntax, scheme, userinfo, and host authority.

    Returns:
        (scheme, hostname, port)
    """
    stripped = url.strip()
    if not stripped:
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=provider,
            message="ATS endpoint URL cannot be empty",
            transient=False,
        )

    parsed = urllib.parse.urlsplit(stripped)
    scheme = parsed.scheme.lower()
    if scheme != "https":
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=provider,
            message=f"HTTPS is required for ATS endpoints, got '{scheme}'",
            transient=False,
        )

    if parsed.username or parsed.password:
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=provider,
            message="URLs containing userinfo are forbidden",
            transient=False,
        )

    hostname = parsed.hostname
    if not hostname:
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=provider,
            message="URL hostname is required",
            transient=False,
        )

    normalized_host = hostname.lower()
    if not allow_custom_hosts:
        allowed = OFFICIAL_ATS_HOSTS_BY_PROVIDER.get(provider, frozenset())
        if normalized_host not in allowed:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=provider,
                message=f"Host '{hostname}' is not an authorized ATS endpoint for {provider}",
                transient=False,
            )

    port = parsed.port or 443
    if not (1 <= port <= 65535):
        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=provider,
            message=f"Invalid port: {port}",
            transient=False,
        )

    return scheme, normalized_host, port


async def resolve_and_validate_ats_dns(
    host: str,
    port: int,
    *,
    provider: str,
) -> list[str]:
    """Pre-resolve DNS (A and AAAA) and validate that every IP is globally reachable and public."""
    loop = asyncio.get_running_loop()
    try:
        addr_info = await loop.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except (socket.gaierror, socket.herror, OSError) as error:
        raise PortError(
            PortErrorCode.UNAVAILABLE,
            provider=provider,
            message=f"Failed to resolve DNS for {provider} host '{host}'",
            transient=True,
        ) from error

    if not addr_info:
        raise PortError(
            PortErrorCode.UNAVAILABLE,
            provider=provider,
            message=f"No DNS records found for {provider} host '{host}'",
            transient=True,
        )

    resolved_ips: list[str] = []
    for info in addr_info:
        sockaddr = info[4]
        ip_str = str(sockaddr[0])
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    # Validate EVERY resolved IP. Fail closed if any IP is private or prohibited.
    for ip_str in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as error:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=provider,
                message=f"Invalid destination IP address for {provider}",
                transient=False,
            ) from error

        if not is_safe_public_ip(ip_obj):
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=provider,
                message=f"Destination address for {provider} host '{host}' is prohibited",
                transient=False,
            )

    return resolved_ips


async def send_ats_request(
    client: httpx.AsyncClient | None,
    method: str,
    url: str,
    *,
    provider: str,
    ctx: CallContext | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allow_custom_hosts: bool = False,
    max_response_bytes: int = MAX_ATS_RESPONSE_BYTES,
) -> httpx.Response:
    """Send an ATS HTTP request with SSRF pinning, timeouts, bounded size, and error mapping."""
    req_headers = dict(headers or {})
    if ctx is not None and ctx.correlation_id:
        req_headers["x-correlation-id"] = ctx.correlation_id

    current_url = url
    visited_urls: set[str] = {current_url}
    redirect_count = 0

    while True:
        # Re-evaluate remaining deadline before each hop dispatch (Hardening H)
        timeout = calculate_effective_timeout(ctx, provider=provider)

        _scheme, host, port = validate_ats_url(
            current_url, provider=provider, allow_custom_hosts=allow_custom_hosts
        )

        # Check if caller supplied an in-memory MockTransport for unit testing
        is_mock_client = (
            client is not None
            and hasattr(client, "_transport")
            and isinstance(client._transport, httpx.MockTransport)
        )

        owns_client = False
        if is_mock_client and client is not None:
            exec_client = client
        else:
            resolved_ips = await resolve_and_validate_ats_dns(host, port, provider=provider)
            pinned_ip = resolved_ips[0]
            transport = PinnedAsyncHTTPTransport(pinned_ip=pinned_ip)
            exec_client = httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                timeout=timeout,
                verify=True,
            )
            owns_client = True

        try:
            async with exec_client.stream(
                method=method,
                url=current_url,
                params=params if redirect_count == 0 else None,
                headers=req_headers,
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                # Handle redirects manually with full destination re-validation
                if response.status_code in (
                    HTTPStatus.MOVED_PERMANENTLY,
                    HTTPStatus.FOUND,
                    HTTPStatus.SEE_OTHER,
                    HTTPStatus.TEMPORARY_REDIRECT,
                    HTTPStatus.PERMANENT_REDIRECT,
                ):
                    redirect_count += 1
                    if redirect_count > MAX_ATS_REDIRECTS:
                        raise PortError(
                            PortErrorCode.PROVIDER_ERROR,
                            provider=provider,
                            message=f"{provider} maximum redirect limit exceeded",
                            transient=False,
                        )

                    location = response.headers.get("location")
                    if not location:
                        raise PortError(
                            PortErrorCode.PROVIDER_ERROR,
                            provider=provider,
                            message=f"{provider} redirect missing Location header",
                            transient=False,
                        )

                    next_url = urllib.parse.urljoin(current_url, location)
                    if next_url in visited_urls:
                        raise PortError(
                            PortErrorCode.PROVIDER_ERROR,
                            provider=provider,
                            message=f"{provider} redirect loop detected",
                            transient=False,
                        )

                    visited_urls.add(next_url)
                    current_url = next_url
                    continue

                # Fast-fail on Content-Length header
                content_len_hdr = response.headers.get("content-length")
                if (
                    content_len_hdr
                    and content_len_hdr.isdigit()
                    and int(content_len_hdr) > max_response_bytes
                ):
                    raise PortError(
                        PortErrorCode.PROVIDER_ERROR,
                        provider=provider,
                        message=(
                            f"Response from {provider} exceeded max allowed bytes "
                            f"({max_response_bytes})"
                        ),
                        transient=False,
                    )

                # Stream and accumulate uncompressed/decoded bytes
                total_bytes = 0
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > max_response_bytes:
                        raise PortError(
                            PortErrorCode.PROVIDER_ERROR,
                            provider=provider,
                            message=(
                                f"Response body from {provider} exceeded maximum size "
                                f"({max_response_bytes})"
                            ),
                            transient=False,
                        )
                    chunks.append(chunk)

                body_content = b"".join(chunks)

                # Map status codes to PortError
                status = response.status_code
                if status == HTTPStatus.TOO_MANY_REQUESTS:
                    retry_after = parse_retry_after(response.headers.get("retry-after"))
                    raise PortError(
                        PortErrorCode.RATE_LIMITED,
                        provider=provider,
                        message=f"{provider} rate limit exceeded",
                        transient=True,
                        retry_after_seconds=retry_after,
                    )
                if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise PortError(
                        PortErrorCode.AUTH_FAILED,
                        provider=provider,
                        message=f"{provider} authentication/authorization failed (status {status})",
                        transient=False,
                    )
                if status == HTTPStatus.NOT_FOUND:
                    raise PortError(
                        PortErrorCode.INVALID_REQUEST,
                        provider=provider,
                        message=f"{provider} resource not found (status 404)",
                        transient=False,
                    )
                if status >= 500:
                    raise PortError(
                        PortErrorCode.UNAVAILABLE,
                        provider=provider,
                        message=f"{provider} server error (status {status})",
                        transient=True,
                    )
                if status >= 400:
                    raise PortError(
                        PortErrorCode.INVALID_REQUEST,
                        provider=provider,
                        message=f"{provider} request error (status {status})",
                        transient=False,
                    )

                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=body_content,
                    request=response.request,
                )

        except httpx.TimeoutException as exc:
            raise PortError(
                PortErrorCode.TIMEOUT,
                provider=provider,
                message=f"Request to {provider} timed out",
                transient=True,
            ) from exc
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            raise PortError(
                PortErrorCode.UNAVAILABLE,
                provider=provider,
                message=f"Network error communicating with {provider}",
                transient=True,
            ) from exc
        except PortError:
            raise
        except Exception as exc:
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=provider,
                message=f"Unexpected error communicating with {provider}",
                transient=True,
            ) from exc
        finally:
            if owns_client:
                await exec_client.aclose()


def parse_json_response(response: httpx.Response, *, provider: str) -> Any:
    """Parse JSON body safely, raising PortError on invalid payload."""
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise PortError(
            PortErrorCode.PROVIDER_ERROR,
            provider=provider,
            message=f"Invalid JSON returned by {provider}",
            transient=False,
        ) from exc
