"""Capability model shared by every port (docs/adr/0005-internal-ports-capability-model.md).

One shared `CapabilityStatus` vocabulary; each port defines its OWN small capability-key enum
(`JobSourceCapability`, `MessagingCapability`, ...) rather than sharing one giant enum that mixes
unrelated boundaries. This module intentionally stays generic over the capability-key type instead
of depending on any specific port.

`CapabilityManifest` is the ONLY way to look up a capability's status: construction itself rejects
a duplicate report, so there is no separate "please remember to validate this" step a caller could
skip, and no lookup path that could silently return the first of two conflicting reports.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    REQUIRES_PARTNER_ACCESS = "requires_partner_access"
    REQUIRES_USER_ACTION = "requires_user_action"
    DEGRADED = "degraded"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class CapabilityReport[CapabilityT]:
    capability: CapabilityT
    status: CapabilityStatus
    reason_code: str | None = None


class CapabilityManifest[CapabilityT]:
    """An immutable, validated set of `CapabilityReport`s for one port call.

    The same capability key appearing twice -- even with the same status -- is always an invalid,
    ambiguous combination (docs/adr/0005-internal-ports-capability-model.md): the constructor
    raises `ValueError` rather than accepting it, so an adapter cannot construct one, and no caller
    can end up holding one, with a duplicate inside. `status_for` is the only lookup: a capability
    that was never reported resolves deterministically to `UNSUPPORTED`, never an exception, never
    `None`, and never "whichever report happened to come first".
    """

    __slots__ = ("_by_capability",)

    def __init__(self, reports: Sequence[CapabilityReport[CapabilityT]]) -> None:
        by_capability: dict[CapabilityT, CapabilityReport[CapabilityT]] = {}
        for report in reports:
            if report.capability in by_capability:
                raise ValueError(f"duplicate capability report for {report.capability!r}")
            by_capability[report.capability] = report
        self._by_capability: MappingProxyType[CapabilityT, CapabilityReport[CapabilityT]] = (
            MappingProxyType(by_capability)
        )

    def status_for(self, capability: CapabilityT) -> CapabilityStatus:
        report = self._by_capability.get(capability)
        return report.status if report is not None else CapabilityStatus.UNSUPPORTED

    def reason_for(self, capability: CapabilityT) -> str | None:
        report = self._by_capability.get(capability)
        return report.reason_code if report is not None else None

    def __len__(self) -> int:
        return len(self._by_capability)

    def __iter__(self) -> Iterator[CapabilityReport[CapabilityT]]:
        return iter(self._by_capability.values())

    def __repr__(self) -> str:
        return f"CapabilityManifest({list(self._by_capability.values())!r})"
