"""InboxConnector: provider-neutral mailbox boundary
(docs/adr/0005-internal-ports-capability-model.md).

Gmail is a future adapter (WP6.1/6.2); nothing is implemented here. `list_changes` returns
metadata/snippets only -- full-body persistence is never assumed
(docs/SECURITY_PRIVACY.md §4: "metadata/snippet -> heuristic -> selective body fetch -> normalized
event"); a caller fetches a body explicitly, one message at a time, only when it decides it is
actually needed.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from tapjob_api.ports._common import AdapterHealth, CallContext, normalize_utc_field
from tapjob_api.ports.capabilities import CapabilityManifest


class InboxCapability(StrEnum):
    METADATA_QUERY = "metadata_query"
    SELECTIVE_BODY_FETCH = "selective_body_fetch"
    WATCH_PUSH = "watch_push"


@dataclass(frozen=True, slots=True)
class InboxAccountRef:
    provider: str
    account_external_id: str


@dataclass(frozen=True, slots=True)
class InboxMessageSummary:
    provider_message_id: str
    received_at: datetime
    snippet: str

    def __post_init__(self) -> None:
        normalize_utc_field(self, "received_at")


@dataclass(frozen=True, slots=True)
class InboxChangeBatch:
    summaries: tuple[InboxMessageSummary, ...]
    next_cursor: str | None
    full_resync_required: bool


@dataclass(frozen=True, slots=True)
class InboxMessageBody:
    provider_message_id: str
    body_text: str


class InboxConnector(Protocol):
    provider: str

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[InboxCapability]: ...

    async def list_changes(
        self, account: InboxAccountRef, cursor: str | None, ctx: CallContext
    ) -> InboxChangeBatch: ...

    async def fetch_body(
        self, account: InboxAccountRef, provider_message_id: str, ctx: CallContext
    ) -> InboxMessageBody: ...

    async def health(self, ctx: CallContext) -> AdapterHealth: ...
