"""Cursor pagination primitives (docs/adr/0004-http-errors-pagination-correlation-idempotency.md).

No endpoint uses this yet -- WP0.2-A ships contracts, not product routes
(docs/IMPLEMENTATION_PLAN.md WP0.2). WP2.3's job feed is the first real consumer. This module
exists now so every future list endpoint shares one cursor codec/envelope instead of inventing its
own (docs/ENGINEERING_STANDARDS.md §1).

The cursor is NOT tamper-evident: there is no HMAC/signature in this checkpoint. Authorization and
ownership must always be re-checked independently of cursor contents on every request; nothing may
ever be inferred as "already authorized" merely because it round-tripped through a cursor.
"""

import base64
import binascii
import hashlib
import json
from http import HTTPStatus

from pydantic import BaseModel, Field, ValidationError

from tapjob_api.http.errors import ApiError, ErrorCode

# Named defaults/limits (docs/ENGINEERING_STANDARDS.md §2: no magic page-size literals in feature
# code). A resource may lower its own default/max; it must not raise MAX_PAGE_SIZE without a new
# documented decision.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Bumped whenever CursorPayload's shape changes incompatibly; decode_cursor rejects anything else
# rather than guessing at an old/foreign shape.
CURSOR_SCHEMA_VERSION = 1

# A real cursor (schema version + sort name + a 16-char filters fingerprint + a handful of sort-key
# fields) is a few hundred characters at most. Bounded generously above that so decode_cursor can
# reject an oversized value before spending any base64/JSON-parsing work on it -- the same class of
# guard tapjob_api.http.request_context applies to X-Request-ID.
MAX_CURSOR_LENGTH = 2048


class CursorPage[ItemT](BaseModel):
    """Canonical list envelope. No `total` by default: counting a mutable, relevance-ordered feed
    exactly is expensive and the count is stale the moment it is computed; a resource that truly
    needs one exposes it as an explicit, separately named field instead of overloading this one."""

    items: list[ItemT]
    next_cursor: str | None = Field(default=None, alias="nextCursor")

    model_config = {"populate_by_name": True}


class CursorPayload(BaseModel):
    """What a cursor actually encodes, opaque to the client. `sort` and `filters_fingerprint` bind
    the cursor to the query that produced it, so paging with a cursor from a different sort/filter
    combination is rejected instead of silently returning a wrong/unstable page."""

    v: int = CURSOR_SCHEMA_VERSION
    sort: str
    filters_fingerprint: str
    last_key: dict[str, str | int | float]


def fingerprint_filters(filters: dict[str, object]) -> str:
    """Stable short fingerprint of a filter set, for binding a cursor to the filters that produced
    it. Not a security control -- just detects "the client changed filters mid-pagination"."""
    canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_cursor(payload: CursorPayload) -> str:
    raw = payload.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _invalid_cursor() -> ApiError:
    return ApiError(
        ErrorCode.INVALID_CURSOR,
        status_code=HTTPStatus.BAD_REQUEST,
    )


def decode_cursor(
    cursor: str, *, expected_sort: str, expected_filters_fingerprint: str
) -> CursorPayload:
    """Raises ApiError(INVALID_CURSOR) for anything malformed, the wrong schema version, or a
    sort/filter combination that does not match the current request's. Never raises any other
    exception type: every failure mode is a client-facing 400, not a 500."""
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise _invalid_cursor()

    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as error:
        raise _invalid_cursor() from error

    try:
        payload = CursorPayload.model_validate_json(raw)
    except ValidationError as error:
        raise _invalid_cursor() from error

    if payload.v != CURSOR_SCHEMA_VERSION:
        raise _invalid_cursor()
    if payload.sort != expected_sort or payload.filters_fingerprint != expected_filters_fingerprint:
        raise _invalid_cursor()
    return payload
