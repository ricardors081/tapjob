"""Unit tests for GreenhouseJobSource adapter (WP2.1-B).

Conforms strictly to official Greenhouse Job Board API documentation:
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}
Documented contract fields:
- id, internal_job_id, title, location, absolute_url, updated_at, content
Invariants:
- Prospect Posts have internal_job_id = None/null and are filtered from discovery.
- Normal job postings retain their job post id as external_id.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from tapjob_api.jobs.sources.greenhouse import GreenhouseJobSource
from tapjob_api.ports import (
    AdapterHealthStatus,
    CallContext,
    CapabilityStatus,
    ExternalJobRef,
    JobSource,
    JobSourceCapability,
    PortError,
    PortErrorCode,
)


def _ctx() -> CallContext:
    return CallContext.with_timeout("test-corr", 10.0)


def test_greenhouse_statically_satisfies_job_source_protocol() -> None:
    source: JobSource = GreenhouseJobSource(board_token="test-board")
    assert source.provider == "greenhouse"


def test_greenhouse_capabilities() -> None:
    async def scenario() -> None:
        source = GreenhouseJobSource(board_token="test-board")
        caps = await source.capabilities(_ctx())
        assert caps.status_for(JobSourceCapability.DISCOVERY) == CapabilityStatus.SUPPORTED
        assert caps.status_for(JobSourceCapability.JOB_DETAILS) == CapabilityStatus.SUPPORTED
        assert caps.status_for(JobSourceCapability.INCREMENTAL_SYNC) == CapabilityStatus.UNSUPPORTED

    asyncio.run(scenario())


def test_greenhouse_discover_filters_prospect_posts_and_caches_content() -> None:
    async def scenario() -> None:
        # 1 normal job + 1 prospect post (internal_job_id = None)
        mock_response = {
            "jobs": [
                {
                    "id": 101,
                    "internal_job_id": 9901,
                    "title": "Backend Software Engineer",
                    "updated_at": "2026-03-01T15:30:00-03:00",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                    "location": {"name": "New York, NY"},
                    "content": "<p>Build high scale distributed systems.</p>",
                },
                {
                    "id": 102,
                    "internal_job_id": None,  # Prospect post / talent pool opening: must be ignored
                    "title": "General Talent Pool",
                    "updated_at": "2026-03-01T12:00:00Z",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
                    "location": None,
                    "content": "<p>Submit your resume for future openings.</p>",
                },
                {
                    "id": 103,
                    "internal_job_id": 9903,
                    "title": "Product Designer",
                    "updated_at": None,
                    "absolute_url": None,
                    "location": None,
                    "content": "<p>Design intuitive user experiences.</p>",
                },
            ]
        }

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response))
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", company_name="Acme Corp", client=client)

        page = await source.discover(None, _ctx())
        # Only 2 jobs returned (prospect post 102 filtered out)
        assert len(page.items) == 2
        assert page.next_cursor is None

        item1 = page.items[0]
        assert item1.ref.provider == "greenhouse"
        assert item1.ref.external_id == "101"
        assert item1.ref.url == "https://boards.greenhouse.io/acme/jobs/101"
        assert item1.title == "Backend Software Engineer"
        assert item1.company_name == "Acme Corp"
        assert item1.posted_at == datetime(2026, 3, 1, 18, 30, 0, tzinfo=UTC)
        assert item1.location_raw == "New York, NY"

        item2 = page.items[1]
        assert item2.ref.external_id == "103"
        assert item2.ref.url == "https://boards.greenhouse.io/acme/jobs/103"
        assert item2.posted_at is None
        assert item2.location_raw is None

        # Verify content was cached during discovery, avoiding N+1 requests
        details1 = await source.fetch_details(item1.ref, _ctx())
        assert details1.description_raw == "<p>Build high scale distributed systems.</p>"

        details2 = await source.fetch_details(item2.ref, _ctx())
        assert details2.description_raw == "<p>Design intuitive user experiences.</p>"

    asyncio.run(scenario())


def test_greenhouse_fetch_details_fetches_when_not_cached() -> None:
    async def scenario() -> None:
        mock_response = {
            "id": 101,
            "title": "Backend Software Engineer",
            "content": "<p>Build high scale distributed systems.</p>",
            "updated_at": "2026-03-01T12:00:00Z",
        }

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response))
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", client=client)

        ref = ExternalJobRef(
            provider="greenhouse",
            external_id="101",
            url="https://boards.greenhouse.io/acme/jobs/101",
        )
        details = await source.fetch_details(ref, _ctx())
        assert details.ref == ref
        assert details.description_raw == "<p>Build high scale distributed systems.</p>"
        assert details.fetched_at.tzinfo == UTC

    asyncio.run(scenario())


def test_greenhouse_fetch_details_provider_mismatch() -> None:
    async def scenario() -> None:
        source = GreenhouseJobSource(board_token="acme")
        ref = ExternalJobRef(provider="lever", external_id="101", url="https://example.com")
        with pytest.raises(PortError) as exc_info:
            await source.fetch_details(ref, _ctx())
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_greenhouse_health_healthy() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"name": "Acme"}))
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.HEALTHY
        assert health.reason_code is None

    asyncio.run(scenario())


def test_greenhouse_health_degraded_on_rate_limit() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"retry-after": "30"})
        )
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.DEGRADED
        assert health.reason_code == "rate_limited"

    asyncio.run(scenario())


def test_greenhouse_health_unavailable_on_500() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, text="Internal Server Error")
        )
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.UNAVAILABLE
        assert health.reason_code == "unavailable"

    asyncio.run(scenario())


def test_greenhouse_discover_rate_limited() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"retry-after": "60"})
        )
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", client=client)

        with pytest.raises(PortError) as exc_info:
            await source.discover(None, _ctx())
        assert exc_info.value.code == PortErrorCode.RATE_LIMITED
        assert exc_info.value.retry_after_seconds == 60.0
        assert exc_info.value.transient is True

    asyncio.run(scenario())


def test_greenhouse_discover_malformed_shape() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"not_jobs": []}))
        client = httpx.AsyncClient(transport=transport)
        source = GreenhouseJobSource(board_token="acme", client=client)

        with pytest.raises(PortError) as exc_info:
            await source.discover(None, _ctx())
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())
