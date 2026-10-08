"""Unit tests for AshbyJobSource adapter (WP2.1-B).

Conforms strictly to official Ashby Public Job Board API contract:
- GET https://api.ashbyhq.com/posting-api/job-board/{board_token}
Documented contract fields:
- title, location, secondaryLocations (list of dicts), department, team,
  isRemote, workplaceType, descriptionHtml, descriptionPlain, publishedAt,
  employmentType, address, jobUrl, applyUrl, isListed.
Notice: jobs[].id is NOT in the documented official public contract.
"""

import asyncio
import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from tapjob_api.jobs.sources.ashby import AshbyJobSource
from tapjob_api.jobs.url_canonicalization import canonicalize_url
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


def test_ashby_statically_satisfies_job_source_protocol() -> None:
    source: JobSource = AshbyJobSource(board_token="test-token")
    assert source.provider == "ashby"


def test_ashby_capabilities() -> None:
    async def scenario() -> None:
        source = AshbyJobSource(board_token="test-token")
        caps = await source.capabilities(_ctx())
        assert caps.status_for(JobSourceCapability.DISCOVERY) == CapabilityStatus.SUPPORTED
        assert caps.status_for(JobSourceCapability.JOB_DETAILS) == CapabilityStatus.SUPPORTED
        assert caps.status_for(JobSourceCapability.INCREMENTAL_SYNC) == CapabilityStatus.UNSUPPORTED

    asyncio.run(scenario())


def test_ashby_discover_official_contract_without_id_produces_stable_external_id() -> None:
    async def scenario() -> None:
        # Official Ashby contract fixture: apiVersion "1", NO "id" field, secondaryLocations objects
        url1 = "https://jobs.ashbyhq.com/monzo/5a1b2c3d-architect"
        url2 = "https://jobs.ashbyhq.com/monzo/9e8d7c6b-data-science"
        unlisted_url = "https://jobs.ashbyhq.com/monzo/hidden-unlisted"

        mock_response = {
            "apiVersion": "1",
            "jobs": [
                {
                    "title": "Principal Architect",
                    "jobUrl": url1,
                    "applyUrl": f"{url1}/apply",
                    "publishedAt": "2026-03-01T14:00:00.000Z",
                    "location": "London, UK",
                    "secondaryLocations": [{"location": "Manchester, UK"}],
                    "isRemote": False,
                    "workplaceType": "Hybrid",
                    "descriptionHtml": "<p>Lead technical evolution</p>",
                    "descriptionPlain": "Lead technical evolution",
                    "employmentType": "FullTime",
                    "isListed": True,
                },
                {
                    "title": "Data Scientist",
                    "jobUrl": url2,
                    "applyUrl": f"{url2}/apply",
                    "publishedAt": None,
                    "location": None,
                    "secondaryLocations": [],
                    "isRemote": True,
                    "workplaceType": "Remote",
                    "descriptionHtml": "<p>Build ML pipelines</p>",
                    "isListed": True,
                },
                {
                    # isListed == False: Direct-link only posting, must be excluded from discovery
                    "title": "Confidential Role",
                    "jobUrl": unlisted_url,
                    "applyUrl": f"{unlisted_url}/apply",
                    "isListed": False,
                },
                {
                    # Malformed: missing jobUrl -> must be skipped fail-safe without crashing
                    "title": "Malformed Opening",
                    "isListed": True,
                },
            ],
        }

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response))
        client = httpx.AsyncClient(transport=transport)
        source = AshbyJobSource(board_token="monzo", company_name="Monzo", client=client)

        page = await source.discover(None, _ctx())
        # Only 2 jobs returned (unlisted and malformed are filtered)
        assert len(page.items) == 2
        assert page.next_cursor is None

        item1 = page.items[0]
        canon1 = canonicalize_url(url1)
        expected_id1 = f"ashby_{hashlib.sha256(canon1.encode('utf-8')).hexdigest()[:32]}"

        assert item1.ref.provider == "ashby"
        assert item1.ref.external_id == expected_id1
        assert item1.ref.url == canon1
        assert item1.title == "Principal Architect"
        assert item1.company_name == "Monzo"
        assert item1.posted_at == datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC)
        assert item1.location_raw == "London, UK"
        assert item1.additional_locations_raw == ("Manchester, UK",)
        assert item1.workplace_type_hint == "Hybrid"

        item2 = page.items[1]
        canon2 = canonicalize_url(url2)
        expected_id2 = f"ashby_{hashlib.sha256(canon2.encode('utf-8')).hexdigest()[:32]}"

        assert item2.ref.external_id == expected_id2
        assert item2.ref.url == canon2
        assert item2.posted_at is None
        assert item2.location_raw is None
        assert item2.workplace_type_hint == "Remote"

        # Distinct jobs have distinct deterministic IDs
        assert item1.ref.external_id != item2.ref.external_id

        # Verify cached details hit without extra network requests
        details1 = await source.fetch_details(item1.ref, _ctx())
        assert details1.description_raw == "<p>Lead technical evolution</p>"

        details2 = await source.fetch_details(item2.ref, _ctx())
        assert details2.description_raw == "<p>Build ML pipelines</p>"

    asyncio.run(scenario())


def test_ashby_fetch_details_fetches_board_when_not_cached() -> None:
    async def scenario() -> None:
        url = "https://jobs.ashbyhq.com/monzo/job-123"
        canon = canonicalize_url(url)
        ext_id = f"ashby_{hashlib.sha256(canon.encode('utf-8')).hexdigest()[:32]}"

        mock_response = {
            "apiVersion": "1",
            "jobs": [
                {
                    "title": "Principal Architect",
                    "jobUrl": url,
                    "descriptionHtml": "<p>Lead technical evolution</p>",
                    "publishedAt": "2026-03-01T14:00:00.000Z",
                    "isListed": True,
                }
            ],
        }

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response))
        client = httpx.AsyncClient(transport=transport)
        source = AshbyJobSource(board_token="monzo", client=client)

        ref = ExternalJobRef(
            provider="ashby",
            external_id=ext_id,
            url=canon,
        )
        details = await source.fetch_details(ref, _ctx())
        assert details.ref == ref
        assert details.description_raw == "<p>Lead technical evolution</p>"
        assert details.fetched_at.tzinfo == UTC

    asyncio.run(scenario())


def test_ashby_fetch_details_not_found_on_board() -> None:
    async def scenario() -> None:
        mock_response = {
            "apiVersion": "1",
            "jobs": [
                {
                    "title": "Other Job",
                    "jobUrl": "https://jobs.ashbyhq.com/monzo/other-job",
                    "isListed": True,
                }
            ],
        }

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response))
        client = httpx.AsyncClient(transport=transport)
        source = AshbyJobSource(board_token="monzo", client=client)

        ref = ExternalJobRef(
            provider="ashby",
            external_id="ashby_nonexistent1234567890abcdef12",
            url="https://jobs.ashbyhq.com/monzo/non-existent-id",
        )
        with pytest.raises(PortError) as exc_info:
            await source.fetch_details(ref, _ctx())
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST
        assert "not found" in exc_info.value.message

    asyncio.run(scenario())


def test_ashby_fetch_details_provider_mismatch() -> None:
    async def scenario() -> None:
        source = AshbyJobSource(board_token="monzo")
        ref = ExternalJobRef(provider="lever", external_id="ashby-1", url="https://example.com")
        with pytest.raises(PortError) as exc_info:
            await source.fetch_details(ref, _ctx())
        assert exc_info.value.code == PortErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_ashby_health_healthy() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"apiVersion": "1", "jobs": []})
        )
        client = httpx.AsyncClient(transport=transport)
        source = AshbyJobSource(board_token="monzo", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.HEALTHY
        assert health.reason_code is None

    asyncio.run(scenario())


def test_ashby_health_unavailable_on_error() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, text="Internal Server Error")
        )
        client = httpx.AsyncClient(transport=transport)
        source = AshbyJobSource(board_token="monzo", client=client)

        health = await source.health(_ctx())
        assert health.status == AdapterHealthStatus.UNAVAILABLE
        assert health.reason_code == "unavailable"

    asyncio.run(scenario())


def test_ashby_discover_malformed_shape() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"unexpected": 123})
        )
        client = httpx.AsyncClient(transport=transport)
        source = AshbyJobSource(board_token="monzo", client=client)

        with pytest.raises(PortError) as exc_info:
            await source.discover(None, _ctx())
        assert exc_info.value.code == PortErrorCode.PROVIDER_ERROR

    asyncio.run(scenario())
