"""Unit tests for LeverJobSource adapter (WP2.1-B)."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from tapjob_api.jobs.sources.lever import LeverJobSource
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


def test_lever_statically_satisfies_job_source_protocol() -> None:
    source: JobSource = LeverJobSource(site="test-site")
    assert source.provider == "lever"


def test_lever_capabilities() -> None:
    async def scenario() -> None:
        source = LeverJobSource(site="test-site")
        caps = await source.capabilities(_ctx())
        assert caps.status_for(JobSourceCapability.DISCOVERY) == CapabilityStatus.SUPPORTED
        assert caps.status_for(JobSourceCapability.JOB_DETAILS) == CapabilityStatus.SUPPORTED
        assert caps.status_for(JobSourceCapability.INCREMENTAL_SYNC) == CapabilityStatus.UNSUPPORTED

    asyncio.run(scenario())


def test_lever_discover_success_and_pagination() -> None:
    async def scenario() -> None:
        # Mock 100 items to trigger next_cursor
        mock_postings = [
            {
                "id": f"job-{i}",
                "text": f"Role {i}",
                "createdAt": 1772366400000,
                "hostedUrl": f"https://jobs.lever.co/netflix/job-{i}",
                "categories": {"location": "Remote", "team": "Platform"},
            }
            for i in range(100)
        ]

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_postings))
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", company_name="Netflix", client=client)

        page = await source.discover(None, _ctx())
        assert len(page.items) == 100
        assert page.next_cursor == "100"

        item0 = page.items[0]
        assert item0.ref.provider == "lever"
        assert item0.ref.external_id == "job-0"
        assert item0.ref.url == "https://jobs.lever.co/netflix/job-0"
        assert item0.title == "Role 0"
        assert item0.company_name == "Netflix"
        assert item0.posted_at == datetime.fromtimestamp(1772366400, tz=UTC)
        assert item0.location_raw == "Remote"

    asyncio.run(scenario())


def test_lever_discover_single_page() -> None:
    async def scenario() -> None:
        mock_postings = [
            {
                "id": "eng-1",
                "text": "Senior Engineer",
                "createdAt": 1772366400000,
                "hostedUrl": "https://jobs.lever.co/netflix/eng-1",
            }
        ]

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_postings))
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", client=client)

        page = await source.discover(None, _ctx())
        assert len(page.items) == 1
        assert page.next_cursor is None

    asyncio.run(scenario())


def test_lever_discover_multi_location_and_workplace_type_and_caching() -> None:
    async def scenario() -> None:
        mock_postings = [
            {
                "id": "eng-multi",
                "text": "Senior Distributed Systems Engineer",
                "createdAt": 1772366400000,
                "hostedUrl": "https://jobs.lever.co/netflix/eng-multi",
                "categories": {
                    "location": "San Francisco, CA",
                    "allLocations": ["San Francisco, CA", "New York, NY", "London, UK"],
                },
                "workplaceType": "hybrid",
                "description": "<p>Core platform team</p>",
                "lists": [{"text": "Tech Stack", "content": "<li>Python, PostgreSQL</li>"}],
                "additional": "<p>Competitive compensation</p>",
            }
        ]

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_postings))
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", company_name="Netflix", client=client)

        page = await source.discover(None, _ctx())
        assert len(page.items) == 1
        item = page.items[0]

        assert item.location_raw == "San Francisco, CA"
        # allLocations must NOT duplicate primary location
        assert item.additional_locations_raw == ("New York, NY", "London, UK")
        assert item.workplace_type_hint == "hybrid"

        # Details were cached from discover, no extra network request needed
        details = await source.fetch_details(item.ref, _ctx())
        assert "<p>Core platform team</p>" in details.description_raw
        assert "<h3>Tech Stack</h3>" in details.description_raw
        assert "<li>Python, PostgreSQL</li>" in details.description_raw

    asyncio.run(scenario())


def test_lever_fetch_details_success() -> None:
    async def scenario() -> None:
        mock_response = {
            "id": "eng-1",
            "text": "Senior Engineer",
            "description": "<p>About the role</p>",
            "lists": [
                {
                    "text": "Requirements",
                    "content": "<li>5+ years experience</li>",
                }
            ],
            "additional": "<p>Benefits and compensation</p>",
        }

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response))
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", client=client)

        ref = ExternalJobRef(
            provider="lever",
            external_id="eng-1",
            url="https://jobs.lever.co/netflix/eng-1",
        )
        details = await source.fetch_details(ref, _ctx())
        assert details.ref == ref
        assert "<p>About the role</p>" in details.description_raw
        assert "<h3>Requirements</h3>" in details.description_raw
        assert "<li>5+ years experience</li>" in details.description_raw
        assert "<p>Benefits and compensation</p>" in details.description_raw
        assert details.fetched_at.tzinfo == UTC

    asyncio.run(scenario())


def test_lever_fetch_details_provider_mismatch() -> None:
    async def scenario() -> None:
        source = LeverJobSource(site="netflix")
        ref = ExternalJobRef(provider="greenhouse", external_id="eng-1", url="https://example.com")
        with pytest.raises(PortError) as exc_info:
            await source.fetch_details(ref, _ctx())
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_lever_health_healthy() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[{"id": "1"}]))
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.HEALTHY
        assert health.reason_code is None

    asyncio.run(scenario())


def test_lever_health_unavailable_on_error() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(503, text="Service Unavailable")
        )
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.UNAVAILABLE
        assert health.reason_code == "unavailable"

    asyncio.run(scenario())


def test_lever_discover_malformed_shape() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"error": "not a list"})
        )
        client = httpx.AsyncClient(transport=transport)
        source = LeverJobSource(site="netflix", client=client)

        with pytest.raises(PortError) as exc_info:
            await source.discover(None, _ctx())
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())
