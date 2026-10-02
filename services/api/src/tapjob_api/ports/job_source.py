"""JobSource: provider-neutral job discovery/detail boundary
(docs/adr/0005-internal-ports-capability-model.md).

No adapter is implemented in this checkpoint (Greenhouse/Lever/Ashby arrive in WP2.1,
docs/IMPLEMENTATION_PLAN.md). The DTOs below are deliberately minimal and provisional: WP2.1 may
extend them once real adapters exist, via a documented decision, not silently.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext, normalize_utc_field
from tapjob_api.ports.capabilities import CapabilityManifest


class JobSourceCapability(StrEnum):
    DISCOVERY = "discovery"
    JOB_DETAILS = "job_details"
    INCREMENTAL_SYNC = "incremental_sync"


@dataclass(frozen=True, slots=True)
class ExternalJobRef:
    """Identifies one job at its source. Never substitutes TapJob's own internal Job id
    (docs/DATA_MODEL.md §1 "External IDs never substitute internal UUIDs")."""

    provider: str
    external_id: str
    url: str


@dataclass(frozen=True, slots=True)
class DiscoveredJob:
    """One item from a discovery listing -- intentionally thin (no description body): fetching
    full details is a separate call (`fetch_details`), since a discovery page can be large and
    most normalization/dedupe work (docs/DATA_MODEL.md §5) only needs this much."""

    ref: ExternalJobRef
    title: str
    company_name: str
    posted_at: datetime | None

    def __post_init__(self) -> None:
        normalize_utc_field(self, "posted_at", optional=True)


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    items: tuple[DiscoveredJob, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class JobDetails:
    ref: ExternalJobRef
    description_raw: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        normalize_utc_field(self, "fetched_at")


class JobSource(Protocol):
    provider: str

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[JobSourceCapability]: ...

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage: ...

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...
