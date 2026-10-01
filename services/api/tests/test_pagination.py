"""Cursor codec round-trip and rejection behaviour (docs/adr/0004-http-errors-pagination-
correlation-idempotency.md). No endpoint uses CursorPage yet; WP2.3's job feed is the first real
consumer (docs/IMPLEMENTATION_PLAN.md WP0.2/WP2.3)."""

import base64
from http import HTTPStatus

import pytest

from tapjob_api.http.errors import ApiError, ErrorCode
from tapjob_api.http.pagination import (
    MAX_CURSOR_LENGTH,
    CursorPage,
    CursorPayload,
    decode_cursor,
    encode_cursor,
    fingerprint_filters,
)


def _payload(**overrides: object) -> CursorPayload:
    base: dict[str, object] = {
        "sort": "relevance",
        "filters_fingerprint": fingerprint_filters({"workplaceType": "remote"}),
        "last_key": {"score": 0.82, "id": "job-123"},
    }
    base.update(overrides)
    return CursorPayload.model_validate(base)


def test_round_trip_preserves_the_payload() -> None:
    payload = _payload()
    cursor = encode_cursor(payload)

    decoded = decode_cursor(
        cursor,
        expected_sort=payload.sort,
        expected_filters_fingerprint=payload.filters_fingerprint,
    )

    assert decoded == payload


def test_cursor_is_url_safe_and_has_no_padding() -> None:
    cursor = encode_cursor(_payload())

    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor


def test_malformed_base64_is_rejected_as_invalid_cursor() -> None:
    with pytest.raises(ApiError) as exc_info:
        decode_cursor(
            "not-valid-base64!!!", expected_sort="relevance", expected_filters_fingerprint="x"
        )

    assert exc_info.value.code == ErrorCode.INVALID_CURSOR
    assert exc_info.value.status_code == HTTPStatus.BAD_REQUEST


def test_an_oversized_cursor_is_rejected_before_any_decoding_work() -> None:
    oversized = "a" * (MAX_CURSOR_LENGTH + 1)

    with pytest.raises(ApiError) as exc_info:
        decode_cursor(oversized, expected_sort="relevance", expected_filters_fingerprint="x")

    assert exc_info.value.code == ErrorCode.INVALID_CURSOR


def test_base64_that_is_not_the_expected_json_shape_is_rejected() -> None:
    garbage = base64.urlsafe_b64encode(b"not json at all").decode("ascii").rstrip("=")

    with pytest.raises(ApiError) as exc_info:
        decode_cursor(garbage, expected_sort="relevance", expected_filters_fingerprint="x")

    assert exc_info.value.code == ErrorCode.INVALID_CURSOR


def test_incompatible_schema_version_is_rejected() -> None:
    payload = _payload()
    cursor = encode_cursor(payload)
    tampered = CursorPayload.model_validate({**payload.model_dump(), "v": 999})
    tampered_cursor = encode_cursor(tampered)

    with pytest.raises(ApiError) as exc_info:
        decode_cursor(
            tampered_cursor,
            expected_sort=payload.sort,
            expected_filters_fingerprint=payload.filters_fingerprint,
        )
    assert exc_info.value.code == ErrorCode.INVALID_CURSOR
    # The original (correctly versioned) cursor still works, proving the fixture isn't broken.
    decode_cursor(
        cursor,
        expected_sort=payload.sort,
        expected_filters_fingerprint=payload.filters_fingerprint,
    )


def test_sort_mismatch_is_rejected() -> None:
    payload = _payload(sort="newest")
    cursor = encode_cursor(payload)

    with pytest.raises(ApiError) as exc_info:
        decode_cursor(
            cursor,
            expected_sort="relevance",
            expected_filters_fingerprint=payload.filters_fingerprint,
        )
    assert exc_info.value.code == ErrorCode.INVALID_CURSOR


def test_filters_fingerprint_mismatch_is_rejected() -> None:
    payload = _payload()
    cursor = encode_cursor(payload)

    with pytest.raises(ApiError) as exc_info:
        decode_cursor(cursor, expected_sort=payload.sort, expected_filters_fingerprint="different")
    assert exc_info.value.code == ErrorCode.INVALID_CURSOR


def test_fingerprint_is_stable_regardless_of_key_order() -> None:
    first = fingerprint_filters({"a": 1, "b": 2})
    second = fingerprint_filters({"b": 2, "a": 1})

    assert first == second


def test_fingerprint_changes_when_filters_change() -> None:
    assert fingerprint_filters({"a": 1}) != fingerprint_filters({"a": 2})


def test_cursor_page_serializes_with_camel_case_alias() -> None:
    page = CursorPage[str](items=["a", "b"], next_cursor="abc")

    assert page.model_dump(by_alias=True) == {"items": ["a", "b"], "nextCursor": "abc"}


def test_cursor_page_next_cursor_defaults_to_none_meaning_no_more_pages() -> None:
    page = CursorPage[str](items=["a"])

    assert page.model_dump(by_alias=True, exclude_none=True) == {"items": ["a"]}
