"""Greenhouse Job Board adapter conforming to JobSource port (WP2.1-B).

Consumes Greenhouse Public Job Board API:
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}
- GET https://boards-api.greenhouse.io/v1/boards/{board_token} (health probe)

Invariants:
- Public discovery only; no universal application submission without employer API key.
- Strict timeout, SSRF pinning, and payload bounds enforced via base ATS HTTP client.
- Timestamp normalization to UTC-aware datetime.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from tapjob_api.jobs.sources.base import parse_json_response, send_ats_request
from tapjob_api.ports import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
    DiscoveredJob,
    DiscoveryPage,
    ExternalJobRef,
    JobDetails,
    JobSource,
    JobSourceCapability,
    PortError,
    PortErrorCode,
)

logger = logging.getLogger(__name__)

DEFAULT_GREENHOUSE_BASE_URL = "https://boards-api.greenhouse.io/v1"


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse ISO formatted timestamp string into UTC-aware datetime."""
    if not value or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


class GreenhouseJobSource:
    """Greenhouse Public Job Board adapter implementing JobSource protocol."""

    provider: str = "greenhouse"

    def __init__(
        self,
        board_token: str,
        *,
        company_name: str | None = None,
        base_url: str = DEFAULT_GREENHOUSE_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cleaned_token = board_token.strip()
        if not cleaned_token:
            raise ValueError("board_token must not be empty")
        self.board_token = cleaned_token
        self.company_name = company_name.strip() if company_name else None
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._cached_details: dict[str, JobDetails] = {}

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[JobSourceCapability]:
        return CapabilityManifest(
            [
                CapabilityReport(JobSourceCapability.DISCOVERY, CapabilityStatus.SUPPORTED),
                CapabilityReport(JobSourceCapability.JOB_DETAILS, CapabilityStatus.SUPPORTED),
                CapabilityReport(
                    JobSourceCapability.INCREMENTAL_SYNC, CapabilityStatus.UNSUPPORTED
                ),
            ]
        )

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        url = f"{self.base_url}/boards/{self.board_token}/jobs"
        response = await send_ats_request(
            self._client,
            "GET",
            url,
            provider=self.provider,
            ctx=ctx,
            params={"content": "true"},
        )
        data = parse_json_response(response, provider=self.provider)
        if not isinstance(data, dict) or "jobs" not in data or not isinstance(data["jobs"], list):
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="Unexpected Greenhouse response shape: missing 'jobs' array",
                transient=False,
            )

        jobs_raw: list[dict[str, Any]] = data["jobs"]
        items: list[DiscoveredJob] = []
        now = datetime.now(UTC)
        for j in jobs_raw:
            if not isinstance(j, dict) or "id" not in j:
                continue

            # Greenhouse Prospect Posts have internal_job_id = None/null
            # and are not concrete vacancies (Finding D)
            if j.get("internal_job_id") is None:
                continue

            ext_id = str(j["id"])
            job_url = (
                j.get("absolute_url")
                or f"https://boards.greenhouse.io/{self.board_token}/jobs/{ext_id}"
            )
            title = str(j.get("title") or "Untitled")
            posted_at = parse_iso_datetime(j.get("updated_at"))

            loc_data = j.get("location")
            loc_str: str | None = None
            if isinstance(loc_data, dict):
                loc_name = loc_data.get("name")
                loc_str = str(loc_name).strip() if loc_name else None
            elif isinstance(loc_data, str) and loc_data.strip():
                loc_str = loc_data.strip()

            ref = ExternalJobRef(
                provider=self.provider,
                external_id=ext_id,
                url=job_url,
            )

            # Cache listing content to avoid N+1 requests
            if j.get("content"):
                self._cached_details[ext_id] = JobDetails(
                    ref=ref,
                    description_raw=str(j["content"]),
                    fetched_at=now,
                )

            items.append(
                DiscoveredJob(
                    ref=ref,
                    title=title,
                    company_name=self.company_name or self.board_token,
                    posted_at=posted_at,
                    location_raw=loc_str,
                )
            )

        return DiscoveryPage(items=tuple(items), next_cursor=None)

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
        if ref.provider != self.provider:
            raise PortError(
                PortErrorCode.INVALID_REQUEST,
                provider=self.provider,
                message=f"Ref provider mismatch: expected {self.provider}, got {ref.provider}",
                transient=False,
            )

        if ref.external_id in self._cached_details:
            return self._cached_details[ref.external_id]

        url = f"{self.base_url}/boards/{self.board_token}/jobs/{ref.external_id}"
        response = await send_ats_request(
            self._client,
            "GET",
            url,
            provider=self.provider,
            ctx=ctx,
        )
        data = parse_json_response(response, provider=self.provider)
        if not isinstance(data, dict):
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="Invalid Greenhouse job detail response: expected object",
                transient=False,
            )

        content = str(data.get("content") or "")
        details = JobDetails(
            ref=ref,
            description_raw=content,
            fetched_at=datetime.now(UTC),
        )
        self._cached_details[ref.external_id] = details
        return details

    async def health(self, ctx: CallContext) -> AdapterHealth:
        url = f"{self.base_url}/boards/{self.board_token}"
        now = datetime.now(UTC)
        try:
            await send_ats_request(
                self._client,
                "GET",
                url,
                provider=self.provider,
                ctx=ctx,
            )
            return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=now)
        except PortError as exc:
            status = (
                AdapterHealthStatus.DEGRADED
                if exc.code in (PortErrorCode.RATE_LIMITED, PortErrorCode.TIMEOUT)
                else AdapterHealthStatus.UNAVAILABLE
            )
            return AdapterHealth(status=status, checked_at=now, reason_code=str(exc.code))


# Static verification that GreenhouseJobSource satisfies JobSource protocol
_typed_greenhouse_source: JobSource = GreenhouseJobSource("test-board")
