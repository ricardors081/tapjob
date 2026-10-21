"""Dedicated SSRF-safe HTTP client for Job URL Intake (WP2.1-A).

Security invariants:
- Pre-resolves DNS (A and AAAA); validates ALL returned IPs.
- Fails closed if ANY resolved address is private, loopback, link-local, multicast,
  metadata, or non-global.
- Eliminates DNS rebinding by pinning the TCP connection directly to the validated IP.
- Preserves original hostname for HTTP Host header, TLS SNI, and strict certificate verification.
- Enforces trust_env=False: ignores HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY.
- Disables automatic redirects; manual redirect loop re-validates each new destination.
- Enforces streaming response limit (MAX_JOB_URL_DECODED_BYTES = 2 MiB) on uncompressed bytes.
- Whitelists safe content types (text/html, application/xhtml+xml, text/plain).
- Never leaks internal resolved IPs or credentials in logs/exceptions.
"""

import asyncio
import ipaddress
import socket
import ssl
import urllib.parse
from http import HTTPStatus
from typing import Any

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from tapjob_api.jobs.errors import (
    JobContentTypeUnsupportedError,
    JobUrlDestinationForbiddenError,
    JobUrlInvalidError,
    JobUrlTlsFailedError,
    JobUrlTooLargeError,
    JobUrlUnreachableError,
)

MAX_JOB_URL_DECODED_BYTES = 2_097_152  # 2 MiB
MAX_REDIRECTS = 3
TOTAL_FETCH_TIMEOUT_SECONDS = 8.0
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 5.0

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})


def is_safe_public_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if ip_obj is an unreserved, globally reachable public IP address."""
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            return is_safe_public_ip(ip_obj.ipv4_mapped)
        if not ip_obj.is_global:
            return False
        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False
        return not (
            ip_obj in ipaddress.ip_network("2001:db8::/32")
            or ip_obj in ipaddress.ip_network("fc00::/7")
            or ip_obj in ipaddress.ip_network("fd00:ec2::/64")
        )

    if isinstance(ip_obj, ipaddress.IPv4Address):
        if not ip_obj.is_global:
            return False
        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False
        if (
            ip_obj in ipaddress.ip_network("100.64.0.0/10")
            or ip_obj in ipaddress.ip_network("0.0.0.0/8")
            or ip_obj in ipaddress.ip_network("192.0.2.0/24")
            or ip_obj in ipaddress.ip_network("198.51.100.0/24")
            or ip_obj in ipaddress.ip_network("203.0.113.0/24")
            or ip_obj in ipaddress.ip_network("198.18.0.0/15")
        ):
            return False
        return ip_obj not in (
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("169.254.169.253"),
        )

    return False


class PinnedNetworkBackend(AutoBackend):
    """Custom network backend connecting TCP directly to a pinned, pre-validated IP address."""

    def __init__(self, pinned_ip: str) -> None:
        super().__init__()
        self.pinned_ip = pinned_ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        # Ignore target `host` for TCP routing: connect directly to pre-validated IP address
        return await super().connect_tcp(
            self.pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """Transport that pins connection to a specific IP while preserving TLS SNI and Host header."""

    def __init__(self, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(trust_env=False, **kwargs)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._pool._ssl_context,
            network_backend=PinnedNetworkBackend(pinned_ip),
            http1=True,
            http2=False,
            retries=0,
        )


class SafeHttpClient:
    """SSRF-guarded HTTP client for URL intake."""

    def __init__(
        self,
        *,
        max_decoded_bytes: int = MAX_JOB_URL_DECODED_BYTES,
        total_timeout_seconds: float = TOTAL_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        self.max_decoded_bytes = max_decoded_bytes
        self.total_timeout_seconds = total_timeout_seconds

    def parse_and_validate_url(self, raw_url: str) -> tuple[str, str, int]:
        """Validate URL syntax, scheme, userinfo and return (scheme, host, port)."""
        stripped = raw_url.strip()
        if not stripped:
            raise JobUrlInvalidError("URL cannot be empty")

        parsed = urllib.parse.urlsplit(stripped)
        scheme = parsed.scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise JobUrlInvalidError(f"Unsupported scheme: {scheme}")

        if parsed.username or parsed.password:
            raise JobUrlInvalidError("URLs containing userinfo are forbidden")

        hostname = parsed.hostname
        if not hostname:
            raise JobUrlInvalidError("URL hostname is required")

        port = parsed.port or (443 if scheme == "https" else 80)
        if not (1 <= port <= 65535):
            raise JobUrlInvalidError(f"Invalid port: {port}")

        return scheme, hostname, port

    async def resolve_and_validate_dns(self, host: str, port: int) -> list[str]:
        """Pre-resolve A and AAAA records and validate every IP address. Fails closed."""
        loop = asyncio.get_running_loop()
        try:
            addr_info = await loop.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except (socket.gaierror, socket.herror, OSError) as error:
            raise JobUrlUnreachableError("Failed to resolve host") from error

        if not addr_info:
            raise JobUrlUnreachableError("No DNS records resolved for host")

        resolved_ips: list[str] = []
        for info in addr_info:
            sockaddr = info[4]
            ip_str = str(sockaddr[0])
            if ip_str not in resolved_ips:
                resolved_ips.append(ip_str)

        # Validate EVERY resolved IP. Fail closed if any IP is prohibited.
        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as error:
                raise JobUrlDestinationForbiddenError("Invalid destination IP address") from error

            if not is_safe_public_ip(ip_obj):
                raise JobUrlDestinationForbiddenError("Destination address is not permitted")

        return resolved_ips

    async def fetch(self, initial_url: str) -> tuple[str, str, str]:
        """Fetch URL with SSRF protection, manual redirects, and bounded decoded streaming.

        Returns:
            (final_canonical_url, content_type, decoded_body_text)
        """
        current_url = initial_url
        visited_urls: set[str] = {current_url}
        redirect_count = 0

        try:
            async with asyncio.timeout(self.total_timeout_seconds):
                while True:
                    _scheme, host, port = self.parse_and_validate_url(current_url)
                    resolved_ips = await self.resolve_and_validate_dns(host, port)
                    pinned_ip = resolved_ips[0]

                    transport = PinnedAsyncHTTPTransport(pinned_ip=pinned_ip)
                    timeout = httpx.Timeout(
                        connect=CONNECT_TIMEOUT_SECONDS,
                        read=READ_TIMEOUT_SECONDS,
                        write=CONNECT_TIMEOUT_SECONDS,
                        pool=CONNECT_TIMEOUT_SECONDS,
                    )

                    headers = {
                        "User-Agent": "TapJob-Intake/1.0 (+https://tapjob.com.br)",
                        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
                        "Accept-Encoding": "gzip, deflate, br",
                    }

                    try:
                        async with (
                            httpx.AsyncClient(
                                transport=transport,
                                trust_env=False,
                                timeout=timeout,
                                verify=True,
                            ) as client,
                            client.stream(
                                "GET",
                                current_url,
                                headers=headers,
                                follow_redirects=False,
                            ) as response,
                        ):
                            # Check for redirect
                            if response.status_code in (
                                HTTPStatus.MOVED_PERMANENTLY,
                                HTTPStatus.FOUND,
                                HTTPStatus.SEE_OTHER,
                                HTTPStatus.TEMPORARY_REDIRECT,
                                HTTPStatus.PERMANENT_REDIRECT,
                            ):
                                redirect_count += 1
                                if redirect_count > MAX_REDIRECTS:
                                    raise JobUrlUnreachableError("Maximum redirect limit exceeded")

                                location = response.headers.get("Location")
                                if not location:
                                    raise JobUrlUnreachableError("Redirect missing Location header")

                                next_url = urllib.parse.urljoin(current_url, location)
                                if next_url in visited_urls:
                                    raise JobUrlUnreachableError("Redirect loop detected")

                                visited_urls.add(next_url)
                                current_url = next_url
                                continue

                            if response.status_code != HTTPStatus.OK:
                                raise JobUrlUnreachableError(
                                    f"Upstream server returned HTTP {response.status_code}"
                                )

                            content_type_header = response.headers.get("content-type", "")
                            media_type = content_type_header.split(";")[0].strip().lower()
                            if media_type not in ALLOWED_MEDIA_TYPES:
                                raise JobContentTypeUnsupportedError(
                                    f"Unsupported content-type: {media_type}"
                                )

                            # Stream uncompressed/decoded content enforcing safety boundary
                            total_bytes = 0
                            chunks: list[bytes] = []
                            async for chunk in response.aiter_bytes():
                                total_bytes += len(chunk)
                                if total_bytes > self.max_decoded_bytes:
                                    raise JobUrlTooLargeError(
                                        "Response exceeded maximum 2 MiB limit"
                                    )
                                chunks.append(chunk)

                            raw_content = b"".join(chunks)
                            encoding = response.encoding or "utf-8"
                            try:
                                text = raw_content.decode(encoding, errors="replace")
                            except Exception:
                                text = raw_content.decode("utf-8", errors="replace")

                            return current_url, media_type, text

                    except (httpx.ConnectError, ssl.SSLError) as error:
                        # Distinguish TLS/SSL failures
                        is_cert_err = (
                            isinstance(error, ssl.SSLCertVerificationError)
                            or "certificate" in str(error).lower()
                        )
                        if is_cert_err:
                            raise JobUrlTlsFailedError(
                                "TLS certificate validation failed"
                            ) from error
                        raise JobUrlUnreachableError("Failed to connect to host") from error
                    except httpx.TimeoutException as error:
                        raise JobUrlUnreachableError("Connection timed out") from error
                    except (
                        JobUrlInvalidError,
                        JobUrlDestinationForbiddenError,
                        JobUrlUnreachableError,
                        JobUrlTlsFailedError,
                        JobUrlTooLargeError,
                        JobContentTypeUnsupportedError,
                    ):
                        raise
                    except Exception as error:
                        raise JobUrlUnreachableError("Unexpected HTTP transport error") from error
        except TimeoutError as error:
            raise JobUrlUnreachableError("Connection timed out") from error
