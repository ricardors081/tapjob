"""Every normalized timestamp field across the port DTOs applies the same rule
(docs/ENGINEERING_STANDARDS.md §10, docs/adr/0005-internal-ports-capability-model.md): reject a
naive datetime, normalize an aware-but-non-UTC datetime to UTC, and leave an optional field alone
when it is genuinely absent. This file proves the rule actually reaches every DTO it claims to,
not just the shared helper it is built from."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from tapjob_api.ports import (
    AdapterHealth,
    AdapterHealthStatus,
    ChannelIdentity,
    DiscoveredJob,
    ExternalJobRef,
    InboundMessage,
    InboxMessageSummary,
    JobDetails,
    ResultMetadata,
    SendReceipt,
)
from tapjob_api.ports._common import require_utc_datetime

_NON_UTC_MINUS_3 = timezone(timedelta(hours=-3))
_NAIVE = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 (deliberately naive, to trigger the guard)
_AWARE_NON_UTC = datetime(2026, 1, 1, 9, 0, 0, tzinfo=_NON_UTC_MINUS_3)
_EXPECTED_UTC = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _TzinfoWithNoOffset(tzinfo):
    """A `tzinfo` that is present (`tzinfo is not None`) but reports no UTC offset -- per Python's
    own awareness definition (`tzinfo is not None AND tzinfo.utcoffset(dt) is not None`), a
    datetime carrying this `tzinfo` is still naive/ambiguous, not aware."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


_AWARE_SHAPED_BUT_ACTUALLY_NAIVE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=_TzinfoWithNoOffset())


# -- the shared helper itself ------------------------------------------------------------------


def test_require_utc_datetime_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        require_utc_datetime(_NAIVE, field_name="at")


def test_require_utc_datetime_normalizes_aware_non_utc_to_utc() -> None:
    assert require_utc_datetime(_AWARE_NON_UTC, field_name="at") == _EXPECTED_UTC


def test_require_utc_datetime_leaves_utc_unchanged() -> None:
    assert require_utc_datetime(_EXPECTED_UTC, field_name="at") == _EXPECTED_UTC


def test_require_utc_datetime_rejects_a_tzinfo_whose_utcoffset_is_none() -> None:
    # tzinfo is not None here, but utcoffset(dt) is -- per Python's own definition this is still
    # naive/ambiguous, so a bare `tzinfo is None` check would wrongly accept it.
    with pytest.raises(ValueError, match="timezone-aware"):
        require_utc_datetime(_AWARE_SHAPED_BUT_ACTUALLY_NAIVE, field_name="at")


# -- AdapterHealth.checked_at -------------------------------------------------------------------


def test_adapter_health_checked_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=_NAIVE)


def test_adapter_health_checked_at_normalizes_to_utc() -> None:
    health = AdapterHealth(status=AdapterHealthStatus.HEALTHY, checked_at=_AWARE_NON_UTC)
    assert health.checked_at == _EXPECTED_UTC


# -- ResultMetadata.observed_at -----------------------------------------------------------------


def test_result_metadata_observed_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ResultMetadata(provider="fake", observed_at=_NAIVE)


def test_result_metadata_observed_at_normalizes_to_utc() -> None:
    metadata = ResultMetadata(provider="fake", observed_at=_AWARE_NON_UTC)
    assert metadata.observed_at == _EXPECTED_UTC


# -- DiscoveredJob.posted_at (optional) -----------------------------------------------------------


def _job_ref() -> ExternalJobRef:
    return ExternalJobRef(provider="fake", external_id="1", url="https://example.com/1")


def test_discovered_job_posted_at_none_passes_through_untouched() -> None:
    job = DiscoveredJob(ref=_job_ref(), title="Engineer", company_name="Acme", posted_at=None)
    assert job.posted_at is None


def test_discovered_job_posted_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        DiscoveredJob(ref=_job_ref(), title="Engineer", company_name="Acme", posted_at=_NAIVE)


def test_discovered_job_posted_at_normalizes_to_utc() -> None:
    job = DiscoveredJob(
        ref=_job_ref(), title="Engineer", company_name="Acme", posted_at=_AWARE_NON_UTC
    )
    assert job.posted_at == _EXPECTED_UTC


# -- JobDetails.fetched_at (required) ------------------------------------------------------------


def test_job_details_fetched_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        JobDetails(ref=_job_ref(), description_raw="A job.", fetched_at=_NAIVE)


def test_job_details_fetched_at_normalizes_to_utc() -> None:
    details = JobDetails(ref=_job_ref(), description_raw="A job.", fetched_at=_AWARE_NON_UTC)
    assert details.fetched_at == _EXPECTED_UTC


# -- InboundMessage.received_at / SendReceipt.accepted_at ----------------------------------------


def _sender() -> ChannelIdentity:
    return ChannelIdentity(channel="whatsapp", external_id="+5500000000000")


def test_inbound_message_received_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        InboundMessage(
            sender=_sender(),
            provider_message_id="wamid.1",
            received_at=_NAIVE,
            text="oi",
            interactive_reply_id=None,
        )


def test_inbound_message_received_at_normalizes_to_utc() -> None:
    message = InboundMessage(
        sender=_sender(),
        provider_message_id="wamid.1",
        received_at=_AWARE_NON_UTC,
        text="oi",
        interactive_reply_id=None,
    )
    assert message.received_at == _EXPECTED_UTC


def test_send_receipt_accepted_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SendReceipt(provider_message_id="wamid.1", accepted_at=_NAIVE)


def test_send_receipt_accepted_at_normalizes_to_utc() -> None:
    receipt = SendReceipt(provider_message_id="wamid.1", accepted_at=_AWARE_NON_UTC)
    assert receipt.accepted_at == _EXPECTED_UTC


# -- InboxMessageSummary.received_at --------------------------------------------------------------


def test_inbox_message_summary_received_at_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        InboxMessageSummary(provider_message_id="msg-1", received_at=_NAIVE, snippet="hi")


def test_inbox_message_summary_received_at_normalizes_to_utc() -> None:
    summary = InboxMessageSummary(
        provider_message_id="msg-1", received_at=_AWARE_NON_UTC, snippet="hi"
    )
    assert summary.received_at == _EXPECTED_UTC
