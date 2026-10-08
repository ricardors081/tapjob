"""Ashby Job Board adapter conforming to JobSource port (WP2.1-B).

Consumes Ashby Official Public Job Board API:
- GET https://api.ashbyhq.com/posting-api/job-board/{board_token} (board listing & health probe)

Invariants:
- Conforms strictly to official public Ashby job-board contract
  (GET /posting-api/job-board/{board}).
  The official public API returns full job descriptions in the board listing.
- Does not call unofficial or non-existent endpoints (e.g. /job/{job_id}).
- Stores discovered job details in-memory for the lifecycle of discovery to satisfy `fetch_details`.
- If `fetch_details` is invoked in isolation, performs a single board fetch to locate the job.
- Public discovery only; official submit requires candidate credentials.
- Safe HTTP boundaries (SSRF pinning, timeouts, bounded sizes) via base ATS HTTP client.
- Timestamp normalization to UTC-aware datetime.
"""

import hashlib
import logging
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx

from tapjob_api.jobs.sources.base import parse_json_response, send_ats_request
from tapjob_api.jobs.url_canonicalization import canonicalize_url
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

DEFAULT_ASHBY_BASE_URL = "https://api.ashbyhq.com/posting-api"


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


class AshbyJobSource:
    """Ashby Public Job Board API adapter implementing JobSource protocol."""

    provider: str = "ashby"

    def __init__(
        self,
        board_token: str,
        *,
        company_name: str | None = None,
        base_url: str = DEFAULT_ASHBY_BASE_URL,
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
        url = f"{self.base_url}/job-board/{self.board_token}"
        response = await send_ats_request(
            self._client,
            "GET",
            url,
            provider=self.provider,
            ctx=ctx,
        )
        data = parse_json_response(response, provider=self.provider)
        if not isinstance(data, dict) or "jobs" not in data or not isinstance(data["jobs"], list):
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="Unexpected Ashby response shape: missing 'jobs' array",
                transient=False,
            )

        jobs_raw: list[dict[str, Any]] = data["jobs"]
        items: list[DiscoveredJob] = []
        now = datetime.now(UTC)

        for j in jobs_raw:
            if not isinstance(j, dict):
                continue

            # Direct link only / unlisted jobs are excluded from global discovery
            if j.get("isListed") is False:
                continue

            raw_job_url = j.get("jobUrl")
            if not raw_job_url or not isinstance(raw_job_url, str) or not raw_job_url.strip():
                # Fail-safe skip malformed entry without valid jobUrl
                continue

            parsed_url = urllib.parse.urlsplit(raw_job_url.strip())
            if parsed_url.scheme.lower() not in ("http", "https") or not parsed_url.netloc:
                continue

            # Deterministic, bounded external_id derived solely from canonical jobUrl
            canon_job_url = canonicalize_url(raw_job_url)
            ext_digest = hashlib.sha256(canon_job_url.encode("utf-8")).hexdigest()[:32]
            ext_id = f"ashby_{ext_digest}"

            title = str(j.get("title") or "Untitled")
            posted_at = parse_iso_datetime(j.get("publishedAt"))

            loc_val = j.get("location")
            loc_str: str | None = (
                str(loc_val).strip() if (loc_val and str(loc_val).strip()) else None
            )

            additional_locations: list[str] = []
            sec_locs = j.get("secondaryLocations")
            if isinstance(sec_locs, list):
                for sec in sec_locs:
                    if isinstance(sec, dict):
                        s_loc = sec.get("location")
                        if s_loc and isinstance(s_loc, str) and s_loc.strip():
                            cleaned_sec = s_loc.strip()
                            if (
                                loc_str is None or cleaned_sec.lower() != loc_str.lower()
                            ) and cleaned_sec not in additional_locations:
                                additional_locations.append(cleaned_sec)
                    elif isinstance(sec, str) and sec.strip():
                        cleaned_sec = sec.strip()
                        if (
                            loc_str is None or cleaned_sec.lower() != loc_str.lower()
                        ) and cleaned_sec not in additional_locations:
                            additional_locations.append(cleaned_sec)

            is_remote = bool(j.get("isRemote", False))
            wp_type = j.get("workplaceType")
            workplace_hint: str | None = None
            if wp_type and isinstance(wp_type, str) and wp_type.strip():
                workplace_hint = wp_type.strip()
            elif is_remote:
                workplace_hint = "Remote"

            ref = ExternalJobRef(
                provider=self.provider,
                external_id=ext_id,
                url=canon_job_url,
            )

            # The official public Ashby API provides descriptions directly in board listing
            desc = j.get("descriptionHtml") or j.get("descriptionPlain") or ""
            self._cached_details[ext_id] = JobDetails(
                ref=ref,
                description_raw=str(desc),
                fetched_at=now,
            )

            items.append(
                DiscoveredJob(
                    ref=ref,
                    title=title,
                    company_name=self.company_name or self.board_token,
                    posted_at=posted_at,
                    location_raw=loc_str,
                    additional_locations_raw=tuple(additional_locations),
                    workplace_type_hint=workplace_hint,
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

        # First check details cached during discovery
        if ref.external_id in self._cached_details:
            return self._cached_details[ref.external_id]

        # If not cached, fetch board listing once to locate the job
        await self.discover(None, ctx)

        if ref.external_id in self._cached_details:
            return self._cached_details[ref.external_id]

        raise PortError(
            PortErrorCode.INVALID_REQUEST,
            provider=self.provider,
            message=f"Job {ref.external_id} not found on Ashby board '{self.board_token}'",
            transient=False,
        )

    async def health(self, ctx: CallContext) -> AdapterHealth:
        url = f"{self.base_url}/job-board/{self.board_token}"
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


# Static verification that AshbyJobSource satisfies JobSource protocol
_typed_ashby_source: JobSource = AshbyJobSource("test-board")
