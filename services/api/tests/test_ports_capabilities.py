"""The capability model must reject an invalid combination AT CONSTRUCTION and resolve lookups
deterministically (docs/adr/0005-internal-ports-capability-model.md). `CapabilityManifest` is the
only way to hold or query a set of `CapabilityReport`s: there is no separate opt-in validation step
a caller could skip, and no lookup path that could silently return the first of two conflicting
reports."""

from enum import StrEnum

import pytest

from tapjob_api.ports import CapabilityManifest, CapabilityReport, CapabilityStatus


class _FakeCapability(StrEnum):
    ALPHA = "alpha"
    BETA = "beta"


def test_manifest_with_no_duplicates_constructs_and_resolves_reported_status() -> None:
    manifest = CapabilityManifest(
        [
            CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED),
            CapabilityReport(_FakeCapability.BETA, CapabilityStatus.UNSUPPORTED),
        ]
    )

    assert manifest.status_for(_FakeCapability.ALPHA) == CapabilityStatus.SUPPORTED
    assert manifest.status_for(_FakeCapability.BETA) == CapabilityStatus.UNSUPPORTED


def test_duplicate_capability_report_with_the_same_status_fails_construction() -> None:
    with pytest.raises(ValueError, match="duplicate capability report"):
        CapabilityManifest(
            [
                CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED),
                CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED),
            ]
        )


def test_duplicate_capability_report_with_different_statuses_fails_construction() -> None:
    with pytest.raises(ValueError, match="duplicate capability report"):
        CapabilityManifest(
            [
                CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED),
                CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.DISABLED),
            ]
        )


def test_no_caller_can_construct_a_manifest_holding_a_duplicate() -> None:
    # There is no alternate constructor, no "unsafe" factory and no way to bypass __init__: any
    # attempt to build a manifest from a duplicate-containing sequence raises before a manifest
    # object exists, so no caller can end up holding one with a duplicate inside.
    reports = [
        CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED),
        CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.DEGRADED),
    ]

    with pytest.raises(ValueError):
        CapabilityManifest(reports)


def test_status_for_an_unreported_capability_is_deterministically_unsupported() -> None:
    manifest = CapabilityManifest(
        [CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED)]
    )

    assert manifest.status_for(_FakeCapability.BETA) == CapabilityStatus.UNSUPPORTED


def test_status_for_an_empty_manifest_is_unsupported() -> None:
    manifest = CapabilityManifest[_FakeCapability]([])
    assert manifest.status_for(_FakeCapability.ALPHA) == CapabilityStatus.UNSUPPORTED


def test_reason_for_returns_the_reported_reason_code_or_none() -> None:
    manifest = CapabilityManifest(
        [CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.DISABLED, reason_code="no-quota")]
    )

    assert manifest.reason_for(_FakeCapability.ALPHA) == "no-quota"
    assert manifest.reason_for(_FakeCapability.BETA) is None


def test_manifest_len_and_iteration_reflect_the_validated_reports() -> None:
    reports = [
        CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED),
        CapabilityReport(_FakeCapability.BETA, CapabilityStatus.UNSUPPORTED),
    ]
    manifest = CapabilityManifest(reports)

    assert len(manifest) == 2
    assert {report.capability for report in manifest} == {
        _FakeCapability.ALPHA,
        _FakeCapability.BETA,
    }


def test_capability_report_is_frozen() -> None:
    report = CapabilityReport(_FakeCapability.ALPHA, CapabilityStatus.SUPPORTED)

    with pytest.raises(AttributeError):
        report.status = CapabilityStatus.DISABLED  # type: ignore[misc]


def test_every_capability_status_value_is_reachable_and_distinct() -> None:
    assert len(set(CapabilityStatus)) == 6
