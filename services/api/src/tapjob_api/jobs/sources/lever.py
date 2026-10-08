"""Lever Postings API adapter conforming to JobSource port (WP2.1-B).

Consumes Lever Public Postings API:
- GET https://api.lever.co/v0/postings/{site}?mode=json
- GET https://api.lever.co/v0/postings/{site}/{posting_id}
- GET https://api.lever.co/v0/postings/{site}?limit=1 (health probe)

Invariants:
- Public discovery only; official submit requires employer API key.
- Safe HTTP boundaries (SSRF pinning, timeouts, bounded sizes) via base ATS HTTP client.
- Timestamp normalization from epoch milliseconds to UTC-aware datetime.
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

DEFAULT_LEVER_BASE_URL = "https://api.lever.co/v0"


def parse_epoch_ms(value: int | float | None) -> datetime | None:
    """Parse epoch millisecond timestamp into UTC-aware datetime."""
    if value is None:
        return None
    try:
        seconds = float(value) / 1000.0
        return datetime.fromtimestamp(seconds, tz=UTC)
    except Exception:
        return None


class LeverJobSource:
    """Lever Public Postings API adapter implementing JobSource protocol."""

    provider: str = "lever"

    def __init__(
        self,
        site: str,
        *,
        company_name: str | None = None,
        base_url: str = DEFAULT_LEVER_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cleaned_site = site.strip()
        if not cleaned_site:
            raise ValueError("site must not be empty")
        self.site = cleaned_site
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
        url = f"{self.base_url}/postings/{self.site}"
        params: dict[str, Any] = {"mode": "json"}
        limit = 100
        params["limit"] = limit
        if cursor is not None and cursor.isdigit():
            params["skip"] = int(cursor)

        response = await send_ats_request(
            self._client,
            "GET",
            url,
            provider=self.provider,
            ctx=ctx,
            params=params,
        )
        data = parse_json_response(response, provider=self.provider)
        if not isinstance(data, list):
            raise PortError(
                PortErrorCode.PROVIDER_ERROR,
                provider=self.provider,
                message="Unexpected Lever response shape: expected JSON array",
                transient=False,
            )

        items: list[DiscoveredJob] = []
        now = datetime.now(UTC)
        for posting in data:
            if not isinstance(posting, dict) or "id" not in posting:
                continue
            ext_id = str(posting["id"])
            job_url = posting.get("hostedUrl") or f"https://jobs.lever.co/{self.site}/{ext_id}"
            title = str(posting.get("text") or "Untitled")
            posted_at = parse_epoch_ms(posting.get("createdAt"))

            categories = posting.get("categories")
            loc_str: str | None = None
            additional_locations: list[str] = []
            if isinstance(categories, dict):
                loc_val = categories.get("location")
                if loc_val and str(loc_val).strip():
                    loc_str = str(loc_val).strip()
                all_locs = categories.get("allLocations")
                if isinstance(all_locs, list):
                    for loc in all_locs:
                        if isinstance(loc, str) and loc.strip():
                            cleaned_loc = loc.strip()
                            if (
                                loc_str is None or cleaned_loc.lower() != loc_str.lower()
                            ) and cleaned_loc not in additional_locations:
                                additional_locations.append(cleaned_loc)

            wp_type = posting.get("workplaceType")
            workplace_hint = str(wp_type).strip() if (wp_type and str(wp_type).strip()) else None

            ref = ExternalJobRef(
                provider=self.provider,
                external_id=ext_id,
                url=job_url,
            )

            # Lever mode=json listing payload includes description, lists, and additional sections
            parts: list[str] = []
            desc_html = posting.get("description") or posting.get("descriptionPlain")
            if desc_html:
                parts.append(str(desc_html))
            lists = posting.get("lists")
            if isinstance(lists, list):
                for lst in lists:
                    if isinstance(lst, dict):
                        heading = lst.get("text")
                        content = lst.get("content")
                        if heading:
                            parts.append(f"<h3>{heading}</h3>")
                        if content:
                            parts.append(str(content))
            additional = posting.get("additional") or posting.get("additionalPlain")
            if additional:
                parts.append(str(additional))
            description_raw = "\n\n".join(parts) if parts else ""
            self._cached_details[ext_id] = JobDetails(
                ref=ref,
                description_raw=description_raw,
                fetched_at=now,
            )

            items.append(
                DiscoveredJob(
                    ref=ref,
                    title=title,
                    company_name=self.company_name or self.site,
                    posted_at=posted_at,
                    location_raw=loc_str,
                    additional_locations_raw=tuple(additional_locations),
                    workplace_type_hint=workplace_hint,
                )
            )

        # If we received a full page, calculate next_cursor for pagination
        next_cursor = None
        if len(data) == limit:
            current_skip = int(cursor) if (cursor and cursor.isdigit()) else 0
            next_cursor = str(current_skip + limit)

        return DiscoveryPage(items=tuple(items), next_cursor=next_cursor)

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

        url = f"{self.base_url}/postings/{self.site}/{ref.external_id}"
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
                message="Invalid Lever job detail response: expected object",
                transient=False,
            )

        # Assemble description from description HTML, lists and additional sections
        parts: list[str] = []
        desc_html = data.get("description") or data.get("descriptionPlain")
        if desc_html:
            parts.append(str(desc_html))

        lists = data.get("lists")
        if isinstance(lists, list):
            for lst in lists:
                if isinstance(lst, dict):
                    heading = lst.get("text")
                    content = lst.get("content")
                    if heading:
                        parts.append(f"<h3>{heading}</h3>")
                    if content:
                        parts.append(str(content))

        additional = data.get("additional") or data.get("additionalPlain")
        if additional:
            parts.append(str(additional))

        description_raw = "\n\n".join(parts) if parts else ""
        details = JobDetails(
            ref=ref,
            description_raw=description_raw,
            fetched_at=datetime.now(UTC),
        )
        self._cached_details[ref.external_id] = details
        return details

    async def health(self, ctx: CallContext) -> AdapterHealth:
        url = f"{self.base_url}/postings/{self.site}"
        now = datetime.now(UTC)
        try:
            await send_ats_request(
                self._client,
                "GET",
                url,
                provider=self.provider,
                ctx=ctx,
                params={"limit": 1},
            )
            return AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=now)
        except PortError as exc:
            status = (
                AdapterHealthStatus.DEGRADED
                if exc.code in (PortErrorCode.RATE_LIMITED, PortErrorCode.TIMEOUT)
                else AdapterHealthStatus.UNAVAILABLE
            )
            return AdapterHealth(status=status, checked_at=now, reason_code=str(exc.code))


# Static verification that LeverJobSource satisfies JobSource protocol
_typed_lever_source: JobSource = LeverJobSource(site="test")
